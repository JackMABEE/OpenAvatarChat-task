# Task Spec — Conversational Naturalness Upgrades (Phase 0 \+ Phase 1\)

Read `AGENTS.md` first. This spec covers **Phase 0 (read-only recon)** and the framework for **Phase 1 (conversational upgrades)**. The Phase 1 tasks have intentional `[FILL AFTER PHASE 0]` placeholders — you cannot write accurate Phase 1 code until Phase 0 has reported the real interfaces. Do Phase 0 first and stop for review before writing any Phase 1 feature code.

**Goal of this whole effort:** make the digital human feel more *conversationally natural* — fewer dead silences, more lifelike turn-taking, more expressive voice — WITHOUT changing the avatar model (LiteAvatar stays) and WITHOUT breaking the existing barge-in or personalization features.

**Out of scope for this spec:** MuseTalk / any avatar-model swap; server/SLURM deployment (environment undecided — see placeholder at the end).

---

## Phase 0 — Code reconnaissance (READ-ONLY, do this first)

**Do not modify any code in Phase 0\.** Clone/checkout the repo, read, and produce a report. The purpose is to replace assumptions with verified facts before Phase 1\.

### 0.1 Confirm repo \+ submodule state

- Confirm the parent repo and the WebUI frontend submodule are both present and on the correct branches/commits. **Do NOT run any recursive submodule command** (see AGENTS.md §3). Just report the current state (`git submodule status`, current branch of the frontend).

### 0.2 Map the pipeline call path

Read the code and report, with **real file paths and symbol names**:

- **LLM call site:** Where is the LLM (qwen-plus / OpenAI-compatible) actually invoked? Is the response streamed token-by-token or returned whole? What handler / function owns this? What is the latency gap between "user finished speaking (ASR done)" and "first LLM token / first TTS audio"?  
- **TTS (CosyVoice) interface:** How is CosyVoice called? What parameters does the call expose? **Specifically: is there any emotion / style / prosody control parameter available** (e.g. an instruction field, an emotion tag, SSML-like markup), or is it plain text-in / audio-out? Report the exact call signature.  
- **Barge-in implementation (current):** Confirm how the existing voice barge-in works end to end — the frontend mic VAD, the message it sends, and the backend cancel chain it triggers. Report the real symbols (the prior work referenced a cancel path around `rtc_stream.py`, an `interrupt_handler`, and a `STREAM_CANCEL` branch in the RTC client handler — **verify these actually exist and report their real current names/locations**).  
- **Frontend mic VAD structure:** Where in the frontend is the mic energy/VAD logic that drives barge-in? What would it take to add a *second* lightweight signal (e.g. "user is currently speaking" vs "user paused") if needed for backchannels?  
- **Output/send queues:** How is outbound audio/video sent to the client, and how is it flushed on interrupt? (Prior work added a `flush_output()` call on cancel — verify and report the real path.)  
- **Personalization hook:** Confirm where `build_personalized_system_prompt()` (or its real equivalent) injects participant info into the system prompt, so Phase 1 changes don't collide with it.

### 0.3 Report and STOP

Write the findings to `submission/PHASE0_RECON.md` and summarize in your response. Lead with the concrete interface facts (especially: **does CosyVoice expose emotion control?** and **how is the LLM streamed?** — these two decide how Phase 1 tasks 1 and 2 are written). Flag anything that contradicts this spec. **Then stop and wait for the updated Phase 1 task details before writing feature code.**

---

## Phase 1 — Conversational upgrades (ordered low-risk → higher-risk)

These are written as a framework. After Phase 0, the maintainer will fill the `[FILL AFTER PHASE 0]` blocks with the real interfaces you reported, and you'll implement them one at a time, each independently testable.

**Global constraint for ALL Phase 1 tasks:** nothing you add may falsely trigger the existing barge-in, and the avatar's own output (backchannels, filler) must NEVER be treated as user speech that interrupts itself. The barge-in mic VAD must remain armed only during the appropriate state. Regression-test barge-in after every task.

### Task 1 — LLM-latency filler ("thinking" cue) \[LOWEST RISK — do first\]

**Problem:** After the user stops speaking, there's a silent gap (ASR → LLM first token → first TTS audio). Silence feels unnatural / broken.

**Behavior:** During that gap, emit a short, low-cost "thinking" cue so the avatar doesn't sit frozen and silent. Options (decide based on Phase 0 findings):

- a brief verbal filler ("嗯…" / "let me think") via TTS, and/or  
- a subtle avatar idle/thinking motion if LiteAvatar supports it.

**Design constraints:**

