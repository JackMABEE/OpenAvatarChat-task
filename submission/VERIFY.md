# Verification Request for Claude Code

> Before we decide the approach (and before I email my colleague), I need to **verify**
> your claim that the three issues from `TASK.md` are already fixed upstream. Don't just
> assert it — show evidence I can check myself. "There is code that handles X" is NOT the
> same as "X actually works." Keep those two separate in your answer.

## What I need you to establish

For **each** of the three issues, answer with concrete evidence, not a summary:

1. **Incomplete speech capture** (VAD clips start/end of utterances)
2. **Environmental-noise sensitivity** (false triggers / wrong ASR from background noise)
3. **No barge-in** (user can't interrupt the bot while it's speaking)

For each one, give me:

- **Git evidence.** Run `git log` / `git blame` and identify the specific commit(s) or PR
  that addressed it. Quote the commit hash, date, and message, and show the key diff
  (which file/lines changed, before vs after). If you can't find a commit that fixes it,
  say so plainly — that changes everything.
- **Code-level mechanism.** Point to the exact file + lines in the *current* code that
  implement the fix, and explain in one or two sentences how it works.
- **Mechanism present vs. actually works.** State clearly which of these two you have
  shown. You can usually confirm "the mechanism is present in the code" by reading. You
  generally **cannot** confirm "it actually works at runtime" without running it + live
  testing. Don't blur the line.

## Special attention: barge-in (Issue 3)

You previously said barge-in here is **frontend-driven** — the interrupt comes from the
WebUI over the RTC data channel (`rtc_stream.py:274`), server-side VAD is disabled during
playback (simplex mode), and that frontend is a **submodule that isn't even cloned yet**.

So be explicit:

- If the frontend submodule isn't cloned, on what basis are you judging that barge-in
  works? You'd be inferring it from the backend's receive-side handling of an interrupt
  signal you can't actually see being sent. Say that outright if so.
- What would it take to truly confirm barge-in: clone the frontend submodule? run it?
  live mic test? List the concrete steps.

## How to report back

Give me a short table or per-issue breakdown:

| Issue | Fixing commit (hash/date) | Mechanism (file:lines) | Verified by reading? | Verified at runtime? |

Then a one-line bottom line per issue: **"fixed (read-verified)"**, **"fixed (runtime-verified)"**,
**"appears fixed but unverified"**, or **"not actually fixed / still present."**

## Don't do yet

- Don't start implementing fixes or checking out the old baseline yet.
- Don't set up the full heavy environment (multi-GB weights, API key) just for this — but
  if a lightweight subset (cloning the repo, reading git history, optionally cloning the
  frontend submodule) is enough to gather the evidence above, do that.

The goal of this step is only: **give me verifiable proof of what is and isn't fixed**, so
I can email my colleague with facts rather than guesses.
