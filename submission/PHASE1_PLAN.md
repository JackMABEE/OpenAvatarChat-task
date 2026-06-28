# Phase 1 — Implementation Plan (prose; no code)

Read `PHASE0_RECON.md` first. This plan refers throughout to the real symbols and file
paths found there.

**Global constraints applied to every task below:**
- Reuse the existing barge-in/cancel chain — never invent a parallel cancel path.
- Anything the avatar emits must NOT arm or fire barge-in against itself.
- Preserve the existing participant-info block: any prompt change layers **after**
  `_refresh_system_prompt(...)` so the personalization block remains opaque/inert.

---

## Task 1 — LLM-latency filler ("thinking" cue) [lowest risk, do first]

### What we are filling
After the user's utterance ends (ASR forwards `is_last_data=True` into
`HandlerLLM.handle`), the LLM call blocks at
`llm_handler_openai_compatible.py:211-218` until the first chunk arrives, which then
flows into TTS → LiteAvatar. During that blocking window the avatar is silent and
frozen. We want a short verbal filler (e.g. "嗯…" or "let me think") to play only when
that gap exceeds a threshold (~600 ms), and to be cleanly replaced by the real answer
the moment the first real TTS audio is ready.

### Where to touch
- **Primary:** `src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py`,
  specifically the area around lines 196-237 — the path from "we know the user is
  done" (`text_end = inputs.is_last_data`) through "first non-empty chunk arrives".
- **Secondary:** *no change* to TTS, LiteAvatar, RtcClient, VAD, or the frontend. The
  filler is just another `AVATAR_TEXT` chunk submitted into the *same* stream the
  real reply will later submit chunks into — so it inherits cancel/flush behavior
  automatically.

### The change in prose
1. After opening the per-turn `AVATAR_TEXT` stream (lines 184-187 — already there) and
   before the `chat.completions.create(stream=True)` blocking call, kick off a small
   **delayed-filler timer** (e.g. 600 ms; configurable on `LLMConfig`). Implementation
   note: doing it as a `threading.Timer` is the smallest possible change because
   `handle()` runs on a worker thread already; the timer's callback would call back
   into the same `streamer.stream_data(...)` with a tiny `DataBundle` containing the
   filler text (one short utterance, e.g. "嗯…"). The timer must capture the current
   `stream_key` so it can no-op if the stream has already been cancelled.
2. Add a one-shot guard: **cancel the timer as soon as the first non-empty
   `chunk.choices[0].delta.content` arrives** in the `for chunk in completion:` loop
   (around line 231). If the timer hadn't fired yet, cancel it — no filler. If it has
   fired, we still proceed normally: TTS will already have started speaking the
   filler, but the next chunk we send is the real answer, which the downstream stream
   handles as just more chunks on the same stream.
3. Because the filler and the real answer share *one* output stream (the one opened
   at line 186 with `cancelable=True`), the filler is interruptible by:
   - the user's barge-in (via the same `STREAM_CANCEL` chain that already cancels TTS
     and clears `RtcClientSessionDelegate.flush_output`);
   - the real LLM answer (which simply submits chunks after the filler text — TTS
     keeps appending to the same audio output stream).

### How it reuses the existing cancel/flush path
No new cancel surface. The filler text becomes part of the existing per-turn output
stream the LLM is already using. When the user interrupts during the filler:
`rtc_stream.py:283-291` → `InterruptHandler.on_signal` →
`cancel_streams_by_type(CLIENT_PLAYBACK)` →
- LLM's `on_signal` removes the stream key from `active_stream_keys`
  (`llm_handler_openai_compatible.py:265-271`);
- TTS's `on_signal` calls `streaming_cancel()` on the current synthesizer
  (`tts_handler_cosyvoice_bailian.py:199-218`);
- RTC client's `on_signal` calls `flush_output()`
  (`client_handler_rtc.py:629-633`).
That is exactly what we want; nothing new to add.

### Avoiding false barge-in and self-interrupt
- The frontend `BargeInDetector` is "armed only while `chatStore.replying === true`"
  and uses input-stream `echoCancellation: true` (set in
  `client_handler_rtc.py:455-463`). The filler causes the same `replying` state as a
  normal answer (because the same `STREAM_BEGIN` flows from playback), so the only
  new risk is that the filler audio leaks via the mic and *trips its own barge-in*.
- Mitigations: (a) keep the filler intentionally short and modest in volume (no
  exclamation, no excited prosody); (b) leave the existing `cooldownMs: 800`,
  `minSpeechMs: 250` guards in place — they already protect against a sub-half-second
  blip from echo; (c) do not lower `startThresholdDb` for Task 1.
