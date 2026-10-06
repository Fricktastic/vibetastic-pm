---
framework: vibetastic-pm
version: "2.1"
---

# VERIFY — the risk-tiered merge gate

Why this exists: the old gate (`build + unit tests`) passed code with real integration
bugs. hometastic #72 shipped with all 57 tests green because the mocked seam bypassed the
production JSON decode path; #73 shipped because a single lucky screenshot "verified" a
launch-timing race. **Green must mean "works," proportional to risk.** This file defines
what "verified" means per tier. The orchestrator (partner) enforces it as a merge gate —
branch protection alone does not.

---

## Verify tiers — what evidence proves a change

`verify_tier` answers one question: **what evidence proves this change works?** It decides
what must run before merge. It does **not** decide whether the plan gets a pre-build critique
— that is the task's `risk` flag (§ Pre-build critique, issue #50). Keeping the two apart is
the point: a UI tweak that needs a run-and-look is a high verify tier but may carry no design
risk at all.

The orchestrator (or the Tech Lead / Architect that writes the spec) assigns the tier at spec
time = the **highest** tier any changed behavior needs (bias up when unsure). Record it as
`verify_tier:` on the task in PLAN.md and state it in the task prompt.

The framework defines the tiers as **kinds of evidence**; each is cumulative:

| Tier | Evidence kind | Gate (cumulative) |
|---|---|---|
| **R0 — logic** | the change is fully proven by compiling and running unit tests that exercise the changed logic; it crosses no external boundary | build + unit tests + **diff review** (see below) |
| **R1 — real-path integration** | the change crosses a boundary (serialization, network, persistence, IPC, configuration, a third-party API) | R0 **+ real-path integration test**: a representative **real input** through the **production code path** |
| **R2 — run-and-observe** | correctness is only visible by running the product (rendered output, timing, runtime or device behavior) | R1 **+ run it and observe**: build, run, drive to the state, capture the outcome (screenshot, response, log), orchestrator inspects |

**What falls in each tier is project policy.** A project states its own meanings in
`PROJECT.md § Verify tiers` (and its round caps and risk triggers alongside — see § Project
policy below); the definitions above are the generic default when it does not.
`Docs/examples/policy-ios.md` is a copyable iOS/iPadOS policy: views and tiles are R2,
JSON/URLSession/persistence are R1, and what counts as a valid R2 pass on a simulator.

### Who runs what — and what a green dispatch actually means

Mechanical checks run inside `dispatch.sh`'s verify loop via the verify-cmd argument **only
to the extent that project's verify-cmd covers them**. A green dispatch means "the verify-cmd
exited 0" and nothing more. It does not mean tests ran. `logs/cost.jsonl` says so on every
run: `verify_scope` is `verify_cmd` (the green covers that command only) or `none`.

**The builder's acceptance line (issues #21, #58).** Every build turn is told to end its
report with `ACCEPTANCE: MET` or `ACCEPTANCE: UNMET — <which criterion, and why>`. The last
such line across the run's turns wins. A run whose builder says UNMET **exits 22**, never 0,
even when the verify-cmd passed: gamedaytastic T073's builder reported that the spec's
red-first proof could not run, and dispatch still printed `verify passed` and exited 0. Exit
22 is neither green nor a failure (`.claude/rules/dispatch.md`, exit table); `cost.jsonl`
records `acceptance: met | unmet | null`. A MET claim proves nothing — acceptance is observed
at the merge gate (§ Merge gate) — but an UNMET claim is the builder telling you the green is
not what it looks like.

| Check | Runs where | Owner |
|---|---|---|
| Compile (incl. the **test target**) | `dispatch.sh` verify loop, out-of-sandbox | verify-cmd in `PROJECT.md` |
| R1 fixture / integration test | verify loop **iff** the verify-cmd invokes it | verify-cmd |
| **Test execution** (unit + simulator/device) | after the dispatch, on real hardware or a simulator; **also** in the verify loop when the project opts in (below) | **orchestrator** |
| R2 run-and-observe | after the dispatch | orchestrator |
| Diff review | after the dispatch returns green | orchestrator (cheap first pass) |

