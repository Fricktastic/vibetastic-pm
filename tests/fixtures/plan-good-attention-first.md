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
  - id: T002
    stage: 1
    title: "Second task"
    agent: opencode
    status: pending
    depends_on: [T001]
    failure_count: 0
    tier: standard
    verify_tier: R1
attention:
  - id: gate-T001-device
    kind: device_evidence
    task_id: T001
    reason: "Run the approved device verification steps"
    requested_at: "2026-09-13T00:00:00Z"
recommended_next: [T002]
---