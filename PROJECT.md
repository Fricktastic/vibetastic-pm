---
project: vibetastic-pm
setup_at: 2026-08-18T00:00:00Z
builder_backends: [codex, claude, opencode]
reviewer_backends: [opencode]
critic_backends: [codex, opencode]
---

## Project Paths

| Key | Path |
|-----|------|
| PM directory | `/Users/tim/Developer/vibetastic-pm` |
| Code directory | `/Users/tim/Developer/vibetastic-pm` |
| Issue repo | `Fricktastic/vibetastic-pm` |

<!-- This repo is the framework SOURCE, not a framework-managed project. PM dir and code dir
     are the same path, and PLAN.md / TASK_LOG.md at the repo root are the shipped TEMPLATES
     consumed by setup.sh — they are NOT live state and must never be overwritten with real
     task data. Work here is tracked in git branches and commits, not a live PLAN.md. -->

## Verify command

```
bash scripts/selftest.sh
```

## Notes