**Builders cannot execute simulator-dependent tests.** CoreSimulatorService is a Mach
service, not a filesystem path, so no `workspace-write` grant can reach it — see
`.claude/rules/dispatch.md` § codex + iOS/Xcode tasks. `dispatch.sh` injects a preamble
telling the builder this outright, so it neither flails against the denial nor invents a
result (issue #37).

> **Standing rule: a builder's claim about test results is never evidence.** Not "tests
> pass", not a flake table, not a baseline run count, not a mutation score. If the
> orchestrator did not run the suite, the suite did not run. Field case (gamedaytastic
> T077): a test-only task returned exit 0 with `verify passed on attempt 1/3` and a report
> containing a 10-run flake table — while the test target did not compile at all, because
> the verify-cmd was the app scheme's `build`. See issue #36.

**Opt-in: tests in the verify loop.** A project may put test runs in its verify-cmd. The verify
loop runs out of the sandbox, so the simulator is reachable, and on failure `dispatch.sh`
feeds the output (failing assertions included) back to the same builder session. That gives
the builder a test result it did not have to claim. Field case (gamedaytastic T247): fixup r2
reported two failures fixed without running them, and both were still red; r3 needed a PM
probe the builder could have made itself. Rules when opting in:

- Wrap every simulator use in `python3 framework/scripts/sim-lock.py -- <cmd>`, in the
  verify-cmd **and** `PROJECT.md § Test command`. Parallel dispatches and the merge gate then
  queue on one machine-wide lock instead of wedging CoreSimulator. A lock timeout exits 75,
  so the verifier fails and the loop retries.
- The standing rule above still holds. A verify-loop green is mechanical evidence that the
  verify-cmd passed, and the orchestrator still runs the suite at the merge gate. What the
  opt-in removes is the builder's blindness, not the gate.
- It catches tests that fail. It does not catch hollow tests that pass on the branch and fail
  on base for the wrong reason (source-text greps, set/read-back); only review or mutation
  catches those.
- Budget it: every verify attempt now pays a test run (gamedaytastic: about 1–2 min
  incremental, ~10 min cold).

**Corollary for the verify-cmd:** it must compile the test target, not just the app. On iOS
that is `xcodebuild build-for-testing`, which compiles tests without booting a simulator —
sim-independent *and* test-covering are not in conflict.

**Why test execution is not a dispatch lane** (decision, issue #37). Every other loud job —
spec, review, critique, diagnosis — is delegated to a cheap tier, so a "Test Runner" agent
lane looks like the consistent move. It was measured and rejected: across 35 gamedaytastic
partner transcripts, hand-run `xcodebuild`/`xcrun`/`simctl` accounted for 177 calls but only
~25K tokens — **2.4%** of everything the orchestrator ingested. Running a suite is
deterministic; an agent adds cost without adding capability. So the command lives in
`PROJECT.md § Test command` and the orchestrator shells out to it directly.

Revisit only if **triage** of failures becomes the expensive part — that is judgment work and
would earn a lane. Simply running the suite never will.

---

## Diff review — cheap-first, partner adjudicates (all tiers)

Every builder diff gets read for **intent and integration traps** — "does it do the thing,
match the spec, mock the right layer, stay in scope" — before merge. Compilation and tests
do not check intent; this rung is what catches the #72 class of bug.

Cost structure (this is deliberate — see RULES.md operating lesson 3):

1. **First pass runs on a cheap tier.** Dispatch a **read-only** review (`dispatch.sh
   --read-only` with `prompts/reviewer.md` rendered for the task) on the `standard` tier,
   or spawn a Sonnet subagent. The reviewer returns a verdict + findings, changes nothing.
2. **The lease-owning partner adjudicates only.** It reads the reviewer's findings against
   the spec and decides merge / reject / re-dispatch. It does not perform the line-by-line
   first pass itself. A `security: true` diff remains the explicit exception: its final
   adjudication must be performed by Opus as described below.

A diff merged without this rung is a gate violation regardless of tier.

### Reviewer family diversity (hard rule, 2026-07-17)

**The first-pass Reviewer must be a different model family than the builder backend that
produced the diff.** Same-family review reproduces the builder's blind spots: the reviewer
finds the diff reasonable for exactly the reasons the builder wrote it that way, and the
rung silently degrades into self-review.

| Diff built by | Allowed first-pass Reviewer |
|---|---|
| `claude` backend (sonnet/opus) | opencode `standard` tier (deepseek) — **not** the Sonnet subagent |
| `codex` backend (gpt-5.6-*) | either variant (opencode `standard`, or Sonnet subagent) |
| `opencode` backend (deepseek/glm/qwen) | either variant (Sonnet subagent, or opencode `standard` on a **different** family than the builder used) |

Cost note: the claude backend is the minority lane (second in `builder_backends`, reached
only after the codex ladder is exhausted), so forcing its diffs onto the opencode reviewer
costs ~zero in practice.

### Reviewer lane selection

Which of the *allowed* variants above gets used is the project's call: take the first entry of
`reviewer_backends` in `PROJECT.md` frontmatter (default `[opencode]`) that the diversity table
permits for this diff. **The table is a hard gate and config is only a preference** — if the
configured first choice would be same-family with the builder, skip it and take the next legal
entry. If no configured entry is legal, stop and tell the user; never fall back to same-family
review.

Note the default is deliberately *not* the flat-rate claude lane even for codex-built diffs,
where the table permits it — review volume against the shared 5h window would compete with the
orchestrator. Deferred pending the 2026-08-17 telemetry review; see `MODELS.md` § Project
configuration keys and issue #14.

---

## Pre-build critique — shift-left review (risk / security; decoupled from the tier, issue #50)

Diff review reacts: it runs *after* the builder has already burned budget, and it can only
find the gotcha once it is in the diff. The **pre-build critique** is the mirror rung — it
reads the **plan** for blast radius, lost behavior, and underspecification **before** dispatch,
which is the cheapest place to fix a design-level gotcha. It exists to catch the "just talk to
the Partner and let it go" failure: a technically-correct diff that passes verify and still
does the wrong thing or breaks something adjacent.

**Applies to** any build task with **`risk: true` or `security: true`** — whatever its origin
(a Tech Lead task spec, an Architect Stage-2 task, or a change the Partner talked itself into
conversationally). The spec author sets `risk: true` when any of the **project's risk
triggers** applies (`PROJECT.md § Risk triggers`; generic defaults: shared state or
invariants, a persisted format / contract / API, concurrency or timing, an open design
decision). `risk: false` tasks skip it **whatever their verify tier**. Every target-code
change flows through `dispatch.sh` except one the Partner authors inline, and that is allowed
only for a change that would be `risk: false` and not `security`
(`.claude/rules/pm-scope.md` § Inline authoring gate), so wiring the rung to the dispatch
boundary still catches the conversational path for free — see
`.claude/rules/dispatch.md` § Pre-Build Critique.

**Why not the tier any more.** Critique used to run on every R1/R2 task. With R2 defined as
"UI / user-visible data path", nearly every task in an app was R2, so a layout tweak that
needed a device check dragged a critic in with it. The gamedaytastic tier trial (issue #50):
T228 ran as R0 plus a device check with 0 escapes; on T144 the diff reviewer caught every
defect that mattered and critique caught none. Evidence and design risk are different axes.

**Legacy tasks** (no `risk:` field — every task written before #50) keep the old rule:
critique when `verify_tier` is R1/R2 or `security: true`. Upgrading the framework therefore
never silently drops a critique a task was planned under; set `risk: false` explicitly to
opt a legacy task out.

**Enforced, not advised (issue #18).** `dispatch.sh` refuses (exit 31) a build dispatch for a
PLAN.md task that needs critique until `logs/verdicts.jsonl` holds a `proceed` or `override`
adjudication newer than the task's latest critic verdict. Critic runs record their verdict
there themselves (a structured `CRITIC_RESULT` block, never parsed from prose); the Partner
records its decision with `scripts/review_gate.py adjudicate --model <its model>`.

The adjudication is bound to what it decided on (issue #57). It records the SHA-256 of the
task spec (`prompts/task-T0XX.md`, or `--spec <path>`), and the build gate refuses once that
file has changed or disappeared: re-critique the changed spec, or re-adjudicate it. It
records the adjudicating model, and for a `security: true` task the build gate accepts only an
Opus-class `proceed` (§ Security-sensitive tasks) or the operator's `override`. A critic
never runs as a build turn (`--role critic` without `--read-only` is exit 2), and only one
critic run per task is in flight at a time, so concurrent runs cannot share a round.

**Defect fixes carry their evidence (issue #46).** A Tech Lead defect-fix spec names
**Symptom**, **Mechanism** and **Evidence**, and the Evidence is an observed artifact cited by
path, never reasoning from source (`RULES.md` § Operating lessons, lesson 4). With only
reasoning, the Tech Lead specs an instrumentation task instead of the fix. The critic returns
`[BLOCKING-PLAN]` for a defect fix whose Evidence is missing, reasoning-only, or shows only the
symptom, so the existing gate holds it. The check rides on the critique, so it covers
`risk`/`security` tasks only; a `risk: false` defect fix carries the fields but nothing
checks them.

Cost structure — identical cheap-first / Opus-adjudicates split as diff review:

1. **The critique runs on a cheap read-only tier.** Dispatch `dispatch.sh --read-only --role
   critic` with `prompts/critic.md` rendered for the task. The critic returns a verdict +
   findings + a machine-readable result block, and changes nothing.
2. **The Partner adjudicates only.** It reads the findings against SPEC and decides
   dispatch / rework / escalate — it does not perform the plan critique itself at Opus rates.

### Critic family diversity (hard rule)

**The critic must be a different model family than whoever authored the plan** — the Tech Lead
(Sonnet) or the Partner (Opus). Same-family critique reproduces the author's blind spots, the
same failure the Reviewer's diversity rule guards against. Route the critique to the **codex**
backend (gpt-5.6-terra, `standard`) or **opencode** `standard` — never an Anthropic critic of an
Anthropic-authored plan. (This is the genuinely good use of the flat-rate codex lane in a review
role.)

Lane selection follows `critic_backends` in `PROJECT.md` frontmatter (default
`[codex, opencode]`), with the same precedence as the Reviewer: diversity is the hard gate,
config only orders the legal choices.

### Security floor

For `security: true` the critique runs at a **capable rung**, not the cheap tier: a non-Anthropic
reviewer at or above Sonnet capability (opencode `heavy`, glm-5.2) or a family-diverse Sonnet
subagent. **Fable is never used** (policy-restricted from security work — MODELS.md § Orchestrator).

### Adjudication

The Partner reads the critic's output:

- **BLOCKING findings must be resolved before dispatch** — fold them into the spec / re-plan via
  the Tech Lead and re-run the critic, or record an explicit **user override**
  (`review_gate.py adjudicate --outcome override --reason ...`, plus `critic_override` in
  TASK_LOG with the finding and the reason). `adjudicate --outcome proceed` is refused while
  the latest verdict carries a `[BLOCKING-PLAN]` finding. No silent proceed.
- **ADVISORY findings** are logged (`critic_returned`); fold in at the Partner's discretion.
- **`RECOMMENDED_VERIFY_TIER`**, if higher than the task's stated `verify_tier`, **raises it**
  (bias up) before dispatch.

**A risk/security task dispatched to a builder with an unresolved BLOCKING finding is a gate
violation**, exactly as a diff merged without the diff-review rung is — and `dispatch.sh` now
refuses it.

### Round caps — then the operator (issue #50)

Neither rung converges on its own: T211 ran **5** critique rounds and **10** reviewer fixup
rounds. Both are capped per task, counted from the verdict ledger (never from prose):

| Rung | Counts as a round | Default cap | Project key |
|---|---|---|---|
| Pre-build critique | every recorded critic verdict except `ERROR` (could not read the plan) | 2 | `critic_round_cap` |
| Reviewer fixup | every recorded review that did not approve (`REJECT`, any blocker, or `MALFORMED`) | 3 | `reviewer_fixup_round_cap` |

`dispatch.sh --role critic` refuses round cap+1; `--role reviewer` and the fixup build refuse
once non-approving reviews exceed the cap (the review of the last allowed fixup still runs).
A per-task round lock (`logs/locks/`, issue #57) is held from the cap check to the verdict
record, so a second concurrent critic/reviewer run on the same task is refused rather than
sharing a round; the lock dies with the dispatch, so there is nothing stale to clear.
All refusals are exit 31 — a policy stop, never a `failure_count` event. The Partner then
escalates to the operator with a Gate-2-style choice (`.claude/rules/dispatch.md` § Round
caps): **redesign** (re-spec, usually as a new task), **override** (a logged
`review_gate.py override-cap --reason ...` or `adjudicate --outcome override`), or **abort**.

---

## Security-sensitive tasks — the one place cheap-first is wrong (2026-07-17)

A task carries `security: true` when its diff touches **auth, credentials, keychain,
entitlements, network trust, sandboxing, or input validation on data from outside the
app**. The Tech Lead sets the flag in its result metadata; the Architect sets it for
Stage-2 tasks. The orchestrator records it on the task in PLAN.md alongside `verify_tier`
and states it in the task prompt. **Bias up when unsure** — the flag is cheap, a miss is not.

Effect — the review rung is forced up, in two places:

1. **First-pass review runs on Sonnet minimum.** The cheap opencode tier is not an
   acceptable first pass for a security diff. Use a Sonnet subagent (or higher), or an
   opencode reviewer only *in addition to*, never *instead of*, the Sonnet rung.
2. **Adjudication is mandatory Opus.** A Claude partner on Opus performs it directly. A
   Codex partner requests the exceptional Claude adjudication defined in `ORCHESTRATOR.md`
   and records its result; if Opus is unavailable, the task remains blocked. **Fable must
   never be used** for security adjudication or security review.
   For the pre-build critique this is enforced (issue #57): `review_gate.py adjudicate
   --outcome proceed` on a `security: true` task is refused unless `--model` is Opus-class
   (`opus`, `claude-opus-*`; never Fable, never via OpenRouter), and the build gate refuses a
   security task whose clearing adjudication names another model. The operator's logged
   `--outcome override --reason ...` stands whatever model records it. Adjudications
   recorded before #57 carry no model and are accepted as legacy, with a warning. The
   check is `review_gate.security_floor_problem`.
   At merge time it is enforced too (issue #58): after the Sonnet-or-higher first-pass review
   approves the commit, the Opus partner records its adjudication of that review with
   `merge_gate.py adjudicate --task T0XX --dir <worktree> --model <opus model>`. The call is
   refused for a `security: true` task unless `--model` is Opus-class, and `merge_gate.py
   check` refuses a security task (`security` check) without such a row **at the commit being
   merged** — the same `security_floor_problem` test, applied to the `merge_adjudication` row,
   so a hand-written or pre-flag row from another model still fails. The operator's decision
   to merge without it is `merge_gate.py override --check security --reason ...`. The row
   type is new, so there is no legacy form: an in-flight security task needs one adjudicate
   call (or the override) before it merges. The Sonnet-minimum first pass is still the
   orchestrator's routing choice (the reviewer's model is recorded, not ranked).

Where this collides with the family-diversity rule above (a claude-built security diff),
both gates apply: use a non-Anthropic reviewer at or above the Sonnet capability rung in
addition to the Sonnet pass. If no legal diverse reviewer is available, the task remains
blocked. Never waive diversity or resolve the collision by dropping to the cheap tier.

**Rationale:** a missed security bug does not fail a verify loop — it ships, silently, and
the cost lands later and outside the project. Every other rung in this file assumes a
failure surfaces as a red build or a wrong pixel; security failures surface as nothing at
all. That asymmetry is why this is the one category where cheap-first review is the wrong
bias, and why the token cost of an Opus read is not a consideration here.

---

## R1 rules — real inputs through the real path

- **Never mock the boundary under test.** Mocks above the boundary (to isolate logic that
  *consumes* it) are fine; the decode/transport/persistence code itself must run for real.
- **No replicas.** A test that rebuilds its own decoder/client "equivalent to" production
  stays green when production drifts (the #74 near-miss). The test must call the
  production function. If the dependency isn't injectable, stub the transport *underneath*
  it and feed the fixture through the real call.
- **Fixtures are captured, not invented.** Pull representative inputs from the live system
  and commit them under the target project's test fixtures directory with a note of the
  capture date and source.

## R2 rules — run it and observe

- **Reproduce the triggering interaction**, not just a static start. A scroll bug needs a
  scroll; a request bug needs the request. Synthetic input is valid for presence/absence,
  weak for precise timing — flag precise checks as a human pass.
- **Races need N cold runs.** For start-up timing / nondeterministic output: restart
  **5–10×**; pass only if the defect never appears. A single observation is not a valid
  pass for a race.
- **Demand a named mechanism, not a bundle.** A race fix must state *why* it is now
  deterministic. Two or three "complementary" changes shipped hoping one wins is a tell
  the race wasn't pinned — reject and ask for the mechanism.
- **Verify the data source before the output.** Before judging what is shown, confirm the
  component actually receives the data it should. An empty data source mimics rendering
  bugs and burns cycles (RULES.md operating lesson 1).
- **Run a configuration where the real data path runs.** A build that silently falls back
  to demo or cached data is not a valid R2 pass.
- **Scope discipline:** one bug = one mechanism per PR. Split unrelated changes so the
  fix's effect is attributable.

Platform-specific R1/R2 practice (stubbing `URLProtocol` under `URLSession`, simulator
cold-launch loops with `scripts/app_screenshot.sh`, the unsigned-simulator Keychain trap)
lives in the project's policy; `Docs/examples/policy-ios.md` carries the iOS version.

---

## Merge gate — pin the tree, observe the change (issue #35)

The ladder and the review verify *a* tree. Until #35 nothing said *which*: gamedaytastic T078
ran its full ladder green, with genuine mutation evidence and an APPROVE, on one commit — and
merged the next, which had reverted the production fix while "guarding" the tests. Net
production diff: empty. Every gate did its job against the wrong input.

`scripts/merge_gate.py` binds every piece of merge evidence to a commit SHA, and refuses
(exit 31) unless, **at the commit being merged**:

| Check | Passes when | Evidence comes from |
|---|---|---|
| `verification` | every rung ever verified for the task (`--label`) passed on this commit | `merge_gate.py verify` — runs `PROJECT.md § Test command` (or `--cmd`) in a **clean** checkout |
| `review` | the latest reviewer verdict for this commit approves, with no blockers, and read a clean tree | `dispatch.sh --role reviewer` (records `head_sha`/`tree_clean`), or `review_gate.py record --head-sha` for a subagent review |
| `production_diff` | the net diff merge-base..commit is non-empty and touches a production path — unless the task is `observation: none` | git; path classes are project policy |
| `observation` | `test`: a passing fail-on-base run on this commit · `runtime`: an observation recorded on this commit · `none`: nothing more | `merge_gate.py fail-on-base` / `observe` |
| `security` | `security: true` only: an Opus-class adjudication of this commit's approving review (§ Security-sensitive tasks) | `merge_gate.py adjudicate --model` |

Nothing carries forward: a commit after the evidence voids all of it, and the refusal lists the
commits since. `merge_gate.py merge` runs the check and then `gh pr merge --match-head-commit
<sha>`, so GitHub refuses the merge if the PR head moves after the check. The evidence lives in
`logs/verdicts.jsonl` beside the reviewer verdicts it has to be joined with. The orchestrator's
procedure is `.claude/rules/dispatch.md` § Merge gate.

**The base is fetched, not trusted (issue #58).** A remote-tracking `--base origin/<x>` is
fetched by the gate before the merge base is computed (`--no-fetch` skips it and records a
warning; a failed fetch is a refusal, not a silent stale base). A local `--base <x>` whose
upstream has commits it lacks is refused with the fix; a local branch with no upstream, a tag
or a SHA is used as given, with a warning. Pass `origin/<base>`.

**Closing the task (issue #58).** A task carrying the `observation:` field — every build or
inline task written since #35 — may only be marked `done` (through `plan-update.py`) once its
latest `merge_gate.py check` passed or its `merge` succeeded, so a task can no longer be
closed at PR-open time or on a refused check. The two legitimate closes without a merge are
operator decisions, recorded before the PLAN write: `merge_gate.py exempt-close --task T0XX
--kind gate2-skip|state-correction|operator --reason "..."` (lease owner). Designer,
architect and user tasks, and legacy tasks with no `observation:` field, are not gated;
`superseded`/`failed` transitions are not closes.

**Net-empty production diff.** A path is *test* if it matches `PROJECT.md § Test paths`,
*non-production* if it matches `§ Non-production paths`, and *production* otherwise (generic
defaults: common test directories and `*Test.*`/`*_test.*`/`*.spec.*` names; Markdown, docs
directories, licences and changelogs). The diff is taken **net**, so a fix that a later commit
reverts does not count. A task that really changes no behaviour declares `observation: none`.

### Fails on base, passes on the branch (issues #35, #46, #21)

A green suite is not evidence that a change took effect — it was green before the change
too. Every task's spec names **one observation that fails on the base tree and passes on the
branch**, recorded on the task as `observation: test | runtime | none`:

- **`test`** — a new or changed test and `observation_cmd`, the single command that runs it.
  `merge_gate.py fail-on-base` checks out the merge base in a temporary worktree, overlays the
  branch's test-path files (and removes the ones it deleted), runs the command there and
  requires it to **fail**, then runs it on the branch and requires it to **pass**. A test that
  passes on the base tree observes nothing and is refused; so is a diff that changes no test.
  A failure that is a compile or harness error is weaker evidence than an assertion failure —
  `--expect-fail-pattern` pins the expected one. Test wiring that lives in a production file
  (an Xcode `project.pbxproj` registering the new test file) is overlaid too when the project
  declares it under `PROJECT.md § Test support paths` (issue #58): without it the base run
  fails on the harness ("no such test") rather than the assertion, or does not run the test at
  all. A support file stays a production path for the production-diff check and never counts
  as the changed test. `--overlay <path>` remains for a one-off test file outside the declared
  classes. This is the red-first proof #21's T073 asked
  the builder for, run by the orchestrator, where a builder's claim cannot stand in for it.
- **`runtime`** — only visible by running the product. The orchestrator (or operator)
  observes it on the commit being merged and records it with `merge_gate.py observe
  --evidence <artifact> --summary "..."`. What counts is **project policy**
  (`PROJECT.md § Observations`; generic default: pinned to the merged SHA, the captured
  artifact cited, before and after shown, never a builder's or reviewer's report).
- **`none`** — no behaviour change (refactor, docs, test-only). The only kind whose net diff
  may carry no production change.

The Tech Lead and Architect name the observation in the spec; the critic blocks a plan without
one; the reviewer checks the net diff still carries the change it depends on.

**Escape hatch.** `merge_gate.py override --check <name> --reason "..."` records the operator's
decision to waive one check for one commit (lease owner; a new commit voids it). Log it as
`merge_gate_override`.

**Legacy default.** A task with no `observation:` field — every task written before #35 —
skips the observation check with a warning. The pin, review-at-SHA and production-diff checks
apply to every task: they are about the tree, not the spec. A reviewer verdict recorded before
this change has no `head_sha`; re-review the commit or override `review` with the reason. A
task that carries the field but leaves it `null` was never decided and is refused.

---

## Project policy — the framework enforces, the project decides (issue #50)

The framework owns the mechanisms: the critique gate, the round caps, the verdict ledger and
the evidence ladder. What they apply to is **project policy**, declared in the project's own
`PROJECT.md` (setup writes it once; the installer never touches it):

- frontmatter `critic_round_cap`, `reviewer_fixup_round_cap` — positive integers;
- `## Verify tiers` — one bullet per tier, `- R0: <what evidence proves this>`, all of R0–R2;
- `## Risk triggers` — one bullet per trigger that sets `risk: true`;
- `## Observations` — one bullet per rule for what counts as a runtime observation (#35);
- `## Test paths`, `## Non-production paths` — one glob per bullet; they classify the net diff
  for the merge gate (#35). A glob with no `/` except a trailing one matches at any depth,
  `**` spans directories, a trailing `/` means everything under it. Declaring a class
  replaces its default.
- `## Test support paths` — same glob syntax, default none: production files that carry test
  wiring, overlaid onto the base tree by fail-on-base (#58). Policy, not a per-call flag,
  because which files wire tests is a property of the project's build system, and a flag the
  orchestrator must remember on every call is the kind of discipline that gets skipped.

Everything is optional; absent parts use the generic defaults in
`scripts/project_policy.py`. `orchestrator-doctor.py` fails on a malformed declaration, and
`project_policy.py --pm-dir . render` produces the block the Tech Lead, Architect, critic and
reviewer prompts receive as `{{PROJECT_POLICY}}` (`dispatch.sh` fills any placeholder left
unrendered). A project changes its policy by editing PROJECT.md — never by forking the
framework. Policy that a second project also needs, or that only works when enforced,
graduates into the framework.

---

## Enforcement

- Task specs and PLAN.md tasks carry `verify_tier: R0|R1|R2`, `risk: true|false` and
  `security: true|false`. `risk` or `security` requires the pre-build critique (enforced by
  `dispatch.sh`); a `security: true` task also forces the review rung up (Sonnet-minimum
  first pass, mandatory Opus adjudication — see Security-sensitive tasks above).
- The orchestrator does not merge until the tier's full ladder has passed and the diff
  review verdict is recorded — and `scripts/merge_gate.py check` (or `merge`) passes on the
  exact commit being merged (§ Merge gate). Tasks also carry `observation: test|runtime|none`
  (+ `observation_cmd` for `test`). `plan-update.py` refuses to close such a task without
  that pass or a recorded exemption (issue #58).
- Genuinely device-only checks (GPU effects, haptics, perf feel) are flagged as an
  explicit human pass — never silently skipped, never auto-passed.
