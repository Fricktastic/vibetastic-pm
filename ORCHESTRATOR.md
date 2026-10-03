# Shared orchestrator contract

This guide applies to **installed `<project>-pm/` directories**, where this repository is
available as `framework/`. The framework source repository itself uses normal git development;
its root PLAN, SPEC and TASK_LOG are shipped templates, not live orchestration state.

Claude and Codex are interchangeable partner sessions against the same project. They share
SPEC.md, PLAN.md, TASK_LOG.md, HANDOFF.md, prompts/, logs/, and the existing gate definitions.
Choose the provider per session; never fork project state or overwrite normal backend preferences.

Read this guide, RULES.md, VERIFY.md and MODELS.md before orchestrating. Read the detailed
mechanics in `.claude/rules/{lifecycle,state,dispatch,economy,pm-scope}.md` under framework/;
that directory name is historical. The shared rules apply to both providers.
Harness-specific tool names are translated as described below.

## Start and finish a session

Install once, or rerun the additive installer after a framework update:

```sh
python3 framework/scripts/install-orchestrators.py --pm-dir . --framework-dir framework
python3 framework/scripts/orchestrator-doctor.py --pm-dir . --framework-dir framework
```

The installer preserves existing project preferences, instruction text and custom hooks.
It does not grant hook trust or modify global configuration. In Codex inspect `/hooks` and
review/trust the project hooks. Run the doctor again after a session; fixture checks are
separate from evidence that the native harness actually invoked the hooks.

Launch from the PM directory:

```sh
python3 framework/orchestrate.py claude
python3 framework/orchestrate.py codex
python3 framework/orchestrate.py --profile normal codex
```

The wrapper holds a lease for its lifetime, renews it while the CLI runs, and releases it
when the CLI exits. Codex defaults to the `codex-fallback` profile; Claude defaults to `normal`.
Native CLI arguments follow the provider, for example `codex -- resume --last`.
`PM_DIR`, `PM_ORCHESTRATOR_PROVIDER`, `PM_ORCHESTRATOR_SESSION` and
`PM_ORCHESTRATOR_TOKEN` are session environment variables, not PROJECT.md preferences.
Never pass the token to a builder or copy it into prompts or logs.

A second writer is refused. Read-only inspection outside the session is allowed, but never
mutate durable state without ownership. A desktop/native session launched independently
must explicitly acquire a lease using its live process ID and export the returned token
into its tool environment; the CLI wrapper is the supported, tested pilot entry point.

Before exiting, update HANDOFF.md with stage, in-flight run IDs/worktrees, latest results,
open gates, next action, and unresolved recovery evidence. Time-bound claims go only in its
`## Volatile — re-verify before use` section, one per line with an as-of date and a check
command. Releasing ownership does not
cancel builders or make their tasks ready to retry.

## Durable state mutations

Read the current file before each mutation. PLAN changes use a candidate file outside
PLAN.md and an expected SHA256 from the version read. Prepare an event file in the existing
TASK_LOG format (`### <ISO8601> · <event_type>` followed by a YAML block).

```sh
python3 framework/scripts/plan-update.py --pm-dir . \
  --expected-hash <sha256-of-current-PLAN> --candidate /tmp/plan-candidate.md \
  --event-file /tmp/plan-event.md --operation-id <unique-transition-id>
```

The command takes the lease token from the environment. It locks, checks ownership and the
expected hash, lints the candidate, durably records the intent, atomically replaces PLAN,
and appends the event exactly once. Lint exits 0 and 3 are accepted (3 means vocabulary
drift); structural corruption, unreadability and unexpected linter failures are rejected.
When PLAN.md does not exist yet, pass `--expected-hash missing`.

A crash between PLAN replacement and event append leaves a recoverable intent. The next
state command rolls it forward; conflicting external edits block recovery. Retry with the
same operation ID and identical inputs to obtain the prior result. Reusing an ID with
different content is rejected. Do not delete the journal to bypass a conflict.

For other durable files use the guarded commands:

```sh
python3 framework/scripts/orchestrator-state.py --pm-dir . write \
  --path HANDOFF.md --source /tmp/handoff.md --operation-id <unique-id>
python3 framework/scripts/orchestrator-state.py --pm-dir . append \
  --path TASK_LOG.md --source /tmp/event.md --operation-id <unique-id>
```

