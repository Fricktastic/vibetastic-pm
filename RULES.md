---
framework: vibetastic-pm
version: "2.0"
---

## Directory Convention

Each project gets a sibling PM directory named `<project-name>-pm/`:

```
Developer/
├── my-app/          ← target project (any stack)
└── my-app-pm/       ← PM framework instance (this structure)
    ├── CLAUDE.md    ← managed Claude entry block (existing content preserved)
    ├── AGENTS.md    ← managed Codex entry block (existing content preserved)
    ├── SPEC.md
    ├── PLAN.md
    ├── TASK_LOG.md
    ├── prompts/     ← PM-written project outputs (design-spec, build-spec, task files)
    └── framework/   ← git subtree tracking vibetastic-pm; read-only
        ├── CLAUDE.md
        ├── RULES.md
        ├── WALKTHROUGH.md
        ├── dispatch.sh
        └── prompts/ ← agent prompt templates (designer, architect, tech-lead)
```

The `-pm/` directory is the sole source of truth for project state. The target project directory is treated as an opaque build output.

---

## File Ownership

The lease-owning orchestrator is the **sole writer** to durable state files. Role workers
receive context slices and return structured results. A worker may write only to its isolated
staging path; the lease owner validates and promotes that output through the state commands.

| File | Writer | Readers |
|---|---|---|
| SPEC.md | PM | PM, Designer |
| PLAN.md | PM | PM |
| RULES.md | Human (setup) | PM, all agents |
| TASK_LOG.md | PM (append-only) | PM (recovery) |
| HANDOFF.md | PM (sole writer, overwritten in place) | PM (next session, read first at startup) |
| PROPOSALS.md | PM (append-only, **fallback only** — `gh` unreachable) | Maintainer (archive) |
| GitHub issues (`Fricktastic/vibetastic-pm`) | PM (`gh issue create`) | Maintainer (triage) |
| prompts/design-spec.md | PM (from Designer output) | Architect, Tech Lead |
| prompts/build-spec.md | PM (from Architect output, Stage 2 only — never modified after) | Tech Lead |
| prompts/task-T0XX.md | PM (awk extract for Architect tasks; direct write for Tech Lead tasks) | OpenCode |
| framework/* | git subtree (read-only) | PM, all agents |

---

## Task Lifecycle

```
pending → in_progress → done
                      → failed
```

- **pending**: task exists but has unmet dependencies or has not been dispatched
- **in_progress**: PM has dispatched the task to an agent; awaiting return
- **done**: agent returned successfully; PM has written outputs and updated PLAN.md
- **failed**: agent returned an error; PM writes `error` field and evaluates retry budget

**Ready computation (PM, each dispatch cycle):**
A task is ready to dispatch when `status == pending` AND all tasks in `depends_on` have `status == done`.

The PM resolves the dependency DAG on each cycle. It does not store `ready` or `blocked` states — those are always computed, never persisted, to avoid stale state.

---

## Dependency Rules

- Dependencies are declared as task ids in `depends_on`.
- Circular dependencies are a fatal error — PM escalates to user and halts.
- A task with `depends_on: []` is always ready to dispatch (after SPEC is approved).
- Tasks with the same dependency profile and no inter-dependency may be dispatched in parallel at PM's discretion.

---

## SPEC Approval Gate

The PM **cannot generate or modify PLAN.md** while `SPEC.md` has `status: draft`.

Flow:
1. PM interviews user, populates SPEC.md body, sets `status: draft`.
2. PM presents SPEC.md to user for review.
3. On user approval: PM sets `status: approved`, writes `approved_at`, updates `updated`.
4. PM generates PLAN.md and appends `spec_approved` event to TASK_LOG.md.

---

## Model Selection

All model assignments are defined in `framework/MODELS.md`. That file is the single source of truth — do not hardcode model slugs in prompts or instructions.

**For agent roles** (Designer, Architect, Tech Lead): resolve the effective backend through
`orchestrator-routing.py`. Claude sessions may use native role agents; the Codex fallback
profile dispatches roles through OpenCode with `dispatch-role.py`.

**For build tasks**: the Tech Lead recommends a tier (`fast` / `standard` / `heavy`) in its
output metadata. The orchestrator resolves the backend/profile and writes the corresponding
model slug to `tasks[n].model` before dispatch. For Stage 2 Architect-selected tasks, the
Architect classifies the task complexity and picks a tier directly.

**Fallback**: each tier's fallback model is the `Fallback` column of the OpenCode Tiers
table in `framework/MODELS.md` — dispatch.sh retries with it automatically on an infra
failure of the primary.

---

## Stages

A **Stage** is a named group of tasks in PLAN.md that represents a logical phase of the project. Stages are defined in the `stages:` list and referenced by `stage:` on each task.

Default stages:
- **Stage 1 — Design**: Designer agent produces `prompts/design-spec.md`
- **Stage 2 — Architecture**: Architect agent produces `prompts/build-spec.md` and selects model
- **Stage 3 — Implementation**: OpenCode executes against the target project

The PM generates stage definitions as part of plan generation. Custom projects may have more or fewer stages.

**Stage status transitions:** `pending → in_progress → done`

A stage moves to `done` when all tasks with that `stage:` id have `status: done`. When a stage reaches `done`, the PM auto-advances to the next stage (see Gate 3 below) — it posts a summary but does not wait.

---

## Lifecycle Gates

**Two hard gates** require an explicit pause for user confirmation in chat: Gate 1 (SPEC approval) and Gate 2 (task double-failure). The PM **must not proceed** past either — no timeout, no self-approval, no inference of consent from prior messages. **Gate 3 (stage transition) auto-advances**: the PM posts a stage summary and continues to the next stage without waiting. The user can interject adjustments at any time, but the default is forward motion.

### Gate 1 — SPEC Approval

**Trigger:** SPEC.md has `status: draft`

**PM behavior:**
1. Display the full SPEC.md body to the user in chat
2. Say: *"Please review the spec above. Type **approved** to unlock the build plan, or give me feedback to revise it."*
3. Wait for user response. If feedback: revise SPEC, re-present, repeat.
4. On approval: set `status: approved`, write `approved_at`, append `spec_approved` to TASK_LOG, then proceed to plan generation.

**PM must not:** generate PLAN.md, dispatch any agent, or take any other action while `status: draft`.

---

### Gate 2 — Task Double-Failure

**Trigger:** A task's `failure_count` reaches 2

**PM behavior:**
1. Do not retry automatically.
2. Report to user in chat: task id, title, both error messages (from TASK_LOG), and current state of PLAN.md.
3. Say: *"Task T00X has failed twice. How would you like to proceed? Options: **retry** / **skip** / **abort**."*
4. Wait for explicit user decision. Apply it.

**Retry budget:** 1 automatic retry per task (i.e., PM retries once on first failure, increments `failure_count`, then hits Gate 2 on second failure). Not configurable — Gate 2 is always at `failure_count == 2`.

---

### Gate 3 — Stage Transition (auto-advance)

**Trigger:** All tasks in Stage N reach `status: done`

**PM behavior:**
1. Mark the stage `status: done` in PLAN.md.
2. Summarize the completed stage in chat: what was built/produced, key outputs, and what Stage N+1 will do.
3. Say: *"Stage N ([name]) is complete — auto-advancing to Stage N+1 ([name]). Reply now if you want to adjust or pause."*
4. **Do not wait.** Immediately mark the next stage `status: in_progress`, append `stage_transition` to TASK_LOG, and dispatch the first ready tasks.
5. Run the **lesson consolidation** pass below while those dispatches run.
6. If the user sends adjustments before or during Stage N+1, accept them and update PLAN.md/SPEC.md (re-dispatch as needed).

**Rationale:** the self-correction loop and tier escalation keep per-task quality bounded without a human, so the stage boundary no longer needs a hard stop. Gate 1 still guarantees the spec was right before any of this runs.

---

### Lesson consolidation (every stage transition, issue #50)

Projects accumulate lessons — hard-won rules in the project's own instructions (its CLAUDE.md /
AGENTS.md outside the managed harness block, a `LESSONS.md`, or wherever the project keeps
them). They only ever grow: one field project reached ~150, most restating each other or
guarding against failures a mechanism now blocks, and a long rule list is the kind of prose
that erodes. The lessons are **project-owned**; the framework supplies only this trigger and
procedure.

**Trigger:** every Gate 3 stage transition, after the next stage's first dispatches are
running. Never block a dispatch on it. It can also be run on the operator's request.

**Procedure:**

1. **Inventory** the active lessons (a read-only cheap-tier dispatch can do the first pass
   and propose the edits; the orchestrator decides and applies).
2. **Merge duplicates** — lessons naming the same failure and the same remedy become one,
   keeping every evidence pointer (task ids, issue numbers).
3. **Retire what a mechanism now enforces** — if a hook, a `dispatch.sh` refusal, plan-lint,
   the review gates or a doctor check now blocks the failure a lesson warns about, move the
   lesson to an archive section with one line naming the mechanism. Archive, never delete: the
   evidence is why the mechanism exists.
4. **Cap the active set** — keep it at or below the project's cap (30 unless the project's
   PROJECT.md Notes state another number). Over the cap, archive the lessons with the least
   recent evidence first, and propose the most expensive recurring ones as framework issues
   (§ Self-Improvement Capture): a rule that must hold everywhere, or only works when
   enforced, belongs upstream as a mechanism.
5. **Log** `lessons_consolidated` in TASK_LOG with `before`, `merged`, `retired`, `after`,
   and the mechanisms cited for each retirement.

---

## Escalation Triggers (Autonomous — No Gate)

The PM handles these autonomously without pausing for user input:

| Trigger | Action |
|---|---|
| Circular dependency detected in task graph | Report to user, halt, request PLAN correction |
| OpenRouter unreachable for >2 attempts | Use fallback model, log `model_fallback` event |
| Agent returns malformed/unparseable output | Log, retry once; if second parse failure, treat as task failure (increments `failure_count`) |

---

## Session Handoff (2026-07-17)

Observed failure: the orchestrator sometimes hasn't flushed session state to disk when the
user clears context at cache expiry, so the next session burns tokens re-exploring to
reconstruct what was already known. State that lives only in the conversation dies with the
context window. Two rules close the gap.

### Write-through state rule

**State writes happen at the moment of the event, never batched.** The PM may not proceed
past any state-changing event — a dispatch, a task completion or failure, a gate decision, a
stage transition, an escalation — until `PLAN.md` / `TASK_LOG.md` reflect it on disk. An
unwritten event does not exist: if the context is cleared between the event and the write,
the event is lost with no trace. This is stricter than "apply results promptly" — it forbids
holding *any* state change in-conversation while doing further work. One read-write cycle per
event (see `.claude/rules/state.md`), completed before the next action.

### HANDOFF.md checkpoint

`HANDOFF.md` lives in the `-pm/` directory (PM is the **sole writer**; overwritten in place,
never appended). It captures the in-session context that the durable state files don't — the
things a fresh session would otherwise have to re-derive. It contains:

- **Current stage + status** — which stage, which tasks in flight vs. done.
- **In-flight dispatches** — for each: task id, backend, tier, started-at, worktree path.
- **Next planned action** — what the orchestrator intended to do next.
- **Open questions awaiting the user** — any gate or decision the session is blocked on.
- **In-session-only context** — anything load-bearing that isn't already in PLAN/TASK_LOG
  (a diagnosis conclusion, a decision rationale, a reviewer verdict not yet merged).
- **Volatile claims** — in the fixed section below, and nowhere else.

### Volatile claims — one fixed section (issue #51)

Observed failure (gamedaytastic, 2026-09-24): the handoff said *"Codex quota is exhausted —
dispatches run on opencode."* The quota had reset. The next session read the sentence as a
fact and routed a spec critique and a build to metered opencode; the operator caught it, the
build was killed mid-run and redispatched on Codex. A handoff mixes **durable** content
(decisions, rationale, next action) with **volatile** state that is true only at the moment
it was written — quota or backend availability, what build is on a device, a branch head, a
PR's open/merged state, whether a task or process is still running. The durable part is
orientation; the volatile part is a claim that expires.

Every volatile claim goes in **one** section with exactly this heading (tooling detects the
exact string — do not reword it):

```markdown
## Volatile — re-verify before use
- <claim> | as-of <YYYY-MM-DD> | check: `<command that re-establishes it>`
```

Example lines:

```markdown
## Volatile — re-verify before use
- T041 build installed on test device | as-of 2026-09-24 | check: `bash scripts/device-build-watermark.sh`
- PR #88 open, awaiting review | as-of 2026-09-24 | check: `gh pr view 88 --json state -q .state`
- task/T043 head is 4f2c1ab | as-of 2026-09-24 | check: `git -C ../app rev-parse task/T043`
```

- **One claim per line**, each with an as-of date and a check command. A claim with no check
  command is not a claim anyone can use; leave it out or find the command.
- **Volatile claims are banned from prose and headers.** "Current stage", "In-flight
  dispatches" and "Next planned action" may *refer* to a volatile line ("resume once the PR in
  § Volatile is merged"), never restate it as fact.
- **Backend availability is never a handoff claim at all.** It is re-derived live: dispatch,
  and branch on exit 30 (`dispatch.sh` classifies the backend's own quota/rate-limit/auth
  refusal as exit 30). A "quota exhausted" line is exactly the sentence that caused #51.
- Check commands are **project-supplied** (a device-build watermark, a branch-head probe).
  The framework supplies the format and the mechanism, not the checks.

**Mechanism.** `scripts/handoff-volatile-hook.py` runs as a `SessionStart` hook for both
Claude and Codex (installed by `install-orchestrators.py`, verified by
`orchestrator-doctor.py`). It prints this section — and only this section — under a
*"claims, not facts"* banner, with each line's age in days and a flag on any line missing its
date or check. It is silent when `HANDOFF.md` or the section is absent and never fails a
session. The consuming rule is in `.claude/rules/state.md` § Volatile handoff claims.

The PM rewrites `HANDOFF.md` **after every gate decision and every stage transition**, and
whenever the user says **"checkpoint"**.

**The `checkpoint` command (explicit):** when the user types `checkpoint`, the PM (1) verifies
disk state (`PLAN.md`/`TASK_LOG.md`) matches its session understanding, (2) flushes anything
missing — including any unwritten event, which the write-through rule should already have
caught, (3) rewrites `HANDOFF.md`, and (4) confirms **"safe to clear"** (or names exactly
what is still in flight if it is not). This is the user's signal that they are about to clear
context; the PM's job is to make the clear lossless.

## Self-Improvement Capture — the framework proposes, the maintainer disposes (2026-07-17, revised 2026-08-19)

The PM runs the framework in the field but **never edits the framework itself** (`framework/`
is a read-only subtree). When it observes a framework defect in the field — a repeated tier
failure, a rule that forces a bad outcome, a stall, an escalation that telemetry later shows
was mispriced — it does not work around it silently and it does not patch the framework. It
**files a GitHub issue against the framework repo** (`Fricktastic/vibetastic-pm`) for a
maintainer to adjudicate.

```bash
gh issue create --repo Fricktastic/vibetastic-pm \
  --title "<component>: <one-line defect>" \
  --body "$(cat <<'EOF'
- **Project:** <project-name>
- **Observed problem:** <what went wrong, one or two sentences>
- **Evidence:** <TASK_LOG event ids / cost.jsonl line refs / log file paths — a concrete pointer>
- **Suggested change:** <the framework change that would prevent it — a proposal, not a patch>
EOF
)"
```

**Who files.** The **orchestrator** files, always. Builders dispatched via `dispatch.sh
--worktree` run with `gh` unauthenticated and `remote.origin.pushurl` poisoned by design —
they *cannot* file, and must not be asked to. A builder's framework observation reaches the
issue tracker through its dispatch return: the orchestrator reads it, judges it, files it.

**Fallback when `gh` fails** (no auth, no network — see issue #20): append the same four
fields to `PROPOSALS.md` in the `-pm/` directory (the **writable** side — **not**
`framework/`), append-only, and note in the TASK_LOG entry that the issue is unfiled. Never
drop the observation because the tracker was unreachable. `PROPOSALS.md` is otherwise
**retired** — existing files are archives, not queues; do not harvest them as live input.

```markdown
### <ISO8601> · <project-name>   <!-- fallback only -->
- **Observed problem:** …
- **Evidence:** …
- **Suggested change:** …
```

Maintainer sessions triage the issue queue together with `cost-report.sh` output and decide
what graduates into the framework. The division is deliberate: **the framework proposes, the
maintainer disposes** — field sessions surface evidence, a maintainer with cross-project view
decides what becomes a rule. Issues give that harvest for free: cross-project by
construction, deduplicated, closable, and linkable from `TASK_LOG.md`. This keeps the
framework from accreting one-off local fixes while still capturing every real defect the
moment it's seen.

## Operating lessons (hard-won 2026-06-29)

These override convenience. Each cost real cycles when ignored.

1. **Verify inputs/data before pixels or "looks done".** A component that compiles, passes mocked tests, and renders can still be fed empty or wrong data. Before judging a UI/feature, confirm it is actually *receiving the data it should* — trace the data source, not just the rendered output. Mock-backed tests that bypass the real fetch/decode/transport path are not verification; an integration test must exercise the production path with a real payload. (Cost of ignoring: an empty data binding was mistaken for layout/glass bugs across ~5 build cycles.)

2. **Route open-ended diagnosis to the cheap-but-capable OpenCode tiers, not Anthropic.** Reading code to find a root cause is grunt reasoning the `standard`/`heavy` tiers (deepseek/glm) do well and cheaply. Doing it on the Anthropic subscription burns the biggest cost lever for no quality gain. Dispatch a **read-only** "investigate X → report root cause + minimal fix, change nothing" task; reserve Anthropic for genuine peak-judgment and gate decisions.

3. **Keep spec-writing and code review on the Tech Lead tier (Sonnet/cheap) — never silently on Opus.** The PM/orchestrator must not absorb the Tech Lead role and run every build-prompt and diff-review itself at peak cost. Delegate spec + review to the Tech Lead; the orchestrator decides and gates, it does not personally author and review on Opus. For diff review this is structural: the first pass runs as a read-only Reviewer dispatch (`dispatch.sh --read-only` + `prompts/reviewer.md`, standard tier) or a Sonnet subagent; the orchestrator only adjudicates the verdict (see `VERIFY.md` § Diff review).

4. **Instrument the decision, then measure — before forming a root cause.** When a defect's cause is not directly observable, the first dispatch is an **instrumentation** task that logs *the branch taken and the values it was taken on* — not the outcome, and not a fix. Then run it for real and read the log. Corollaries: (a) a handoff or spec must carry **the measurement to take, not the hypothesis to check** — a named lead anchors the next session's attention and is not cheaper than measuring; (b) **absence of a log string is not evidence until the string is confirmed to exist in the source**; (c) when a test fails, dump the actual runtime state (accessibility tree, device log, payload) before editing either the test or the production code. This is lesson 1 generalised from data-binding to root-cause diagnosis. (Cost of ignoring: one defect consumed **four sessions and three device rounds**, each closed with a confident wrong root cause, and was settled in one morning by a single decision-level log line. The same pattern then repeated twice in one day — three named candidate causes plus a flagged lead, all four wrong.)

5. **A well-diagnosed bug still pays for the placement check.** Where lesson 4's measurement already exists, the spec cites the artifact and skips re-deriving the root cause — but the pre-build critique's placement/blast-radius pass is never collapsed, and diff size is never a process input. The gate is the *type* of evidence (observed runtime artifact vs. reasoning from source), never the author's confidence in it. Full rule: `.claude/rules/dispatch.md` § Pre-Build Critique `[0h]`. (Cost of ignoring, in the other direction: a one-line fix on a shared audio path where both critic rounds found real defects — the second caught that the guard as specified would land in the shared writer and break a working Apple Music code path.)

6. **Tight visual/layout tuning does not belong in the dispatch loop.** Build + test + screenshot per nudge is far too slow for "move it up 40pt." Do trivial visual nudges directly, or hand the on-device visual pass to the human. Automated screenshots confirm an artifact's presence/absence; they are weak for landing a precise interaction frame (e.g. a mid-scroll state).

## Checks must be able to fail (issue #34)

Applies to framework changes. A check nobody has seen fail may not be able to: T078 passed
genuine mutation evidence measured on a tree that no longer existed (#35), and a selftest
extraction bounded on the wrong line once tested nothing while reporting green.

- **Mutation-test every new check before it ships** — gate, hook, lint rule, selftest
  assertion. Break the check (delete or invert its condition, drop the field it reads), run
  its test, see it go red, restore. Name the mutation and the red test in the commit message.
- **Every reference to a file or section must be checkable.** Write it as `<file>.md §
  <Heading>`, naming a heading that exists. `scripts/check-refs.py` (run by selftest) fails
  on a dangling one, so renaming a heading means fixing its references in the same change.

---

## Agent Contracts

### Designer
- **Receives:** SPEC.md (full), design brief in prompt
- **Returns:** Structured design spec (markdown) to be written to `prompts/design-spec.md`
- **Does not:** Write files, invoke tools, make implementation decisions

### Architect
- **Receives:** SPEC.md, `prompts/design-spec.md`, target project path, RULES.md (model selection section)
- **Returns:** Structured build spec (markdown) to be written to `prompts/build-spec.md`, plus a selected tier (`fast`/`standard`/`heavy`), a `security: true|false` flag and a `risk: true|false` flag in the result YAML, and a `Verify tier:` / `Risk:` / `Observation:` line per task section (judged against the project's review policy — `VERIFY.md` § Project policy, § Merge gate)
- **Does:** Classify task complexity against the tier definitions in `framework/MODELS.md` (the curated inventory — it does **not** query OpenRouter); set `security: true` on any Stage-2 task whose diff touches auth, credentials, keychain, entitlements, network trust, sandboxing, or input validation on external data (see `VERIFY.md` § Security-sensitive tasks)
- **Does not:** Execute OpenCode or pick raw model slugs — the PM resolves tier → model/fallback from MODELS.md

### Tech Lead
- **Receives:** Issue description, full build-spec, PLAN.md summary, target project path, optional error output
- **Does:** Reads actual source files in the target project to understand current state; fetches Apple/framework docs via Sosumi MCP if relevant; writes a precise task spec
- **Returns:** Task spec section (appended to build-spec.md) + structured YAML metadata (task title, branch, issue refs, depends_on, suggested tier, `verify_tier`, a `risk: true|false` flag set from the project's risk triggers — it alone decides pre-build critique — a `security: true|false` flag, and `observation: test|runtime|none` + `observation_cmd`: the one observation that fails on the base tree and passes on the branch, which the merge gate checks — `VERIFY.md` § Merge gate). Sets `security: true` when the diff touches auth, credentials, keychain, entitlements, network trust, sandboxing, or input validation on external data — this forces the review rung up (see `VERIFY.md` § Security-sensitive tasks). Bias toward `true` when unsure.
- **Does not:** Write code, execute commands in the target project, or make implementation decisions beyond speccing
- **Model:** Sonnet by default; PM may use Opus for complex architectural tasks

### Reviewer (first-pass diff review — cheap tier, read-only)
- **Invoked with:** `bash framework/dispatch.sh --read-only <standard-tier-model> <target-project-path> <rendered-reviewer-prompt>` (template: `framework/prompts/reviewer.md`), or as a Sonnet `Agent` subagent with the same rendered prompt
- **Receives:** task spec, `verify_tier`, diff range — run in the task worktree, so the verdict is pinned to the commit it read (`VERIFY.md` § Merge gate)
- **Returns:** VERDICT (APPROVE / APPROVE-WITH-FOLLOWUPS / REJECT) + findings + a machine-readable `REVIEWER_RESULT` block that `dispatch.sh --role reviewer` records in `logs/verdicts.jsonl`; the orchestrator adjudicates against the spec and decides merge / reject / re-dispatch. Non-approving reviews count against the task's reviewer fixup cap (`.claude/rules/dispatch.md` § Round caps)
- **Does not:** modify any file (enforced — dispatch exits 21 on a dirty tree), merge, or decide
- **Why:** the intent-review rung of the gate (`VERIFY.md`) must run on every diff; running it on Opus was the measured top cost sink, so Opus only adjudicates
- **Family diversity (hard rule):** the reviewer must be a different model family than the builder backend that produced the diff — claude-backend diffs never use the Sonnet subagent (route to opencode `standard`/deepseek); codex/opencode diffs may use either. See `VERIFY.md` § Diff review.
- **Security override:** for a `security: true` task the first pass runs on **Sonnet minimum** (not the cheap opencode tier) and adjudication is **mandatory Opus, never delegated, never Fable**. This is the deliberate exception to the cheap-first bias (see `VERIFY.md` § Security-sensitive tasks).

### OpenCode (via PM shell invocation)
- **Invoked with:** `bash framework/dispatch.sh --worktree <branch> <model> <target-project-path> <per-task-prompt-file> [fallback] [verify-cmd] [max-attempts] [tier]` — PM extracts a task-scoped prompt file via awk before calling dispatch (see `.claude/rules/dispatch.md`). `--worktree` isolates the builder in a per-task git worktree so the live checkout is never touched.
- **PM captures:** stdout/stderr, exit code
- **On success:** PM opens the PR, logs output summary; it merges only through `scripts/merge_gate.py` on the exact commit verified and reviewed (`.claude/rules/dispatch.md` § Merge gate)
- **On failure:** PM writes exit code + stderr to `error` field, evaluates retry
