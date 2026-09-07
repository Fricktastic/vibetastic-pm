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
| PLAN has tasks `in_progress` | Mark them `failed` (`failure_count +1`), log `task_interrupted`, re-evaluate |
| PLAN has ready tasks, current stage `in_progress` | Resume dispatch loop |
| Stage done, next stage `pending` (interrupted mid-transition) | Auto-advance: summarize, set next stage `in_progress`, dispatch (Gate 3 no longer waits) |

---

## Onboarding

Run only when `PROJECT.md` does not exist. Note: `framework/setup.sh` does this automatically if run before the first session — check before asking.

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

Then detect installed builder CLIs (`command -v codex claude opencode`) and propose the
backend order — default `codex, claude, opencode`, filtered to what's installed. The user
can reorder or drop backends.

Write `PROJECT.md`:

```markdown
---
project: <project-name>
setup_at: <ISO8601>
builder_backends: [<detected order, e.g. codex, claude, opencode>]
reviewer_backends: [opencode]
critic_backends: [codex, opencode]
codex_weekly_burn_threshold: 4000000
claude_window_burn_threshold: null
max_concurrent: {codex: 2, claude_builder: 1, claude_review: 2, opencode: 4}
max_concurrent_total: 6
---

## Project Paths

| Key | Path |
|-----|------|
| PM directory | `<absolute-path-to-this-pm-dir>` |
| Code directory | `<absolute-path-to-code-dir>` |
| Issue repo | `<org/repo>` |

## Verify command

<!-- Single-line command run in the code directory after each OpenCode task. Exit 0 = the
     task didn't break the project. Passed to dispatch.sh as the verifier; on failure the loop
     feeds its output back to the model and retries. Leave the code block empty to disable.

     It must COMPILE THE TEST TARGET, not just the app — on iOS use
     `xcodebuild build-for-testing`, never a bare `build` (issue #36).

     THIS IS NOT A TEST RUN. Builders cannot execute simulator-dependent tests
     (CoreSimulatorService is a Mach service no sandbox grant provides). Running the suite is
     the orchestrator's job, on a real simulator/device — framework/VERIFY.md § Who runs what. -->

```
<verify-command-or-empty>
```

## Test command

<!-- Single-line command that RUNS the suite on a real simulator/device, from the code
     directory. The orchestrator's command — dispatch.sh never runs it and builders cannot.
     Pin the destination explicitly. Empty if the project has no runnable suite. -->

```
<test-command-or-empty>
```

## Notes

<!-- Add any project-specific notes here for future PM sessions. -->
```

**Tunable frontmatter keys.** Beyond `builder_backends`, the frontmatter carries the
per-project routing/threshold knobs. `framework/` is a read-only subtree, so these live here
— never edit the framework defaults to tune one project. Defaults match framework behavior;
omitting a key uses the default. Authority for each: `framework/MODELS.md` § Project
configuration keys.

| Key | Default | Meaning |
|---|---|---|
| `reviewer_backends` | `[opencode]` | Ordered preference for the first-pass Reviewer lane. Still subject to the family-diversity hard rule (`VERIFY.md`) — config narrows the choice, it never overrides diversity. |
| `critic_backends` | `[codex, opencode]` | Ordered preference for the pre-build Critic lane. Same diversity constraint, measured against the plan's author. |
| `codex_weekly_burn_threshold` | `4000000` | ISO-week codex burn proxy above which the `sol@high` rung is skipped (see `dispatch.md` § Backend & Tier Escalation). |
| `claude_window_burn_threshold` | `null` (ungated) | 5-hour-window claude burn proxy, symmetric to the codex gate. Ungated until the 2026-08-17 telemetry review (issue #14). |
| `max_concurrent` | `{codex: 2, claude_builder: 1, claude_review: 2, opencode: 4}` | Per-lane concurrent-dispatch cap for parallel fan-out. `claude_review` is unused while `reviewer_backends` is `[opencode]`. |
| `max_concurrent_total` | `6` | Global concurrent-dispatch cap per project. |

Check whether `.claude/settings.json` exists. If not, tell the user to run `bash framework/setup.sh <project-name> <code-dir>` from the PM directory and restart.

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
5. Write `PLAN.md` with all tasks at `status: pending`, `failure_count: 0`.
5. Append `plan_generated` to TASK_LOG.
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
