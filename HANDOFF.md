# HANDOFF — vibetastic-pm (framework repo)

**Last session:** 2026-09-08 · **Branch:** `feat/33-codex-orchestrator` from `95d9ea2`.
**Stage:** issue #33 implementation complete and verified locally; branch is not pushed or merged.
No deployed project was modified. Live trial phases have not started.

---

## READ THIS FIRST — binding operator directives (carried forward, still binding)

> **"I don't want to push updates to other projects unless I explicitly ask. A PR merge request
> from me is not a pull into all projects request."**

Merging a framework PR and propagating it to deployments are **separate decisions**. Merge when
asked; then stop, report which projects are behind, and offer — naming them.

> **gamedaytastic is the active project. hometastic is on the backlog.**

Do not start work, pull updates, or make changes in either unless asked for it by name.

> **A concurrent session may be live in a project repo.** Check before touching one. **Never
> `git stash` in a repo you do not have exclusive use of.**

> **Do not present a dismissal as a review.** If a candidate was skipped rather than evaluated,
> say so. (Caught twice by the operator on 2026-08-22; right both times.)

> **Keep HANDOFF.md current at the end of every session.** Not just when something dramatic
> happened — a session that ends without refreshing this file has failed its last step.

---

## Issue #33 session — 2026-09-07–08

