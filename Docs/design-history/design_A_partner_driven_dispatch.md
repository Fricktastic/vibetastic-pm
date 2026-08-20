# Design A — Collapse the deployment PM session into the partner workspace

**Status:** shape proposal (2026-06-29). Decide the shape here; it frames where B's gate lives. Wiring is a later step.

## The principle
**Collapse the *session*, keep the *process*.** The thing that grates is a separate interactive PM session you context-switch into. The thing worth keeping is the framework's *discipline*: cheap-tier builder dispatch, PLAN/TASK_LOG state, Gates, verify, stacked PRs, the uncertainty-queue protocol. A must preserve all of that while removing the second session.

## What moves
| Role (today) | Today | After A |
|---|---|---|
| Human-facing orchestration | separate `hometastic-pm` Claude session (`setup.sh` → `claude --model sonnet` + PM rules) | the **Opus partner** in `hometastic-run` (the session Tim already prefers) |
| Architecture / spec | PM spawns Architect/Tech-Lead subagents | partner authors specs inline (already the architect-partner role), or spawns `Agent` subagents for parallel work |
| Cheap implementation | `dispatch.sh` → opencode (non-Anthropic tiers) | **unchanged** — partner invokes the same `dispatch.sh` |
| Diff review before merge | ad hoc / weak | **partner reviews the builder diff with Opus** — a structural gate rung (this is exactly what caught #72 by hand) |
| Runtime state | `hometastic-pm/` root (`PLAN/TASK_LOG/logs/artifacts`) + `framework/` subtree | **unchanged dir, no longer a session** — it's plumbing the partner reads/writes |

## Why this is cost-safe (the founding goal)
The cost lever was never the orchestrator — it was the old **API-Opus builder tier** (`openrouter/anthropic/*`), already removed. The PM session billed on Tim's subscription separately. Collapsing orchestration into an **Opus subscription partner** reintroduces **no** dashboard cost: Anthropic stays on subscription, cheap builder work stays on the non-Anthropic API tiers via `dispatch.sh`. If anything A *removes* the parallel Sonnet PM session. Invariant intact: **no Anthropic via API in opencode tiers.**

## The A↔B synergy (why decide A first)
In the collapsed model, **Opus diff-review before merge is structurally free** — the partner is already Opus and already holds the diff. That's precisely B's hardest rung ("Opus review as a non-optional gate step"). So B's gate gets *built into A's shape* rather than bolted onto a remote PM. Decide A → build B into it → wire A.

## Shape options
- **A1 — Partner *is* the orchestrator (recommended).** The Opus partner in `…-run` drives `dispatch.sh` directly (Bash) against the deployment's `framework/`, specs inline, reviews diffs, manages PRs/merges, keeps PLAN/TASK_LOG current. Matches Tim's stated preference ("I'd rather talk to you than the PM"). The PM *rules* (`dispatch/state/economy/lifecycle/pm-scope`) become the partner's operating guidance (referenced from the run CLAUDE.md). No new orchestration layer.
- **A2 — Partner spawns an in-session PM subagent.** Keeps a distinct PM "role" but as an `Agent` (no separate window). Cleaner main thread, but reintroduces the PM layer Tim wanted to shed — and loses the in-context fluency that made this session better.
- **A3 — Hybrid:** PM survives only as a subagent invoked for big multi-task initiatives; small work the partner does directly. More moving parts.

**Recommendation: A1.** It's the literal expression of "collapse the session," gives B's review-gate for free, and is cost-neutral-to-positive.

## Ergonomics & context hygiene (core to A1, not optional)
A1 only works if the partner thread stays conversational and lean while orchestrating. Design rules:
- **Conductor, not laborer.** The main thread holds *intent, decisions, and a lean running state* — never raw byproducts.
- **Background the long pole.** `dispatch.sh`/opencode builder runs launch as **background** processes; the session stays interactive (Tim keeps chatting) and the partner is re-notified on completion. Only a *summary* (PR link, verify result, notable diffs) returns to the thread — raw logs go to `logs/`/`artifacts/`.
- **State on disk, not in context.** PLAN/TASK_LOG/uncertainty-queue are read on demand, not held resident (framework already persists these).
- **Subagents absorb the loud work.** Context-heavy or parallel sub-tasks (spec a task, review a diff, investigate a failure) run in `Agent` subagents (tier-selectable — Opus for judgement, Sonnet/Haiku for bulk); only their *conclusion* returns. There is **no dedicated "orchestrator subagent"** — orchestration is the main thread.
- **Interactivity guarantee:** Tim can chat with the partner throughout; the partner is never heads-down for long stretches because the minutes-long work is backgrounded and the verbose work is delegated.
- **Honest limit:** a very long single session still grows the main thread despite offloading; rely on deliberate delegation (and harness compaction as backstop), and start a fresh session per initiative when practical.

## Open questions for the wiring step (not now)
1. How do the PM `RULES`/prompts get surfaced to the partner — inline excerpt in the run `CLAUDE.md`, or a referenced `framework/RULES.md` read on demand? (Lean: reference + a short operating-rules section in CLAUDE.md.)
2. Does `dispatch.sh` need a "partner mode" (skip the PM-session assumptions in `setup.sh`), or does the partner just call it directly with the right env?
3. State location: partner writes PLAN/TASK_LOG into the existing `hometastic-pm/` dir (keep deployment state co-located with logs/artifacts) — confirm that's the home vs. moving state into `…-run`.
4. Multi-project generality: A1 should generalize so any `<app>-run` can drive any `<app>-pm/framework`. Bake the pattern into `vibetastic-pm` docs so it's not hometastic-specific.
