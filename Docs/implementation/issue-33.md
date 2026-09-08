# Additive Codex orchestration implementation plan

Approved design: user approved the eight-point critique on 2026-09-07.
Goal: either provider can orchestrate one PM directory without migrating project state.
Architecture: shared Markdown contract, small Python standard-library state commands,
provider adapters, additive installation, existing dispatch wrapper. No new workflow engine.
Baseline: annotated remote tag pre-issue-33 at 95d9ea2; selftest PASS.

## Constraints
- Never modify deployed projects in this work.
- Preserve Claude operation, gate semantics, Markdown formats, and normal project routing.
- A lease is cooperative enforcement, not a filesystem security boundary.
- Unsupported/untrusted hooks must not be described as verified enforcement.
- No fabricated trial outcomes; implementation readiness and empirical acceptance are separate.

## 1. State safety
Files: scripts/pm_state.py, scripts/orchestrator-state.py, scripts/plan-update.py,
tests/test_pm_state.py.
- [x] Write failing tests for competing acquire, stale ownership token, stale PLAN hash,
  rejected structural lint, vocabulary acceptance, recovery after replace before log append,
  idempotent operation replay, live dispatches blocking duplicate task reservations.
- [x] Implement lock-protected lease acquire/check/renew/release/handoff/takeover with
  provider, session, owner process identity, random token. Never auto-steal on age alone.
- [x] Implement PLAN update under lock: expected SHA256, candidate lint (0/3 only), durable
  intent, atomic PLAN replace, idempotent TASK_LOG event; reconcile intent on next command.
- [x] Add guarded durable file write/append for HANDOFF, SPEC, prompts, TASK_LOG.
- [x] Report runs/process/worktree evidence for takeover; no automatic task failure.
- [x] Verify with python3 -m unittest discover -s tests -p 'test_pm_state.py'.

## 2. Comparable telemetry
Files: scripts/log-partner-burn.py, scripts/partner_telemetry.py,
scripts/backfill-partner-burn.py (only compatibility if needed), tests/test_partner_telemetry.py.
- [x] Write failing fixtures/tests for Claude compatibility and Codex cumulative token_count
  usage, duplicate Stop, provider/session collisions, subagent exclusion and attribution,
  restart after append without checkpoint, missing/unknown usage diagnostics.
- [x] Normalize Codex input to fresh input plus separately reported cache reads; output
  includes reasoning, record explicit quota_proxy_tokens to prevent double accounting.
- [x] Make shared journal authoritative for deduplication under lock; retain legacy state
  compatibility. No zero usage records, unknown schemas log visible errors.
- [x] Verify new tests and old selftest telemetry assertions.

## 3. Additive harness integration
Files: scripts/install-orchestrators.py, scripts/orchestrator-hook.py,
scripts/orchestrator-doctor.py, tests/test_orchestrator_integration.py, setup.sh.
- [x] Test repeat install preserves custom settings and PROJECT, invalid config writes
  nothing, hooks block corrupt PLAN for Codex command-shaped apply_patch and Claude paths.
- [x] Merge managed entries into .claude/settings.json and .codex/hooks.json; append managed
  instruction blocks to project CLAUDE/AGENTS. Never change trust or user-global settings.
- [x] Hook PreToolUse checks active owner; PostToolUse checks PLAN, Stop logs telemetry;
  hook evidence distinguishes actual invocation from manual fixture checks.
- [x] Doctor checks config and exercises scripts in disposable directories, reports actual
  runtime evidence separately; no bypass of hook trust.
- [x] Verify integration tests. Installed local Codex version: 0.153.4. Official hooks docs:
  https://learn.chatgpt.com/docs/hooks (retrieved 2026-09-07); command input is tool_input.command,
  hooks.json lives beside config layers, synchronous exit 2 blocks feedback, trust required,
  transcript format is explicitly unstable.

## 4. Routing and operating contract
Files: dispatch.sh, scripts/orchestrator-routing.py, tests/test_orchestrator_routing.py,
ORCHESTRATOR.md, AGENTS.md, CLAUDE.md, .claude/rules/{state,lifecycle,dispatch}.md,
README.md, Docs/codex-orchestrator-trial.md.
- [x] Test fallback denies subscription lanes and same-family reviewer/critic choices,
  including fallback models. Normal profile retains project preferences.
- [x] Guard managed dispatches with lease and duplicate task reservations; add exit 31
  for state/policy blocked (never builder failure). Capture ownership in run records.
- [x] Route planning roles through dispatch initially; read-only planning writes reports
  outside target repo and Tech Lead spec artifact is registered through state command.
- [x] Shared guide carries gates, state operations, recovery, routing and harness mechanics.
- [x] Pre-register matched-phase trial, outcomes, stop conditions and decision rule; results pending.
- [x] Run full selftest, focused concurrency/crash tests, shell syntax, diff review.

## Progress and decisions
- 2026-09-07: baseline PASS; feature branch feat/33-codex-orchestrator.
- Work in current checkout on feature branch; original release preserved by remote tag.
- Plan execution authorized by user approval; routine implementation decisions proceed without further gates.
- 2026-09-08: full `bash scripts/selftest.sh` PASS, including 79 additive regression tests.
- Live Claude/Codex hook evidence and representative matched project phases remain pending by design.
