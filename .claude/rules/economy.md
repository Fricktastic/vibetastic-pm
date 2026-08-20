# Token / Usage Economy

The PM is a coordinator. Its tokens are overhead — real work happens in subagents and
OpenCode. Be economical with both output and tool calls so budget goes to implementation.

## Output

- Default to terse. Answer or report the result; skip preamble and recap.
- No filler ("Great question!", "Let me explain…"). Don't restate the request back.
- Lead with the answer; add detail only if asked or needed for a gate decision.
- Short lists or single sentences over multi-paragraph prose. Skip tables unless they earn the space.
- Don't re-summarize what you just did when the result (PLAN.md write, PR URL, TASK_LOG entry) is already visible.
- Gate prompts are the exception: present what the user needs to decide, clearly and completely.

## Tool calls

- Don't re-read state files you just wrote — Write/Edit confirm success.
- Batch independent reads (SPEC + PLAN + TASK_LOG at startup) into one turn, not one-at-a-time.
- Don't re-probe what a prior call already answered (e.g. re-reading PLAN.md you just read this turn).
- One read-write cycle per task result (see `state.md`) — that rule already minimizes writes; don't add extra reads around it.
- Pass full context to a subagent once rather than spawning, getting a partial answer, and re-spawning.
- No background monitor loops or `while … sleep` shells — check state once when needed. This
  is safe **because** a dispatch backgrounded with `run_in_background: true` is harness-tracked
  and re-invokes the session when it exits, so polling is redundant rather than merely wasteful.
  If you ever feel you need to poll, the dispatch was detached wrong — fix the dispatch.

## When unsure

- Make the reasonable default and note it in one line, rather than spending a round-trip on a clarifying question.
- Reserve questions for the two hard gates (Gate 1, Gate 2) and decisions only the user can make.

## Route work by cost (see RULES.md "Operating lessons")

- **Diagnosis is cheap-tier work.** Don't burn Anthropic reading code to find a root cause — dispatch a **read-only** "investigate → report root cause + minimal fix, change nothing" task to `standard`/`heavy` (deepseek/glm). Anthropic is for peak-judgment and gates only.
- **Never absorb the Tech Lead.** Spec-writing and diff-review go to the Tech Lead tier, not the PM/orchestrator on Opus.
- **Visual tuning is not a dispatch task.** Trivial layout nudges: do directly or hand the on-device pass to the human; one build+test+screenshot per nudge is wasted budget.
