# Walkthrough — Instantiating vibetastic-pm for a New Project

> Sections 4–12 retain an annotated historical example of the original staged workflow.
> Current bootstrap, provider launch, ownership, recovery, routing, and gate rules are stated
> explicitly in sections 1–3, 13–14 and in `ORCHESTRATOR.md`. Where an old transcript says
> `proceed` or names a particular model, follow `RULES.md` and `MODELS.md`: stage transitions
> auto-advance and the effective provider profile selects the backend.

## TL;DR

vibetastic-pm lets either a Claude or Codex partner orchestrate work from a project PM
directory. The lease-owning partner drives Designer → Architect → Tech Lead → builder work,
then enforces the risk-tiered merge gate. Your required inputs are:

- Answer 6 questions at the start (SPEC interview)
- Type `approved` once (SPEC approval)
- Decide retry / skip / abort only if a task fails twice

Everything else — role dispatch, model selection, first retry, state management, and stage
transitions — runs autonomously. After an interruption, restart the chosen provider through
`orchestrate.py`; reconciliation checks recorded runs and worktrees before changing task state.

**One-time setup** (run before the first provider session):
```bash
bash framework/setup.sh <project-name> /absolute/path/to/code-dir <org/repo> ['verify-cmd'] ['test-cmd']
```

**Framework updates** (without touching project files):
```bash
git subtree pull --prefix framework framework main --squash
```

---

This guide walks through setting up and running the PM framework on a new project from first invocation to completed implementation.

---

## 1. Set Up the Instance

You need a target project directory (empty or existing) and a sibling `-pm/` directory containing the framework. The `-pm/` naming convention is defined in `RULES.md`.

### Recommended: git subtree (pulls future framework updates cleanly)

```bash
cd ~/dev/projects

# Target project (create or use existing)
mkdir my-app

# Create the pm directory as its own git repo
mkdir my-app-pm && cd my-app-pm && git init

# Add vibetastic-pm as a named remote so future pulls are one command
git remote add framework https://github.com/Fricktastic/vibetastic-pm.git

# Pull vibetastic-pm framework files into a framework/ subdirectory
git subtree add --prefix framework framework main --squash

# Run one-time project setup. It writes PROJECT.md and installs additive
# CLAUDE.md/AGENTS.md entry blocks plus Claude/Codex hooks.
# 4th arg (optional): the single-line verify command powering dispatch.sh's
# self-correction loop.
bash framework/setup.sh my-app /absolute/path/to/my-app my-org/my-app 'npm run build' 'npm test'
```

To pull framework updates later (project files are never touched):
```bash
git subtree pull --prefix framework framework main --squash
```

Project-specific files (`SPEC.md`, `PLAN.md`, `TASK_LOG.md`, `PROJECT.md`, `prompts/`) live in the root alongside the `framework/` directory — they are not tracked by upstream.

### Simple alternative: cp -r (no upstream tracking)

```bash
cd ~/dev/projects
mkdir my-app
cp -r vibetastic-pm/ my-app-pm/
cd my-app-pm/
```

This works but framework improvements must be applied manually to each project instance.

---

## 2. Prerequisites

**OpenRouter API key** — the OpenCode lanes use OpenRouter. Set it in your shell environment before starting:

```bash
export OPENROUTER_API_KEY=sk-or-your-key-here
```

To persist it across sessions, add that line to your shell profile (`~/.zshrc`, `~/.bashrc`, etc.) and reload:

```bash
source ~/.zshrc
```

The provider wrapper and dispatched OpenCode processes inherit this environment.

**OpenCode** — must be installed and on your `$PATH` for OpenCode-backed roles and builds.

Validate the installation before the first session:

```bash
python3 framework/scripts/orchestrator-doctor.py --pm-dir . --framework-dir framework
```

---

## 3. Start the PM

Review Codex project hooks with `/hooks`, then launch exactly one lease owner:

```bash
python3 framework/orchestrate.py claude
# or
python3 framework/orchestrate.py codex
```

Claude reads the managed `CLAUDE.md` entry and Codex reads `AGENTS.md`; both point to the
same `ORCHESTRATOR.md` contract. The wrapper records provider, PID, start time and routing
profile in `.orchestrator/lease.json`. A second orchestrator is refused while that lease is
live. On a new project the partner begins the SPEC interview.

---

## 4. SPEC Interview

The PM asks all questions in one message:

> I'll ask a few questions to build the project spec before we start.
>
> 1. What problem does this project solve?
> 2. Who are the users?
> 3. What are the 3–5 most important things it must do?
> 4. What is explicitly out of scope?
> 5. Any technical constraints — stack, platform, existing APIs?
> 6. How will we know it's done and correct?

Answer them. The PM writes `SPEC.md`, sets `status: draft`, and appends `spec_drafted` to `TASK_LOG.md`.

