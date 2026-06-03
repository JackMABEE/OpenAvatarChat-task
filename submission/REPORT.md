# OpenAvatarChat — Task Report

**Project**: OpenAvatarChat (LiteAvatar config `config/chat_with_openai_compatible_bailian_cosyvoice.yaml`)
**Task**: Verify and resolve three VAD / interruption issues
**Baseline**: upstream `c9d823c` (frontend submodule upstream `a6182af`, i.e. v0.6.0)
**Status**: All core functionality passed live testing; pushed to my own remote repositories

---

## How to get the code (for the reviewer)

This project uses a frontend submodule: the backend changes live in the parent repo,
while the frontend "voice barge-in + personalization UI" lives in the submodule.
**You only need to initialize that one frontend submodule** to see all the changes —
there is no need to pull the other 7 heavy third-party submodules (CosyVoice / MuseTalk
/ lite-avatar, etc., several GB):

```bash
git clone https://github.com/JackMABEE/OpenAvatarChat-task.git
cd OpenAvatarChat-task
git submodule update --init src/service/frontend_service/frontend
```

- Frontend submodule remote: `https://github.com/JackMABEE/OpenAvatarChat-WebUI.git`
  (`.gitmodules` already points here, so a recursive clone also resolves it automatically).
- To **run the full stack** (with avatar / TTS / VAD, etc.), run `git submodule update --init`
  to fetch the remaining submodules, then configure `.env` (`DASHSCOPE_API_KEY`) and the
  model weights as described in `README.md`.
- This report and the design docs all live under `submission/`: this file,
  `BARGEIN_DESIGN.md`, `PERSONALIZATION_DESIGN.md`, the original task brief `TASK.md`,
  and the verification checklist `VERIFY.md`.

---

## 1. Verification of the three original issues

These conclusions are based on reading the upstream code and config (read-verified) and
confirming the behavior during live testing.

### Issue 1: Speech clipping (start/end of utterance cut off) — already mitigated upstream

Current upstream mitigates this with two mechanisms:

- **Config layer (predates this task)**: the ASR/VAD config's `end_delay` and
  `buffer_look_back` (= 5000 ms) keep a trailing-silence buffer and look back at the
  start of the utterance, preventing endpoint detection from clipping the head/tail.
  This mechanism has existed **since v0.3.0** — to be honest: **this part of the fix
  predates the release of this task**; it was not added for it.
- **Added in v0.6.0**: reconnection logic in the `POST_END` phase, further reducing
  dropped words caused by endpoint jitter.

**Conclusion**: Issue 1 is already handled systematically in current upstream; no change
needed from me.

### Issue 2: Noise false-triggering — already mitigated upstream, but the approach must be stated honestly

Upstream (v0.6.0, commit `475e71f`) addresses this with a combination of
**`volume_threshold` (volume/energy threshold) + AGC (automatic gain control) + an energy gate**.

**To be stated honestly**: this approach takes the **"energy gate + automatic gain"**
route, **not** simply "raising the VAD threshold" or "adding a denoising model". That is,
it filters out low-energy noise via energy gating and normalizes input level with AGC,
rather than relying on a more aggressive threshold or a separate noise-reduction
algorithm. This is an engineering trade-off: usable in quiet/normal-noise environments,
but not equivalent to dedicated denoising.

**Conclusion**: Issue 2 already has a mitigation mechanism in current upstream; no change
needed from me — but its essence is energy gating rather than denoising, and that should
not be glossed over when reporting.

### Issue 3: Voice barge-in (speak to interrupt the bot) — the real gap

This is the **only genuine gap** among the three issues:

- Upstream **only has a manual "interrupt" button**, and that button is not even shown in
  voice mode;
- The server-side VAD is **simplex** — during bot playback the server-side mic input is
  muted, so **the server cannot detect the user speaking over the bot**.

In other words, upstream has no ability for "the user starts speaking → the bot stops".
That is exactly the capability this task needed to add.

---

## 2. What I actually built

### 1. Real voice barge-in

Implemented "the user starts speaking → interrupt" on the **frontend**, reusing the
backend's existing cancel chain without adding any new backend signal:

