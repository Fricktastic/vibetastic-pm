# HANDOFF — vibetastic-pm (framework repo)

**Last session:** 2026-08-18 · **Branch:** `main` @ `0ef41bd`, **pushed** (origin in sync).
Working tree clean apart from this file.

**Stage:** framework maintenance. Items 1 and 2 of a 3-item state-integrity program are done,
merged and field-proven. **Item 3 is deliberately blocked on fresh gamedaytastic data** — see
"Next action" below. Do not start it early; the reason is substantive, not scheduling.

---

## Next action

**Wait for gamedaytastic to complete 2–3 real tasks under the current framework, then write
item 3.** Nothing else here is pending.

When that data exists, item 3 is: **normalize the critic/review event schema** — a canonical
event shape written into `.claude/rules/state.md` and `VERIFY.md`, plus a linter for new
TASK_LOG entries.

**Why it waits.** Measured on gamedaytastic: 22 `critic_returned` events across **17 distinct
key sets**, split `task_id`/`task_ids` and `log`/`logs`, plus ad-hoc names like
`critic_r3_returned`. But `critic_r3_returned` is an artefact of a *pre-round-limit* framework —
the `[0e]` two-round cap (already shipped, unexercised until 2026-08-19) makes a round 3
impossible. Writing the schema against that corpus would enshrine a shape the current rules can
no longer produce. TASK_LOG is append-only by rule, so this is forward-only: no migration is
possible, which makes getting the schema right the first time the whole job.

**What to collect from those runs:** the actual key sets emitted by `critic_returned`,
`reviewer_returned`, `review_adjudicated`, and `critic_escalated` under current rules. Two
fresh critic runs already exist from 2026-08-19 (T073 rounds 1 and 2) — start there.

---

## Done this session — the state-integrity program

Both items came from two adversarial design critiques (codex `gpt-5.6-sol@medium`, read-only)
run against live corpora. Full outputs are gone with the session; conclusions are below.

**Item 1 — `plan-lint.sh` hardening** (`d4607d4`)
Now rejects two defect classes as STRUCTURAL that previously passed while breaking every
YAML-based consumer:
- `depends_on: [[T055]]` — Obsidian wikilink syntax; YAML reads it as a list of lists. The old
  regex stripped brackets and extracted the id happily. **28 of 76 gamedaytastic tasks** had a
  silently wrong dependency graph.
- Illegal YAML escapes in double-quoted scalars — Swift's `\.dismiss` in a `notes:` field made
  hometastic's entire 1,549-line PLAN.md unparseable while plan-lint reported `OK (89 tasks)`.
Stays regex-based; **no PyYAML dependency** (deliberate — stock macOS python3 may lack it).

**Item 2 — run-lifecycle telemetry** (`740b63e`)
`dispatch.sh` now emits `run_start` / `run_finish` to `logs/runs.jsonl`: `run_id` (threaded into
the log filename), pid, role, backend, tier, branch, worktree, `base_sha`/`head_sha`, commit
count, exit, duration. Start record is written **before** argument validation and finish comes
from an **EXIT trap**, so early exits (2 = no verifier, 30 = backend unavailable) and signals
are covered — previously those left no telemetry at all. `cost.jsonl` schema and write site are
untouched.

**Supporting — `scripts/selftest.sh`** (`fffbab7`, `a2de4ad`)
The framework repo's **first verify command**, and `PROJECT.md` to declare it. Checks shell/python
syntax, asserts plan-lint's exit code against 6 fixtures in `tests/fixtures/`, and asserts a
matched run_start/run_finish pair on an early exit. Two fixtures encode the real defects above.

**Field-proven, not just tested:** gamedaytastic's `logs/runs.jsonl` has 4 rows from two real
critic dispatches, with `role: read-only` correctly inferred.

---

## Facts established — do not re-derive

- **Corpus selection.** `gamedaytastic-pm` is the reference corpus: live, exercises the current
  framework (`backend` on 217/245 cost rows, 22 critic events, `verify_tier` on 96% of tasks).
  `hometastic-pm` is the **drift** test case — stale since 2026-07-17, predates `backend`
  entirely, and its PLAN.md is the illegal-escape specimen. A parser must survive both.
- **`tier` is genuinely thin** (51/245 cost rows) and partly *correctly* null: read-only
  reviewer/critic dispatches emit no tier. Don't "fix" that by backfilling.
- **Per-run tier cannot be reconstructed from PLAN.md** — retries and escalations run at a
  different tier than the task's final value.
- **Single-writer is policy, not concurrency control.** Two orchestrator sessions can both read,
  both write, both pass plan-lint, and one silently wins. Read-before-write is not CAS. This is
  unfixed and is the largest known structural gap.
- **Only the frontmatter is YAML**, not the whole PLAN.md file. hometastic's frontmatter is
  never closed with `---`, so plan-lint degrades to linting the whole file.
- **A browser cannot wake a conversational Claude session.** Any "inbox file the orchestrator
  drains" design is inert — the decision sits unread while the UI claims it was submitted.

## Known issues, unfixed

- **`git push` from a session that has run `eval "$(~/.ssh/gh-agent-token.sh)"` gets 403** —
  the agent bot lacks write access to `Timeteo/vibetastic-pm`. Push over SSH instead:
  `git push git@github.com:Timeteo/vibetastic-pm.git main`. This bit once and cost a round trip.
- `cost-report.sh` has **no machine-readable mode**. Any consumer must parse human stdout.
  Add `--json` before anything depends on it.
- `logs/` has **no rotation**. gamedaytastic is at ~1,065 files / 102 MB.
- Nothing consumes `runs.jsonl` yet. `cost-report.sh` was not extended to read it.

## Deferred by decision — the UI layer

A UI was investigated and **deliberately deferred behind these framework fixes**. Conclusion:
build a read-mostly renderer over the file contract, never a second writer. Order when it
resumes: `pm-status --json` adapter → OS notifications → dashboard → gates **display-only** →
dependency/branch/PR DAG last. Reject: the inbox pattern (see above), a canvas of terminals
(builders are non-interactive), and localhost-without-auth once any action exists.

## Scope note

This session **overstepped into gamedaytastic-pm** — it pulled the subtree (in scope), then
also wrote its PLAN.md, wrote T073's spec, dispatched two critic rounds, and appended to its
TASK_LOG (out of scope; that project's orchestrator owns those). Its `TASK_LOG.md`,
`prompts/task-T073.md` and `prompts/critic-T073.md` were left **uncommitted on purpose** for
that session to review. T073 sits at an unresolved `critic_escalated` (2-round limit, 3 open
BLOCKING findings). **Do not resume T073 from here.**

## Prior context still live

`~/Developer/vibe-rework/` — read `NEXT.md`, then the phase specs. Phase 0 is merged.
Open issues: #9 (critic convergence), #12 (parsing ≠ type-checking), #13 (external iOS sim
verification), #14 (route review to claude — deliberately not done). #10 and #11 are fixed.