---

## 5. Gate 1 — SPEC Approval

The PM presents the full spec in chat:

> Here's the project spec I've written. Please review it carefully.
>
> ---
> **Project:** my-app
> **Problem Statement:** ...
> **Goals:** ...
> *(full spec body)*
> ---
>
> Type **approved** to unlock the build plan, or give me feedback to revise it.

**Hard stop.** The PM does nothing until you respond.

- Give feedback → PM edits `SPEC.md`, re-presents, repeats
- Type `approved` → PM sets `status: approved`, writes `approved_at`, appends `spec_approved` to TASK_LOG, generates the plan

After approval, `SPEC.md` frontmatter looks like:

```yaml
project: my-app
status: approved
version: "1.0"
created: 2026-05-22T10:00:00Z
updated: 2026-05-22T10:14:00Z
approved_at: 2026-05-22T10:14:00Z
```

---

## 6. Plan Generation + Gate 3 (Stage 1)

The PM generates `PLAN.md` from the approved spec and presents a plain-language summary:

> Plan is ready. Here's what I'll build:
>
> **Stage 1 — Design** (1 task)
> T001: Designer agent produces `prompts/design-spec.md`
>
> **Stage 2 — Architecture** (1 task)
> T002: Architect agent produces `prompts/build-spec.md` and selects model
>
> **Stage 3 — Implementation** (2 tasks)
> T003: Set up project structure and auth
> T004: Build main feature screens
>
> Starting Stage 1 — Design now. Reply if you want to adjust.

Gate 3 posts the summary and auto-advances. It is not a confirmation gate.

---

## 7. Stage 1 Runs Autonomously

The partner immediately:

1. Sets `stages[1].status: in_progress` in PLAN.md
2. Appends `stage_transition` to TASK_LOG
3. Reads `framework/prompts/designer.md`, substitutes `{{SPEC_CONTENT}}`
4. Spawns Designer agent
5. Receives design spec back
6. Writes output to `prompts/design-spec.md`
7. Updates T001 in PLAN.md: `status: done`, `completed_at`
8. Appends `agent_returned` + `task_completed` to TASK_LOG

No user input required during any of that. When T001 is done, Gate 3 fires for Stage 2:

> Stage 1 — Design is complete.
>
> **Output:** `prompts/design-spec.md`
> Covers: 3 user flows, 7 screens, 12 components, interaction model.
>
> Auto-advancing to Stage 2 — Architecture. Reply if you want to adjust or pause.

---

## 8. Stage 2 → Stage 3

The same pattern auto-advances through Stage 2. The Architect role produces
`prompts/build-spec.md` plus tier and security metadata. The lease owner validates and
promotes the staged result, then resolves each task's effective backend/model from the
session routing profile. Gate 3 posts a summary and immediately starts Stage 3.

At this point the state files look like:

```
my-app-pm/
├── CLAUDE.md           managed Claude entry block
├── AGENTS.md           managed Codex entry block
├── SPEC.md             status: approved
├── PLAN.md             T001 done, T002 done, T003/T004 pending
├── TASK_LOG.md         6+ entries
├── prompts/
│   ├── design-spec.md  ← Designer output
│   └── build-spec.md   ← Architect output
└── framework/          ← git subtree (vibetastic-pm); read-only
    ├── CLAUDE.md
    ├── RULES.md
    ├── WALKTHROUGH.md
    ├── dispatch.sh
    └── prompts/
        ├── designer.md
        ├── architect.md
        └── tech-lead.md
```

---

## 9. Stage 3 — Builders Execute

The partner dispatches each implementation task through `dispatch.sh` in an isolated
worktree. Backend order comes from the effective session profile. The normal profile uses
project routing; Codex fallback uses OpenCode only for child work.

```bash
bash framework/dispatch.sh --worktree <branch> --backend <backend> \
  <model> ../my-app/ prompts/task-T00X.md [fallback] [verify-cmd]
```

The model comes from the task tier, backend order, and routing profile. Anthropic models run
only through the Claude subscription backend; OpenCode never routes Anthropic through
OpenRouter.

PM captures exit code and output. On success: task marked `done`. Tasks with no inter-dependencies within a stage may run in parallel at the PM's discretion.

---

## 10. Mid-Project Work — Routing Rule

The Architect's build-spec covers the work known at project start. It will not cover every bug, regression, or new requirement that emerges during Stage 3. When new work appears, the PM routes it before creating any new OpenCode task.

**Routing rule:**
- **New screen, new UI component, or any feature with visual design decisions** → Designer first, then Tech Lead, then OpenCode
- **Bug fix or non-UI change** → Tech Lead directly, then OpenCode

