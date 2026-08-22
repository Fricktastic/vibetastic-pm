# Dispatch

## Task Dispatch Loop

```
loop:
  read PLAN.md
  ready = tasks where status == pending AND all depends_on tasks have status == done
  if no ready tasks:
    if all tasks done → project complete (report to user, stop)
    else → blocked state (investigate and report to user)
    break
  for each task in ready (parallel only if same stage and no inter-dependencies):
    if task.verify_tier in {R1,R2} or task.security → run Pre-Build Critique first (below)
    dispatch task
    on return → apply result (see state.md)
  re-evaluate ready tasks
```

---

## Designer

Invoke at Stage 1, and again mid-project for any new UI work (new screen, new component, visual design decisions). Skip for bug fixes and non-UI changes.

Read `framework/prompts/designer.md`. Substitute:
- `{{SPEC_CONTENT}}` → full body of `SPEC.md`

For mid-project invocations, also prepend a brief description of the specific UI addition so the Designer scopes to the new work only.

Spawn a fresh Agent with the rendered prompt.

After return:
- **Stage 1:** Write output verbatim to `prompts/design-spec.md`
- **Mid-project:** Append as a new section; note the addition in TASK_LOG
- Append `agent_returned` + `task_completed` to TASK_LOG
- Update task in PLAN.md: `status: done`, `completed_at`

---

## Architect

Read `framework/prompts/architect.md`. Substitute:
- `{{SPEC_CONTENT}}` → full body of `SPEC.md`
- `{{DESIGN_SPEC_CONTENT}}` → full contents of `prompts/design-spec.md`
- `{{TARGET_PROJECT_PATH}}` → absolute path to `../<project-name>/`

Spawn a fresh Agent with the rendered prompt.

After return, parse on delimiter `<!-- ARCHITECT_RESULT_START -->`:
1. Everything **before** the delimiter → write to `prompts/build-spec.md`. **Written once, never appended to again.**
2. YAML block **after** the delimiter → extract `selected_tier`, read `framework/MODELS.md` to resolve the `model` and `fallback` columns for that tier, write to `tasks[n].model` and `tasks[n].fallback_model` in PLAN.md; log `model_fallback_used` if true. Also extract `security` and write it to `tasks[n].security` (default `false`); for a multi-task build spec, apply the per-task flag noted in each task section.

Then: append `model_selected`, `agent_returned`, `task_completed` to TASK_LOG. Update task: `status: done`, `completed_at`.

Missing delimiter or malformed YAML → treat as parse failure (increments `failure_count`).

---

## Tech Lead

Invoke when new work is identified not already specced in `prompts/build-spec.md`: user-reported bugs, new requirements, Gate 2 fix-before-retry, follow-on work revealed by a completed task.

**Routing rule — always apply:**
- New screen, new UI component, or any feature with visual design decisions → **Designer first, then Tech Lead**
- Bug fix or non-UI change → **Tech Lead directly**

Do not create a PLAN.md task without first running Tech Lead (unless trivially covered by existing spec). Do not run Tech Lead on UI work without a Designer pass first.

