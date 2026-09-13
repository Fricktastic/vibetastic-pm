---
project: "fixture"
created: "2026-09-13T00:00:00Z"
updated: "2026-09-13T00:00:00Z"
stages:
  - id: 1
    name: "Implementation"
    status: in_progress
tasks:
  - id: T001
    stage: 1
    title: "Build the parser"
    agent: codex
    status: building
    depends_on: []
    failure_count: 0
    tier: standard
    verify_tier: R1
  - id: T002
    stage: 1
    title: "Review drift"
    agent: codex
    status: awaiting_approval
    depends_on: [T001]
    failure_count: 0
  - id: T003
    stage: 1
    title: "Missing counter remains visible"
    agent: codex
    status: pending
    depends_on: []

## Deliberately unclosed frontmatter