- **Frontend mic VAD** (`bargeInDetector.ts`): energy-based detection (RMS → dBFS), with
  - minimum duration (min-duration, to avoid transient-noise triggers);
  - start/stop hysteresis;
  - armed only while the bot is replying (armed-only-while-replying);
  - a post-fire cooldown (to avoid repeated false triggers).
- **Reuse the backend cancel mechanism**: on detected barge-in, send the existing
  `Interrupt` data-channel message → backend `INTERRUPT` signal → `interrupt_handler`
  cancels `CLIENT_PLAYBACK`. No change to backend interrupt logic.
- **Self-interrupt prevention (key)**: request `echoCancellation` / `noiseSuppression` /
  `autoGainControl` via `getUserMedia`, so the bot's own playback is not picked up by the
  mic and mistaken for "the user speaking".

### 2. Personalization (tailored from a participant's basic info)

Inject participant info into the LLM system prompt via two paths (sharing one merge function):

- **Option B (config-driven, backend)**: `participant_info.py` provides
  `build_personalized_system_prompt()`, a pure-stdlib implementation.
- **Option A (runtime, per-session)**: frontend form → `SetParticipantInfo` data-channel
  message → backend stores it on the session `shared_states` → the LLM handler re-merges
  per turn as needed (runtime overrides config).

**Prompt-injection handling** (important, implemented in the merge logic):
- **Append, never overwrite**: participant info is **appended** as a clearly-delimited
  block to the base prompt; it does not overwrite the original instructions;
- **Omit empty fields**: unfilled fields don't enter the prompt;
- **Treat values as data, not instructions**: neutralize block markers, collapse
  newlines, and cap length, to resist users injecting a prompt into the fields;
- **Regression-safe**: with no fields, the base prompt is returned unchanged, leaving
  default behavior intact.

---

## 3. Two real bugs found and fixed during live testing

Both are real issues surfaced during integration/live testing — not paper analysis.

### Bug 1: Personalization message hits the backend's 0.5 s start-delay drop window (timing)

- **Symptom**: participant info was filled in, but the bot didn't reflect it at all.
- **Diagnosis**: the frontend sent `SetParticipantInfo` **immediately** in the
  data-channel `open` event, but the backend `rtc_stream` has an ~**0.5 s stream
  start-delay drop window** (`stream_start_delay`) that drops messages received during
  it. So the personalization message was dropped before reaching the handler and never
  took effect.
- **Fix (belt-and-suspenders)**:
  - **Fix A (frontend, `6d9f0cb`)**: delay the send by 1.5 s to clear that window;
  - **Fix B (backend, `0f9737d`)**: exempt control messages (`SetParticipantInfo`) from
    the drop logic as a backend backstop; other messages keep the original behavior.

### Bug 2: Interrupt left in-flight audio/video queues unflushed, causing a 1–2 s tail

- **Symptom**: after a voice barge-in, the bot's reply audio/video kept playing for
  ~1–2 seconds before stopping — not "clean" enough.
- **Diagnosis**: on interrupt the upstream (avatar worker) queues were indeed cleared,
  **but frames already handed to the RTC delegate's `output_queues` kept draining to the
  client**, so the in-flight portion of the reply finished playing.
- **Fix (`8c50170`)**: on `STREAM_CANCEL` (cancel of `CLIENT_PLAYBACK`), call the new
  `flush_output()` to drain the delegate's **AUDIO + VIDEO** `output_queues`. Because
  `on_signal` may run on another thread while the emit loop owns these queues, the drain
  is scheduled onto the emit loop via `call_soon_threadsafe` (thread-safe). The log prints
  `RtcClient: flushed N buffered ... frames on interrupt`.

---

## 4. Test results

Tested on a single machine (RTX 4090 Laptop); all core functionality passed:

**Voice barge-in (A1–A4):**
- A1 can interrupt: speaking can interrupt the bot mid-speech ✅
- A2 stops cleanly, no tail: audio/video stops immediately after interrupt (no 1–2 s
  residual after the Bug 2 fix) ✅
- A3 no self-interrupt when quiet: while the bot speaks and the user is silent, it does
  not interrupt itself ✅
- A4 no false-trigger from noise / manual button still works: no false triggers under
  normal noise, and the original manual interrupt button is unaffected ✅

**Personalization:** the bot correctly recognizes and reflects the participant info ✅

