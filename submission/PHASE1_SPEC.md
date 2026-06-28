# Phase 1 — Implementation Spec (interfaces filled from Phase 0 recon)

Read `AGENTS.md` and `submission/PHASE0_RECON.md` first. All file/line anchors below come from the recon and must be re-verified by `view`/read before editing (line numbers drift). This spec covers the three tasks chosen for this round:

- **Task 1 — LLM-latency filler** (DO FIRST)  
- **Task 4 — Turn-taking timing** (`end_delay` tuning)  
- **Task 2-ALT — Emotion via LLM word choice** (prompt-layer, no TTS change)

**Task 3 (backchannels) is DEFERRED** — see the backlog note at the end. Do not implement it.

## Execution discipline (applies to ALL tasks)

1. **One task at a time.** Implement a task → commit → deploy to the UH GPU host → test live → only then start the next task. Do NOT batch them.  
2. **Each task is its own commit(s).** Clear message, e.g. `feat(filler): LLM-latency thinking cue`.  
3. **Regression-test barge-in \+ personalization after every task** using the smoke tests in `DEPLOY_PLAN.md` §9.6–9.7. Barge-in is the crown jewel; nothing ships that regresses it.  
4. **Submodule landmine (AGENTS.md §3):** never run `git submodule update --init --recursive` or any recursive submodule command.  
5. **Verify before writing.** Re-read the anchored code; if reality differs from this spec, stop and report rather than forcing the spec onto the code.

---

# TASK 1 — LLM-latency filler ("thinking" cue)  ← DO FIRST

## Problem

After the user stops speaking there is a silent gap: ASR last-text → first LLM token → first TTS audio. During that gap the avatar sits frozen and silent, which reads as "broken / not listening."

## Goal

During that gap (and only when it exceeds a threshold), emit a short, **cancelable** "thinking" cue so the avatar doesn't freeze. When the real answer's first TTS audio is ready, the filler must yield cleanly with no overlap and no double audio.

## Verified interfaces (from recon — re-read before editing)

- **Hook point:** `src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py`, `HandlerLLM.handle(...)` (from line 168). The per-turn output stream is opened on the first chunk with `ChatStreamConfig(cancelable=True)` (lines \~184-187); chunks are forwarded via `streamer.stream_data(output)` (line \~237); stream closes with `finish_stream=True` (lines \~261-263). The OpenAI call uses `stream=True` (line \~216) and `timeout=5.0` (line \~132).  
- **The gap to cover** \= "ASR `is_last_data` arrives → first non-empty `chunk.choices[0].delta.content`." The call is synchronous-blocking in `handle()` until the first chunk, so the gap is structurally real.  
- **Cancel/flush plumbing to REUSE (do not invent a parallel one):**  
  - LLM honors `STREAM_CANCEL` by dropping the key from `active_stream_keys` (lines \~265-271), which makes the generation loop exit.  
  - TTS resets its session → `streaming_cancel()` (`tts_handler_cosyvoice_bailian.py:199-218`, `46-53`).  
  - `flush_output()` drains the AUDIO/VIDEO queues on `STREAM_CANCEL` of `CLIENT_PLAYBACK` (`client_handler_rtc.py:333-360`, called at \~629-633).  
  - Avatar `context.interrupt()` (`avatar_handler_liteavatar.py:162-169`); VAD re-enables input (`vad_handler_silero.py:600-602`).

## Design (the safe shape)

The filler is **just another piece of avatar output on the same per-turn stream**, emitted early, then superseded. Two viable shapes — recon supports either; pick based on what you confirm on re-read:

- **Shape A (preferred): verbal filler via the normal text→TTS path.** When the gap threshold trips, push a tiny pre-chosen filler string (e.g. one of a small pool: 「嗯…」「让我想想」/ "let me see") into the SAME downstream path the real tokens use, so it flows through CosyVoice → LiteAvatar like any text. When the first real LLM chunk arrives, cancel/flush the filler exactly as a `STREAM_CANCEL` would, then emit the real answer. **Reuse the existing cancel/flush chain — do not hand-roll audio teardown.**  
- **Shape B (fallback): silent avatar "thinking" motion only**, if re-read shows injecting filler text into the TTS path risks corrupting the per-turn stream bookkeeping or the session history. Lower payoff but lower risk.

Decide A vs B in your pre-implementation read and state which you chose and why.

## Constraints (hard)

