# Design — Voice Barge-In for OpenAvatarChat (LiteAvatar config)

> Design only. No code yet. This turns the verified findings into an implementation plan
> for Claude Code to follow once the approach is confirmed with the colleague.
> Goal: make "user speaks while the bot is talking → bot stops promptly" actually work,
> reusing the backend cancel machinery that already exists.

---

## 1. What already exists (from verification) — do not rebuild

- **Backend cancel chain is complete.** `rtc_stream.py:274-281` parses an `Interrupt`
  data-channel message and emits `ChatSignalType.INTERRUPT`; `interrupt_handler.py:78-117`
  receives it and calls `cancel_stream_chain` / `cancel_streams_by_type(CLIENT_PLAYBACK)`.
  → The "stop TTS + avatar playback" half is done. We must not duplicate it.
- **The trigger is the missing half.** The only thing that currently sends
  `WsProtocol.Interrupt` with `needSendInterrupt=true` is a **manual button**
  (`ChatInput.vue:74` → `store/webrtc.ts:123`). The other `interrupt()` calls are reactive
  (`needSendInterrupt=false`).
- **Server mic is simplex.** `vad_handler_silero.py:603-605` sets `input_enabled=False` on
  `CLIENT_PLAYBACK STREAM_BEGIN`; `handle()` early-returns while disabled (:434). So the
  server ignores the mic during bot speech by design.

**Implication:** the whole task reduces to *"detect the user starting to speak while the
bot is talking, and fire the interrupt signal the backend already understands."*

---

## 2. Two possible places to put the trigger

### Option A — Frontend mic VAD (recommended)
Run a lightweight VAD in the browser on the existing mic stream. While the bot is replying,
if sustained user speech is detected, send the same `Interrupt` message the manual button
already sends.

- **Pros:** smallest change; reuses the entire existing signal path (just calls the
  existing `interrupt()` with `needSendInterrupt=true`); naturally avoids the simplex
  problem because it doesn't depend on the server mic; echo cancellation is available in
  the browser (WebRTC `echoCancellation`); no change to the server's intended simplex design.
- **Cons:** VAD logic lives client-side; need to guard against the bot's own audio being
  picked up (echo) and against incidental sounds.
- **Where:** frontend submodule `src/service/frontend_service/frontend` — the mic capture
  path (currently the AnalyserNodes are used for waveform/playback visuals only) and
  `store/webrtc.ts` (reuse `interrupt()`).

### Option B — Backend duplex VAD
Stop disabling `input_enabled` during playback and let the server VAD detect barge-in,
emitting the interrupt internally.

- **Pros:** keeps all VAD logic in one place (Python); no browser-side audio processing.
- **Cons:** larger, riskier change — it reverses an intentional simplex design; the server
  would receive the bot's own audio echo over the mic and could self-interrupt unless echo
  cancellation/AEC is handled server-side (harder than in the browser); touches the shared
  VAD handler used by other configs.

**Recommendation: Option A.** It's the minimal, architecture-respecting change and the
echo problem is far easier to solve in the browser. Keep B as a documented alternative.

---

## 3. Anti-false-trigger requirements (critical — this is what separates demo from real)

Whatever option, the trigger must not fire on noise, backchannels, or the bot's own voice:

1. **Echo cancellation.** Ensure the mic stream uses `echoCancellation: true` (and ideally
   `noiseSuppression`, `autoGainControl`) so the bot's playback isn't detected as the user.
   This is the #1 cause of self-interruption.
2. **Minimum sustained duration.** Require speech above threshold for ~200–300 ms before
   firing, so a cough or a 50 ms blip doesn't interrupt. (Mirrors the backend's existing
   `start_delay` concept.)
3. **Confidence/energy threshold.** Use a VAD probability and/or energy gate; tune so quiet
   speech still triggers but ambient noise doesn't. Consider hysteresis (separate start/stop
   thresholds) like the backend already does.
4. **Only while bot is speaking.** The barge-in trigger should be armed only during bot
   playback state; otherwise normal turn-taking applies.
5. **Backchannel tolerance (optional/stretch):** ignore very short "uh-huh / mm" if it
   proves too trigger-happy in testing. Don't over-engineer this until live tests show a need.

---

## 4. Implementation steps (for Claude Code, once approved)

1. **Confirm the existing path end-to-end.** Trace the manual button: `ChatInput.vue`
   interrupt button → `store/webrtc.ts:123 interrupt({needSendInterrupt:true})` →
   `chatDataChannel.send({name: Interrupt})` → backend `rtc_stream.py:274` →
   `interrupt_handler.py`. Confirm clicking it really stops TTS + avatar (this is the
   reference behavior the voice path must replicate).
2. **Add a client-side mic VAD module** in the frontend, fed by the existing mic stream,
   active only during bot-playback state.
3. **Wire it to the existing interrupt sender** — on a confirmed barge-in, call the same
   `interrupt({needSendInterrupt:true})` the button uses. Do not invent a new signal.
4. **Apply the anti-false-trigger guards** from §3 (echo cancellation on the stream,
   min-duration, threshold/hysteresis, armed-only-during-playback).
5. **Make it configurable / toggleable** so it can be disabled and compared against the
   manual button, and so thresholds are tunable.
6. **Keep it isolated** (its own commit), and leave the manual button working as a fallback.

---

## 5. How to verify (live, with mic — the user will do this pass)

- Start a long bot reply, **speak over it without clicking** → bot should stop promptly
  (TTS + avatar), avatar returns to idle, and the new utterance is captured as a fresh turn.
- Let the bot talk while **staying silent / making no speech** → bot must NOT self-interrupt
  (tests echo cancellation).
- **Background noise only** (typing, fan) during bot speech → must NOT trigger.
- Confirm the **manual button still works** (regression check).
- Measure rough stop latency; aim for the bot to go quiet within a few hundred ms of the
  user speaking.

---

## 6. Open decisions to confirm

- **A vs B** (frontend mic VAD vs backend duplex). Recommendation: A.
- Which **VAD** to use client-side (e.g. a small JS/WASM Silero build, or an energy+
  duration heuristic to start simple). Start simple; upgrade only if false-trigger rate
  is too high.
- How aggressive the interrupt should be (instant on first detection vs. the 200–300 ms
  guard). Default to the guard — false barge-ins are worse than slightly slow ones.
