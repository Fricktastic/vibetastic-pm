# Upgrading an existing project

For a `<project>-pm/` directory set up before PRs #52, #53, #55, #56 and #59 (issues #15,
#18, #35, #45, #50, #51). Those changes turned the critique gate, the round caps, the
worktree rule and the merge gate from prose into refusals, so an unmigrated project will hit
exit 2 / exit 31 stops it did not see before. Work through the steps in order, at a quiet
point with no build dispatch running.

Everything below edits project-owned files. `framework/` stays a read-only subtree. PLAN
changes go through `plan-update.py` and HANDOFF/TASK_LOG changes through
`orchestrator-state.py` (`ORCHESTRATOR.md` § Durable state mutations), from the lease owner.

## 1. Pull the framework and reinstall the adapters

```sh
git add PLAN.md TASK_LOG.md SPEC.md prompts/
git diff --staged --quiet || git commit -m "Checkpoint project state before framework update"
git subtree pull --prefix framework framework main --squash
python3 framework/scripts/install-orchestrators.py --pm-dir . --framework-dir framework
python3 framework/scripts/orchestrator-doctor.py --pm-dir . --framework-dir framework
```

- [ ] `install-orchestrators.py` is idempotent and additive. It adds the `SessionStart` hook
      (`scripts/handoff-volatile-hook.py`) to `.claude/settings.json` and `.codex/hooks.json`.
- [ ] The doctor passes. It checks both SessionStart hooks, exercises the volatile banner,
      and validates the policy in `PROJECT.md` (step 2).
- [ ] In Codex, review and trust the new hook with `/hooks`. An untrusted hook does not run.

## 2. Declare project policy in PROJECT.md (optional)

Every part is optional; whatever is absent uses the generic defaults in
`framework/scripts/project_policy.py`. What each part means: `VERIFY.md` § Project policy.

- [ ] Frontmatter: `critic_round_cap` (default 2), `reviewer_fixup_round_cap` (default 3).
- [ ] `## Verify tiers` — one bullet per tier, `- R0: ...`, all of R0–R2.
- [ ] `## Risk triggers` — one bullet per kind of change that sets `risk: true`.
- [ ] `## Observations`, `## Test paths`, `## Non-production paths` — used by the merge gate
      (step 9).
- [ ] `## Test command` — a fenced block holding the single command that runs the suite on
      the real target. `merge_gate.py verify` runs it unless given `--cmd`. Projects set up
      with a test command already have it.

`framework/Docs/examples/policy-ios.md` is a worked iOS starting point: copy its frontmatter
keys and sections, then edit. Check the result:

```sh
python3 framework/scripts/project_policy.py --pm-dir . validate
python3 framework/scripts/project_policy.py --pm-dir . show
```

## 3. Re-tier open PLAN tasks with an explicit `risk`

Pre-build critique is now keyed on `risk: true` or `security: true`, not the verify tier.
A task with no `risk:` field (or `risk: null`) is a legacy task and keeps the old rule:
critiqued at R1/R2 or `security: true`. `dispatch.sh` refuses (exit 31) the build of any task
that needs critique until its critique has been adjudicated (step 4).

- [ ] For every `pending` / `in_progress` task, set `risk: true|false` from the project's risk
      triggers. `risk: false` is the explicit opt-out for an R1/R2 task with no design risk.
- [ ] Optionally add `observation: test|runtime|none` (+ `observation_cmd` for `test`) — see
      step 9. Do not add `observation: null`: an undecided field is refused at merge, while an
      absent one is treated as legacy.
- [ ] plan-lint reports a bad `risk`, `security` or `observation` value as vocabulary drift
      (exit 3), not structural corruption.

## 4. Tasks critiqued before the upgrade

The gate reads `logs/verdicts.jsonl`, and a critique run under the old framework left no
record there. A risk/security (or legacy R1/R2) task critiqued before the upgrade therefore
looks uncritiqued. For each, either:

- [ ] re-run the critic (step 5) and adjudicate its verdict:
      `python3 framework/scripts/review_gate.py --pm-dir . adjudicate --task T0XX --outcome proceed`, or
- [ ] record the operator's decision to accept the earlier critique:
      `python3 framework/scripts/review_gate.py --pm-dir . adjudicate --task T0XX --outcome override --reason "<prior critique + operator decision>"`,
      plus `critic_override` in TASK_LOG.

