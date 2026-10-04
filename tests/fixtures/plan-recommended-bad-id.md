---
project: "fixture"
created: "2026-01-01T00:00:00Z"
updated: "2026-01-01T00:00:00Z"
stages:
  - id: 1
    name: "Implementation"
    status: in_progress
tasks:
  - id: T001
    stage: 1
    title: "First task"
    agent: codex
    status: done
    depends_on: []
    failure_count: 0
    tier: fast
    verify_tier: R0
recommended_next: [T999]
---