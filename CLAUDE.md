---
role: pm-orchestrator
framework: vibetastic-pm
version: "3.0"
---

# Claude orchestrator entry point

Read [ORCHESTRATOR.md](ORCHESTRATOR.md), the single shared orchestrator contract, then
RULES.md, VERIFY.md and MODELS.md. The detailed `.claude/rules/` files apply to both
providers; Claude-specific tool names describe the native implementation here.

In an installed project, start with `python3 framework/orchestrate.py claude` from the PM
directory. Use tracked Bash background sessions for long dispatches. Native Claude role
agents remain available; stage their artifacts and let the lease owner register them.
Use the transactional state commands for all durable changes. Do not write PLAN directly.

This repository is the framework source, not a framework-managed project. Its root PLAN,
SPEC and TASK_LOG are shipped templates. Framework maintenance uses normal branches,
commits and `bash scripts/selftest.sh`; do not acquire a project lease here. Read the ignored
local HANDOFF.md when it exists for current work and operator directives. Never propagate
changes to deployments unless asked.