- We will **not** suppress barge-in during the filler — we want the user to be able
  to interrupt the filler too. The existing guards are sufficient on the test
  hardware and have not regressed historically.

### Edge cases
- **Fast LLM response:** if the first chunk arrives in under 600 ms, the timer is
  cancelled before firing. No filler — matches T1.2.
- **Empty LLM input (`chat_text` length < 1`)`** at
  `llm_handler_openai_compatible.py:198-203`: do not start the timer in this branch
  — there is no real call to wait for.
- **LLM error path** at lines 241-254 (`APIStatusError` / connection error): if the
  filler fired and the error path then writes its own text, the filler will already
  have played; that is acceptable. We must, however, ensure we don't double-finish
  the stream — cancel the filler timer in the error path before the error branch
  submits text.
- **Cancellation race:** the timer must check (under a small lock or atomic) that
  `stream_key` is still in `active_stream_keys` before submitting; otherwise it
  silently drops, which is the correct behavior post-cancel.

### Acceptance mapping
- T1.1 satisfied by steps 1-3 above.
- T1.2 satisfied by the cancellation in step 2.
- T1.3 inherits from the existing cancel chain (verified in Phase 0).

### Risk: low. This is the lowest-risk task because it touches one handler and
reuses every existing pipeline guarantee.

---

## Task 2 — Emotion-tagged TTS

### Honest verdict on feasibility (do this first)
**Not feasible as the task spec describes — the active CosyVoice path does not expose
emotion control.** From Phase 0: the handler instantiates
`SpeechSynthesizer(model, voice, callback, format)` and drives it with raw text via
`streaming_call(...)`. The model in the active config is `cosyvoice-v1` with voice
`longxiaochun`. The DashScope tts_v2 SDK does support an "instruction-driven" mode for
specific newer CosyVoice voice models (some `cosyvoice-v2.*` voices), but neither the
configured voice nor the call signature uses that surface. There is no `style`,
`emotion`, or SSML hook on the current call.

Forcing it would require **one of three architectural changes**, listed cheapest →
most expensive:

### Alternative A — Steer the voice through the LLM, leave TTS untouched (recommended)
- **Idea:** ask the LLM to bake the affective register into the **text itself** —
  natural-language emotion expressed through punctuation, interjections ("嗯，", "啊，"),
  sentence length, and word choice. Modern qwen-plus does this well when its system
  prompt asks for it.
- **Where to touch:** `LLMConfig.system_prompt` is layered on by
  `_refresh_system_prompt` (`llm_handler_openai_compatible.py:154-166`). Append a
  light style directive *outside* the participant block (so the prompt-injection
  hardening stays intact). Optionally make the style configurable from
  `LLMConfig` as a new field (e.g. `voice_style: Literal["neutral","friendly",
  "empathetic"] = "neutral"`).
- **Why this is the right starting point:** zero TTS risk, zero new latency on the
  audio path (no extra model call), no extra moving parts. The avatar's *delivery*
  is shaped by the words it actually says — exactly how a human conveys emotion when
  their voice has fixed timbre.
- **Limits:** does not produce truly different *prosodic* delivery for the same
  text; it can't make `longxiaochun` itself sound sad-vs-happy. Good enough as a
  first pass; honest about what it is.

### Alternative B — Switch to a CosyVoice voice/model that does accept an instruct field
- **Idea:** switch `model_name` to `cosyvoice-v2` (or the specific instruct-enabled
  voice) and pass an instruction string per utterance. This means adding an
  `instruction` (or equivalent) argument into the `SpeechSynthesizer` construction
  in `tts_handler_cosyvoice_bailian.py:106-108`, and lifting the instruction value
  from a new metadata field on `ChatData` that the LLM handler attaches.
- **Why this is a real change:** the SDK surface, the per-turn session reset
  semantics (`BailianTTSSession.reset` clears the synthesizer between streams), and
  the voice the maintainer has tuned around all shift. It needs a separate config
  variant so the existing `cosyvoice-v1`/`longxiaochun` setup is not disturbed.
- **Why not as the default:** the task spec says **LiteAvatar stays** and constrains
  scope to "conversational naturalness without changing the avatar model" — the
  voice the LiteAvatar's lip-sync was tuned for is `longxiaochun`, so silently
  switching to another voice or model risks lip-sync regression that is out of scope
  for Task 2.

### Alternative C — Add a second TTS handler (style-aware)
- **Idea:** for cases where Alternative A is not enough, plumb a second TTS path
  (e.g. a different `edgetts` or local CosyVoice with instruct) selected per
  utterance based on derived tag. The pipeline already has `tts/edgetts` and
  `tts/cosyvoice` modules in `src/handlers/tts/`.
- **Why I would not do this for Phase 1:** it doubles the moving parts, doubles the
  failure modes, and changes the voice mid-conversation, which is unsettling. Park
  this as a Phase 2 option only if Alternative A proves insufficient.

### Tag derivation (independent of A vs B)
- Keep it **cheap and on the existing latency path**: do not add a separate LLM
  call. Two viable spots:
  - **Inline in the LLM output** — instruct the system prompt to prefix each reply
    with a short structured marker (e.g. `[mood=friendly]\n...`) that we strip
    before submitting to TTS but use as the instruction in Alternative B; or for
    Alternative A, just rely on the LLM's natural choice without an explicit tag.
  - **Rule-based on emitted text** in the LLM handler's chunk loop
    (around line 231): a tiny keyword/punctuation heuristic mapped to a tag. Stable,
    no extra latency, but coarse.
- Must **fall back to neutral** when nothing is derivable — satisfies T2.3.

### How it reuses the existing cancel/flush path
None of A/B/C changes the cancel chain. A only edits the system-prompt input
string; B adds a kwarg to the TTS constructor on session start — the
`streaming_cancel()` path is unchanged. C would need its own cancel wiring per
handler but is **deferred**.

### Avoiding false barge-in / self-interrupt
None of these change the audio path's relationship to the mic — same echo
cancellation, same cooldown. Risk unchanged.

### Recommendation
Ship **Alternative A** as Task 2's deliverable. Note Alternative B in the task
report as a follow-up if reviewers want true prosodic variation.

### Acceptance mapping
- T2.1: under Alternative A, the LLM produces audibly different word/punctuation
  patterns for different emotional contexts (verify by ear with three scripted
  prompts).
- T2.2: zero extra latency (no new model call, only a prompt addition).
- T2.3: graceful neutral fallback is the default — no tag, no change in behavior.

### Risk: low (Alternative A); medium (B); high (C).

---

## Task 3 — Backchannel / listening cues [higher risk]

### Honest verdict
**Recommend deferring.** The architecture today cannot cleanly separate "avatar is
backchanneling" from "user is speaking" without inviting either (a) the avatar
self-interrupting on its own backchannel, or (b) the avatar talking over the user.
The reasoning, file-by-file:

- **The barge-in arming condition is binary** (`chatStore.replying` in
  `webrtc.ts:172-178`). It does not distinguish "the avatar is producing a real
  reply" from "the avatar is producing a tiny backchannel". So if we play any audio
  while the user is speaking, the existing `BargeInDetector` is armed, the user's
  next syllable trips it, and we fire an `Interrupt` against the avatar's own
  backchannel stream — desyncing the turn state.
- **The "is the user speaking" signal we'd need on the backend exists only as a
  client-side analyzer (`BargeInDetector`)** — and `BargeInDetector` is currently
  *not running* during user turns (it's disarmed until `replying === true`). We
  could add an always-on second VAD as discussed in Phase 0 §0.2, but it is the
  *same input stream* and would share the same echo-cancellation limits. Echo from
  the avatar backchannel would feed back in.
- **The simplex VAD on the server-side disables itself during playback**
  (`vad_handler_silero.py:600-605` flips `input_enabled` on `STREAM_BEGIN` of
  `CLIENT_PLAYBACK`). If we open a `CLIENT_PLAYBACK` stream for a backchannel, the
  server stops listening to the user — *worse than no backchannel*. If we play
  audio *without* opening `CLIENT_PLAYBACK`, we have to build a parallel audio path
  that does not go through LiteAvatar's lip-sync (or LiteAvatar has to drive a
  mouth animation for it), and we lose all the existing flush/cancel guarantees.

In short: every cheap implementation route either breaks barge-in or breaks the
audio plumbing. The task spec itself allows this outcome: *"If Phase 0 shows the
architecture can't cleanly separate ... report that and recommend deferring this
task."*

### Where I would touch only if forced to ship
If the maintainer wants a minimum-viable backchannel anyway, do it as a
**video-only nod**, not audio. A pure animation cue does not arm barge-in (because
it's silent) and can be cleanly cancelled. Specifically:
- Add a new lightweight `ChatSignal` or data-channel message (e.g.
  `AvatarNod`) consumed by the frontend's avatar renderer (`avatarHandler.ts`,
  which already exists per `webrtc.ts:5`) to play a one-off animation. This avoids
  going through the audio pipeline entirely.
- Trigger heuristic on the backend's mic input: when the user has been speaking
  for >Xs without pause (some signal off the early-VAD-end machinery in
  `vad_handler_silero.py:204-220`, which already emits `early_vad_end` events),
  emit at most one `AvatarNod` per pause-boundary. Throttle aggressively.
- This still costs design time and live testing to validate — it is not a freebie —
  but it is the only path that doesn't risk regressing barge-in.

### Verdict to submit
Defer Task 3. Document the silent-nod variant as a follow-up.

### Risk: high (audio backchannel); medium (video-only nod).

---

## Task 4 — Turn-taking timing refinement

### Where to touch
- **File:** `src/handlers/vad/silerovad/vad_handler_silero.py` and the config block
  `SileroVad:` in `config/chat_with_openai_compatible_bailian_cosyvoice.yaml:22-27`.
- **Knobs to tune (not refactor):**
  - `speaking_threshold` (currently `0.5`) — Silero probability threshold for
    "speaking".
  - `start_delay` (currently `2048` samples ≈ 128 ms at 16 kHz) — sustained-speech
    samples before turning ON.
  - `end_delay` (currently `5000` samples ≈ 312 ms) — sustained silence to turn OFF
    a turn. **This is the parameter that most affects "how eager is end-of-turn"**;
    smaller means the system commits to "you're done" faster.
  - `early_end_delay` (`1500` samples ≈ 94 ms) — first early-VAD-end event
    threshold; this is what the semantic turn detector / EOU listeners can use to
    prefetch.
  - `buffer_look_back` (`5000` samples ≈ 312 ms) — head-of-utterance buffer that
    keeps us from clipping the first word; do **not** touch this for Task 4 (it is
    the speech-clipping mitigation).
  - `post_end_monitor_samples` / `reconnect_threshold_samples` — these govern the
    "user paused too long but actually continued" reconnection path. Touch only if
    the early experiments show false-final detection regressions; otherwise leave
    alone.
- **Hand-off timing** — when the user is done, the LLM starts. The only knob on
  that side that meaningfully changes perceived rhythm is Task 1's filler (already
  covered).

