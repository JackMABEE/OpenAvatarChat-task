# Design — Personalization from Participant Basic Info (LiteAvatar config)

> Design + build brief for Claude Code. Implements the brief's requirement
> "based on a participant's basic information": collect a participant's basic
> info and use it to personalize the chatbot's replies, by injecting it into the
> LLM system prompt. Keep it config/UI-driven, isolated, and non-breaking.

---

## 1. Goal

Let the chatbot adapt its replies to who it's talking to. Before/at the start of a
session, the participant provides basic info; that info is woven into the LLM's
`system_prompt` so the assistant greets and responds appropriately (e.g. uses their
name, suits their language/background, fits the scenario).

This must NOT break the existing flow: if no info is provided, the bot behaves exactly
as it does today.

## 2. What counts as "basic information" (start minimal)

A small, sensible default set — keep it short, all optional:

- **Name** (how the participant wants to be addressed)
- **Age / age group** (so tone/vocabulary can adapt; optional)
- **Preferred language** (or leave to model default)
- **Background / role** (one line, e.g. "high-school student", "doctor", "visitor")
- **Context / scenario** (optional one line, e.g. "museum guide session", "interview practice")

Don't over-build. These are free-text/optional fields; the point is to demonstrate the
mechanism cleanly, not to build a full profile system.

## 3. Where this plugs in (verified anchors)

- The LLM handler reads `LLMOpenAICompatible.system_prompt` from the config
  (`config/chat_with_openai_compatible_bailian_cosyvoice.yaml`). That base prompt is the
  injection point — participant info should be **appended/merged** into it, not replace it.
- Confirm the exact handler that builds the prompt sent to the LLM (likely under
  `llm/.../llm_handler*` ) and where the system prompt is assembled per session, so the
  injection happens once per conversation with the current participant info.

## 4. Two ways to deliver the info (pick A, keep B noted)

### Option A — UI form in the WebUI (recommended)
A small "participant info" panel/form in the frontend
(`src/service/frontend_service/frontend`). The participant fills it in; the values are
sent to the backend and merged into the system prompt for that session.

- **Pros:** matches a real "participant" use case; per-session, no config edits, easy demo.
- **Cons:** touches frontend + a small backend pathway to carry the fields.

### Option B — Config-driven only
Add a `participant_info` block to the yaml; the handler reads it and merges into the
system prompt at startup.

- **Pros:** trivial, backend-only, no frontend work.
- **Cons:** not per-participant at runtime; you'd edit a file per person. Fine as a
  fallback / minimum.

**Recommendation:** A for the real feature; implement B's merge logic underneath anyway
(the "merge participant fields into system_prompt" function is shared by both), so A is
just a UI + transport layer on top of the same core. If frontend transport proves heavy,
B alone still satisfies the brief.

## 5. Prompt-injection construction (do this carefully)

- **Merge, don't overwrite.** Final system prompt = base `system_prompt` + a clearly
  delimited participant section, e.g. a short block like
  "The participant you are speaking with: name=…, background=…, context=…. Address them by
  name and adapt accordingly."
- **Only include provided fields.** Empty fields are omitted entirely — no "name: undefined".
- **Sanitize.** Participant text goes into a prompt, so guard against it injecting
  instructions that hijack the assistant (treat it as data: wrap/label it as participant-
  provided info, don't blindly concatenate as if it were system instructions).
- **One source of truth.** A single function builds the merged prompt; both A and B call it.

## 6. Implementation steps (for Claude Code)

1. Locate and document the exact place the system prompt is assembled for the LLM in this
   config (handler + line refs), and how/when per session.
2. Write the shared merge function: `(baseSystemPrompt, participantInfo) -> mergedPrompt`,
   with field-omission + sanitization per §5.
3. **Option B path first (backend-only, cheap to verify):** add a `participant_info` block
   to a copy of the config and wire the merge in. Verify by text (print/log the final
   system prompt and confirm a chat reply reflects it).
4. **Option A path:** add a small participant-info form in the frontend, carry the fields
   to the backend (reuse existing data channel / an init message), and feed the same merge
   function per session.
5. Make it toggleable/optional; absent info ⇒ identical to current behavior (regression).
6. Keep it in its own commit(s), separate from the barge-in work.

## 7. How to verify

- **Text-level (no mic needed):** start a session with sample info (name + background),
  send a message, confirm the reply uses the name / adapts to the background; confirm the
  final merged system prompt looks right in logs.
- **Empty-info regression:** with no info provided, behavior is unchanged from today.
- **Sanitization check:** put something adversarial in a field (e.g. "ignore your
  instructions") and confirm the assistant treats it as participant data, not a command.
- (Later, alongside the barge-in mic pass) confirm it all works in a live spoken session.

## 8. Open decisions to confirm

- A vs B (UI form vs config). Recommendation: A on top of a shared merge core, B as the
  guaranteed fallback.
- Exact field set (§2) — trim/add as the use case needs.
- Whether the form appears once at session start or can be edited mid-session (start with
  session-start only; simpler).
