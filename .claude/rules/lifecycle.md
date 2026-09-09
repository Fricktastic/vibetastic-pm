# Lifecycle

## Startup Sequence

After `eval "$(~/.ssh/gh-agent-token.sh)"`, check for `PROJECT.md`. If missing, run Onboarding. Otherwise read `PROJECT.md` for the code directory path, then:

0. **Read `HANDOFF.md` FIRST** (if it exists). It is the prior session's flushed picture of
   where things stood — current stage, in-flight dispatches, next planned action, open
   questions, and in-session-only context. It exists so a fresh session does **not** have to
   re-derive state by exploring. Treat needing to explore the code/logs to reconstruct state
   as a **failure signal**: the handoff was incomplete. Log a `handoff_gap` note to TASK_LOG
   (what was missing) so the gap can be fixed — the write-through and checkpoint rules
   (`framework/RULES.md` § Session Handoff) are supposed to prevent it.
1. Read `SPEC.md` — check `status`
2. Read `PLAN.md` — check stage and task statuses
3. Read `TASK_LOG.md` last 20 entries — understand what just happened

`HANDOFF.md` is orientation, not authority: where it disagrees with `PLAN.md`/`TASK_LOG.md`,
the durable state files win (the handoff may predate the last write). Reconcile and, if the
handoff was stale, note it.

Branch on state:

| State | Action |
|---|---|
| `PROJECT.md` missing | Run Onboarding |
| SPEC doesn't exist | Begin SPEC Interview |
| SPEC `status: draft` | **Gate 1** — present spec for approval |
| SPEC `status: approved`, PLAN empty | Generate PLAN |
| PLAN has tasks `in_progress` | Reconcile runs, processes and worktrees per ORCHESTRATOR.md; preserve live/completed work, never infer failure from interruption |
| PLAN has ready tasks, current stage `in_progress` | Resume dispatch loop |
| Stage done, next stage `pending` (interrupted mid-transition) | Auto-advance: summarize, set next stage `in_progress`, dispatch (Gate 3 no longer waits) |

---

## Onboarding

Run only when `PROJECT.md` does not exist. Prefer the one-time setup command before the first
provider session; it creates `PROJECT.md` and installs both provider adapters. If setup has
already run, skip this section.

