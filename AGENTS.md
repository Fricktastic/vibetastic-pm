# Codex entry point

Read [ORCHESTRATOR.md](ORCHESTRATOR.md), the shared contract for Claude and Codex, then
RULES.md, VERIFY.md and MODELS.md before orchestrating an installed project.

In an installed PM directory, start with `python3 framework/orchestrate.py codex`.
The default `codex-fallback` session profile uses OpenCode for dispatched work; use
`--profile normal` before `codex` when explicitly choosing the project's normal routing.
Use exec session handles for long dispatches. The pilot uses `dispatch-role.py` for
Designer, Tech Lead and Architect, not native Codex subagents. Read the shared detailed
mechanics under framework/.claude/rules/ explicitly; Codex does not auto-load that folder.

This repository is the framework source. Its root PLAN, SPEC and TASK_LOG are templates,
not live state. Work on framework issues through normal git branches and commits, run
`bash scripts/selftest.sh`, and keep the ignored local HANDOFF.md current when it exists.
Do not enable project hooks or a project lease in this source checkout. Do not modify
deployed projects without instruction.
