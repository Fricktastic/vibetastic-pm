---
project: "<project-name>"
created: "<ISO8601>"
---

<!-- APPEND-ONLY. PM orchestrator is the sole writer. Never edit or delete existing entries. -->

<!-- Entry format:

### <ISO8601> · <event_type>
```yaml
task_id: <id or null>
agent: <designer | architect | opencode | pm>
<event-specific fields>
```

Valid event_type values:
  spec_drafted        - PM generated initial SPEC.md from user interview
  spec_approved       - User approved SPEC; PLAN.md generation unblocked
  plan_generated      - PM generated initial PLAN.md from approved SPEC
  task_started        - PM dispatched task to agent
  model_selected      - Architect selected model via OpenRouter (opencode tasks)
  model_fallback      - OpenRouter unavailable; fallback model used
  agent_returned      - Agent returned structured result to PM
  task_completed      - PM applied result, wrote outputs, updated PLAN.md
  task_failed         - Agent error; PM wrote error field to PLAN.md
  task_retrying       - PM retrying failed task (first failure, automatic)
  stage_complete      - All tasks in a stage reached done; Gate 3 triggered
  stage_transition    - User confirmed Gate 3; PM advancing to next stage
  user_escalation     - PM halted and escalated to user with reason (Gate 2 or fatal)
  state_correction    - PM corrected PLAN.md to match evidence (e.g. a shipped task left
                        in_progress); fields: field, from, to, evidence (required), note.
                        Never touches failure_count.
  critic_returned     - Pre-build critique adjudicated; fields: round, verdict, blocking_plan,
                        decision (the logs/verdicts.jsonl record is the gate state)
  critic_override     - Operator overrode unresolved critique findings; fields: finding, reason
  critic_escalated    - Critique round cap reached; fields: rounds, unresolved findings
  review_escalated    - Reviewer fixup round cap reached; fields: rounds, unresolved findings
  round_cap_override  - Operator granted extra rounds; fields: role, extra, reason
  lessons_consolidated - Stage-transition lesson pass; fields: before, merged, retired,
                        after, mechanisms
  observation_recorded - Runtime observation recorded with merge_gate.py observe; fields:
                        sha, kind, evidence, summary
  merge_gate          - merge_gate.py check/merge result; fields: sha (the commit merged),
                        base_sha, allowed, checks (status per check), pr
  merge_gate_override - Operator waived one merge-gate check for one commit; fields: sha,
                        check, reason
  inline_authored     - Orchestrator authored a change itself under the inline authoring
                        gate; fields: model, file, branch, observation, conditions (one line
                        per condition met)
-->
