# Codex orchestrator trial — pre-registration

Status: **implementation under verification; live trial not started**.
Registered 2026-09-08 for issue #33, before collecting trial outcomes.
Baseline rollback tag: `pre-issue-33` (95d9ea2). Choosing Claude next session remains the
operational rollback; do not revert the shared-state safety commands to switch providers.

## Question and decision rule

Can a Codex partner preserve orchestration quality while converting idle subscription
capacity into useful progress when Claude's rolling window is exhausted?

If orchestration quality is equal and quota asymmetry persists, choose Codex as the default.
A modest quality regression may still be worthwhile if the operator remains productive;
that is an explicit operator decision based on recorded corrections and outcomes, not an
inference from tokens. Reject the change of default if Codex becomes the binding quota,
extra critique/re-spec/verification cycles erase the gain, merge requirements are missed,
or conversational continuity is unacceptable to the operator. Never trade security gates
for quota headroom.

## Entry criteria

- Same framework revision and shared state formats for both providers.
- Automated state, routing, adapter and legacy regression suites pass.
- In a disposable PM project, demonstrate native hook rejection of an explicit PLAN edit,
  PostToolUse feedback for malformed PLAN, and Stop telemetry for each provider. Record
  native CLI versions, hook config hashes, trust state and transcripts/usage schema labels.
  Doctor fixture checks alone do not meet this criterion. No hook trust bypass in the pilot.
- Confirm the lease rejects a second writer and stale tokens. Practice Claude → Codex →
  Claude handoff with a surviving background build; no duplicate dispatch or inferred failure.
- Record actual subscription availability before each phase; token proxies are not quota meters.
- Select the named project and phase pairs with the operator before touching a deployment.

## Phase selection

Use at least three matched pairs of complete phases: bounded maintenance, a feature with
R1 review, and a phase requiring recovery or handoff. Match scope, project maturity, verify
tier and expected task count before assigning providers. Alternate the first provider across
pairs to reduce ordering effects. Do not compare unrelated greenfield and maintenance work.

Separate two questions in the report:
1. Quality comparison with the same role-routing profile and model versions where capacity permits.
2. Operational fallback comparison with Codex's OpenCode-only profile when Claude is exhausted.

Do not attribute profile/provider/builder changes solely to the orchestrator model. Record
those confounds explicitly. No required paid workload is launched by the installer or tests.

## Measurements per phase

Store a phase record under the selected PM project's `logs/trial-33/` and append a summary
into TASK_LOG. Include phase ID, paired phase ID, task IDs, start/end timestamps, orchestrator
provider/model/version, routing profile, role models/families, framework commit, and observed
quota state. Retain raw logs outside the main conversation.

Record:
- Completed tasks and wall-clock productive minutes before throttling, including wait time.
- Partner turns per completed task; fresh, cached and reasoning tokens per provider, with
  normalization/schema labels and telemetry gaps.
- Observed subscription throttling, its time, and which lane was binding.
- Incorrect state transitions, duplicate dispatches, operator corrections and their causes.
- Critic/re-spec rounds, verifier retries, builder outcome and review/adjudication outcome.
- Merge-gate omissions (including human/device checks), caught and uncaught.
- Operator confidence and conversational friction, scored 1–5 with short examples immediately
  after each phase; do not reconstruct these impressions after seeing token totals.

A missing telemetry stream invalidates token comparisons for that interval; retain the phase's
quality evidence and explicitly mark accounting incomplete. Never fill missing usage with zero.

## Stop and recovery conditions

Pause the trial on any lost state, duplicate live builder, stale-owner mutation, bypassed hard
gate or security review gap. Preserve artifacts and repair the invariant before resuming.
A provider throttle is an observed outcome; use the other provider next session through normal
lease handoff/recovery. Never treat it as a task failure or retry an exhausted lane indefinitely.

## Results ledger

| Pair / phase | Provider / profile | Quality and corrections | Quota / progress | Decision |
|---|---|---|---|---|
| No phases run | — | Not measured | Not measured | Pending |

Implementation test results belong in the implementation plan/HANDOFF, not this outcomes table.
After the paired phases, publish a report with absolute counts and denominators, matched-pair
comparisons, missing-data intervals and confounds. The operator decides the default using the
rule above. Issue #33's empirical acceptance remains open until those results exist.
