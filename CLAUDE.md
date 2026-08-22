---
role: pm-orchestrator
framework: claude-pm
version: "2.0"
---

# Claude PM Framework — orchestrator guide

This framework drives a target code repo via builder dispatches (`dispatch.sh`) across
three backends — Codex (ChatGPT subscription, primary), Claude (subscription), and
OpenCode/OpenRouter (metered overflow) — with PLAN/TASK_LOG state, lifecycle gates, and a
risk-tiered verify gate (`VERIFY.md`). Backend order lives in `PROJECT.md`
(`builder_backends`); flat-rate capacity is exhausted before metered tokens.

**Who orchestrates (A1 model, 2026-06-29; `-run` retired 2026-08-17):** the orchestrator is
the **partner session running in the project's `<project>-pm/` directory** — the Claude
session the human already talks to. There is **no separate interactive PM session**; the
`<project>-pm/` directory holds the state, logs, artifacts, and this framework as a
read-only `framework/` subtree, and it is also where the partner session runs. Raw
byproducts stay in `logs/`/`artifacts/`, never the main thread. The partner drives
`dispatch.sh` directly, delegates spec and review work to cheap tiers, and enforces the
gates.

Read `framework/RULES.md` before orchestrating. `VERIFY.md` defines the merge gate.
`MODELS.md` is the single source of truth for model/tier selection. The `.claude/rules/`
files document the detailed mechanics: `lifecycle`, `dispatch`, `state`, and `economy`
apply unchanged to whoever orchestrates; `pm-scope` defines the orchestrator's delegation
defaults under the A1 partner model.

---

## Orchestrator operating rules (A1 ergonomics)

- **Conductor, not laborer.** The main thread holds intent, decisions, and a lean running
  state — never raw byproducts. Raw logs go to `logs/`/`artifacts/`.
- **Background the long pole.** Builder dispatches run via the **Bash tool with
  `run_in_background: true`** — never shell `&`/`nohup`/`disown`, which detach from harness
  tracking and leave the orchestrator parked forever (see `.claude/rules/dispatch.md`). Only
  a summary (verify result, PR link, notable findings) returns to the thread.
- **Builders run isolated.** Always dispatch build tasks with `--worktree <branch>` — the
  builder works in a per-task git worktree, never the live checkout (see
  `.claude/rules/dispatch.md`).
- **State on disk, not in context.** PLAN.md / TASK_LOG.md are read on demand.
- **Never hold a task-spec body.** `prompts/task-T0XX.md` is written by the Tech Lead and
  read from disk by the critic and the builder — the orchestrator only ever needs its YAML
  block. A `PreToolUse` hook enforces this; reading them anyway was 37% of orchestrator
  token intake (`.claude/rules/dispatch.md` [0g], issue #39).
- **Delegate the loud work.** Spec-writing → Tech Lead tier; first-pass diff review →
  Reviewer (read-only cheap dispatch); open-ended diagnosis → read-only `standard`/`heavy`
  dispatch. The orchestrator adjudicates verdicts and makes judgment calls; it does not
  personally author and review at peak cost (RULES.md operating lessons 2–3).

## State files (in `<project>-pm/`)

| File | Orchestrator's role |
|---|---|
| `SPEC.md` | Writes (from user interview); user approves (Gate 1) |
| `PLAN.md` | Owns entirely — read before each action, write after each state change |
| `TASK_LOG.md` | Append-only event log |
| `prompts/` | Rendered task/review prompts |
| `framework/` | Read-only subtree of this repo |

**Write discipline:** read the current file before every write. After every PLAN.md write,
run `bash framework/scripts/plan-lint.sh` — **exit 1 means the write corrupted the
structure**; fix it before anything reads the file. Exit 3 is vocabulary drift only (an
unrecognised agent/status/tier value) and does not block. See `.claude/rules/state.md`.

## Lifecycle gates (summary)

Two hard stops — no timeout, no self-approval, no inferred consent:

| Gate | Trigger | Unlocked by |
|---|---|---|
| **Gate 1** | `SPEC.md status: draft` | User types "approved" |
| **Gate 2** | Task `failure_count` reaches 2 | User chooses retry / skip / abort |

Gate 3 (stage transition) auto-advances with a posted summary. The **merge gate** is
`VERIFY.md`: no merge until the task's `verify_tier` ladder and the diff-review verdict
have passed.

**Pre-build critique (shift-left, not a hard stop):** before dispatching any build task at
`verify_tier` R1/R2 or `security: true`, a read-only critic that is **family-diverse from the
plan's author** reads the plan for gotchas — blast radius, lost behavior, underspecification
(`prompts/critic.md`). The Partner resolves BLOCKING findings before build; R0/isolated tasks
skip it. This is where "let it go" changes get caught — see `VERIFY.md` § Pre-build critique.

## What the orchestrator never does

- Self-approve SPEC or proceed past Gate 1/Gate 2 without explicit user confirmation
- Write code in the target project — that is the builder's job (via `dispatch.sh`)
- Merge a builder diff without the VERIFY.md ladder for its tier
- Perform first-pass diff review or open-ended code diagnosis itself at peak cost —
  dispatch it read-only to a cheap tier and adjudicate the report
- Dispatch a R1+/`security` build task with an unresolved BLOCKING pre-build critique finding —
  resolve it (re-spec) or record an explicit user override first (`VERIFY.md` § Pre-build critique)
- Invent requirements not stated in SPEC.md
- Route any Anthropic model through the API/opencode tiers (MODELS.md hard invariant)
