# HANDOFF — vibetastic-pm (framework repo)

**Last session:** 2026-08-19 · **Branch:** `main` @ `c070e33`, **pushed** (origin in sync).
Working tree clean apart from this file.

**Stage:** framework maintenance. The 3-item state-integrity program: items 1 and 2 are done,
merged and field-proven. **Item 3 is now UNBLOCKED** — the data it was waiting on exists as of
2026-08-19. Two items outside that program also shipped this session: T074 (builder-turn log
capture) and the rule-mechanization pair ([0h] + the plan-lint hook).

---

## Next action

**Write item 3: normalize the critic/review event schema.** A canonical event shape recorded in
`.claude/rules/state.md` and `VERIFY.md`, plus a linter for new TASK_LOG entries.

**Why it was blocked, and why it no longer is.** Measured on gamedaytastic: 22 `critic_returned`
events across **17 distinct key sets**, split `task_id`/`task_ids` and `log`/`logs`, plus ad-hoc
names like `critic_r3_returned`. But `critic_r3_returned` is an artefact of a *pre-round-limit*
framework — the `[0e]` two-round cap makes a round 3 impossible now. Writing the schema against
that corpus would have enshrined a shape the current rules can no longer produce. It waited for
events emitted under current rules; those now exist.

**The corpus to build the schema from** (all under current rules):
- gamedaytastic T073 — 2 critic rounds, ending in `critic_escalated` (2-round limit).
  **Read its `TASK_LOG.md` entries only. Do not touch the task, its PLAN.md, or its prompts** —
  T073 is that project's orchestrator's to resume, and this repo already overstepped there once
  (see "Scope note" below). You need the event *shapes*, not the task.
- vibetastic T074 (this session) — 2 critic rounds (REWORK → PROCEED) and 1 reviewer pass
  (APPROVE), i.e. the first clean convergence-inside-the-cap on record, plus the adjudication.

TASK_LOG is append-only by rule, so this is forward-only: no migration is possible, which makes
getting the schema right the first time the whole job.

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

**Item 4 (unplanned) — `dispatch.sh` builder-turn capture** (`5971f48`, merged `c6ae7f8`)
Triggered by a gamedaytastic report that a critic audit trail was lost. It was not lost, but the
framework said it was: `$LOG_FILE` only ever received the builder's **stderr**, so on the quiet
backends (codex, claude) it was near-empty — a 39-byte log reading `Reading additional input
from stdin...` while the run's full REWORK verdict sat unreferenced in the `.lastout` sibling.
Worse, the **verify-resume turns were captured nowhere at all**; only the fresh run's stdout hit
`$STALL_OUT`. `finish()` then printed that empty file as "full log" and tailed it on failure, so
the "failures are never silent" contract was hollow on two of three backends.
- `capture_builder_turn()` records every fresh **and** resume turn under a numbered header.
  Redirect-then-`cat`, **never a pipe** — a pipeline would strand `CODEX_THREAD_ID` /
  `CLAUDE_SESSION_ID` in a subshell and break the verify-resume loop.
- `claude_postrun` no longer appends raw result JSON to the human log (it duplicated
  `$CLAUDE_RESULTS`); `finish()` now names the JSONL siblings.
- `selftest.sh` gained a **hermetic fake-backend harness** — fake `codex`/`claude`/`opencode` on
  PATH — covering multi-turn capture, multi-message ordering, session-id propagation, JSONL
  non-duplication, read-only capture, and the `[0f/B3]` empty-output stall retry. This was a
  critic finding: the old selftest could not have detected any of these regressions.

**Item 5 (unplanned) — rule mechanization: `[0h]` + the plan-lint hook** (`c070e33`)
Prompted by a challenge from the gamedaytastic session ("excessive process for a one-line
change") and by the general question of whether prose rules drift.

- **`[0h]` in `.claude/rules/dispatch.md`** (pointer as `RULES.md` lesson 5): a well-diagnosed
  bug may skip re-deriving its root cause, but the gate is the **type of evidence** — an
  observed runtime artifact (device log, instrumentation dispatch, a test pinning the branch
  *and* the values) — never reasoning from source, and never the author's confidence, which is
  self-certifying. The critique's placement/blast-radius pass never collapses; **diff size is
  never a process input**.
- **`scripts/plan-lint-hook.py`** — `PostToolUse` on `Write|Edit`, gating files named `PLAN.md`.
  Mirrors plan-lint's `[0b]` taxonomy: exit 1 STRUCTURAL blocks (hook exit 2, linter output fed
  back to the model), exit 3 vocabulary drift does not, malformed input never blocks. Six
  selftest cases pin the mapping.

