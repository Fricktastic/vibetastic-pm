# Orchestrator Scope (A1 partner model)

The orchestrator is the partner session — it also serves the human as a thinking partner,
so unlike the retired standalone PM it is *allowed* to touch anything. Scope discipline
here is economic, not absolute: every token the orchestrator spends reading code or docs
at peak cost is budget that a cheap tier could have spent instead (RULES.md operating
lessons 2–3). The rule is **delegate by default, do it yourself only when delegation is
clearly wasteful**.

## Delegation defaults

| Urge | Default action | Do it yourself only when |
|------|----------------|--------------------------|
| Read target-project source to find a root cause | **`bash framework/investigate.sh <code-dir> "<question>" [context-file] [tier]`** — one command, read-only, cheap tier, returns a report | The answer is one file you already know, and the user asked you directly |
| Form a root cause for a defect you cannot directly observe | **Instrumentation dispatch first** — log the branch taken and the values it was taken on, run it, read the log (RULES.md lesson 4) | Never — reasoning from source is what cost four sessions on one defect |
| Review a builder diff | Reviewer: `dispatch.sh --read-only` + `prompts/reviewer.md` (standard tier) or Sonnet subagent; you adjudicate the verdict | Never — first-pass review at peak cost is the measured top sink (VERIFY.md) |
| Write a task/build spec | Tech Lead tier | Trivially covered by the existing build-spec |
| Fetch framework/Apple/library docs | Tech Lead (it has Sosumi/doc tools) | The user asked a direct question needing one lookup |
| Trivial visual/layout nudge | Do it directly or hand to the human | — (dispatching a build cycle for a 40pt nudge is the waste; lesson 6) |

### Why there is a command for this (issue #42)

Delegation in this framework happens where the flow **forces** it, and almost nowhere else.
Measured on gamedaytastic:

| lane | status in the flow | dispatches |
|---|---|---|
| critic | hard precondition for any R1+/`security` build | **104** |
| reviewer | merge gate | **24** |
| **diagnosis** | *advice, in this table* | **10** |

Over the same period the orchestrator personally read **467 target-repo source files**
(~304K tokens, 30.7% of everything it ingested). Critique and review run constantly because
there is no legal way past them; diagnosis had a recommendation. The work it should have
absorbed came back as source reads at peak cost.

Part of that is friction — dispatching an investigation meant hand-writing a prompt file,
picking a backend and tier, and assembling the `dispatch.sh` line, while reading the file
was one tool call. `investigate.sh` collapses that to one command so the cheap path is also
the easy one.

This is deliberately **not** a block. `pm-scope.md`'s "the user asked you directly"
exemption is real and the orchestrator is also the human's thinking partner — a hook cannot
tell answering Tim from diagnosing by reading. If the ratio does not move, the next lever is
giving diagnosis a *place in the flow* (a defect task that cannot enter the build loop
without an instrumentation artifact or a diagnosis report attached), not a wall.

## Hard rules (unchanged from the gates)

- Never write implementation code in the target project — that is the builder's job via
  `dispatch.sh` (with `--worktree`, so builders never touch the live checkout).
- Never merge without the task's `VERIFY.md` ladder and a recorded diff-review verdict.
- Never self-approve Gate 1 / Gate 2.
- MCP denials in `.claude/settings.json` (Sosumi, Figma) stay — those tools belong to the
  Designer/Tech Lead subagents, which have the context to use them well.
