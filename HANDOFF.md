# HANDOFF — vibetastic-pm (framework repo)

**Last session:** 2026-08-20 · **Branch:** `main` @ `5183d6b`, **pushed** (origin in sync).
Working tree clean.

**Stage:** framework maintenance. This session was **diagnostic, not constructive** — it started
from an operator report ("the orchestrator was slow, waited for me to ask status") and ended with
six issues filed, three fixes merged, and the first orchestrator cost telemetry the framework has
ever had. Nothing is half-built; the open work is all filed as issues.

---

## READ THIS FIRST — binding operator directives from this session

> **"I don't want to push updates to other projects unless I explicitly ask. A PR merge request
> from me is not a pull into all projects request."**

Merging a framework PR and propagating it to deployments are **separate decisions**. Merge when
asked; then stop, report which projects are behind, and offer — naming them. This was violated
this session (framework pulled into both gamedaytastic and hometastic off one "merge it").

> **gamedaytastic is the active project. hometastic is on the backlog.**

Do not start work, pull updates, or make changes in hometastic unless asked for it by name.

> **A concurrent session may be live in a project repo.** Check before touching one: unexpected
> untracked files, or recent commits you did not make. **Never `git stash` in a repo you do not
> have exclusive use of.** This session ran `git stash -u` in gamedaytastic while another session
> was mid-T075 device round. It landed clean; that was luck, not care.

---

## Unanswered question for the operator

The three uninvited pushes were never resolved — the operator stated the rule and moved on
without saying whether to revert:

| Repo | Commit | What |
|---|---|---|
| hometastic-pm | `d1b58fa` | framework subtree pull |
| hometastic-pm | `ada3dd1` | Stop hook wiring + TASK_LOG entry |
| gamedaytastic-pm | `9ef4e35` | framework subtree pull |

All are on `origin/main` and are framework/config only — no project state touched. **Ask before
acting.** A plain revert of a subtree pull is awkward to re-apply later, so this is the
operator's call, not a default.

---

## Done this session

**#27 merged (`a95fb7e`) — the backgrounding mechanism is now named.** `CLAUDE.md` had said
"background the long pole" since 2026-07-02 while naming no mechanism, and `economy.md` bans
monitor loops — so an orchestrator that backgrounded with shell `&`/`nohup` got no completion
notification and **parked until the operator typed**. `dispatch.md` now requires the Bash tool
with `run_in_background: true` and forbids shell detachment. This was **never a regression**:
`git log -S "run_in_background" --all` is empty; the mechanism was simply never encoded, so each
session picked by chance. Verified before proposing a revert, which was the operator's first
instinct.

**#31 merged (`aa85bfb`) — orchestrator telemetry actually works now.** `log-partner-burn.py` was
wired 2026-08-15 and had **never written a single record**: it read usage from the `Stop` payload,
which carries none. It now reads the transcript (`message.usage` per assistant turn), logs
per-turn deltas against a high-water mark in `logs/.partner-burn-state.json`, excludes sidechain
(subagent) turns, and captures `cache_creation_tokens`. Six new selftest assertions, and the
suite was **mutation-verified** — reintroducing the bug turns it red.

**#28 merged (`d2bab07`) — design history archived** under `Docs/design-history/` (design_A,
design_B, pilot_51, STATUS.md), recovered from the retired `vibetastic-run/` iCloud workspace.
Never previously under version control. The README marks them rationale-not-rules and lists the
known drift (both describe the retired `-run` model; `design_B` predates the cheap-first Reviewer
lane).

---

## The number that reframes everything

First orchestrator telemetry, backfilled across 56 sessions and 327 dispatches in three projects:

| Lane | Runs | Total tokens | Share | Fresh (in+out) | Share |
|---|---:|---:|---:|---:|---:|
| **Orchestrator (Opus)** | 56 | 983,660,286 | **69.8%** | 5,891,029 | 4.4% |
| codex builders | 175 | 219,269,200 | 15.6% | 115,089,232 | **86.7%** |
| opencode builders | 94 | 117,210,764 | 8.3% | 10,127,500 | 7.6% |
| claude builders | 58 | 88,669,916 | 6.3% | 1,561,776 | 1.2% |

- Orchestrator composition: **97.3% cache reads**, 0.6% fresh.
- Median Opus session: **169 turns, ~98K context/turn, 16.7M cache reads.**
- **Turn count is the multiplier, not context size.** The operator already keeps context under
  ~30%, so "trim context" is not an available lever — this was corrected mid-session.