`write` supports HANDOFF.md, SPEC.md and files under prompts/. TASK_LOG is append-only.
PLAN must use plan-update. Candidate/staging artifacts can live under logs/ or /tmp.
Project metadata under `.orchestrator/` is local operational state; preserve it across
sessions and exclude it from commits. It never replaces the shared Markdown contract.

Hooks reject direct Write/Edit/apply_patch edits to PLAN, block bulk task/critic-spec reads
from either provider's orchestrator context, and lint PLAN after supported tools. These are
cooperative safeguards, not filesystem security: arbitrary shell commands,
disabled/untrusted hooks and specialized tools can bypass them. Do not claim that a
PostToolUse hook undoes a write or that lint proves a transition satisfies every merge gate.

## Recovery and handoff

Read HANDOFF first for orientation, then SPEC, PLAN and recent TASK_LOG. Durable evidence
wins over the handoff. Never turn `in_progress` into `failed` just because a session ended.

HANDOFF's `## Volatile — re-verify before use` section holds claims, not facts (format in
RULES.md § Session Handoff; rule in `.claude/rules/state.md`). Both providers' SessionStart
hook prints it with a banner. Run a line's check before acting on it, re-verify before
carrying it into a new handoff, and never choose a backend from a handoff: dispatch and
branch on exit 30.

```sh
python3 framework/scripts/orchestrator-state.py --pm-dir . active
```

Inspect ownership, run records, process identity and worktrees. Live builders remain live;
completed runs need their actual outputs and verifier/review results checked; uncertain
runs require reconciliation. No missing finish record, timeout or stale heartbeat alone
proves that a build failed. Check the code repository and shipped commits before retrying.
A task TASK_LOG shows shipped but PLAN still holds `in_progress` is recorded `done` with a
`state_correction` event carrying `evidence:` (`.claude/rules/state.md` § Recovery Protocol),
never a failure.

For a dead owner, `takeover` requires the current evidence hash and a written reason.
For a live owner, use `handoff` or release from that owner. New ownership invalidates the
old token. Unresolved dispatch reservations survive ownership changes and prevent duplicate
builds. Only the recorded dispatch process can finish its reservation; a dead reservation
requires `reconcile` with a fresh evidence hash and reason. Reconciliation releases the
reservation, **not** the verify/merge gate, and does not change failure counters.
Use `orchestrator-state.py <command> --help` for the exact takeover/handoff arguments.

## Dispatch and role routing

The partner owns intent, decisions and lean summaries. Builders own target-code edits,
except a change that meets all five conditions of `.claude/rules/pm-scope.md` § Inline
authoring gate: the partner may author that itself in a worktree, declared as
`inline_authored`, and it still merges only through `scripts/merge_gate.py` with a
family-diverse review of the partner's commit.
Dispatch build work with `--worktree`; keep long runs under the native tool's session/job
handle. Claude uses Bash background jobs; Codex uses exec session IDs and polls their results.
Never launch detached `nohup`/`disown` processes. Record run IDs and worktree ownership.

The Codex pilot routes Designer, Architect and Tech Lead through dispatch.sh as well as
builders. Use `scripts/dispatch-role.py` for planning: it stages artifacts outside the code
repo, enforces read-only target inspection, then promotes the result through the state
command. Tech Lead returns only metadata; its spec body stays on disk. Claude can keep its
native role agents, but their output must also be staged then registered by the lease owner.
Native Codex subagents are not part of this pilot.

Resolve effective backend order with `scripts/orchestrator-routing.py order --profile ...
--project PROJECT.md --role ...`. `normal` uses the project's existing order. `codex-fallback`
uses only OpenCode for builders, planning roles, investigation and ordinary review/critique.
Do not retry exhausted Claude or consume the Codex pool being reserved for the orchestrator.
`dispatch.sh` checks the profile stored in the lease, regardless of caller environment.

For reviews/critics pass `--role reviewer|critic --author-model <actual-author-model>`.
The author is the builder for a diff review and the spec author for a critique. Both primary
and fallback models must be family-diverse; OpenRouter is a transport, not a model family.
Unknown family or no legal configured choice blocks instead of silently weakening review.
Record the actual author/fallback model in task metadata and the event log.

