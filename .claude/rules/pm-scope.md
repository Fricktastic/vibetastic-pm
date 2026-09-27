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
| Author a change in the target project | Builder via `dispatch.sh --worktree` (Tech Lead spec, critique when `risk`/`security`) | All five conditions of § Inline authoring gate hold — e.g. a trivial visual/layout nudge (lesson 6); otherwise hand the on-device pass to the human |

### Why there is a command for this (issue #42)

Delegation in this framework happens where the flow **forces** it, and almost nowhere else.
Measured on gamedaytastic:

| lane | status in the flow | dispatches |
|---|---|---|
| critic | hard precondition for any R1+/`security` build (since #50: `risk`/`security`) | **104** |
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
without an instrumentation artifact or a diagnosis report attached), not a wall. Issue #46
took the first step: a defect-fix spec carries Symptom / Mechanism / Evidence, and the critic
blocks a critiqued (`risk`/`security`) defect fix whose Evidence is missing or reasoning-only
(`VERIFY.md` § Pre-build critique).

## Inline authoring gate (issue #23)

Target-project code comes from a dispatched builder by default. The orchestrator may author a
change itself only when **all five** hold; if any fails, or it is unsure one holds, the change
goes to a builder through the normal flow.

1. **Bounded** — one file, small enough to read whole in the diff: a deletion, a label, a
   string, a constant, a comment, a layout value.
2. **No new mechanism** — no new type, interaction model, framework dependency the file does
   not already use, concurrency or lifecycle. Adopting a UIKit control is a new mechanism;
   renaming its label is not.
3. **Off the critical runtime path, or provably inert on it** (gamedaytastic: live audio). A
   change that would carry `risk: true` (a `PROJECT.md § Risk triggers` entry applies) or
   `security: true` fails 2 or 3 by definition — critique never gets skipped this way.
4. **Verifiable without a new test** — an existing test covers the behaviour, the change is a
   strict deletion of something a test now asserts against, or it is a visual change seen on
   the running product (`observation: runtime`). A change that needs a new or changed test is
   two files and a builder task.
5. **Declared** — an `inline_authored` TASK_LOG entry names the task, the file and each
   condition met (`state.md` § Inline authoring).

Why it is a gate and not a ban: the absolute rule was broken whenever it was cheaper to break
(gamedaytastic T066a, a two-line deletion; T066b, a moved accessibility label), and because
it admitted no gradation those were indistinguishable from T069, a rewrite onto a UIKit press
lifecycle with a new VoiceOver interaction that should never have been inline. The T063
precedent — hand-rolled gesture code that passed seven critic rounds and still armed at 63ms
on device — is the class conditions 2–3 keep in the builder lane: the change the orchestrator
most wants to write inline because it is interesting.

What an inline change skips: the Tech Lead spec, the pre-build critique (it is `risk: false`
by condition 3) and the builder dispatch. What still applies, unchanged:

- **A PLAN task.** Register it `in_progress` (never `pending`: the dispatch loop would pick
  it up) through `plan-update.py` with `agent: pm`, `risk: false`, `security: false`, a
  `verify_tier` and `observation: runtime | none` (`test` is unavailable: fail-on-base
  refuses a diff that changes no test). The transaction's event is the `inline_authored`
  entry.
- **A worktree, never the live checkout** —
  `git -C <code-dir> worktree add ../<project-name>-worktrees/task-T0XX -b task/T0XX`, the
  path and branch `dispatch.sh` would use. Commit there.
- **The merge gate** (`dispatch.md` § Merge gate), every check at the merged SHA:
  `merge_gate.py verify`, a runtime observation when declared, and an approving review from a
  **family-diverse** reviewer — `dispatch.sh --read-only --role reviewer --author-model
  <orchestrator model>`. The orchestrator is the author: a Claude orchestrator's change is
  never reviewed by a Sonnet subagent. This is #23's optional "critic diff review" made
  mandatory, because the merge gate already requires a review of that commit.
- **Fixups** stay inline only while the change still meets all five conditions; the
  reviewer fixup round cap counts them as usual. Otherwise re-spec via the Tech Lead.

## Hard rules (unchanged from the gates)

- Never author target-project code outside § Inline authoring gate — otherwise that is the
  builder's job via `dispatch.sh` (with `--worktree`, so builders never touch the live
  checkout). An inline change also lives in a worktree, never the live checkout.
- Never merge without the task's `VERIFY.md` ladder and a recorded diff-review verdict, both
  pinned to the commit being merged by `scripts/merge_gate.py` (`dispatch.md` § Merge gate).
- Never self-approve Gate 1 / Gate 2.
- MCP denials in `.claude/settings.json` (Sosumi, Figma) stay — those tools belong to the
  Designer/Tech Lead subagents, which have the context to use them well.