---

## 5. Known minor issues and limitations

- ~~**UI minor defect**: when the personalization form is expanded, the "start chat"
  button is pushed out of the visible area~~ — **Fixed** (commit `f772415`): the
  pre-session layout was changed to a flex column with the video shrinking to fit, so the
  form/video/button all stay within view; the in-call layout is unaffected, and a refresh
  confirmed the button returns.
- **Limited test coverage**: single machine, single manual test run; no multi-machine /
  multi-browser / weak-network / stress testing, and no long-running stability observation.
- **Nature of Issue 2**: as noted above it is energy gating + AGC, not dedicated
  denoising; robustness under strong noise was not specifically verified.
- **Frontend build artifacts uncommitted**: the submodule's `dist/` contains rebuilt
  artifacts left uncommitted (unrelated to the feature code; build output).
- **Pre-existing upstream type errors not fixed**: the frontend's standalone
  `pnpm typecheck` reports 26 errors that **already exist in upstream**, across 17
  untouched files; they are out of scope and left unfixed (to avoid breaking commit
  isolation). The files I changed themselves typecheck cleanly, and `pnpm build` passes.

---

## Appendix: Full change inventory (branches + commits)

> Baseline: parent repo upstream `c9d823c`; frontend submodule upstream `a6182af` (v0.6.0).
> All commits below are added on top of the baseline.

### Parent repo (OpenAvatarChat) — branch `feature/participant-personalization`

| commit | Description |
|--------|------|
| `8c50170` | fix(barge-in): flush the RTC delegate's in-flight AUDIO+VIDEO output queues on interrupt, removing the 1–2 s tail (Bug 2 fix) |
| `0f9737d` | fix(personalization): backend exempts control messages (SetParticipantInfo) from the 0.5 s start-delay drop window (Bug 1 backend backstop, Fix B) |
| `6d4ea50` | feat(llm): runtime per-session personalization — receive SetParticipantInfo over the data channel into shared_states, LLM re-merges per turn as needed (Option A backend) |
| `6db7e1b` | feat(llm): config-driven system-prompt personalization merge logic `participant_info.py`, with prompt-injection handling (Option B) |

Files touched (7 total, +448/−7):
`src/handlers/client/rtc_client/client_handler_rtc.py`, `src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py`, `src/handlers/llm/openai_compatible/participant_info.py`, `src/chat_engine/contexts/session_context.py`, `src/service/rtc_service/rtc_stream.py`, `config/chat_with_openai_compatible_bailian_cosyvoice_personalized.yaml`, `scripts/verify_personalization.py`

### Frontend submodule (src/service/frontend_service/frontend) — branch `feature/participant-personalization-ui`

> This branch is stacked on the barge-in branch (shares app.ts / webrtc.ts). There is also
> a standalone branch `feature/voice-barge-in` (containing `7755a97`).

| commit | Description |
|--------|------|
| `f772415` | fix(ui): change the pre-session layout to a flex column so the "start chat" button stays visible when the personalization form is expanded (fix for the known-limitation item) |
| `6d9f0cb` | fix(personalization): delay sending SetParticipantInfo by 1.5 s to clear the backend's start-delay drop window (Bug 1 fix, Fix A) |
| `db1cadc` | feat(personalization): participant info form + per-session transport (Option A frontend) |
| `7755a97` | feat(barge-in): frontend mic VAD for voice barge-in, reusing the backend cancel chain, with echo cancellation / min-duration / hysteresis / cooldown for false-trigger prevention |

Files touched (9 total, +481/−1, excluding dist build artifacts):
`src/renderer/src/helpers/bargeInDetector.ts`, `src/renderer/src/store/webrtc.ts`, `src/renderer/src/store/app.ts`, `src/renderer/src/store/media.ts`, `src/renderer/src/components/ParticipantInfoForm.vue`, `src/renderer/src/interface/participant.ts`, `src/renderer/src/interface/eventType.ts`, `src/renderer/src/views/VideoChat/index.vue`, `BARGE_IN.md`

### Design docs

`submission/BARGEIN_DESIGN.md` (barge-in design), `submission/PERSONALIZATION_DESIGN.md`
(personalization design), `submission/VERIFY.md` (verification checklist).
