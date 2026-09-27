# vibetastic-pm

A framework for running an AI orchestrator that drives coding work in a separate target
repo, instead of writing code itself. The orchestrator (a Claude or Codex session) plans the work,
dispatches implementation tasks to builder models, and enforces review and merge gates
before anything lands.

## How it works

A project gets its own `<project>-pm/` directory that pulls this repo in as a read-only
`framework/` subtree. That directory holds the live state for one project: `SPEC.md`,
`PLAN.md`, `TASK_LOG.md`, and rendered prompts. The partner session running in that
directory is the orchestrator - it reads the state files, decides what's ready to build,
and calls `dispatch.sh` to hand tasks off to a builder.

Builders run across three backends, tried in order until one is available and capable
enough to pass the task's verify command: Codex (ChatGPT subscription), Claude
(subscription), and OpenCode/OpenRouter (metered, used only when the flat-rate backends
are exhausted). Each task escalates in tier (fast/standard/heavy) before falling through
to the next backend, so cheap models are tried first and cost only climbs when a builder
actually fails to pass verification.

Two hard gates require explicit human sign-off: approving the SPEC before planning starts,
and deciding what to do after a task fails twice in a row. Everything else - stage
transitions, tier escalation, backend fallback - proceeds without asking.

## Gates and policy

Review gates are enforced by the scripts, not left to the orchestrator's discipline:

- **Pre-build critique** runs on any task with `risk: true` or `security: true`, set by the
  spec author from the project's risk triggers. `verify_tier` says what evidence proves a
  task; it no longer decides critique (legacy tasks with no `risk:` field keep the old R1/R2
  rule). `dispatch.sh` refuses the build until `scripts/review_gate.py adjudicate` records
  the orchestrator's `proceed` or the operator's logged `override`.
- **Round caps**: critique rounds and reviewer fixup rounds are capped per task (default
  2 / 3). The next round is refused and the operator chooses redesign, override or abort.
- **Exit 31** is a gate or policy refusal - ownership, an unadjudicated critique, a round
  cap, a merge-gate check. It is never a task failure and never touches `failure_count`.
- **Merges** go through `scripts/merge_gate.py`, which pins the verification, the approving
  review and the task's fail-on-base or runtime observation to the exact commit merged, then
  runs `gh pr merge --match-head-commit`.
- **Project policy** - what the verify tiers mean, the risk triggers, the caps, test and
  non-production paths - lives in the project's `PROJECT.md`, with generic defaults in
  `scripts/project_policy.py`. `Docs/examples/policy-ios.md` is a worked example.
- **`logs/verdicts.jsonl`** is the ledger all of these are computed from: critic and reviewer
  verdicts, adjudications, cap overrides and merge evidence. It is gate state; never edit it.

See `VERIFY.md` for the rules and `.claude/rules/dispatch.md` for the procedure.

## What this framework already covers

Check this list before proposing a new rule or adopting an imported plan. A plan that assumes
another harness (e.g. `superpowers:executing-plans`) should be translated onto these
mechanisms, not run beside them. "Enforced" means a script refuses or records the thing
itself; "advisory" means a prompt or rule asks for it and nothing checks.

Enforced:

| Failure mode | What stops it | Where |
|---|---|---|
| Builder edits the live checkout, or parallel builds collide | Build dispatch without `--worktree` refused (exit 2); worktree per branch | `dispatch.sh` |
| Builder pushes or opens PRs | `gh` unauthenticated, worktree `pushurl` poisoned | `dispatch.sh` |
| "Done" with no build check; self-correction never runs | Build dispatch without a verify command refused (exit 2); failing verify fed back to the builder, exit 20 when attempts run out | `dispatch.sh` |
| Tier that doesn't match the model; unladdered or over-budget `sol@high` | Model/tier mismatch and first-attempt `@high` refused (exit 2); weekly burn gate (exit 30) | `dispatch.sh` |
| A read-only review or diagnosis run that edits files | Tree snapshot compared after the run (exit 21) | `dispatch.sh` |
| Risky plan built without critique; critique ignored | Build refused (exit 31) until a `proceed`/`override` adjudication is recorded; `proceed` refused over a `[BLOCKING-PLAN]` | `dispatch.sh`, `scripts/review_gate.py` |
| Critic or reviewer loops that never converge | Round caps per task (exit 31) | `dispatch.sh`, `scripts/review_gate.py` |
| Same-family review or critique | Author and reviewer/critic families compared (managed projects, exit 31) | `scripts/orchestrator-routing.py` |
| Merge of a tree nobody verified or reviewed; green-but-inert change | Verification, review and observation pinned to the merged SHA; a diff with no production change refused; `gh pr merge --match-head-commit` | `scripts/merge_gate.py` |
| A "regression test" that passes without the fix | `fail-on-base` must see the named test fail on the base tree | `scripts/merge_gate.py` |
| Orchestrator reads whole task/critic specs into context | Read/`cat` of `prompts/task-T*.md` / `critic-T*.md` blocked | `scripts/spec-body-guard.py` via `scripts/orchestrator-hook.py` |
| Hand-edited or corrupted PLAN | Direct `PLAN.md` writes blocked; hash-checked, linted transactions | `scripts/orchestrator-hook.py`, `scripts/plan-update.py` |
| Two orchestrators writing state at once | Single-writer lease | `orchestrate.py`, `scripts/orchestrator-state.py` |
| Orchestrator token burn invisible | Stop hook writes `role: partner` rows to `logs/cost.jsonl` | `scripts/log-partner-burn.py` |
| Stale handoff claims read as facts | `## Volatile — re-verify before use` printed under a "claims, not facts" banner at session start (surfaced, not blocked) | `scripts/handoff-volatile-hook.py` |

