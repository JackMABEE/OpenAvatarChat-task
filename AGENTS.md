# AGENTS.md — OpenAvatarChat (Digital Human) Working Agreement

Read this file top to bottom before doing anything. It defines what the project is, the hard constraints, and the one mistake that will destroy existing work. When in doubt, **read the code and report before changing it.**

---

## 0\. Who you are working with

This repo already contains working features built by the maintainer (Jett): voice **barge-in** (frontend mic VAD reusing the backend cancel path) and participant **personalization** (basic info injected into the LLM system prompt). You are **extending** this codebase, not rebuilding it. Preserve the existing modular architecture and the existing features.

---

## 1\. What the project is

**OpenAvatarChat** — a modular, real-time "digital human" voice chatbot. The pipeline is:

User speech

  → VAD       (Silero — detects when the user is / isn't speaking)

  → ASR       (SenseVoice — speech to text)

  → LLM       (API-based, OpenAI-compatible — Aliyun Bailian / qwen-plus)

  → TTS       (CosyVoice via Aliyun Bailian / DashScope)

  → Avatar    (LiteAvatar — drives the 2D talking face)

**Hard constraint: LiteAvatar only.** Do not enable or touch LAM or MuseTalk in this phase. Use the lightest LiteAvatar config:

config/chat\_with\_openai\_compatible\_bailian\_cosyvoice.yaml

Keep the existing modular structure intact. New behavior goes inside the relevant handlers, not as a rewrite.

---

## 2\. Repository layout — TWO repos, hand-managed submodule pointers

This project spans two GitHub repos:

- **Parent repo:** `JackMABEE/OpenAvatarChat-task`  
- **Frontend fork (a submodule):** `JackMABEE/OpenAvatarChat-WebUI`

The frontend lives on a **feature branch** inside the submodule, and the parent repo points at a specific commit of it. The submodule pointers are managed **by hand**. Do not assume the default `.gitmodules` behavior is safe.

---

## 3\. ⚠️ THE LANDMINE — read this twice

**NEVER run `git submodule update --init --recursive`.**

This command resets the frontend submodule to its tracked/default state and **wipes the WebUI feature branch**, destroying the barge-in and personalization frontend work. There is no clean recovery if you do this and then commit.

- Do **not** run `git submodule update --init --recursive`.  
- Do **not** run `git submodule update --recursive`.  
- If you need a specific submodule initialized, initialize **only that one** explicitly (e.g. the maintainer previously initialized only `silero_vad` and `liteavatar`), and confirm with the maintainer first.  
- Before any git operation that touches submodules, **stop and ask.**

If you think you need to touch submodules to make something work, that is a signal to **report the situation and ask**, not to run a recursive command.

---

## 4\. Read-before-write discipline (this is mandatory)

This project has a history of "bugs" that turned out to already be fixed upstream. Assumptions cost real time here. Therefore:

- **Do not write feature code until you have read the relevant code and reported what is actually there.** The first task (see the task spec, Phase 0\) is a read-only reconnaissance task. Honor it.  
- When you report, cite **real file paths and symbol names** you actually found — never guess or pattern-match from memory.  
- If reality differs from what the task spec assumes, **say so in your report** and stop for direction rather than forcing the spec onto the code.  
- Negative findings are valuable. "This interface doesn't exist / works differently than assumed" is a useful, expected outcome — report it plainly.

---

## 5\. Environment notes (from prior setup — verify, don't assume)

These reflect how the project was set up before. Treat as hints, re-verify on the current machine:

- Dependency/venv management via **uv** (previously uv 0.11.18), Python 3.11.x.  
- Torch built against CUDA (previously torch 2.8.0+cu128). Verify CUDA is actually available before running anything GPU-bound.  
- LiteAvatar weights downloaded via **ModelScope**.  
- LLM (qwen-plus) and TTS (CosyVoice) via **Aliyun Bailian / DashScope**. Requires a Bailian **API key** (from `bailian.console.aliyun.com`) in a `.env` file — this is distinct from the Aliyun AccessKey ID.  
- Only two submodules were initialized intentionally: `silero_vad`, `liteavatar`.

**Deployment target is undecided.** The eventual runtime is a university server whose environment (SLURM cluster vs. a plain SSH box) is not yet known, and GPU access may still be pending. **Do not write deployment / launch scripts for a specific server environment yet.** A placeholder section exists in the task spec; leave it until the maintainer confirms the environment.

---

## 6\. Secrets

- Never hardcode API keys. Use the `.env` file.  
- Never commit `.env` or any key.  
- If you encounter an exposed key in the repo or logs, flag it so the maintainer can rotate it. Do not echo full keys back in your output.

---

## 7\. Localization

New content you add (comments, UI strings, docs) should be in **English**. Preserve existing upstream Chinese content where it already exists — do not mass-translate the upstream.

---

## 8\. How to report

When a task says "report," produce a short written summary in your response (and, where useful, a markdown file under `submission/` or a clearly named notes file). Lead with the concrete findings (file paths, symbols, current behavior), then flag anything that contradicts the task spec, then stop for direction if the spec needs to change. Keep it dense and specific — no filler.  