Security requirements in VERIFY.md remain binding: capable, diverse critique; Sonnet-or-higher
first-pass review; mandatory Opus adjudication. A Codex partner requests an explicit exceptional
Claude review/adjudication using `--exceptional-adjudication`; if unavailable, the task remains
blocked pending that judgment. The Codex partner cannot impersonate Opus or treat an ordinary
OpenCode review as satisfying the security floor. Never route Anthropic through OpenRouter.

Dispatch exits: 0 success; 2 invalid invocation; 20 verification exhausted (escalate);
21 read-only violation (inspect); 22 verify passed but the builder reported `ACCEPTANCE:
UNMET` (not green, not a failure — route it per `.claude/rules/dispatch.md`, issue #58);
30 backend unavailable (next legal backend);
31 ownership/routing/reconciliation blocked, or a review gate refused the run — critique
not yet adjudicated, or a critique / reviewer-fixup round cap reached (**do not** increment
failure_count; resolve or escalate per `.claude/rules/dispatch.md` § Round caps).
Only true failures after the configured ladder is exhausted trigger the failure rules.

## Gates that remain unchanged

- Gate 1: a draft SPEC needs the user's explicit approval before planning/building.
- Gate 2: failure_count reaches 2; wait for the user's retry/skip/abort decision.
- Stage transitions auto-advance with a summary; never invent a new approval gate. Each
  transition also runs the lesson-consolidation pass (RULES.md § Lesson consolidation) without
  holding up the next stage's dispatches.
- `risk: true` or `security: true` work requires family-diverse pre-build critique; resolve
  BLOCKING findings. Legacy tasks without a `risk:` field keep the R1/R2 rule. dispatch.sh
  refuses the build until `scripts/review_gate.py adjudicate` records `proceed` or a logged
  operator `override` (issues #18, #50), and again if the spec changes after it; a
  `security: true` `proceed` must be recorded with an Opus-class `--model` (issue #57).
- Critique and reviewer-fixup rounds are capped per task (project policy, default 2 / 3);
  dispatch.sh refuses the next round and the operator chooses redesign / override / abort.
- What the verify tiers mean, which risk triggers force critique, and the round caps are
  **project policy** in PROJECT.md (VERIFY.md § Project policy); the framework enforces it.
- Merge only after VERIFY.md's tier ladder and first-pass review/adjudication pass, and only
  through `scripts/merge_gate.py` (issue #35): the verification, the approving review and the
  task's fail-on-base / runtime observation must all be pinned to the exact commit merged, and
  its net diff must carry a production change unless the task is `observation: none`. A
  refusal is exit 31; the operator's waiver is a logged `merge_gate.py override`. A
  `security: true` task also needs an Opus-class `merge_gate.py adjudicate` at that commit,
  and the gate fetches `origin/<base>` itself and refuses a stale local base (issue #58).
- A task with an `observation:` field closes (`done`) only after its merge gate passes;
  `plan-update.py` refuses otherwise. A Gate-2 skip or a state correction closes it through
  the operator's recorded `merge_gate.py exempt-close` (issue #58).
- R2 inspection and human/device-only verification remain the partner/operator's responsibility.
  A builder's claim is not test evidence. Preserve the device-only exceptions explicitly.
- Never push framework updates into deployed projects without the user's instruction.

## Telemetry and trial

Both providers append `role: partner`, provider-distinguishable usage into logs/cost.jsonl.
Codex transcript adapters are version-sensitive; absent/unsupported usage must produce a
visible telemetry-errors.log entry, never invented zeros. Child usage is separate from the
partner, and dispatched work must not be counted again as a partner session.
Use quota_proxy_tokens for fresh input plus output; reasoning is a subset of output for the
Codex adapter. Cached usage is reported separately. These are quota proxies, not entitlement
meters. Record observed throttling independently.

Follow `Docs/codex-orchestrator-trial.md` before collecting matched phases. Passing fixtures
establishes implementation behavior only. Do not claim a successful trial, change the default,
or close empirical acceptance until representative sessions have been measured.