- **Threshold-gated:** only fire if the gap exceeds \~600ms (make it a config value, default 600ms). Fast responses must produce NO filler.  
- **Cleanly superseded:** when the first real token/audio is ready, the filler must stop with no overlap, no double audio, no leftover queued filler frames (this is exactly what `flush_output()` is for — reuse it).  
- **Must not arm barge-in against itself.** The filler is avatar output; ensure it does not get treated as user speech. (Today barge-in arms on `chatStore.replying`; confirm the filler does not create a state where the avatar's own filler audio trips the mic VAD into a self-interrupt.)  
- **Do not touch the participant/personalization prompt path.**

## Acceptance tests (run live on UH host)

- **T1.1** Slow LLM response → a filler cue plays within the gap and is replaced cleanly by the real answer (no overlap, no double audio, no stutter at the seam).  
- **T1.2** Fast LLM response → NO filler fires.  
- **T1.3** Barge-in still works: user can interrupt during the filler AND during the real answer; the manual interrupt button still works. (Backend logs: `InterruptHandler: Received INTERRUPT signal ...`, `flushed N buffered ... frames`.)  
- **T1.4** Personalization smoke test still passes ("what's my name?").

## Per-task agent prompt (paste into Claude Code / Codex)

Read AGENTS.md and submission/PHASE0\_RECON.md, then read

submission/PHASE1\_SPEC.md (Task 1 only).

Implement ONLY Task 1 (LLM-latency filler). Do not touch Task 2 or Task 4\.

Do NOT run any recursive git submodule command (AGENTS.md §3).

First, RE-READ the anchored code before changing anything:

\- llm\_handler\_openai\_compatible.py around lines 168, 184-187, 211-237, 261-271

\- tts\_handler\_cosyvoice\_bailian.py lines 46-53, 199-218

\- client\_handler\_rtc.py lines 333-360, 613-642

Confirm the line numbers are still accurate and report any drift.

Then decide Shape A (verbal filler through the existing text→TTS path) vs

Shape B (silent thinking motion), based on what reusing the per-turn

AVATAR\_TEXT stream \+ the existing cancel/flush chain safely allows. State

your choice and reasoning in the commit message / a short note.

Implement the filler so that:

\- it only fires when the ASR-last → first-LLM-chunk gap exceeds a configurable

  threshold (default 600ms);

\- it is superseded by the real answer via the EXISTING cancel \+ flush\_output

  path (no parallel teardown);

\- it never causes the avatar's own filler audio to trigger barge-in.

Make the threshold a config value. Do not modify the participant-info prompt

path. Commit with a clear message. Then STOP — I will test on the UH host

before you start the next task.

---

# TASK 4 — Turn-taking timing (`end_delay` tuning)

Recommended to do this SECOND — it's the cheapest probe of the full UH test chain (VPN→SSH→tmux→port-forward→browser). If the loop works for a one-number change, it'll work for everything.

## Problem

End-of-utterance detection may feel too eager or too sluggish, hurting the conversational rhythm (avatar jumps in too fast, or lags after you finish).

## Goal

Tune the silence/end-of-turn delay so hand-off feels natural, WITHOUT refactoring the VAD and WITHOUT regressing barge-in responsiveness.

## Verified interfaces

- Silero VAD handler: `src/handlers/vad/silerovad/vad_handler_silero.py` (the POST\_END monitoring \+ input re-enable on cancel at lines \~600-602 confirm this is the right handler).  
- The tunable is the **`end_delay`** in the Silero VAD config block of `config/chat_with_openai_compatible_bailian_cosyvoice.yaml`. **Leave `start_delay` and `buffer_look_back` alone** — only `end_delay` is in scope.

## Design

This is a **config/parameter tune, not a code refactor.** Read the current `end_delay`, document it, change it deliberately, document the new value and the reasoning. If a code default needs exposing to make `end_delay` configurable and it isn't already, that's the only code change permitted — keep it minimal.

## Constraints (hard)

- Only `end_delay`. Do not touch `start_delay` / `buffer_look_back` / the VAD logic.  
- Must not regress barge-in latency or correctness.  
- Record old value → new value → why, in the commit message and `submission/REPORT.md`.

## Acceptance tests (run live on UH host)

- **T4.1** Hand-off feels subjectively smoother (you judge live). Document before/after `end_delay` values and your reasoning.  
- **T4.2** Barge-in latency NOT regressed (re-run the barge-in smoke test).

## Per-task agent prompt

Read AGENTS.md and submission/PHASE0\_RECON.md, then submission/PHASE1\_SPEC.md

(Task 4 only).

Implement ONLY Task 4 (turn-taking timing). Do not touch Task 1 or Task 2\.

Do NOT run any recursive git submodule command.

Read the Silero VAD config block in

config/chat\_with\_openai\_compatible\_bailian\_cosyvoice.yaml and the handler

src/handlers/vad/silerovad/vad\_handler\_silero.py. Report the current end\_delay

value. Change ONLY end\_delay (leave start\_delay and buffer\_look\_back untouched).

If exposing end\_delay as a config value requires a tiny code change, keep it

minimal and explain it. Record old→new value and reasoning in the commit

message and submission/REPORT.md. Then STOP — I will test on the UH host.

---

# TASK 2-ALT — Emotion via LLM word choice (NOT a TTS change)

Why not the original Task 2: recon confirmed `cosyvoice-v1` \+ `longxiaochun` via `SpeechSynthesizer(model, voice, callback, format)` \+ `streaming_call(text)` has **no emotion/style/SSML channel** (`tts_handler_cosyvoice_bailian.py:106-115`). So we make the *language* more emotionally expressive instead of the *voice*.

## Goal

Make responses feel warmer / more emotionally appropriate by steering the LLM's word choice via the system prompt — a lightweight enhancement, zero architecture risk, no TTS/voice/model swap.

## Verified interfaces

- Prompt assembly: `src/handlers/llm/openai_compatible/participant_info.py`, `build_personalized_system_prompt(base_system_prompt, participant)` (line 104).  
- `_refresh_system_prompt(context)` runs at the top of every `handle(...)` turn (`llm_handler_openai_compatible.py` lines \~154-166) and merges runtime participant info.  
- **Collision rule (critical):** any emotion-steering text must be layered **after** `_refresh_system_prompt` and must treat the participant block as **opaque** — do NOT re-open or modify the sanitization/markers/length-cap that `build_personalized_system_prompt` set up (`participant_info.py:79-101, 118-135`). This is what keeps the prompt-injection surface closed.

## Design

Add a small, configurable "emotional tone" instruction to the system prompt (e.g. "respond with natural warmth and appropriate emotional expressiveness; vary tone to match the content"). Make it a config toggle/string so it can be turned off. Keep it short — it must not bloat the prompt or fight the participant block.

## Constraints (hard)

- Layer AFTER `_refresh_system_prompt`; treat the participant block as opaque.  
- Do NOT modify `build_personalized_system_prompt` internals (markers/sanitization/ length cap).  
- Make the tone instruction a config value (string \+ on/off), default conservative.  
- No change to the TTS handler, voice, or model.

## Acceptance tests (run live on UH host)

- **T2.1** With the tone instruction on, responses use warmer/more varied wording vs. off (judge live).  
- **T2.2** No regression to personalization ("what's my name?" still works, participant info still respected).  
- **T2.3** Toggling the instruction off cleanly returns to baseline wording.

## Per-task agent prompt

Read AGENTS.md and submission/PHASE0\_RECON.md, then submission/PHASE1\_SPEC.md

(Task 2-ALT only).

Implement ONLY Task 2-ALT (emotion via LLM word choice). Do not touch Task 1

or Task 4\. Do NOT run any recursive git submodule command.

Read participant\_info.py (build\_personalized\_system\_prompt, line \~104) and

llm\_handler\_openai\_compatible.py (\_refresh\_system\_prompt, lines \~154-166).

Add a short, configurable emotional-tone instruction to the system prompt,

LAYERED AFTER \_refresh\_system\_prompt, treating the participant block as opaque.

Do NOT modify build\_personalized\_system\_prompt's markers/sanitization/length

cap. Make the tone instruction a config value (string \+ on/off toggle),

defaulting conservative. Do not change the TTS handler, voice, or model.

Commit with a clear message. Then STOP — I will test on the UH host.

---

# Deferred — Task 3 (backchannels)  \[BACKLOG, do not implement\]

Recon verdict: the only "user is speaking" evidence the backend has while the bot talks comes from the **same browser VAD** (`bargeInDetector.ts`) whose firing also triggers barge-in — both run off `chatStore.replying`. A backchannel keyed off "user is speaking" is therefore prone to the **same false-positive failure mode as barge-in**, and there is no architectural separation between "avatar is making sound" and "user speech arming barge-in."

**Revisit only if** a future change introduces a separate, echo-robust "user speaking" signal (recon notes a second always-on threshold \+ a `UserSpeechState` data-channel message is *structurally* possible in `bargeInDetector.ts:144-177`, but it needs its own debounce/hysteresis to survive imperfect echo cancellation). The silent video-nod-only fallback is the lowest-risk version if backchannels are ever forced. Not in scope now.  
