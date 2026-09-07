# HANDOFF — vibetastic-pm (framework repo)

**Last session:** 2026-09-07 · **Branch:** `main` @ `d5cfbd2`. Working tree clean.
**Stage:** framework maintenance. Repo cleanup session — the whole PR stack is merged and
the branch/issue backlog is pruned. Nothing is in flight.

---

## READ THIS FIRST — binding operator directives (carried forward, still binding)

> **"I don't want to push updates to other projects unless I explicitly ask. A PR merge request
> from me is not a pull into all projects request."**

Merging a framework PR and propagating it to deployments are **separate decisions**. Merge when
asked; then stop, report which projects are behind, and offer — naming them.

> **gamedaytastic is the active project. hometastic is on the backlog.**

Do not start work, pull updates, or make changes in hometastic unless asked for it by name.

> **A concurrent session may be live in a project repo.** Check before touching one. **Never
> `git stash` in a repo you do not have exclusive use of.**

> **Do not present a dismissal as a review.** If a candidate was skipped rather than evaluated,
> say so. (The operator caught this twice on 2026-08-22, and was right both times.)

---

## What landed this session (2026-09-07)

**The GLM correction — done.** `MODELS.md` asserted that `glm-5.2`/`5.3` were "unbenchmarked."
False: GLM is scored on SWE-bench **Pro** and **FrontierSWE**, not Verified. `glm-5.2` posts
62.1 SWE-bench Pro (above GPT-5.5's 58.6) and 74.4% FrontierSWE (near Opus 4.8's 75.1%).
Root cause: the 2026-08-22 review ranked the entire catalog on Verified alone and read absence
from one leaderboard as absence of data — overwriting a correct annotation with a wrong one.

- Reverted the "unbenchmarked" language in both places.
- **Operator decision: `glm-5.2` stays the `fast` fallback.** FrontierSWE is closer to this
  framework's long-horizon workload than single-shot Verified. `glm-5` remains documented as
  the cheaper alternative (27 providers, $0.60/$1.92) that was considered and rejected.
- Added a note beside the `glm-5.3` catalog entry so the next review does not repeat the
  Verified-only blind spot.
- **Still open (carried from 2026-08-22, item 3):** the other rungs — `minimax-m3`,
  `kimi-k2.6`, the deepseek variants — were also ranked on Verified only and may be
  mis-ordered against each other. The *bake-off* results are unaffected (measured, not
  benchmarked).

**The PR stack — all merged.** `main` now carries #38, #40, #43, #44.

Merge mechanics worth knowing, because they went sideways once: #38 was squash-merged with
`--delete-branch`, which **auto-closed #40** (its base branch vanished) and left #43/#44
conflicting against the squashed main. Recovery was to re-push #38's commit to restore the
base ref, reopen #40, retarget all three to `main`, then merge main into the stack tip and
land the whole thing through **#44 with a merge commit**. Next time: **retarget every child
PR to `main` before merging the parent**, and don't `--delete-branch` a stacked base.

**Repo cleanup.** 12 stale local branches deleted (all verified merged), all remote branches
pruned — `origin` now holds `main` only. PR #47 (repository README) reviewed and merged.
10 issues closed: #36, #37, #39, #41, #42, #19 (the stack), plus #9, #11, #22, #26 verified
fixed in the merged code.

---

## Open issues — 18, none blocking

Nothing is in flight. The remaining backlog splits roughly:

- **Verified-fixed-in-prose, needs a mechanism:** #15 (refuse a build dispatch without
  `--worktree` — the rule is in `dispatch.md`, `dispatch.sh` does not enforce it), #16
  (setup.sh runs once; hooks never reach onboarded projects), #17 (`LOG_DIR` still derived
  from the prompt file's directory), #18 (mechanize the critic gate).
- **Correctness of the gates:** #21, #35 (the merge gate verifies a tree but never pins
  which tree), #29 (recovery marks shipped work failed), #24.
- **Cost/measurement:** #14 (claude-lane review routing), #32 (how to tell whether a change
  moved token cost), #34 (checks that cannot fail).
- **Larger bets:** #33 (Codex-orchestrator trial, additive), #46 (steal root-cause-before-fix
  and verification-before-completion from superpowers), #23 (inline-authoring gate).
- **iOS-specific:** #10, #12, #13, #45.

`bash scripts/selftest.sh` is green at `d5cfbd2`.

---

## Deployments behind `main`

Not checked this session, and **not to be pulled without being asked by name**:
`gamedaytastic-pm` (active) and `hometastic-pm` (backlog) both carry `framework/` as a
subtree and predate everything merged today.