- Requested: tag the current release, then implement additive Codex orchestration (#33).
- Created and pushed annotated tag `pre-issue-33` at `95d9ea2`; this is the pre-change rollback point.
- Read issue #33 and its September 7 additions: single-writer orchestrator lease and a session-level OpenCode-only fallback routing profile.
- User approved the revised design after critique; implementation is complete on the feature branch.
- Critique: define lease fencing and dispatch ownership, PLAN/TASK_LOG crash reconciliation, conservative recovery instead of blanket failure marking, idempotent additive setup, measured hook capability checks, telemetry deduplication/token semantics, and explicit fallback adjudication behavior. Separate implementation readiness from measured trial completion.
- Implemented the shared orchestrator contract and provider entry points, transactional PLAN/TASK_LOG updates, fenced writer leases, run/worktree recovery evidence, provider telemetry adapters, idempotent hook installation/doctor, planning-role dispatch, family-diverse fallback routing, normalized quota reporting, CI checks, and the pre-registered trial protocol.
- `bash scripts/selftest.sh`: PASS, including 79 additive regression tests. Focused state/integration suite: 47 tests PASS.
- Remaining empirical acceptance: install in a disposable PM project, trust and observe native hooks for both providers, then run the matched phases in `Docs/codex-orchestrator-trial.md`. Fixture tests are not recorded as live hook/trial evidence.
- No deployed projects were modified. Real trial outcomes remain to be measured; fixtures cannot establish orchestration quality or subscription headroom.

## Prior repo state before issue #33 (historical)

| | |
|---|---|
| `main` | `49001a3`, pushed, clean |
| Open PRs | **none** |
| Branches | `main` only, local and on `origin` |
| Open issues | **19** (was 29) |
| `bash scripts/selftest.sh` | **PASS** |

---

## What landed this session (2026-09-07)

### 1. The GLM correction — done

`MODELS.md` asserted `glm-5.2`/`5.3` were "unbenchmarked." False: GLM is scored on SWE-bench
**Pro** and **FrontierSWE**, not Verified. `glm-5.2` posts 62.1 SWE-bench Pro (above GPT-5.5's
58.6) and 74.4% FrontierSWE (near Opus 4.8's 75.1%). Root cause: the 2026-08-22 review ranked
the whole catalog on Verified alone and read absence from one leaderboard as absence of data —
overwriting a correct annotation with a wrong one.

- Reverted the language in both places; added a note beside the `glm-5.3` entry so the next
  review does not repeat the blind spot.
- **Operator decision: `glm-5.2` stays the `fast` fallback.** FrontierSWE is closer to this
  framework's long-horizon workload than single-shot Verified. `glm-5` stays documented as the
  cheaper alternative (27 providers, $0.60/$1.92) that was considered and rejected.
- **Deferred to next maintenance as issue #48:** the *other* rungs (`minimax-m3`, `kimi-k2.6`,
  the deepseek variants) were ranked on the same wrong axis and cluster within 0.4 Verified
  points — a spread too small to order anything on. The **bake-off** results are unaffected
  (measured, not benchmarked) and are explicitly out of scope there.

### 2. The PR stack — all merged

`main` carries #38, #40, #43, #44 (issues #36, #37, #39, #41, #42, #19).

**Merge mechanics worth reading before you stack PRs again.** #38 was squash-merged with
`--delete-branch`, which **auto-closed #40** (its base branch vanished) and left #43/#44
conflicting against the squashed main. Recovery: re-push #38's commit to restore the base ref,
reopen #40, retarget all three to `main`, merge main into the stack tip resolving one
`selftest.sh` conflict, then land everything through **#44 with a merge commit**.

> **Next time: retarget every child PR to `main` before merging the parent, and never
> `--delete-branch` a base that another PR is stacked on.**

### 3. Repo cleanup

- 12 stale local branches deleted (each verified merged first), all remote branches pruned.
- PR #47 (repository README) reviewed and merged — the repo had no README.
- **10 issues closed:** #36, #37, #39, #41, #42, #19 via the stack; #9, #11, #22, #26 verified
  fixed against the merged code rather than assumed.

### 4. Public-repo hygiene

Audited all 52 tracked files **and the full history** for credentials: **none**. The only key
references are placeholders (`sk-or-your-key-here`) and code that deliberately strips keys.
`~/.ssh/gh-agent-token.sh` is referenced but was never committed. `logs/` is correctly ignored;
`.claude/settings.local.json` is untracked.

Removed the three hardcoded `/Users/tim` paths (`49001a3`):

- `scripts/selftest.sh` pinned two specific PM deployments. Which deployments to lint now
  resolves in precedence order: **`$SELFTEST_LIVE_PLANS`** → **`.selftest-live-plans`**
  (gitignored local file, one absolute path per line — *this is where to record the projects
  you develop against*) → **discovery of sibling `*-pm/PLAN.md`**. All three paths verified.
  Repo root comes from `--git-common-dir`, so it still resolves to the main checkout when the
  selftest runs inside a worktree — the original reason those paths were absolute.
- `setup.sh` usage example uses `myapp` / `myorg` / `$HOME`.
- `PROJECT.md` describes its paths relative to the checkout. Only the frontmatter is
  machine-parsed, so nothing depended on the literal string.

`.selftest-live-plans.example` is committed and documents the local file.

**Deliberately left as-is (operator decision):** real project names (gamedaytastic, hometastic)
remain in the docs as *evidence labels* — "gamedaytastic T073, a one-line fix on a shared audio
path." Genericizing them is easy; the cost is that the lessons stop being traceable to what
actually happened. Revisit only if the repo's audience changes.

---

## Deployments

| Project | Framework state | Notes |
|---|---|---|
| **hometastic-pm** | **Current** — pulled this session (`ed84d5e`) | Also needed `spec-body-guard.py` wired into `.claude/settings.json` **by hand** — issue #16 in action: `setup.sh` runs once, so a hook added to the framework never reaches an onboarded project. Verified blocking (exit 2) after wiring. |
| **gamedaytastic-pm** | **Behind** `main` | **Untouched on purpose** — actively building this session. Needs the same framework pull *and* the same manual hook wiring when the operator asks. |

---

## Open issues — 19, none blocking

- **Fixed in prose, still needs a mechanism** (the recurring failure mode — see
  `RULES.md`): #15 (`dispatch.sh` does not refuse a build without `--worktree`, though the rule
  is in `dispatch.md`), #16 (setup.sh runs once; hooks never reach onboarded projects — bit us
  twice today), #17 (`LOG_DIR` still derived from the prompt file's directory), #18 (mechanize
  the critic gate).
- **Gate correctness:** #21, #35 (merge gate verifies a tree but never pins *which* tree), #29
  (recovery marks shipped work failed), #24.
- **Cost / measurement:** #14 (claude-lane review routing), #32 (how to tell whether a change
  moved token cost), #34 (checks that cannot fail).
- **Model selection:** #48 (re-rank the tiers on the right benchmarks — filed today).
- **Larger bets:** #33 (Codex-orchestrator trial, additive), #46 (steal root-cause-before-fix
  and verification-before-completion from superpowers), #23 (inline-authoring gate).
- **iOS-specific:** #10, #12, #13, #45.

Suggested next pickup: **#16**, because it is the one that makes every other framework fix fail
to arrive, and it cost manual intervention twice in a single session.