Advisory (a rule or prompt, nothing refuses):

- Tier and backend escalation on exit 20/30 is the orchestrator's procedure
  (`.claude/rules/dispatch.md` § Backend & Tier Escalation).
- A plain `gh pr merge` is not intercepted; the merge gate holds only when merges go through
  `merge_gate.py`.
- Root cause before fix: defect-fix specs carry Symptom / Mechanism / Evidence, and the
  critic blocks reasoning-only Evidence, but only on `risk`/`security` tasks
  (`VERIFY.md` § Pre-build critique). Diagnosis itself is `investigate.sh`, by choice.
- Cheap-tier delegation of review, diagnosis and spec-writing (`.claude/rules/pm-scope.md`).
- When the orchestrator may author target code itself: the five conditions of
  `.claude/rules/pm-scope.md` § Inline authoring gate are its judgment, declared as
  `inline_authored`. Nothing detects an undeclared inline edit; the merge gate still requires a
  family-diverse review and verification of the merged commit.

## What's in this repo

- `ORCHESTRATOR.md` - shared contract; `CLAUDE.md` and `AGENTS.md` are provider entry points
- `RULES.md` - detailed operating rules and lessons learned from running this in production
- `MODELS.md` - model and tier selection, the source of truth for which model runs what
- `VERIFY.md` - the merge gate: review tiers and what has to pass before a diff merges
- `dispatch.sh` - the wrapper that runs a builder task in an isolated worktree and retries
  it against a verify command
- `setup.sh` - one-time setup for a new `<project>-pm/` directory
- `.claude/rules/` - the mechanics behind lifecycle, dispatch, state, and token economy
- `prompts/` - prompt templates for each role (architect, designer, tech lead, reviewer, critic)
- `scripts/` - support scripts: review and merge gates (`review_gate.py`, `merge_gate.py`),
  project policy, plan linting, cost reporting, screenshotting
- `Docs/` - the upgrade guide, a worked project-policy example, design history

## Setup

From a new `<project>-pm/` directory, with this repo checked out as `framework/`:

```
bash framework/setup.sh <project-name> <path-to-code-dir> <org/repo> [verify-cmd] [test-cmd]
```

The verify command compiles the project and test target without a device; the test command
runs the suite on the real target selected by the project. This writes PROJECT.md plus
additive Claude/Codex adapters. Review the project hooks in
Codex with `/hooks`, then launch either provider from that PM directory:

```sh
python3 framework/orchestrate.py claude
python3 framework/orchestrate.py codex
```

Codex defaults to a session-only OpenCode fallback profile to reserve its subscription
capacity for orchestration. `python3 framework/orchestrate.py --profile normal codex`
uses normal project routing. The single-writer lease prevents concurrent orchestrators;
PLAN updates are linted transactions with recoverable TASK_LOG events. Both provider
adapters block bulk task/critic-spec reads from the expensive partner context.

For an existing project, use the idempotent adapter installer instead of setup:

```sh
python3 framework/scripts/install-orchestrators.py --pm-dir . --framework-dir framework
python3 framework/scripts/orchestrator-doctor.py --pm-dir . --framework-dir framework
```

Merges go through `scripts/merge_gate.py` (`.claude/rules/dispatch.md` § Merge gate).
An existing project pulling framework updates should follow
[the upgrade guide](Docs/upgrading.md): newer releases add enforced gates that refuse work an
unmigrated project would otherwise dispatch.

See [the shared operating guide](ORCHESTRATOR.md) for state commands, recovery, role dispatch,
and hook limitations. [The trial protocol](Docs/codex-orchestrator-trial.md) distinguishes
automated implementation checks from representative project sessions still to be measured.

## Note on this repo itself

This repo is the framework source, not a framework-managed project. The `PLAN.md`,
`SPEC.md`, and `TASK_LOG.md` files at the root are shipped templates consumed by
`setup.sh`, not live state - work on the framework itself is tracked through normal git
branches and commits.