- Trigger only when the gap exceeds a threshold (e.g. \> \~600ms) — don't fire on fast responses.  
- The filler must be **interruptible/cancelable** by the real response — when the first real TTS audio is ready, the filler must cleanly yield (reuse the existing cancel/flush path, don't invent a parallel one).  
- The filler must not arm the barge-in against itself.

**Interface to use:** \`\[FILL AFTER PHASE 0: LLM stream/first-token hook \+ TTS call

+ cancel/flush path\]\`

**Acceptance:**

- T1.1: On a slow LLM response, a filler cue plays within the gap and is replaced cleanly by the real answer (no overlap, no double audio).  
- T1.2: On a fast LLM response, no filler fires.  
- T1.3: Barge-in still works (user can interrupt during filler AND during the real answer). Manual interrupt button still works.

### Task 2 — Emotion-tagged TTS

**Problem:** TTS voice is flat regardless of content. Emotional variation makes it feel more natural.

**Behavior:** Derive a lightweight emotion/style tag from the LLM response (e.g. neutral / friendly / empathetic / encouraging) and pass it to CosyVoice.

**Design constraints:**

- This task is only viable **if Phase 0 confirms CosyVoice exposes emotion/style control.** If it does NOT, report that and propose alternatives (e.g. prompt the LLM to emit a tag, or a different TTS path) rather than forcing it.  
- Keep the tag derivation cheap — a small rule/classifier or a structured field in the LLM output, not a second heavy model call.  
- Don't add latency to the critical path; if tag derivation is non-trivial, it must not delay first audio.

**Interface to use:** `[FILL AFTER PHASE 0: CosyVoice emotion/style parameter — exact signature; and where the LLM response text is available to derive the tag]`

**Acceptance:**

- T2.1: Responses with clearly different emotional content produce audibly different delivery.  
- T2.2: No measurable increase in time-to-first-audio vs. baseline.  
- T2.3: Falls back gracefully to neutral if no tag is derivable.

### Task 3 — Backchannel / listening cues \[HIGHER RISK — barge-in interaction\]

**Problem:** While the user speaks, the avatar sits frozen, which feels unresponsive / dead.

**Behavior:** Occasionally emit a small listening cue (a nod, "嗯", "mm-hm") while the user is speaking, like a human listener.

**Design constraints — READ CAREFULLY:**

- This is the riskiest task because it makes the avatar produce output *while the user is talking*, which is exactly the condition barge-in watches. The avatar's own backchannel audio must **never** be picked up as user speech that triggers a self-interrupt, and must not desync the turn state.  
- Backchannels must be rare and short, and must not talk over the user's actual content or delay processing their speech.  
- If Phase 0 shows the architecture can't cleanly separate "avatar is backchanneling" from "user is speaking" without risking the barge-in, **report that and recommend deferring this task** rather than shipping something that breaks interruption.

**Interface to use:** `[FILL AFTER PHASE 0: "user is currently speaking" signal from frontend VAD; avatar/TTS output path; barge-in arming state]`

**Acceptance:**

- T3.1: Avatar emits occasional brief listening cues during user speech.  
- T3.2: Backchannels NEVER trigger a self-interrupt and NEVER drop/delay the user's actual utterance.  
- T3.3: Full barge-in regression (A1–A4 equivalent): user voice interrupt stops cleanly; no self-interrupt during silence; no false trigger from keyboard/ambient noise; manual button still works.

### Task 4 — Turn-taking timing refinement

**Problem:** End-of-turn detection (when does the system decide the user is done?) may feel too eager or too sluggish, hurting the conversational rhythm.

**Behavior:** Tune the silence/end-of-utterance thresholds and the hand-off timing between user-done and avatar-start, on top of the existing barge-in VAD.

**Design constraints:**

- This adjusts existing VAD/timing parameters — change them deliberately and document old vs. new values. Don't refactor the VAD; tune it.  
- Must not regress barge-in responsiveness.

**Interface to use:** `[FILL AFTER PHASE 0: VAD threshold/timing parameters and where end-of-turn is decided]`

**Acceptance:**

- T4.1: Subjectively smoother hand-off (document the before/after values and your reasoning).  
- T4.2: Barge-in latency not regressed.

---

## Phase 1 delivery

- Each task on its own commit (or small set of commits) with a clear message.  
- Update `submission/REPORT.md` (or a new `submission/PHASE1_REPORT.md`) with what changed per task, old/new parameter values, and honest notes on anything deferred or that didn't work.  
- Regression-test barge-in \+ personalization after the full phase.  
- **Do not push anything that touches submodules without asking** (AGENTS.md §3).

---

## \[TODO: Deployment environment — DO NOT IMPLEMENT YET\]

The eventual runtime is a university server, but the environment is undecided (SLURM cluster vs. plain SSH box) and GPU access may still be pending. Running a real-time **WebRTC** server in either environment has its own networking concerns (inbound connections, port forwarding, interactive vs. batch scheduling on SLURM).

**Leave this section empty until the maintainer confirms the environment.** When confirmed, this will become a Phase 2 task: a launch/run recipe for that specific environment (e.g. SLURM `srun` interactive session \+ SSH port forwarding for the WebRTC port, or a plain systemd/tmux launch on an SSH box). Do not guess now.  