**The organizing idea, worth keeping:** a rule that lives only in prose drifts, so move the
mechanizable ones into the two places that cannot — `dispatch.sh` argument validation, and
hooks over tool calls. The precedent is already in the repo: the verify-command rule was prose
while **86% of dispatches ignored it**, became `exit 2`, and stopped being ignorable. `tier` is
the control group — still only a warning, still missing on **79%** of dispatches (issue #19).
What hooks *cannot* reach is every judgment rule (delegate-don't-do, adjudicate the verdict,
Gate 1/2 no-self-approval); those are the ones that actually decay, and mechanizing three rules
must not make the other twelve feel covered.

**Two bugs found this session, same class:** the T074 log gap and a bug in this hook (it walked
up from the script to guess the pm dir, overshot in this repo, and silently linted nothing while
appearing installed). Both were **silent swallows** — a failure path that returned success. The
hook one surfaced only because a fixture that should have blocked returned 0. Worth suspecting
this class first.

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

## Proposed enhancements — filed as issues 2026-08-19

Captured as GitHub issues on `Fricktastic/vibetastic-pm` (this repo's convention), not as prose
here, so they survive HANDOFF rewrites. Read the issue bodies — each carries its evidence.

| # | Proposal | Note |
|---|---|---|
| **#15** | `dispatch.sh` refuses a build dispatch without `--worktree` | The other half of the mechanization split; **highest value of the six** — it protects the live checkout, and it propagates via the subtree unlike a hook |
| **#16** | `setup.sh` runs once, so framework hooks never reach onboarded projects | Structural: the whole hook layer is un-upgradable in the field. Blocks leaning on hooks for anything important |
| **#17** | `LOG_DIR` follows the prompt file, scattering run logs | Found during T074, deliberately out of its scope |
| **#18** | Mechanize the critic gate after item 3 normalizes the event schema | A second reason to do item 3; note it inherits #16's propagation problem |
| **#19** | Promote the missing-`tier` warning to a refusal | Telemetry quality only; lower priority than #15 |
| **#20** | Startup step installs a token that can't push (403) | Maintainer decision — grant the bot write access, or drop the eval from `lifecycle.md` |

Suggested order: **#15, then #16** (it gates how much weight hooks can carry), then the rest.

## Known issues, unfixed

- ~~**`git push` 403 under the agent bot**~~ — **FIXED 2026-08-19.** The repo was transferred
  `Timeteo/vibetastic-pm` -> `Fricktastic/vibetastic-pm` and added to app installation
  `135198764`. `fricktastic-agent[bot]` now pushes here; verified with a real push, not a
  dry-run. Issue #20 closed. Caveat worth remembering: `eval "$(~/.ssh/gh-agent-token.sh)"`
  exports only into the shell invocation that runs it — a later Bash call is a fresh shell and
  silently falls back to the operator's own credentials, which makes a token test look like it
  passed when it never ran. Put the eval and the push in the *same* invocation. Also: a failing
  `git push` returns shell exit 0 through a pipe.
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

## Process note — the critic earned its keep on T074

Worth remembering when tempted to skip the pre-build critique on a "small" change. The Partner
authored T074's spec directly (delegation would have re-derived analysis already in context) and
routed it to a **family-diverse** codex critic. Round 1 returned REWORK with three findings, all
verified correct against the source:
1. The spec said "final `agent_message`", but `codex_postrun` prints **every** message in a turn
   — the builder would have silently dropped intermediate output.
2. `scripts/selftest.sh` — the verify command — was too weak to detect a broken capture, a lost
   session id, or a stall-guard regression. This forced the fake-backend harness into scope.
3. `claude_postrun` was *already* dumping raw JSON into the human log: the exact duplication the
   task existed to remove, unnoticed until the critic read for it.
Round 2: PROCEED, no findings. Reviewer (deepseek, read-only): APPROVE. Two rounds, converged
inside the `[0e]` cap.

## Known behaviour worth a future task

`LOG_DIR` is derived from the **prompt file's** directory, not the PM directory. A prompt parked
in `/tmp` writes its run logs to `/tmp/logs/`; specs kept in `logs/specs/` produce `logs/logs/`.
Harmless but it scatters the audit trail wherever a prompt happens to live. Not fixed — out of
T074's scope, deliberately.

## Prior context still live

`~/Developer/vibe-rework/` — read `NEXT.md`, then the phase specs. Phase 0 is merged.
Open issues: #9 (critic convergence), #12 (parsing ≠ type-checking), #13 (external iOS sim
verification), #14 (route review to claude — deliberately not done). #10 and #11 are fixed.
