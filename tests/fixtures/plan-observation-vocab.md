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
    title: "Legacy task with no observation field"
    agent: codex
    status: done
    depends_on: []
    failure_count: 0
    verify_tier: R0
  - id: T002
    stage: 1
    title: "Test-observable, but no command to run the test"
    agent: codex
    status: pending
    depends_on: [T001]
    failure_count: 0
    verify_tier: R0
    observation: test   # issue #35: needs observation_cmd
---

## Task Overview