`adjudicate` needs the lease. `--outcome proceed` is refused when there is no recorded verdict
or while the latest one carries a `[BLOCKING-PLAN]` finding. Never self-approve an override.

## 5. Re-render in-flight critic and reviewer prompts

- [ ] Re-render any `prompts/critic-T0XX.md` / `prompts/review-T0XX.md` not yet dispatched
      from the new `framework/prompts/critic.md` / `reviewer.md`. Old renders have no
      `CRITIC_RESULT` / `REVIEWER_RESULT` block, so their run is recorded as `MALFORMED` —
      which still counts as a round.
- [ ] Defect-fix specs not yet critiqued (issue #46): the new critic returns `[BLOCKING-PLAN]`
      when a defect fix has no **Symptom / Mechanism / Evidence**, or its Evidence is
      reasoning from source rather than an observed artifact cited by path. Before rendering
      the critic, have the Tech Lead add the fields, or spec an instrumentation task first
      (`VERIFY.md` § Pre-build critique). Otherwise the finding costs a critique round.
- [ ] Name role prompts `critic-T0XX.md` / `review-T0XX.md` and fixup prompts
      `fixup-T0XX*.md`, or pass `--task T0XX`. The gates find the task from the name; a
      prompt they cannot attribute is not gated or counted.
- [ ] Gate reviews pass `--role critic|reviewer --author-model <actual-model>` (plus
      `--security` when applicable) — `.claude/rules/dispatch.md` § Pre-Build Critique.

## 6. Every build dispatch needs `--worktree`

- [ ] `dispatch.sh` refuses (exit 2) any build (non `--read-only`) dispatch without
      `--worktree <branch>`. `DISPATCH_ALLOW_NO_WORKTREE=1` is the deliberate exception;
      justify it in the TASK_LOG entry.
- [ ] Worktree paths are keyed on the branch (`task/T012` → `../<project>-worktrees/task-T012`),
      not the prompt filename. A branch already checked out in an existing worktree —
      including an old prompt-named one — is still found and reused. A new dispatch on a
      branch checked out nowhere gets the branch-named path; if that path exists holding a
      different branch, dispatch refuses (exit 2). Remove stale worktrees rather than renaming.
- [ ] Build dispatches also need the verify command (5th arg) and the tier (7th arg) —
      `.claude/rules/dispatch.md` § OpenCode.

## 7. Move volatile claims in HANDOFF.md

- [ ] Put every time-bound claim — a branch head, a PR's state, what build is on a device,
      whether a run is still going — in one section with exactly this heading (tooling
      matches the string), one claim per line with an as-of date and a check command.
      Remove them from prose and headers. Format: `RULES.md` § Session Handoff.

      ```markdown
      ## Volatile — re-verify before use
      - PR #88 open, awaiting review | as-of 2026-09-24 | check: `gh pr view 88 --json state -q .state`
      ```
- [ ] Drop backend availability or quota lines entirely. Backends are chosen live: dispatch
      and branch on exit 30.
- [ ] Rewrite HANDOFF.md through `orchestrator-state.py write --path HANDOFF.md`.

The SessionStart hook prints that section, and only it, under a "claims, not facts" banner,
flagging lines missing a date or check. It is silent when the file or section is absent. A
project running an interim `scripts/volatile-shim.sh` of its own: the shim detects the heading
string under `framework/` and prints its own removal steps once the pulled framework has it.

## 8. Codex: Tech Lead metadata

- [ ] `scripts/dispatch-role.py --role tech-lead` now rejects a result missing `verify_tier`,
      `risk` or `observation` (and `observation_cmd` when `observation: test`). Re-run any Tech
      Lead dispatch that returns the old shape; do not register it by hand.

## 9. Merge through `merge_gate.py`

Merges go through `framework/scripts/merge_gate.py`, never a plain `gh pr merge`. Every piece
of evidence is pinned to the commit being merged; a later commit voids it. Procedure:
`.claude/rules/dispatch.md` § Merge gate; rationale: `VERIFY.md` § Merge gate.

```sh
G="python3 framework/scripts/merge_gate.py --pm-dir ."
WT=<task worktree>; git -C "$WT" fetch origin develop; BASE=origin/develop
$G verify       --task T0XX --dir "$WT"
$G fail-on-base --task T0XX --dir "$WT" --base "$BASE"          # observation: test
$G observe      --task T0XX --dir "$WT" --evidence <artifact> --summary "..."   # observation: runtime
$G merge        --task T0XX --dir "$WT" --base "$BASE" --pr <n> --repo <org/repo> -- --squash
```

- [ ] `merge` runs `check`, then `gh pr merge --match-head-commit <sha>`. `--repo` is required.
      Pass a freshly fetched `origin/<base>` as `--base`, never a possibly stale local branch.
- [ ] Review the task worktree, not the live checkout: the gate accepts only an approving
      review of the exact commit, read from a clean tree.
- [ ] Reviewer verdicts recorded before the upgrade carry no `head_sha`. Re-review the commit,
      or record the operator's waiver:
      `$G override --task T0XX --dir "$WT" --check review --reason "..."` (lease owner; this
      commit only) plus `merge_gate_override` in TASK_LOG.
- [ ] Tasks carry `observation: test|runtime|none` (+ `observation_cmd` for `test`). A legacy
      task with no `observation:` field skips the observation check with a warning; the
      pin, review-at-SHA and production-diff checks still apply, so a test-only or
      net-reverted diff is still refused.
- [ ] Path classes come from `## Test paths` / `## Non-production paths` (step 2).

## 10. New TASK_LOG events

| Event | When | Required fields |
|---|---|---|
| `state_correction` | PLAN disagreed with the evidence (e.g. a shipped task left `in_progress`) | `field`, `from`, `to`, **`evidence`** — one without it is an auditable violation |
| `critic_escalated` / `review_escalated` | a critique / reviewer-fixup round cap was reached | `rounds`, unresolved findings |
| `round_cap_override` | the operator granted extra rounds (`review_gate.py override-cap`) | `role`, `extra`, `reason` |
| `observation_recorded` | a runtime observation was recorded (`merge_gate.py observe`) | `sha`, `kind`, `evidence`, `summary` |
| `merge_gate` | `merge_gate.py check`/`merge` ran | `sha`, `base_sha`, `allowed`, `checks`, `pr` |
| `merge_gate_override` | the operator waived one check for one commit | `sha`, `check`, `reason` |

The full vocabulary is in the `TASK_LOG.md` template header (also `critic_returned`,
`critic_override`, `lessons_consolidated`).

## Exit codes you'll now see

| Exit | From | Meaning | Action |
|---|---|---|---|
| `0` | all | ran; for a build, the verify command passed (its scope only) | continue |
| `2` | dispatch.sh, gate scripts | invalid invocation — no `--worktree`, no verify command or tier on a build, model contradicts tier, worktree path holds another branch | fix the call |
| `20` | dispatch.sh | verifier never passed | tier / backend escalation, not a failure |
| `21` | dispatch.sh `--read-only` | the run modified the tree | inspect; changes are left in place |
| `30` | dispatch.sh | backend unavailable (quota, auth, burn gate, CLI missing) | next backend, same tier |
| `31` | dispatch.sh, `review_gate.py`, `merge_gate.py` | ownership/routing stop or gate refusal: critique not adjudicated, round cap reached, merge evidence not pinned to this commit | resolve or escalate to the operator; **never** a `failure_count` event |

`merge_gate.py verify` / `fail-on-base` also exit 1 when the command they ran failed (the
result is recorded).

`logs/verdicts.jsonl` is gate state, not telemetry: the critique gate, the round caps and the
merge gate are computed from it. Never edit or truncate it; write to it only through
`review_gate.py` and `merge_gate.py`.

## Check the upgrade

```sh
python3 framework/scripts/orchestrator-doctor.py --pm-dir . --framework-dir framework
python3 framework/scripts/project_policy.py --pm-dir . validate
bash framework/scripts/plan-lint.sh PLAN.md                         # 0 or 3
python3 framework/scripts/review_gate.py --pm-dir . status --task T0XX      # per open task
python3 framework/scripts/review_gate.py --pm-dir . build-gate --task T0XX  # 0 = buildable
python3 framework/scripts/merge_gate.py --pm-dir . status --task T0XX       # per task awaiting merge
```

- [ ] Start a fresh session: with a volatile section in HANDOFF.md, the banner prints first.
- [ ] `build-gate` exits 0 for every task you intend to dispatch next, or 31 with the reason.
