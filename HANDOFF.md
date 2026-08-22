# HANDOFF — vibetastic-pm (framework repo)

**Last session:** 2026-08-22 · **Branch:** `fix/42-investigate-lane` @ `25b584c`, **pushed**.
Working tree clean. `main` is unchanged at `748b5a9` — nothing merged this session.

**Stage:** framework maintenance. Constructive session: **4 PRs open in a stack**, 5 issues
filed, and the opencode tier table re-anchored on the first real bake-off it has ever had.
Nothing is half-built. One **known-wrong claim is sitting in the stack** and must be fixed
before merge — see the top of "Next action".

---

## READ THIS FIRST — binding operator directives (carried forward, still binding)

> **"I don't want to push updates to other projects unless I explicitly ask. A PR merge request
> from me is not a pull into all projects request."**

Merging a framework PR and propagating it to deployments are **separate decisions**. Merge when
asked; then stop, report which projects are behind, and offer — naming them.

> **gamedaytastic is the active project. hometastic is on the backlog.**

Do not start work, pull updates, or make changes in hometastic unless asked for it by name.

> **A concurrent session may be live in a project repo.** Check before touching one. **Never
> `git stash` in a repo you do not have exclusive use of.**

**New this session — no directive, but an observed preference:** the operator twice caught a
review that was too narrow ("did you already review and dismiss Kimi K3, GLM-5.3, Meta Muse,
Gemini 3.7?" / "GLM-5.2 has been out for weeks, hard to believe there is nothing on it"). Both
challenges were correct and both found real errors. **Do not present a dismissal as a review.**
If a candidate was skipped rather than evaluated, say so.

---

## Next action — fix the GLM claim BEFORE merging

`MODELS.md` on branch `fix/42-investigate-lane` currently asserts, in two places, that
**`glm-5.2` and `glm-5.3` are "unbenchmarked"** and that `glm-5` is "the only GLM on any coding
leaderboard." **That is false**, caught by the operator at the end of the session:

| benchmark | GLM-5.2 | comparison |
|---|---|---|
| SWE-bench **Pro** | **62.1** | beats GPT-5.5 (58.6) and GLM-5.1 (58.4) |
| **FrontierSWE** | **74.4%** | near-tie with Claude Opus 4.8 (75.1%) |
| MCP-Atlas (tool use) | 77.0 | beats GPT-5.5 (75.3), under Opus 4.8 (77.8) |

Sources: [VentureBeat](https://venturebeat.com/technology/z-ais-open-weights-glm-5-2-beats-gpt-5-5-on-multiple-long-horizon-coding-benchmarks-for-1-6th-the-cost),
[apidog](https://apidog.com/blog/glm-5-2-benchmarks/), [kie.ai](https://kie.ai/blog/glm-5-2-benchmark-deep-dive),
[emergent.sh](https://emergent.sh/learn/glm-5-2-benchmark).

**Root cause of the error:** the whole model review ranked on **SWE-bench Verified only**, and
GLM's line is scored on SWE-bench **Pro** / FrontierSWE. Absence from one leaderboard was
treated as absence of data. Worse, `MODELS.md` *already* carried the correct annotation —
"glm-5.2 (different family, ~Opus-4.8 FrontierSWE)" — and this session **overwrote a correct
note with a wrong one**.

Three things to do, in order:

1. **Revert the "unbenchmarked" language** in `MODELS.md` (two spots: the `glm-5` candidate row,
   and the closing sentence of the "Bake-off" section). Restore the FrontierSWE annotation.
2. **Re-decide the `fast` fallback.** The stack swaps `glm-5.2` → `glm-5` partly on the false
   premise. The price argument survives on its own ($0.60/$1.92 vs $0.97/$3.04, 11 providers
   vs 27) but it is now a cost/capability trade, not a free win. FrontierSWE is *closer to this
   framework's workload* than single-shot Verified, and 5.2 sits near Opus 4.8 there — that
   argues for keeping `glm-5.2`. **Operator's call; present both.**
3. **Re-check the other rungs on SWE-bench Pro / FrontierSWE**, not just Verified. The same
   blind spot may have mis-ranked minimax-m3, kimi-k2.6 and the deepseek variants against each
   other. The *bake-off* results (below) are unaffected — those are measured, not benchmarked.

Only then merge the stack, **in order**: **#38 → #40 → #43 → #44**.

---

## The PR stack (all pushed, none merged)

| PR | Base | Closes | What |
|---|---|---|---|
| **#38** | `main` | #37, most of #36 | Builder-facing preamble: who runs the tests |
| **#40** | #38 | #39 (first half) | `spec-body-guard.py` — stop the orchestrator holding task-spec bodies |
| **#43** | #40 | #41, #19 | Tier ladder made real + `sol@high` burn gate enforced in `dispatch.sh` |
| **#44** | #43 | #42 | `investigate.sh` diagnosis lane + MODELS.md tier re-anchoring |

Each PR body carries its own evidence and test plan. **Every new check in all four was
mutation-tested** — deliberately broken to confirm the selftest goes red. `bash
scripts/selftest.sh` is green at `25b584c` (76+ checks).

---

## The measurements this session rests on

All from gamedaytastic telemetry (54 partner sessions / 35 transcripts) unless noted.

- **Partner burn dominates:** orchestrator = **80% of cache tokens**, 62% of output. All builder
  dispatches = 20%. Metered OpenRouter spend is ~$6 lifetime.
- **90% of what the orchestrator ingests is file reads** (Read 62% + Bash reads 28%), across 968
  read operations.
- **By target:** `prompts/task-T0XX.md` **40.4%** (217 reads — a direct violation of
  `dispatch.md` [0g]), target-repo source 30.7%, PM state files 10.1%.
- **The codex-sandbox rediscovery loop is 2.4%** — the operator's opening hypothesis, measured
  and **disconfirmed**. Do not re-litigate; it is a real defect (fixed in #38) but not a cost sink.
- **Delegation follows gates:** critic 104 dispatches (hard precondition), reviewer 24 (merge
  gate), **diagnosis 10** (advice only). Earlier claim of "5 delegations" was wrong — it counted
  only the `Agent` tool and missed `dispatch.sh --read-only`. Corrected in #39/#42.
- **The escalation ladder has never fired:** `grep -c "tier_escalated\|backend_escalated\|
  backend_skipped" TASK_LOG.md` → **0** across 307+ dispatches. `tier` absent on 78% of runs.
  All 6 `sol@high` dispatches were first-attempt direct picks: 21.5M input tokens, ~37% of that
  ISO-week's codex burn.

---

## The bake-off (2026-08-22) — measured, not benchmarked

Six models, one identical task (write `scripts/models-check.sh` to a 64-line spec), each in its
own worktree, verifier `bash -n scripts/models-check.sh && bash scripts/selftest.sh`.
Correctness judged against independently-computed ground truth. **Total spend: $1.26.**

| model | exit | attempts | wall | billed | output correct |
|---|---|---|---|---|---|
| `qwen/qwen3-coder-flash` *(old `fast`)* | **20 — failed** | 3 | 248s | $0.194 | no — 4 of 14 slugs |
| `deepseek/deepseek-v4-flash-0731` | 0 | 2 | 379s | **$0.075** | no — $/tok unit bug |
| `deepseek/deepseek-v4-pro` *(old `standard`/`heavy`)* | 0 | 2 | 595s | $0.164 | yes |
| `deepseek/deepseek-v4-pro-0813` | 0 | 2 | 290s | $0.385 | yes |
| **`minimax/minimax-m3`** | 0 | **1** | **231s** | $0.160 | **yes** |
| **`moonshotai/kimi-k2.6`** | 0 | **1** | 348s | $0.207 | **yes** |

New tiers (committed at `25b584c`): `fast` **deepseek-v4-flash-0731** (fb `glm-5` ← *see Next
action*) → `standard` **minimax-m3** (fb `kimi-k2.6`) → `heavy` **kimi-k2.6** (fb
`deepseek-v4-pro-0813`). Three families, so diversity holds.

**Lessons that outlive the table** (also in memory, `model-selection-lessons`):

1. **SWE-bench did not predict this workload.** `v4-flash-0731` has the highest score of the
   cheap models and shipped the bug; `minimax-m3` explicitly reasoned around the same trap.
2. **Provider count predicted reachability better than capability.** `qwen3-coder-flash` is
   Alibaba-single-provider and was unreachable for hours under an account data policy — invisible
   for six weeks because the `fast` rung had *never once been dispatched*. Screen at
   `openrouter.ai/api/v1/models/<id>/endpoints`; treat <5 providers as disqualifying.
3. **Check tiered pricing in the endpoints API.** qwen's documented $0.195/$0.975 was the
   small-prompt band; above 128K it is $0.52/$2.60, which every real dispatch clears.
4. **Do not filter candidates by release date.** That hid minimax/kimi/glm-5 — and the winners
   came from the excluded set.

**Operator resolved the Qwen block mid-session** (a workspace-level data-policy gate). Qwen is
reachable now; it still lost the bake-off on its own merits.

---

## Issues filed this session

| # | Title | Status |
|---|---|---|
| **#37** | Every session re-derives the codex sandbox test limitation | fixed in #38; premise corrected in a comment |
| **#39** | Orchestrator file reads are 90% of token intake | half fixed in #40; source-read half deferred |
| **#41** | Tier ladder never fired; `tier` is a label, not a selector | fixed in #43 |
| **#42** | Delegation happens only where the flow forces it | fixed in #44 (friction reduction, deliberately not a block) |
| **#45** | `--worktree` derives its path from the prompt filename, not the branch | **open, unfixed** |

**#45 is the most dangerous open one.** `dispatch.sh:524` builds the worktree path from the
prompt basename alone and *reuses an existing path without checking which branch is in it*, so
two concurrent dispatches on different branches silently share one worktree — contradicting
`dispatch.md`'s "parallel dispatches can't collide." Found live during the bake-off (3 branches
requested, 1 created). The realistic trigger is a tier escalation racing a re-dispatch of the
same `task-T0XX.md` — **exactly what #43 is about to make happen for the first time.** Consider
fixing it before or alongside merging #43.

---

## Self-inflicted findings worth remembering

- **My own selftest had the #34 bug it exists to catch.** `gate_case` inherited ambient
  `DISPATCH_ALLOW_NO_TIER`, so with that exported the "no tier is refused" case passed while
  testing nothing. Mutation testing missed it because I mutated `dispatch.sh`, never the
  environment. Fixed at `8035a0e`. **Three of four builder models independently diagnosed it;
  qwen dismissed it** — that dismissal is what cost qwen the bake-off as much as its code did.
- **`pkill -f "<model-slug>"` killed the wrong process** — the task prompt text contains model
  slugs and the whole prompt is in the builder's argv. Kill by PID.
- **Do not read exit codes through a pipe.** I twice reported a `tail` pipeline's status as
  `dispatch.sh`'s and drew a wrong conclusion from it. Read `cost.jsonl`, or check `PIPESTATUS`.

---

## Uncommitted / scratch state

- Bake-off worktrees and `modeltest/*` branches: **cleaned up**. `git worktree list` shows only
  the main checkout.
- Scratch task prompts and per-run logs remain under `/tmp/modeltest/` — disposable.
- **Builder output not merged:** each bake-off model wrote a `scripts/models-check.sh` (a tool
  that checks `MODELS.md` slugs against the live OpenRouter catalog — it mechanically finds the
  delisted/snapshot/price-drift cases found by hand this session). The `minimax-m3` and
  `kimi-k2.6` versions are correct. **None were merged** — builder output needs the VERIFY.md
  diff-review gate, which the orchestrator may not self-approve. Worktrees are gone, so
  re-dispatch if wanted. Genuinely useful; worth a task.

---

## Carried forward from the prior session — still unresolved

Three uninvited framework pushes were never adjudicated (operator stated the rule, did not say
whether to revert):

| Repo | Commit | What |
|---|---|---|
| hometastic-pm | `d1b58fa` | framework subtree pull |
| hometastic-pm | `ada3dd1` | Stop hook wiring + TASK_LOG entry |
| gamedaytastic-pm | `9ef4e35` | framework subtree pull |

All on `origin/main`, framework/config only, no project state touched. **Ask before acting.**

Also still open and untouched: **#33** (Codex-orchestrator portability trial — a consultation
prompt is committed at `prompts/consult-orchestrator-portability.md`), **#32**, **#34**, **#29**,
**#26**, **#24**, **#23**, **#22**, **#21**, **#18**, **#17**, **#16**, **#15**, **#14**,
**#13**, **#12**, **#11**, **#10**, **#9**.

---

## Scope note

This session touched **only the framework repo**. No project repo was modified. No framework
update was propagated to gamedaytastic or hometastic — both remain behind, and per the standing
directive that stays the operator's explicit call.