- Both burn gates read `cost.jsonl` and had therefore been computed **without their dominant
  term** since they were written.

Backfill is applied to all three projects (`logs/` is gitignored, so it is local state; logged in
each TASK_LOG). `scripts/backfill-partner-burn.py` is dry-run by default.

**Caveat that must not be lost:** hometastic's partner history is 1 surviving session vs
gamedaytastic's 47. Earlier transcripts were pruned or ran under a different cwd. Its numbers are
**not comparable** to gamedaytastic's — do not read the gap as low usage.

---

## Next action

Operator's call, but the dependency graph implies an order. **#26 → #32 → #33.**

1. **#26 (stopping at non-gated transitions)** — the behavioral half of the parking problem, and
   a **precondition for #33 being interpretable**: every park-and-wait turn is a full-context turn
   producing nothing, so it inflates turns-per-task while measuring the rule set, not the model.
2. **#32 (assessment methodology)** — `cost-report.sh --since`, ratio metrics, pre-registered
   hypotheses. Needed *before* a trial, not reconstructed after.
3. **#33 (Codex-orchestrator trial)** — see below.

Also worth doing regardless of #33's outcome: the **transactional `plan-update` command** from
#33's design section. A pre-commit hook protects history, not the invariant — it does not stop the
orchestrator writing a corrupt PLAN, reading it across five transitions, and committing later.
Write-temp → lint → atomic replace is the durable fix, and it improves the current setup too.

---

## #33 — the Codex-orchestrator question (filed, not started)

The operator's constraint: **the Claude 5-hour window is binding and regularly exhausted while the
ChatGPT/Codex subscription sits idle.** Both flat-rate; this is a quota-topology problem, not cost.

**A GPT-5.6 consultation was dispatched** (`prompts/consult-orchestrator-portability.md`,
`logs/consult-orchestrator-portability-20260820T221336Z-60418.log`) and **overturned this
framework's assumption**. Verified against current Codex docs, not recollection:

- Codex CLI **has blocking lifecycle hooks** — `PreToolUse`, `PostToolUse` (can exit 2),
  `PermissionRequest`, `Stop`, `SubagentStart/Stop`. `apply_patch` matches `Edit`/`Write`.
- Codex **has real subagents** with separate contexts and per-agent model/effort.
- Persistent rules via `AGENTS.md`; `project_doc_fallback_filenames` can point at `CLAUDE.md`.

Caveats it attached: hooks can be disabled absent managed policy, project hooks need repo trust,
async hooks cannot block, some tool paths evade hooks. **Enforcement, not a security boundary.**

**Hard requirement the operator set:** building this must be **additive**. Both orchestrators must
be invocable against the **same project**, chosen per session — no fork, no global switch, no
migration. Rollback is "use the other one next session." Details in #33's constraint section.

**The load-bearing unknown:** how the operator's Codex entitlement charges/throttles a
169-turn/~100K-context session is **unmeasured**, and Claude cache-read tokens do not translate.
Measure it; do not project it.

---

## Also filed this session

- **#29** — the recovery rule marks any `in_progress` task `failed` without checking TASK_LOG
  first. Caught live: hometastic T071 looked abandoned for five weeks but had **merged**
  (PR #100, `dafbb87`); only its PLAN write was lost. Following the rule would have put a shipped
  task one step from Gate 2. Corrected as `done` (`f1c8d02`), not as a recovery event.
- **#34** — the meta-pattern: **checks and references that cannot fail.** Three instances in one
  day (#24, #30, #16) plus dangling references. Success and failure produce identical output. The
  proposed conventions: mutation-verify new checks, make silence loud
  (`logs/telemetry-errors.log`), and make references mechanically checkable.
- **#16** — commented with field confirmation: hometastic had **no `Stop` hook at all**, so even
  #30's fix would have recorded nothing there. Three hooks now need per-project hand-installation
  with no mechanical check that they arrived. Suggested `scripts/check-wiring.sh`.
- **#24** — `selftest.sh` passes plan-lint exit 1 (structural corruption) as `ok` on live PLANs.
  Note the new partner-burn checks in `selftest.sh` **were** mutation-verified; the live-PLAN
  check would not survive that bar.

## Scope note

`hometastic-pm/PLAN.md:843` pointed at a spec in the deleted `-run` workspace. The operator
restored it from iCloud; **nothing had been lost** — all 8 specs were byte-identical to
`hometastic-code/Docs/specs/`, with the code repo strictly newer on the v0.9 spec. Only the path
reference was stale (fixed, `5b249cd`). Lesson: search the code repo before declaring a spec lost.
