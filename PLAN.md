---
project: "<project-name>"
created: "<ISO8601>"
updated: "<ISO8601>"
stages:
  - id: 1
    name: "Design"
    status: pending
  - id: 2
    name: "Architecture"
    status: pending
  - id: 3
    name: "Implementation"
    status: pending
tasks:
  - id: T001
    stage: 1
    title: "Design UI and interaction model"
    agent: designer
    status: pending
    depends_on: []
    inputs: []
    outputs:
      - prompts/design-spec.md
    model: null
    started_at: null
    completed_at: null
    failure_count: 0
    error: null

  - id: T002
    stage: 2
    title: "Architect build plan"
    agent: architect
    status: pending
    depends_on: [T001]
    inputs:
      - prompts/design-spec.md
    outputs:
      - prompts/build-spec.md
    model: null
    started_at: null
    completed_at: null
    failure_count: 0
    error: null

  - id: T003
    stage: 3
    title: "<implementation task title>"
    agent: opencode
    status: pending
    depends_on: [T002]
    inputs:
      - prompts/build-spec.md
    outputs: []
    model: null
    verify_tier: null   # R0|R1|R2 — the evidence that proves the task (project policy); see framework/VERIFY.md
    risk: null          # true|false — a project risk trigger applies: pre-build critique required
    security: false     # true forces critique and the Sonnet/Opus review floor
    started_at: null
    completed_at: null
    failure_count: 0
    error: null
---

## Task Overview

<!-- PM generates this summary from SPEC.md after approval. Describes phases, key dependencies, and expected deliverables. -->
