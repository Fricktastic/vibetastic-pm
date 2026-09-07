# vibetastic-pm

A framework for running an AI orchestrator that drives coding work in a separate target
repo, instead of writing code itself. The orchestrator (a Claude session) plans the work,
dispatches implementation tasks to builder models, and enforces review and merge gates
before anything lands.

## How it works

A project gets its own `<project>-pm/` directory that pulls this repo in as a read-only
`framework/` subtree. That directory holds the live state for one project: `SPEC.md`,
`PLAN.md`, `TASK_LOG.md`, and rendered prompts. The Claude session running in that
directory is the orchestrator - it reads the state files, decides what's ready to build,
and calls `dispatch.sh` to hand tasks off to a builder.

Builders run across three backends, tried in order until one is available and capable
enough to pass the task's verify command: Codex (ChatGPT subscription), Claude
(subscription), and OpenCode/OpenRouter (metered, used only when the flat-rate backends
are exhausted). Each task escalates in tier (fast/standard/heavy) before falling through
to the next backend, so cheap models are tried first and cost only climbs when a builder
actually fails to pass verification.

Two hard gates require explicit human sign-off: approving the SPEC before planning starts,
and deciding what to do after a task fails twice in a row. Everything else - stage
transitions, tier escalation, backend fallback - proceeds without asking.

## What's in this repo

- `CLAUDE.md` - orchestrator guide, read at the start of every session
- `RULES.md` - detailed operating rules and lessons learned from running this in production
- `MODELS.md` - model and tier selection, the source of truth for which model runs what
- `VERIFY.md` - the merge gate: review tiers and what has to pass before a diff merges
- `dispatch.sh` - the wrapper that runs a builder task in an isolated worktree and retries
  it against a verify command
- `setup.sh` - one-time setup for a new `<project>-pm/` directory
- `.claude/rules/` - the mechanics behind lifecycle, dispatch, state, and token economy
- `prompts/` - prompt templates for each role (architect, designer, tech lead, reviewer, critic)
- `scripts/` - support scripts (plan linting, cost reporting, screenshotting, etc.)

## Setup

From a new `<project>-pm/` directory, with this repo checked out as `framework/`:

```
bash framework/setup.sh <project-name> <path-to-code-dir> <org/repo> [verify-cmd]
```

This writes `PROJECT.md` and a `.claude/settings.json` allowlist for the project. Then
start a Claude session in that directory and follow the SPEC interview.

## Note on this repo itself

This repo is the framework source, not a framework-managed project. The `PLAN.md`,
`SPEC.md`, and `TASK_LOG.md` files at the root are shipped templates consumed by
`setup.sh`, not live state - work on the framework itself is tracked through normal git
branches and commits.