### The change in prose
- Phase A — **measure first**. Reproduce the current behavior on three reference
  prompts (a short Q, a mid-length Q with a mid-utterance hesitation, a Q ending
  with a trailing "...嗯" word). For each, record (a) the time from the user's
  last syllable to ASR's `is_last_data`, and (b) any "false end" reconnection
  events. The instrumentation can be added as `logger.info` calls in
  `_update_status_on_start` (lines 171-222) — they are already nearly there.
- Phase B — **tune deliberately**. Two adjustments, each independently testable:
  1. Lower `end_delay` from 5000 → ~3500 samples (≈ 219 ms). This makes the system
     commit to "you're done" sooner; the POST_END monitor + reconnect mechanism
     (`_update_status_on_post_end`, lines 224-295) is exactly there to catch the
     overshoot, so the risk of a premature commit is mostly absorbed.
  2. Leave `start_delay` and `buffer_look_back` alone — they're the speech-clipping
     mitigation and the maintainer has explicitly noted (`submission/REPORT.md:39-46`)
     they predate the conversational-naturalness work and should not be re-tuned
     casually.
- Phase C — **document old/new values** in `submission/PHASE1_REPORT.md` (as the
  Phase 1 delivery doc says) and re-run barge-in regression (A1-A4 in
  `submission/REPORT.md:160-168`) to confirm no regression.

### How it reuses the existing cancel/flush path
Nothing changes in the cancel/flush chain. We're only changing the VAD's idea of
"when is the user done". The reconnection mechanism in
`_handle_reconnection(...)` (lines 542-592) already cancels and re-emits if we get
it wrong — so the worst case from a too-eager `end_delay` is one wasted LLM call
that gets cancelled by reconnect.

### Avoiding false barge-in / self-interrupt
Task 4 doesn't change anything about how playback arms barge-in. The only
indirect risk is that a too-low `end_delay` causes the avatar to start replying
too early, which the user then naturally barges in on. That's correct behavior,
not a regression.

### Acceptance mapping
- T4.1: subjective smoothness — verified by ear with the three reference prompts;
  document old/new values inline in `PHASE1_REPORT.md`.
- T4.2: barge-in regression is unchanged because the playback-arming condition
  isn't being touched.

### Risk: low to medium, depending on how aggressively `end_delay` is dropped.

---

## Suggested order

1. Task 4 (parameter tune — fastest measurable feedback).
2. Task 1 (latency filler).
3. Task 2 Alternative A (system-prompt voice-style).
4. **Defer Task 3** unless the maintainer explicitly opts in to the silent-nod
   variant; if they do, treat it as its own scoped sub-task.