**What triggers this flow:**
- You report a bug or new requirement in chat
- Gate 2 fires and the fix needs speccing before retry
- A completed task reveals follow-on work not covered by the existing spec

**What the Tech Lead does:**
1. Reads relevant source files in the target project to understand current state
2. Fetches Apple/framework docs via Sosumi MCP if the issue involves Apple APIs
3. Writes a precise, self-contained task spec (root cause, files to change, implementation steps, commit plan)
4. Returns structured metadata: task title, branch, issue refs, suggested OpenCode model

**What you see in chat:**

> New work identified: OAuth redirect URI rejected by HA server.
> Running Tech Lead to spec the fix before dispatch.

The PM appends the Tech Lead's spec to `prompts/build-spec.md`, creates the new task in `PLAN.md`, and dispatches it to OpenCode — all without requiring your input unless a gate fires.

**Model:** Tech Lead runs on Sonnet by default. It suggests the OpenCode model in its returned metadata based on task complexity — simple bugs get Gemini Flash, complex architectural changes get Sonnet.

---

## 11. Major Design Revisions

The PM's Designer agent is a one-shot invocation — good for initial design and discrete new screens, but not suited for iterative, exploratory design work where you want to go back and forth on layout, interaction model, or visual direction.

For major design revision sessions, bypass the PM entirely:

1. Open a **separate Claude session** in the `-pm/` directory (not the PM orchestrator)
2. At the start of that session, read `framework/prompts/designer.md`, `SPEC.md`, and the current `prompts/design-spec.md`
3. Work interactively — revisions, alternatives, open questions — until you're satisfied
4. Write the final result directly to `prompts/design-spec.md`
5. Return to the PM session and tell it: **"design-spec.md has been updated manually"** — the PM will skip the Designer and pass the updated file straight to the Tech Lead

---

## 12. Gate 2 — Task Double-Failure


Gate 2 does not appear on the happy path. It fires only when a task fails twice.

If OpenCode exits non-zero on T003:

- PM increments `failure_count` to 1, writes the error to PLAN.md, appends `task_failed`
- PM retries automatically (first failure), appends `task_retrying`
- If it fails again: `failure_count` hits 2, **Gate 2 fires**:

> Task T003 — "Set up project structure and auth" has failed twice.
>
> **Failure 1:** `exit 1 — cannot find module 'vite'`
> **Failure 2:** `exit 1 — cannot find module 'vite'`
>
> How would you like to proceed?
> - **retry** — try again (resets failure count)
> - **skip** — mark done and continue (only if downstream tasks can proceed)
> - **abort** — halt everything, leave state for manual inspection

---

## 13. Recovery After a Crash

If the provider exits mid-run, restart from the same PM directory with the same wrapper (or
choose the other provider after the previous lease is stale):

```bash
cd my-app-pm/
python3 framework/orchestrate.py claude
# or: python3 framework/orchestrate.py codex
```

The partner runs `orchestrator-state.py reconcile` and compares PLAN run metadata with live
processes, job/session handles, exit records, and worktrees. A live run remains
`in_progress`; a completed run is finalized from evidence; only a confirmed failed run
increments `failure_count`. Interruption alone never counts as failure. Ambiguous ownership
blocks new dispatch until it is resolved, preserving recoverable work.

---

## 14. Newer Framework Behaviors (2026-07-17)

These post-date the historical flow above and change how the orchestrator reviews, escalates,
and hands off. They are live in the current framework; `RULES.md`, `VERIFY.md`, and `MODELS.md`
are the authority.

**Reviewer family diversity (merge gate).** The first-pass Reviewer must be a *different model
family* than the builder backend that produced the diff — same-family review reproduces the
builder's blind spots. Concretely: a diff built by the **claude** backend (sonnet/opus) is
reviewed by the opencode `standard` tier (deepseek), **never** the Sonnet subagent; diffs from
`codex` or `opencode` may use either reviewer. See `VERIFY.md` § Diff review.

**Security-sensitive tasks.** The Tech Lead (and Architect, for Stage-2 tasks) sets a
`security: true` flag when a diff touches auth, credentials, keychain, entitlements, network
trust, sandboxing, or input validation on external data. It rides in the task's result YAML
and lands on the PLAN.md task next to `verify_tier`. Effect: the review rung is forced up —
first-pass review runs on **Sonnet minimum** (not the cheap opencode tier) and adjudication is
**mandatory Opus, never delegated, never Fable**. This is the one place the cheap-first bias is
wrong: a missed security bug ships silently rather than failing a verify loop. See `VERIFY.md`
§ Security-sensitive tasks.