Read `framework/prompts/tech-lead.md`. Substitute:
- `{{ISSUE_DESCRIPTION}}` → bug/requirement as described by the user (or PM's failure analysis)
- `{{BUILD_SPEC_CONTENT}}` → full contents of `prompts/build-spec.md`
- `{{PLAN_SUMMARY}}` → one line per task: id, title, status, notes
- `{{TARGET_PROJECT_PATH}}` → absolute path to `../<project-name>/`
- `{{ERROR_OUTPUT}}` → full stderr/stdout from a failed task, or "none"
- `{{SPEC_OUTPUT_PATH}}` → **absolute** path the agent must write the spec to:
  `<pm-dir>/prompts/task-T0XX.md` (use the next free id; rename after PLAN.md registration
  if it changed)

**[0g] The Tech Lead writes the spec file itself — the orchestrator never holds the body.**
Previously the agent returned the whole spec and the orchestrator re-emitted it through a
`Write`, so every spec crossed the most expensive context in the system twice (~50k tokens
in one observed session for four specs), rendered in the operator's terminal as an apparent
uncommitted Swift diff, and passed through a hand-transcription step that silently
HTML-escaped Swift generics and closure types.

After the agent returns:
1. Verify `{{SPEC_OUTPUT_PATH}}` exists and is non-empty. If not, that is a failed dispatch —
   do **not** accept an inline spec as a substitute.
2. Parse only the returned YAML block to register the task in PLAN.md.
3. Never read the spec body into context. The critic and the builder both read it from disk.

**This is now mechanical.** A `PreToolUse` hook (`framework/scripts/spec-body-guard.py`,
wired by setup.sh) **blocks** a whole-file `Read` or `cat` of `prompts/task-T*.md` /
`prompts/critic-T*.md`. It exists because the rule above was correct, stated its own
rationale, and was violated **217 times** on gamedaytastic — ~367K tokens, **37% of
everything the orchestrator ingested**, the single largest line item in partner burn
(issue #39). Instruction-based discipline degrades over a long session; this is the same
lesson that produced `plan-lint-hook.py`.

Still allowed, because steps 1–2 need them: `test -s` / `wc -c` existence checks, `grep`,
`awk`, `sed -n` YAML extraction, and `head`/`tail`/`Read limit` up to 60 lines. Deliberate
exceptions: `SPEC_BODY_GUARD_OFF=1`, justified in the TASK_LOG entry — same convention as
`DISPATCH_ALLOW_NO_VERIFY=1`.

Projects onboarded before the hook shipped do not have it (issue #16): check
`.claude/settings.json` for a `PreToolUse` entry matching `Read|Bash` and add it by hand if
absent.

Spawn a fresh Agent with the rendered prompt.

After return, parse on delimiter `<!-- TECH_LEAD_RESULT_START -->`:
1. Everything **before** the delimiter → write to `prompts/task-T0XX.md` using the assigned task id. Do not append to `prompts/build-spec.md`.
2. YAML block **after** the delimiter → create new task in PLAN.md:
   - `task_title` → `title`
   - `branch_name`, `issue_refs` → store in `notes`
   - `depends_on` → `depends_on`
   - `suggested_tier` → look up `model` and `fallback` columns in `framework/MODELS.md` for that tier; write to `model` and `fallback_model`
   - `security` → write to `security` on the task (default `false` if absent). A `security: true` task forces the review rung up at merge time (Sonnet-minimum first pass, mandatory Opus adjudication — see `framework/VERIFY.md`).
   - Assign next available task id
   - Set `status: pending`, `agent: opencode`, `failure_count: 0`

Then: append `tech_lead_returned` + `task_created` to TASK_LOG. New task enters the normal dispatch loop.

Missing delimiter or malformed YAML → treat as parse failure.

---

## Pre-Build Critique

The shift-left review rung — it reads the **plan** for gotchas before a builder writes code.
Run it **before dispatching any build task at `verify_tier` R1/R2 or `security: true`** (R0 /
isolated tasks skip it). Because every target-code change goes through this dispatch flow, the
rung also covers changes the Partner talked itself into conversationally — there is no other
path to the target code. Full rules: `framework/VERIFY.md` § Pre-build critique.

1. **Render** `framework/prompts/critic.md`:
   - `{{PLAN}}` → **absolute** path to the task's prompt file
     (`<pm-dir>/prompts/task-T0XX.md`, or the extracted build-spec section). **Never
     relative.** This dispatch runs from the target project directory, so a PM-relative path
     does not resolve — and the critic then reviews what it can see and returns a confident
     `REWORK`. Measured: 5 critic runs could not read their plan; all 5 returned REWORK, four
     of them the same task inside twelve minutes (issue #11).
   - `{{VERIFY_TIER}}`, `{{SECURITY}}` → the task's flags from PLAN.md
   - `{{TARGET_PROJECT_PATH}}` → absolute path to `../<project-name>/`
   - `{{PRIOR_FINDINGS}}` → on round 1, `none`. On round 2, the previous round's FINDINGS
     block verbatim plus one clause each on how the re-spec answered it.
2. **Dispatch read-only, family-diverse from the plan's author** (Tech Lead Sonnet or Partner
   Opus → route to the codex or opencode `standard` critic; `security: true` forces a capable
   rung — see VERIFY.md). Take the first entry of `critic_backends` in `PROJECT.md` (default
   `[codex, opencode]`) that satisfies the family-diversity rule; diversity wins over the
   configured order. Same wrapper the Reviewer uses:

   ```bash
   bash framework/dispatch.sh --read-only <critic-model> ../<project-name>/ prompts/critic-T0XX.md 2>&1
   ```

3. **Adjudicate (Partner):** read the verdict.
   - **`ERROR`** → the critic could not read the plan. This is a **configuration failure, not
     a finding**: fix the `{{PLAN}}` path and re-run. It does not count as a round and must
     never be treated as REWORK.
   - **`REWORK` / any `[BLOCKING-PLAN]` finding** → resolve before dispatch: re-spec via the
     Tech Lead, or record an explicit user override (`critic_override` in TASK_LOG with
     finding + reason). Do **not** dispatch the build with an unresolved `[BLOCKING-PLAN]`
     finding — it is a gate violation.
   - **`[BLOCKING-PREEXISTENT]`** → a real defect that this plan neither causes nor worsens.
     **Register it as its own PLAN.md task** (pending, same read-write-cycle discipline) and
     **proceed with this one.** It is not this task's blocker.
   - **`[ADVISORY]`** findings → log; fold in at discretion.
   - `RECOMMENDED_VERIFY_TIER` higher than the stated tier → raise `verify_tier` on the task.
   - Append `critic_returned` to TASK_LOG. Only then proceed to the build dispatch below.

**[0e] Round limit — two, then the operator.** A critic with no stopping rule does not
converge: observed four-round REWORK loops on T024, T070 and T071, with later rounds
surfacing pre-existing defects rather than plan defects. After **2 critic rounds on one
task**, stop and escalate to the operator with both rounds' findings and your recommendation.
Do not open round 3.

**[0h] Collapsing rounds — evidence type, not confidence.** A well-diagnosed bug should not pay
the full toll, but "I already know the root cause" is self-certifying and must never be the
gate. Type the evidence instead:

- **Observed runtime evidence** — a device/console log, an instrumentation dispatch, or a
  failing test that pins the branch taken *and* the values it was taken on. Either the artifact
  exists and is cited in the task spec, or it does not.
- **Reasoning from source** — however convincing, and regardless of how confident the author is.
  This never qualifies. It is what cost four sessions on one defect (RULES.md lesson 4).

With observed evidence in hand, the spec **skips re-deriving the root cause**: state it in one
paragraph, cite the artifact, and move to the change. Do not commission a diagnosis dispatch to
confirm what the log already proves.

**What never collapses:** the pre-build critique's placement and blast-radius pass. It answers a
different question than diagnosis — *given the right root cause, is this the right edit, and what
working behavior does it endanger?* Field evidence (gamedaytastic T073, a one-line fix on a
shared audio path): round 1 caught that the plan's acceptance criteria required no regression
test and that Apple Music's pause-time snapshot was at risk; round 2 caught that the guard as
specified would land in the shared writer `updateProgress(seconds:)` and break that snapshot plus
trim-relative `seek(to:)`, and that an existing test would go red. The root cause was never in
doubt in either round. A one-line diff is not a small change when the line sits in shared state.

**Corollary — diff size is not a process input.** It is unknowable when the spec is written and
uncorrelated with blast radius. Route on `verify_tier` and `security`, as the gates already do.

This bound is what makes the critique gate safe to treat as a hard precondition for dispatch
(including under parallel fan-out, where an unbounded critic stalls a task indefinitely
instead of blocking it). Log `critic_escalated` with `rounds: 2` and the unresolved findings.

---

## OpenCode

Do not spawn an Agent. Execute via the dispatch wrapper.

**`framework/dispatch.sh` is read-only. Never modify it. Report issues to the user.**

OpenCode always receives a task-scoped file at `prompts/task-T0XX.md`:

- **Tech Lead tasks:** PM writes the file directly from Tech Lead output (before delimiter). Pass straight to dispatch.
- **Architect-generated tasks (Stage 3):** Extract the task section from `prompts/build-spec.md` with awk:

```bash
TASK_ID="<tasks[n].id>"
TASK_PROMPT="prompts/task-${TASK_ID}.md"

awk -v id="${TASK_ID}" '
BEGIN { mode = "preamble" }
/^## OpenCode Execution Notes/ { mode = "notes"; print; next }
/^## T[0-9]/ {
  if (mode == "preamble") { mode = "skip"; next }
  if (mode == "task") { exit }
  if ($0 ~ ("^## " id "( |$)")) { mode = "task"; print; next }
  mode = "skip"; next
}
mode == "preamble" || mode == "notes" || mode == "task" { print }
' prompts/build-spec.md > "${TASK_PROMPT}"
```

Look up `tasks[n].model` and `tasks[n].fallback_model` in PLAN.md, resolved from the task's
current `tier` **and current `backend`** (see Backend & Tier Escalation below). The backend
is the first entry of `builder_backends` in `PROJECT.md` (default `codex`) unless the task
has already cross-backend-escalated; resolve the tier→model mapping from that backend's
column in `framework/MODELS.md` (codex slugs may carry `@<effort>`; codex/claude backends
have no fallback model — pass `""`). Read the `Verify command` from `PROJECT.md` — the
sim-independent command that proves a task didn't break the project (e.g. the Hometastic
generic/iOS build). If `PROJECT.md` has no `Verify command`, pass an empty verifier and the
loop degrades to the legacy single-run behavior.

Dispatch:

```bash
bash framework/dispatch.sh --worktree "<branch>" <tasks[n].model> ../<project-name>/ "${TASK_PROMPT}" "<tasks[n].fallback_model>" "<verify-cmd>" 3 "<tasks[n].tier>" 2>&1
```

**Issue this through the Bash tool with `run_in_background: true`. Never `&`, `nohup`, or
`disown`.** Both forms are "a background process" in the plain-English sense, and they behave
nothing alike:

| Form | What happens when the dispatch finishes |
|---|---|
| Bash tool, `run_in_background: true` | The harness tracks the process and **re-invokes the session on exit**. The dispatch loop continues on its own. |
| Shell `&` / `nohup` / `disown` | The process is **detached and untracked**. Nothing ever wakes the session. The orchestrator parks until the operator types. |

The failure is silent and self-reinforcing: a parked orchestrator and a legitimately gated one
look identical to the operator, whose only recourse either way is to ask "is it done?". Field
symptom (gamedaytastic, 2026-08-19): the orchestrator dispatched a build and sat idle until
prompted, for a task with no gate in front of it. `CLAUDE.md` had named the goal
("background the long pole") since 2026-07-02 without ever naming the mechanism, and
`economy.md` forbids polling — so a detached dispatch left no legal way to make progress.

Corollary: if you find yourself wanting to poll for a dispatch's completion, that is the
signal the dispatch was backgrounded wrong. Fix the dispatch; do not add a monitor loop
(`.claude/rules/economy.md`).

- `--worktree <branch>`: **always pass it for build tasks.** The builder runs in an isolated
  git worktree (`../<project-name>-worktrees/task-T0XX/`) on `<branch>`, never in the live
  checkout — the human's uncommitted work is untouchable and parallel dispatches can't
  collide. `<branch>` is the task's `branch_name` from its notes (Tech Lead tasks), or
  `task/<task-id>` if the task has none. dispatch.sh creates the branch from current HEAD if
  it doesn't exist, reuses the worktree on a re-dispatch (tier escalation) — including when
  the branch is already checked out under a *different* prompt name, so fixup dispatches onto
  an open PR branch reuse the existing worktree instead of failing — and prints the
  path to stderr as `[dispatch] worktree: <path>`. Worktree builders run with gh
  unauthenticated and the worktree's `remote.origin.pushurl` poisoned (per-worktree config),
  so they cannot push or open PRs — that stays the PM's job. Push the branch from the **live
  checkout** (`git -C ../<project-name>/ push origin <branch>`) before `gh pr create`, or
  first `git -C <worktree> config --worktree --unset remote.origin.pushurl`. Also: a builder
  that exits non-zero after self-committing completed work (new commits, clean tree) is
  salvaged — dispatch.sh skips the fallback and sends the committed state to the verifier.
  All other post-dispatch steps (staged-change check, commit) run **in that worktree path**,
  not the live checkout; after the PR is opened, remove it:
  `git -C ../<project-name>/ worktree remove <path>`. Read-only
  review/diagnosis dispatches may target either the worktree (to review its diff) or the
  live checkout, and don't need `--worktree` themselves.

- 4th arg `fallback_model`: if empty, pass `""` so the verifier stays positionally correct.
- 5th arg `verify-cmd`: the single-line verify command from `PROJECT.md` § Verify command.
  dispatch.sh runs it in the target dir after the builder writes files and, on failure, feeds
  the verifier output back into the same builder session and retries — entirely in bash,
  costing no PM tokens.
  **Mandatory. Read it from `PROJECT.md` every dispatch; never omit it, never pass `""`.**
  dispatch.sh now refuses a build dispatch without it (exit 2). Field data: 86% of one
  project's dispatches and 52% of another's carried no verifier, so the self-correct loop
  never ran and the whole tier/backend escalation ladder — which is driven by exit 20 — fired
  4 times in 307 runs. What that actually means is that correctness silently moved into this
  session, at peak cost: with no verifier, "did it work?" gets answered by the orchestrator
  reading code or running the build by hand. `DISPATCH_ALLOW_NO_VERIFY=1` exists for
  deliberate exceptions and must be justified in the TASK_LOG entry.
- 6th arg: max verify attempts (default 3).
- 7th arg `tier`: the task's current tier (`fast`/`standard`/`heavy`). **It selects the
  model.** dispatch.sh resolves tier→model from `MODELS.md` for the given backend and
  **refuses (exit 2)** when the passed model contradicts the passed tier; a missing tier on a
  build dispatch is also refused (`DISPATCH_ALLOW_NO_TIER=1` overrides). Within-rung effort
  bumps still match — `heavy` accepts `sol@low`/`@medium`/`@high` — and on the claude backend
  the alias and the pinned slug are one lane (`sonnet` ≡ `claude-sonnet-*`).

  It was telemetry-only until issue #41, and the consequence was that the ladder was
  decorative: **zero** `tier_escalated`/`backend_escalated`/`backend_skipped` events across
  307+ field dispatches, `tier` missing on 78% of runs, and 11 runs whose tier contradicted
  `MODELS.md` (`standard` on `sol@high`, `fast` on `terra`). Also recorded in
  `logs/cost.jsonl`. Also append
  a `cost_event` to TASK_LOG before dispatching (see `state.md` → Cost telemetry).
  **Always pass it.** It was missing on 79% of field dispatches, which is why `cost-report.sh`
  cannot attribute cost by tier today and why the tier table in `MODELS.md` rests on a
  minority of runs. dispatch.sh warns (does not refuse) when it is absent.

- **codex + iOS/Xcode tasks** (`CODEX_EXTRA_WRITABLE_ROOTS`): on the codex backend the
  builder's in-sandbox `xcodebuild` is denied SwiftPM-cache and DerivedData writes, so it
  can't iterate against build errors. For an iOS build task, export
  `CODEX_EXTRA_WRITABLE_ROOTS` (colon-separated absolute paths — the SwiftPM cache dir e.g.
  `$HOME/Library/Caches/org.swift.swiftpm` and the DerivedData root) on the dispatch command;
  dispatch.sh adds them to codex's least-privilege `writable_roots`. Name the specific cache
  dirs, not `$HOME/Library`. Caveat: CoreSimulatorService is a Mach service and can't be
  granted under workspace-write, so simulator-dependent tests still won't run in-sandbox — the
  out-of-sandbox verify (5th arg) stays the correctness gate for those.

Capture exit code and full stdout/stderr. dispatch.sh writes the complete opencode + verifier
log to a per-run file under `logs/` and prints its path to stderr; on any non-zero exit it
echoes the last 40 lines so a failure is never silent.

**Branch on dispatch.sh exit code:**

| Exit | Meaning | PM action |
|------|---------|-----------|
| `0` | Ran and (if a verifier was set) it passed | Proceed to the staged-change check, then PR Opening |
| `20` | Code runs but the verifier never passed within the attempt budget | **Tier escalation** (below) — not a `failure_count` event |
| `30` | Backend unavailable (CLI missing/unauthenticated, or quota exhausted) | **Backend skip** — re-dispatch same tier on the next backend in `builder_backends`; log `backend_skipped`; not a `failure_count` event |
| other non-0 | builder infra/model failure (even via fallback) | Task failure — see `state.md` (`failure_count +1`) |

**Exit 0 — staged-change check before opening PR** (run in the worktree path dispatch.sh
printed, not the live checkout):

```bash
git -C <worktree-path> diff --cached --quiet
git -C <worktree-path> status --short
```

- Staged uncommitted changes → spawn a `haiku` subagent to commit with an appropriate message (mechanical — no reasoning needed), then proceed to PR Opening
- Clean working tree with commits → proceed to PR Opening
- Nothing committed at all (no new commits vs. branch base) → treat as task failure, do not open PR

---

## Backend & Tier Escalation (two axes)

The cost lever: start on the primary flat-rate backend at the cheapest reasonable tier and
climb only when the verifier proves the model couldn't do the job. **Flat-rate capacity is
exhausted before metered tokens** — that is why `builder_backends` defaults to
`codex → claude → opencode` (see `framework/MODELS.md` → Builder Backends).

**Axis 1 — tier, within the current backend.** Ladder: `fast` → `standard` → `heavy`.

- A task's **starting tier** is the Tech Lead / Architect `suggested_tier`; bias toward
  `fast` — escalation is the safety net.
- On dispatch.sh **exit 20** below `heavy`: bump `tier`, re-resolve `model` (+`fallback_model`,
  opencode only) from the current backend's column in `framework/MODELS.md`, append
  `tier_escalated` (from→to tier, verifier tail), re-dispatch. No `failure_count` change.
- On codex, tier rungs are model-size steps (luna → terra → sol@low); exit 20 at `heavy`
  gets one effort bump (sol@low → sol@medium), then one **burn-gated** bump
  (sol@medium → sol@high) before the backend counts as exhausted. The @high bump fires only
  if the current ISO-week burn proxy in `logs/cost.jsonl` is **below**
  `codex_weekly_burn_threshold` — read from `PROJECT.md` frontmatter, falling back to the
  framework default if the key is absent (`framework/MODELS.md` § Codex tier column); at/above it,
  skip @high and `backend_escalated` to the claude backend immediately. Log the skip reason
  in the escalation event. **The burn gate is enforced in dispatch.sh, not by discipline (issue #41).** It computes
  the current ISO-week codex proxy from `logs/cost.jsonl` itself, reads
  `codex_weekly_burn_threshold` from `PROJECT.md`, **exits 30** when the week is at/above the
  line (treat as `backend_escalated`), and stamps the figure into the run's own `cost.jsonl`
  row as `burn_proxy`. It also **refuses a first-attempt `@high`** — `@high` is the terminal
  rung, reachable only after a prior exit-20 on the same prompt
  (`DISPATCH_ALLOW_UNLADDERED_HIGH=1` overrides) — and refuses `@high` on any `--read-only`
  dispatch, since diagnosis is cheap-tier work. Still log `burn_proxy` in the `cost_event`
  for role attribution; the enforcement no longer depends on it. All 6 field `@high`
  dispatches were first-attempt direct picks totalling 21.5M input tokens, ~37% of that
  ISO-week's codex burn, and `cost-report.sh` flagged every one of them *after* the spend. Efforts above high are never
  auto-dispatched (weekly-cliff guard — Gate 2 only).

**Axis 2 — backend, when the current backend's ladder is exhausted or unavailable.**

- Exit 20 at `heavy` (post effort-bumps on codex — sol@medium, then burn-gated sol@high):
  move to the **next backend** in `builder_backends`, re-enter at `standard`, append
  `backend_escalated` (from→to backend, verifier tail). No `failure_count` change.
- Exit **30** (backend unavailable — CLI missing, auth failure, quota exhausted): skip to
  the next backend at the **same tier**, append `backend_skipped`. No `failure_count` change.
- All backends exhausted: increment `failure_count`, write the verifier output to `error`,
  and follow `state.md` Failure Handling → Gate 2: the Tech Lead (subscription Sonnet,
  escalating to subscription Opus for genuinely architectural cases) re-specs or fixes the
  task on the Agent tool, then it re-enters at the first backend, tier `fast`. Anthropic
  API billing never enters the picture: the claude *backend* runs on subscription auth
  (dispatch.sh strips `ANTHROPIC_API_KEY`), and API Opus is never a rung.

Cap: one pass up each axis per task. Re-dispatch at the same tier+backend is not retried
automatically except via the normal `failure_count` path.

---

## PR Opening

After successful OpenCode exit, **you (the PM) run `gh pr create`**. OpenCode does not open PRs.

Read `issue_repo` from `PROJECT.md` — the GitHub repo (`org/repo`) for all PRs and issues.

```bash
gh pr create \
  --repo <issue_repo> \
  --title "<task title>" \
  --body "$(cat <<'EOF'
## Summary
<1-3 bullet points from task notes and build spec>

Closes #<issue number from task notes>

## Test plan
<bulleted checklist from build spec acceptance criteria>
EOF
)" \
  --base develop
```

- PR title from `tasks[n].title` in PLAN.md
- Issue number from `tasks[n].notes`
- Summary and test plan from the relevant section of `prompts/build-spec.md`

After PR created: append `pr_opened` (with PR URL) to TASK_LOG, mark task `done`, and
remove the task's worktree (`git -C ../<project-name>/ worktree remove <worktree-path>`;
add `--force` only if you've confirmed nothing in it is still needed).

If `gh pr create` fails: log the error, mark task `done` anyway — do not let a PR failure block task completion.
