# Consultation: should the orchestrator move off Claude Code, and what would it cost?

You are GPT-5.6, consulted as a **peer second opinion**, not as a builder. Change nothing.
Read what you need in this repo, then answer. **Disagree freely** — the person asking has
explicitly said capitulation is worse than disagreement, and the assistant requesting this
consultation is arguing a position it has a self-interested stake in (it is a Claude model
arguing about whether to be replaced by you). Weigh that.

## Context

This repo (`vibetastic-pm`) is a build framework. Read these first:
- `CLAUDE.md` — what the orchestrator is and does
- `.claude/rules/dispatch.md` — the dispatch loop, subagent roles, backend/tier escalation
- `MODELS.md` § Orchestrator and § Agent Roles
- `VERIFY.md` — the merge gate
- `dispatch.sh` — the provider-agnostic dispatch layer (skim; note `--read-only`, exit codes 2/20/21/30)
- `scripts/log-partner-burn.py`, `scripts/plan-lint-hook.py` — harness-coupled enforcement

**Architecture today.** A human ("the operator") talks to a Claude Code session (Opus) that
acts as orchestrator: it holds intent, adjudicates gate verdicts, writes PLAN/TASK_LOG state,
and dispatches actual code work to builder CLIs via `dispatch.sh` across three backends
(codex → claude → opencode, flat-rate first). Some roles are already provider-agnostic
(Reviewer, Critic, all builders — they run through `dispatch.sh`). Three roles are pinned to
Anthropic because they spawn via Claude Code's `Agent` tool: Designer, Tech Lead, Architect.
Two enforcement points are pinned to Claude Code hooks: `PostToolUse` (plan-lint blocks a
structurally corrupt PLAN.md write) and `Stop` (orchestrator token telemetry).

## Measured data (first orchestrator telemetry, collected today)

Across 56 orchestrator sessions and 327 dispatches in three projects:

| Lane | Runs | Total tokens | Fresh (in+out) |
|---|---:|---:|---:|
| Orchestrator (Claude Opus) | 56 | 983,660,286 (69.8%) | 5,891,029 (4.4%) |
| codex builders | 175 | 219,269,200 | 115,089,232 (86.7%) |
| opencode builders | 94 | 117,210,764 | 10,127,500 |
| claude builders | 58 | 88,669,916 | 1,561,776 |

Orchestrator composition: **97.3% cache reads**, 0.6% fresh. Median Opus session: **169 turns,
97,763 tokens of context per turn, 16.7M cache reads**. So burn is dominated by turn count,
not context size.

## The operator's position

1. The **Claude 5-hour rolling window is the binding constraint**. They exhaust it regularly
   while their ChatGPT/Codex subscription stays nearly idle. Dollar cost is not the issue;
   both are flat-rate subscriptions. The constraint is a hard cap on one side and slack on
   the other.
2. They keep context under ~30%, so "trim context" is not the available lever.
3. Their experience is that Sonnet underperforms for orchestration and that a stronger model
   at low reasoning effort consumes **less** overall because it is right in fewer turns.
   (The telemetry above cannot confirm this — the model comparison is confounded by task mix.)
4. They wonder whether a **provider-agnostic harness** — able to swap orchestrator model
   providers — is the natural progression.

## The Claude assistant's current position (attack it)

That the harness, not the model, is the value; that a GPT orchestrator would re-read context
identically and merely relocate the burn; and that the gap is narrow — convert three subagent
roles to `dispatch.sh` prompts (their prompt files already exist in `prompts/`) and move
plan-lint from a `PostToolUse` hook to a git `pre-commit` hook. It cited the framework's own
precedent: enforcement in the shell layer (`dispatch.sh` exit 2 for a missing verifier)
survived every harness change, while enforcement in the hook layer silently failed twice today.

## What to answer

Be concrete and cite specifics. Say "I don't know" where you don't.

1. **Codex CLI capability, factual.** What does Codex CLI actually offer for (a) lifecycle
   hooks that can *block* an action, (b) spawning sub-agents with separate context, (c)
   loading persistent project rules? This is the load-bearing unknown. If you are not
   confident about the current state, say so plainly rather than guessing — a wrong
   confident answer here is worse than no answer.
2. **Does relocating the orchestrator actually help?** The operator's asymmetry argument
   (hard cap vs idle slack) versus the claim that burn merely moves. Who is right, and under
   what conditions does the answer flip?
3. **Is turn count reducible by model choice**, or is it a property of the work? What would
   you expect a GPT-5.6 orchestrator's turn count to look like on this workload versus Opus's
   median of 169, and why?
4. **Is the "move enforcement into the shell layer" plan sound**, or is it underestimating
   what the harness provides? Name anything load-bearing it misses.
5. **What would you do**, given the operator's actual constraint? Include the option the
   Claude assistant may be structurally disinclined to recommend.

Return prose. No file changes.
