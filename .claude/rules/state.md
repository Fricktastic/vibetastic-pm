# State Management

## TASK_LOG Append Format

Every entry must follow this format exactly — append to the bottom of TASK_LOG.md:

```markdown
### <ISO8601> · <event_type>
```yaml
task_id: <id or null>
agent: <designer | architect | opencode | pm>
<relevant fields for this event type>
```
```

Include enough detail to reconstruct what happened without reading PLAN.md. On failures, include the full error message.

### Cost telemetry (`cost_event`)

Three of the four telemetry streams are mechanical — no orchestrator discipline required:

| Stream | Written by | Lands in |
|---|---|---|
| Dispatched work | `dispatch.sh` | `logs/cost.jsonl` |
| Agent tool spawns | `PostToolUse` hook, `scripts/log-agent-spawn.py` | `logs/agent-spawns.jsonl` |
| **Orchestrator's own burn** | `Stop` hook, `scripts/log-partner-burn.py` | `logs/cost.jsonl` (`role: partner`) |
| Role + task attribution | **you**, via `cost_event` below | `TASK_LOG.md` |

The manual `cost_event` adds what the hooks cannot know: the **role** (Designer vs Tech Lead
vs Reviewer) and task attribution. Append one after every subagent spawn and before each
`dispatch.sh` call; if you forget, the hook log still catches the spawn, just without role
attribution.

**The partner stream is the largest one.** Until 2026-08-20 it did not exist in practice —
the hook was wired but silently wrote nothing (issue #30), and reconstructing it from session
transcripts put the orchestrator at **~71% of every token the system had consumed**. Both burn
gates read `cost.jsonl`, so for five days they ran on a proxy missing its dominant term. If
`logs/telemetry-errors.log` appears or grows, a stream has stopped writing — read it, do not
ignore it. `scripts/backfill-partner-burn.py` replays historical sessions from transcripts
(dry run by default; `--apply` to write).

```markdown
### <ISO8601> · cost_event
```yaml
task_id: <id or null>
role: <designer | architect | tech_lead | opencode | pm>
model: <model/alias actually used — e.g. sonnet, opus, openrouter/google/gemini-3.5-flash>
tier: <fast | standard | heavy | null>   # OpenCode only
burn_proxy: <ISO-week token total consulted, integer | null>   # REQUIRED for any gpt-5.6-sol@high dispatch (see below)
note: <one line, optional>
```
```

**Burn-gate audit rule (codex `sol@high`):** since issue #41, `dispatch.sh` computes the
burn proxy, enforces `codex_weekly_burn_threshold` itself (exit 30 when closed), and stamps
`burn_proxy` into the `cost.jsonl` row — the gate no longer depends on this being remembered.
It was skipped 6 times out of 6 while it did. Still record the `cost_event` for **role**
attribution, which the hooks cannot infer: every `gpt-5.6-sol@high` dispatch **must**
record in its `cost_event` the `burn_proxy` reading it consulted before the gate (the
current ISO-week token total from `logs/cost.jsonl` — see `framework/MODELS.md` §
`codex_weekly_burn_threshold`). This keeps enforcement out of the read-only `dispatch.sh`
while making a skipped check impossible to hide: an `@high` dispatch whose `cost_event`
carries no `burn_proxy` figure is an **auditable violation**, and `cost-report.sh` can flag
it mechanically. `burn_proxy` is `null`/omitted for every other dispatch.

Run `bash framework/cost-report.sh` to roll up `logs/cost.jsonl` (dispatches **and**
`role: partner` orchestrator turns) + `logs/agent-spawns.jsonl`
+ these `cost_event` entries against the `MODELS.md` Pricing table — the evidence base for tuning tiers (cheapest model that
clears the bar; escalate on proof).

---

## Applying Results

After every agent return, read the current PLAN.md and register one task transition at a
time. For installed projects, use `framework/scripts/plan-update.py` with the expected
SHA256, candidate file, TASK_LOG event and unique operation ID. The command validates and
atomically replaces PLAN, and recovers an interrupted TASK_LOG append exactly once.
Use `orchestrator-state.py write/append` for other durable changes. See
`framework/ORCHESTRATOR.md` for command examples and lease ownership.

Lint exits 0/3 are accepted (3 is vocabulary drift); 1 is structural corruption, 2 is
unreadability. Unknown linter exits are blocking. PostToolUse lint is feedback only; the
transactional command is the authoritative mutation path. Uninstalled legacy projects
must still lint immediately after each write until their adapters are installed.

---

## Failure Handling

A dispatch **exit 31** is an ownership/routing/reconciliation stop: resolve the state or policy; never increment failure_count.

First distinguish **escalation** from a true failure. dispatch.sh **exit 20** (verifier never
passed) and **exit 30** (backend unavailable) are *not* failures: follow Backend & Tier
Escalation in `dispatch.md` (bump tier within the backend, then move to the next backend in
`builder_backends`; log `tier_escalated` / `backend_escalated` / `backend_skipped`;
never touch `failure_count`). Only when the **last** backend's ladder is exhausted (exit 20
at `heavy` on the final backend), or the builder returns any other non-zero exit
(infra/model failure), is it a true failure.

On a true task failure (agent error, malformed output, opencode infra failure, or heavy-tier
verifier exhaustion):

1. Increment `failure_count` on the task in PLAN.md
2. Write error message (or verifier tail) to the `error` field
3. Append `task_failed` to TASK_LOG

Then:

- **`failure_count == 1`**: Retry automatically. Append `task_retrying`. Re-dispatch.
- **`failure_count == 2`**: **Gate 2** — stop. Report both errors to user. Wait for decision:
  - *retry*: reset `failure_count` to 0, re-dispatch
  - *skip*: mark task `done` with note, continue (only if downstream tasks can proceed)
  - *abort*: halt all work, leave state as-is for manual inspection

Because dispatch.sh now self-corrects against the verifier and the PM escalates tiers
automatically, Gate 2 should fire rarely — only when even the `heavy` tier can't make the
verifier pass, or on repeated infra failures.

**Escalation log events** —
`tier_escalated`: `from_tier`, `to_tier`, `verifier_tail`, `new_model`, `new_fallback_model`.
`backend_escalated`: `from_backend`, `to_backend`, `verifier_tail`, `new_model`.
`backend_skipped`: `backend`, `to_backend`, `reason` (exit-30 stderr tail).

---

## Framework Updates

When the user says "pull framework updates":

1. Commit any dirty project files first:
   ```bash
   git add PLAN.md TASK_LOG.md SPEC.md prompts/
   git diff --staged --quiet || git commit -m "Checkpoint project state before framework update"
   ```
2. Pull the framework:
   ```bash
   git subtree pull --prefix framework framework main --squash
   ```
3. Report what changed.

Do not modify any files under `framework/` — it is a read-only subtree.

---

## Recovery Protocol

If invoked mid-project (context was reset, prior session ended):

1. Read the Startup Sequence and shared ORCHESTRATOR.md recovery contract.
2. Inspect `orchestrator-state.py active`, logs/runs.jsonl, process identities and worktrees.
3. Preserve live work. Verify completed output against its actual commit and merge gates.
4. Reconcile dead/ambiguous reservations before retry; do not increment failure_count merely
   because a session ended. A missing finish record is not proof of failure.
5. If evidence cannot establish what happened, keep the task blocked pending reconciliation.