**Codex burn-gated `sol@high` rung.** To preserve the scarcer Claude subscription window, the
codex heavy ladder now extends one rung: after `gpt-5.6-sol@medium` fails, dispatch attempts
`gpt-5.6-sol@high` **once** before falling through to the claude backend — but only if the
current ISO-week burn proxy (`logs/cost.jsonl`) is below `codex_weekly_burn_threshold`
(tunable in `MODELS.md`, conservative default). At/above threshold it skips `@high` and
escalates backend immediately, preserving the weekly-cliff guard. Provisional pending
telemetry (review 2026-08-17). Every `@high` dispatch must log the burn-proxy reading it
consulted in its `cost_event` (`burn_proxy:` field); `cost-report.sh` prints a `⚠ VIOLATION`
for any `@high` dispatch missing it — a self-evidencing audit that keeps enforcement out of
read-only `dispatch.sh`.

**Orchestrator provider / Fable.** The normal Claude partner uses Opus; issue #33 adds a
Codex partner with the same durable-state contract. The Codex default profile reserves Codex
capacity for orchestration and routes ordinary child work through OpenCode. Fable is never a
standing orchestrator and never handles security work. See `MODELS.md` and `ORCHESTRATOR.md`.

**Session handoff — write-through + `HANDOFF.md`.** State writes happen at the moment of the
event, never batched: the PM may not proceed past a dispatch, task completion/failure, gate
decision, stage transition, or escalation until `PLAN.md`/`TASK_LOG.md` reflect it — an
unwritten event does not exist. On top of that, the PM maintains `HANDOFF.md` in the `-pm/`
directory (sole writer, overwritten in place): current stage, in-flight dispatches, next
planned action, open questions, and in-session-only context. It is rewritten after every gate
decision and stage transition, and whenever you type **`checkpoint`** (the PM verifies disk
state matches its understanding, flushes anything missing, and confirms **"safe to clear"**).
A fresh session reads `HANDOFF.md` **first** at startup and treats needing to re-explore as a
logged failure signal. Use `checkpoint` before clearing context at cache expiry.

**Self-improvement capture — file a GitHub issue.** The PM runs the framework but never edits
it (read-only subtree). When it observes a framework defect in the field — a repeated tier
failure, a rule forcing a bad outcome, a stall, a mispriced escalation — it files an issue on
the framework repo (`gh issue create --repo Fricktastic/vibetastic-pm`) carrying project, observed
problem, evidence pointer (TASK_LOG event ids / cost.jsonl lines), and suggested change. The
**orchestrator** files it; worktree builders run `gh` unauthenticated by design and cannot.
If `gh` is unreachable, fall back to appending the same fields to `PROPOSALS.md` on the
writable `-pm/` side and note the issue is unfiled — `PROPOSALS.md` is otherwise retired.
Maintainer sessions triage the queue alongside `cost-report.sh` output. **The framework
proposes, the maintainer disposes.**

---

## Gate Summary

| Gate | Fires when | Unlocked by |
|---|---|---|
| **Gate 1** | SPEC status is `draft` | User types `approved` |
| **Gate 2** | Task `failure_count` reaches 2 | User chooses retry / skip / abort |
| **Gate 3** | All tasks in a stage reach `done` | Nothing — auto-advances with a posted summary (user can interject) |

Gate numbers refer to gate *type*, not the order they appear in a session. On a clean run, the only hard stop is Gate 1. Gate 2 only appears when something breaks.

---

## Full Flow at a Glance

```
python3 framework/orchestrate.py claude  # or codex
  ↓ SPEC interview (PM asks, you answer)
  ↓ Gate 1 — type "approved"
  ↓ Plan generated
  ↓ Gate 3 posts summary and auto-starts Stage 1: Design
  ↓ Designer role runs through the effective profile
  ↓ Gate 3 posts summary and auto-starts Stage 2: Architecture
  ↓ Architect role produces build spec + tier metadata
  ↓ Gate 3 posts summary and auto-starts Stage 3: Implementation
  ↓ builders run through the effective backend order
  ↓ [new bug or requirement]
  ↓ Tech Lead role specs the fix autonomously
  ↓ selected builder runs autonomously
  ↓ Project complete
```

On a clean run, `approved` at Gate 1 is the only required stage-flow response.

**Agent roster** (models per `framework/MODELS.md` — the source of truth):

| Agent | Model | Runs | Job |
|---|---|---|---|
| Designer | Sonnet (escalate Opus) | Once + per new UI work | Design spec |
| Architect | Opus | Once | Build spec + OpenCode tier selection |
| Tech Lead | Sonnet (escalate Opus) | Per new mid-project task | Bug/feature → task spec |
| Reviewer | opencode `standard` tier, read-only (Sonnet min for `security` tasks) | Per builder diff | First-pass diff review; must be a different family than the builder (§14); orchestrator adjudicates |
| OpenCode | tier ladder `fast`→`standard`→`heavy` | Per implementation task | Write code (in a per-task worktree) |