Ask the user (one message):
- What is the project name?
- What is the absolute path to the code directory?
- What is the GitHub repo for issues and PRs? (format: `org/repo`)
- What single command compiles **everything, including the test target**, runnable on *this*
  machine without booting a simulator? On iOS that is `xcodebuild build-for-testing` — not a
  bare `build`, which never compiles the test target and so cannot notice a test file that
  does not build (issue #36). This becomes the per-task gate for the self-correction loop. If
  unknown, leave blank — the loop degrades to a single run with no auto-correction.
  **It is not a test run.** Test execution is the orchestrator's job, on a real
  simulator/device — see `framework/VERIFY.md` § Who runs what.
- What single command **runs the test suite** on a real simulator/device? This is the
  orchestrator's command — `dispatch.sh` never runs it, and no builder can. Pin the
  destination explicitly. It goes in `PROJECT.md § Test command` so no future session has to
  re-derive it.

Run setup with the answers rather than writing `PROJECT.md` by hand. Setup detects installed
builder CLIs in `codex, claude, opencode` order and writes the project routing defaults from
`MODELS.md`. The optional verify and test commands correspond to the two questions above:

```sh
bash framework/setup.sh <project-name> <absolute-code-dir> <org/repo> ['verify-cmd'] ['test-cmd']
python3 framework/scripts/orchestrator-doctor.py --pm-dir . --framework-dir framework
```

Setup writes `PROJECT.md`, merges the managed harness into `CLAUDE.md` and `AGENTS.md`,
installs `.claude/settings.json` and `.codex/hooks.json`, and ignores `.orchestrator/` lease
state. Review Codex project hooks with `/hooks`. Start the chosen provider through the lease
wrapper from the PM directory:

```sh
python3 framework/orchestrate.py claude
python3 framework/orchestrate.py codex
```

For an existing PM directory, do not rerun setup. Use the idempotent
`install-orchestrators.py` command documented in `README.md`, then run the doctor.

Append `onboarding_complete` to TASK_LOG. Then proceed to SPEC Interview.

---

## SPEC Interview

Goal: produce a complete, approved SPEC.md. Do not fabricate requirements — ask.

1. Tell the user you will ask a few questions to build the project spec.
2. Ask (can be batched in one message):
   - What problem does this project solve?
   - Who are the users?
   - What are the 3–5 most important things it must do?
   - What is explicitly out of scope?
   - Are there technical constraints (stack, platform, APIs, existing code)?
   - How will we know it's done and correct?
3. Write `SPEC.md`. Set `status: draft`, `created` and `updated` to current ISO8601.
4. Append `spec_drafted` to TASK_LOG.
5. **Gate 1**: Present spec. Wait for approval or revisions. Revise and re-present as needed.
6. On approval: set `status: approved`, write `approved_at`. Append `spec_approved` to TASK_LOG.

---

## Plan Generation

Only after SPEC `status: approved`.

1. Read `SPEC.md` in full.
2. Determine stages and tasks. Default structure:
   - **Stage 1 — Design**: one Designer task → `prompts/design-spec.md`
   - **Stage 2 — Architecture**: one Architect task → `prompts/build-spec.md`
   - **Stage 3 — Implementation**: one or more OpenCode tasks, each a single coherent invocation
3. Declare explicit `depends_on` for every task. Check for cycles before writing.
4. For each build task, set `backend` to the first entry of `builder_backends` in
   `PROJECT.md` and `tier` to the assigned starting tier (the suggested tier, or lower toward
   `fast` to bias for cost — see Backend & Tier Escalation in `dispatch.md`), then resolve
   `model` (and `fallback_model` — opencode backend only, else empty) from that backend's
   column in `framework/MODELS.md`. The PM re-resolves these on escalation.
5. Use `plan-update.py` to install the candidate PLAN (all tasks pending, failure_count 0) and append the `plan_generated` event in one recoverable transaction.
6. Present plan summary in plain language (not raw YAML) — stage names, task count, key dependencies.
7. Begin Stage 1 — Design immediately (no gate): set `stages[1].status: in_progress`, append `stage_transition`, dispatch. Tell the user: *"Plan is ready — starting Stage 1 (Design). Reply if you want to adjust."*

---

## Lifecycle Gates

Two hard stops (Gate 1, Gate 2). Do not infer consent, do not proceed on timeout, do not
self-approve. Gate 3 auto-advances and does not block.

| Gate | Trigger | What you say | What unlocks it |
|---|---|---|---|
| **Gate 1** | `SPEC.md status: draft` | Present spec, ask for "approved" or feedback | User types "approved" |
| **Gate 2** | Task `failure_count` reaches 2 | Show both errors, ask retry/skip/abort | User chooses an action |
| **Gate 3** | All tasks in Stage N are `done` | Summarize stage, say it's auto-advancing | Nothing — proceeds immediately |

### Gate 3 Detail — Stage Transition (auto-advance)

When all tasks in Stage N reach `status: done`:

1. Set `stages[N].status: done` in PLAN.md.
2. Append `stage_complete` to TASK_LOG.
3. Present to user: what was accomplished, key output file paths, what Stage N+1 will do.
4. Say: *"Stage [N name] complete — auto-advancing to Stage [N+1 name]. Reply now to adjust or pause."*
5. **Do not wait.** Set `stages[N+1].status: in_progress`, append `stage_transition`, enter the dispatch loop.
6. If the user sends adjustments (before or during Stage N+1), apply them to PLAN.md/SPEC.md and re-dispatch as needed.
