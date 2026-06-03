# OpenAvatarChat — Engineering Task Spec

> A working brief for Claude Code. Read this top to bottom before writing any code.
> The repo runs locally on this machine (GPU available). For every fix you must
> **reproduce the problem, change the code, then verify the fix by actually running it.**

---

## 1. Project context

We are working on **OpenAvatarChat** (`https://github.com/HumanAIGC-Engineering/OpenAvatarChat`),
a modular real-time "digital human" voice chatbot. The pipeline is:

```
User speech
  → VAD       (Silero — detects when the user is/ isn't speaking)
  → ASR       (SenseVoice — speech to text)
  → LLM       (API-based, OpenAI-compatible)
  → TTS       (CosyVoice — text to speech)
  → Avatar    (LiteAvatar — drives the 2D talking face)
```

**Constraint: use the LiteAvatar avatar module only.** Do not touch / enable LAM or
MuseTalk. Use the lightest LiteAvatar config:

```
config/chat_with_openai_compatible_bailian_cosyvoice.yaml
```

(SenseVoice ASR + API LLM + Bailian CosyVoice TTS + LiteAvatar). Keep the existing
modular architecture intact — fixes should be inside the relevant handlers, not a rewrite.

---

## 2. Environment setup (do this first, confirm it runs)

```bash
# system deps
sudo apt install git-lfs
git lfs install

# repo + submodules
git submodule update --init --recursive

# python env (project requires Python >=3.10,<3.12; CUDA driver must support >=12.4)
uv venv --python 3.11.11
uv pip install setuptools pip
uv run install.py --uv --config config/chat_with_openai_compatible_bailian_cosyvoice.yaml
./scripts/post_config_install.sh --config config/chat_with_openai_compatible_bailian_cosyvoice.yaml

# LiteAvatar model weights (required)
bash scripts/download_liteavatar_weights.sh

# API key for the LLM + Bailian CosyVoice TTS — put in .env at project root
echo "DASHSCOPE_API_KEY=sk-xxxxx" > .env

# run
uv run src/demo.py --config config/chat_with_openai_compatible_bailian_cosyvoice.yaml
```

Note on access: localhost works with no extra setup. If testing from another device
(e.g. phone over LAN) an SSL cert is required for mic permissions — use
`scripts/create_ssl_certs.sh`. For now assume localhost testing.

**Checkpoint:** confirm you can open the UI, start a conversation, speak, and get the
avatar to reply before changing anything. Record baseline behavior for the 3 issues below.

---

## 3. Problems to fix

There are three known issues. Treat each as: *symptom → root-cause area → where in the
code → fix approach → how to verify.*

### Issue 1 — The bot does not capture the user's full utterance (speech gets cut off)

- **Symptom:** when the user speaks, the bot sometimes only receives part of what was
  said. Beginnings or ends of sentences get clipped, so the ASR text is incomplete.
- **Root-cause area:** VAD end-of-speech detection is firing too early, and/or the
  start of speech is clipped before the VAD locks on.
- **Where:** `vad/silerovad/vad_handler/silero` and its config block `SileraVad` in the
  yaml. Relevant params (see README config table):
  - `SileraVad.start_delay` (default 2048) — how long speech prob must stay above
    threshold to count as "started"
  - `SileraVad.end_delay` (default 2048) — how long below threshold to count as "ended"
  - `SileraVad.buffer_look_back` (default 1024) — re-includes clipped speech onset
  - `SileraVad.speech_padding` (default 512) — silence padded on both ends
- **Fix approach:** increase `end_delay` so brief pauses mid-sentence don't end the turn;
  increase `buffer_look_back` / `speech_padding` to stop onset clipping. Tune values, and
  if parameter tuning alone is insufficient, improve the end-of-speech logic in the handler
  (e.g. require a longer sustained silence before finalizing). Document the chosen values
  and why.
- **Verify:** speak a long sentence with a natural mid-sentence pause; confirm the full
  text reaches the ASR / LLM with no clipping, across several trials.

### Issue 2 — The bot is affected by environmental noise

- **Symptom:** background noise causes false speech detection and/or wrong ASR output.
- **Root-cause area:** VAD threshold too permissive; no noise suppression on the input
  audio before VAD/ASR.
- **Where:** `SileraVad.speaking_threshold` (default 0.5) in config; audio input path
  feeding the VAD/ASR handlers.
- **Fix approach:**
  1. Raise `speaking_threshold` so low-level noise doesn't trip VAD (tune — too high
     clips quiet speech).
  2. Add a noise-suppression / pre-processing step on the incoming audio before VAD
     (e.g. a denoise filter or noise-gate). Keep it lightweight and optional via config.
- **Verify:** play background noise (typing, fan, chatter) with no one speaking →
  bot must not trigger. Then speak over moderate noise → bot should still capture
  speech correctly.

### Issue 3 — The user cannot interrupt the bot while it is talking (no barge-in)

- **Symptom:** while TTS is playing / the avatar is talking, the system ignores the
  user, so you can't interrupt it. This is the most important interaction fix.
- **Root-cause area:** VAD/input is not actively monitored during bot speech, and there
  is no mechanism to stop the in-flight TTS + avatar playback when the user starts talking.
- **Where:** the playback/output control path — TTS handler
  (`tts/bailian_tts/tts_handler_cosyvoice_bailian`), the LiteAvatar handler
  (`avatar/liteavatar/avatar_handler_liteavatar`), and the engine logic that coordinates
  VAD ↔ output. Look at how audio/video frames are streamed to the client and whether the
  VAD keeps running during bot speech.
- **Fix approach (barge-in):**
  1. Keep VAD running while the bot is speaking.
  2. When VAD detects the user has started speaking (above threshold for `start_delay`),
     emit an **interrupt signal**.
  3. On interrupt: stop / flush the current TTS generation and the LiteAvatar frame
     stream immediately, return the avatar to idle, and begin capturing the new user
     utterance.
  4. Make sure no stale audio/frames from the interrupted response continue playing.
- **Verify:** start the bot on a long answer, speak over it → bot stops talking
  promptly, avatar returns to idle, and the new utterance is processed as a fresh turn.

---

## 4. Optional — Personalization from participant basic info

The original brief mentions *"based on a participant's basic information."* If in scope:
add a way to collect basic info (e.g. name / age / background) and inject it into the LLM
`system_prompt` (`LLMOpenAICompatible.system_prompt`) so replies are personalized.
Keep it config/UI-driven and non-breaking. **Skip if not required** — confirm with the
task owner first.

---

## 5. Acceptance criteria

- [ ] Project builds and runs with the LiteAvatar config; baseline reproduced.
- [ ] Issue 1: long / paused utterances are captured in full — no clipping (multi-trial).
- [ ] Issue 2: background noise alone does not trigger the bot; speech over noise still works.
- [ ] Issue 3: user can interrupt mid-response; TTS + avatar stop promptly; new turn starts.
- [ ] (Optional) Personalization injects participant info into the LLM prompt.
- [ ] Only LiteAvatar used; existing modular architecture preserved.
- [ ] All chosen parameter values and code changes documented with rationale.

## 6. Working rules for Claude Code

- Reproduce each issue **before** fixing and verify **after** — don't claim a fix without
  running it.
- Prefer the smallest change that solves the problem; tune config before rewriting logic.
- Keep changes isolated per issue (separate commits) so they can be reviewed/reverted.
- Note any dependency you add (especially for noise suppression) and why.
- If a fix needs a design decision (e.g. how aggressive the interrupt should be), surface
  the trade-off rather than guessing silently.
