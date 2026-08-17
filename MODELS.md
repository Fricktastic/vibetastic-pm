---
version: "1.0"
---

# Model Inventory

Curated model slugs for each PM framework role. All models are referenced via OpenRouter.
Update this file to change which models the framework uses. Mark `confirmed` only after
testing the model end-to-end in a real session.

**This is the single source of truth for model selection. Do not hardcode model slugs
anywhere else in the framework.**

---

## Orchestrator (the partner session — A1 model)

There is no standalone PM session (retired 2026-06-29). The orchestrator is the partner
session running in the project's `<project>-pm/` directory, typically on **Opus** because it
also serves as the human's thinking partner. That is only affordable because the orchestrator
**delegates**: spec-writing to the Tech Lead tier, first-pass diff review to the Reviewer
(cheap, read-only), open-ended diagnosis to read-only `standard`/`heavy` dispatches. The
measured failure mode is the partner absorbing those roles itself at Opus rates —
RULES.md operating lessons 2–3 exist because that burned a session budget in one day.

**The standing orchestrator model is Opus (2026-07-17).** Fable is **not** an orchestrator:
it drains the Claude subscription window ~2× faster than Opus for no orchestration gain
(orchestration is coordination and judgment, not raw reasoning depth — the place Fable's
extra cost would buy something), and its security restriction disqualifies it from security
adjudication, which the orchestrator must always be able to do itself. Do not run the
partner session on Fable.

Fable's **only** sanctioned role is a deliberate, rare, **single-spawn adviser escalation**
for an exceptional **non-security** judgment call the orchestrator wants a second frontier
opinion on — spawned once, consumed, and logged as a `cost_event` (role `pm`, model
`fable`, with a one-line note on why the escalation was worth the window burn). It is never
a standing role, never the orchestrator, and never touches `security: true` work.

## Agent Roles

These agents are spawned via Claude Code's `Agent` tool, which accepts shorthand aliases
only (`opus`, `sonnet`, `haiku`, `fable`) — not pinned version slugs. `opus` resolves dynamically
to Anthropic's current Opus release (Opus 4.8 as of 2026-06).

Balanced cost bias (2026-06-29): the reasoning roles default to **Sonnet** and only the
Architect holds **Opus**, where peak reasoning earns its cost. Tier escalation (and re-speccing
a heavy-tier task that still fails Gate 2 on Opus) is the safety net. Telemetry
(`cost-report.sh`) drives further tuning — refine these once real data is collected.

| Role | Model | Escalate to | Notes |
|------|-------|-------------|-------|
| Designer | `sonnet` | `opus` | UI/structural reasoning; Sonnet handles most. (In Hometastic, design is frozen — Designer should not run at all.) |
| Architect | `opus` | — | Stage-2 subsystem design needs peak reasoning; invoked rarely, so low frequency = low cost |
| Tech Lead | `sonnet` | `opus` | Most frequently spawned reasoning role; a spec is cheap to redo, so default cheap. Escalate to Opus only for architecturally heavy work |
| Reviewer | opencode `standard` tier (or `sonnet` subagent) | orchestrator adjudicates | First-pass diff review runs cheap and read-only (`dispatch.sh --read-only` + `prompts/reviewer.md`); Opus reads only the verdict. **Family-diversity rule applies** (below). See VERIFY.md |
| Critic (pre-build) | codex `standard` (gpt-5.6-terra) or opencode `standard`, read-only | opencode `heavy` (glm-5.2) for `security: true` | Shift-left plan critique **before** dispatch on R1+/security tasks (`dispatch.sh --read-only` + `prompts/critic.md`); Partner adjudicates the verdict. **Family-diverse from the plan's author** (Tech Lead Sonnet / Partner Opus) — never an Anthropic critic of an Anthropic-authored plan. See VERIFY.md § Pre-build critique |

### Reviewer family diversity (2026-07-17)

The first-pass Reviewer must be a **different model family than the builder backend that
produced the diff** — same-family review reproduces the builder's blind spots. Concretely:
a diff built by the **claude** backend (sonnet/opus) must **not** be reviewed by the Sonnet
subagent variant; route it to the opencode `standard` tier (deepseek). Diffs from `codex`
or `opencode` may use either reviewer variant (for an opencode-built diff, pick a reviewer
family the builder didn't use). The claude backend is the minority lane, so the cost delta
of forcing its diffs onto the opencode reviewer is ~zero. Full rule and routing table:
`VERIFY.md` § Diff review.

The same diversity principle governs the **Critic (pre-build)**, measured against the *plan's*
author rather than a builder backend: the critic must be a different family than whoever wrote
the plan — Tech Lead (Sonnet) or the Partner (Opus). Route it to codex (gpt-5.6) or opencode
`standard`; never an Anthropic critic of an Anthropic-authored plan. Full rule: `VERIFY.md`
§ Pre-build critique.

To escalate a role for one spawn (e.g. a genuinely architectural Tech Lead pass), use the
`Escalate to` model and log it in the `cost_event` (see `.claude/rules/state.md`).

---

## Project configuration keys (2026-07-31)

Per-project routing and threshold knobs live in the project's **`PROJECT.md` frontmatter**,
alongside `builder_backends`. Rationale: `framework/` is a read-only subtree in every consuming
project, so a project cannot tune a framework default by editing the framework — it must have a
writable surface of its own. `PROJECT.md` is that surface (it is already read at startup and
already holds `builder_backends`; a second config file would only add a sync burden and a way
for the two to disagree).

Values in this file are the **defaults a project inherits by omitting the key**, not overrides.

| Key | Default | Consumed by |
|---|---|---|
| `builder_backends` | `[codex, claude, opencode]` | Build dispatch + backend escalation (`.claude/rules/dispatch.md`) |
| `reviewer_backends` | `[opencode]` | First-pass Reviewer routing (`VERIFY.md` § Diff review). Flipping this to `[claude, opencode]` is issue #14 and is **deferred to the 2026-08-17 telemetry review** — see the rationale below; do not change it without that review. |
| `claude_window_burn_threshold` | `null` (ungated) | Reviewer/Critic claude routing. Symmetric to `codex_weekly_burn_threshold` but over the 5-hour window `cost-report.sh` already computes. Leave `null` until telemetry supports a number — see the deferral note below. |
| `max_concurrent` | `{codex: 2, claude_builder: 1, claude_review: 2, opencode: 4}` | Parallel dispatch fan-out (`SPEC-03`). **claude is two lanes on purpose:** a builder holds the 5-hour window for a ~295 s median (48 observed runs) and competes with the orchestrator session itself, so it stays a codex-exhausted fallback capped at 1. The separate `claude_review` cap is pre-provisioned for the issue-#14 decision — a short read-only review is a materially different claim on the same bucket than a 25-minute build — and is **unused while `reviewer_backends` stays `[opencode]`**. |
| `critic_backends` | `[codex, opencode]` | Pre-build Critic routing (`VERIFY.md` § Pre-build critique). Adding a claude rung is part of issue #14 and carries the same 2026-08-17 deferral. The **family-diversity rule wins over this order** regardless — never an Anthropic critic of an Anthropic-authored plan. |
| `codex_weekly_burn_threshold` | `4000000` | `sol@high` burn gate (§ Codex tier column) |

**Config narrows, it never overrides a hard rule.** `reviewer_backends` / `critic_backends`
express *preference order among otherwise-legal lanes*. The family-diversity rules in
`VERIFY.md` are hard gates: if the configured first choice would put the reviewer in the same
family as the builder (or the critic in the same family as the plan's author), skip it and take
the next legal entry. A config that leaves no legal lane is a misconfiguration — report it to
the user rather than silently degrading to same-family review.

**Why `reviewer_backends` defaults to `[opencode]` and not `[claude]`.** The flat-rate-first
principle (§ Builder Backends) argues the other way, and `VERIFY.md` already permits a Sonnet
reviewer for codex-built diffs — so this default is metered spend the rules do not require.
It stands deliberately: review is the highest-frequency dispatch in the framework, and the
Claude 5h window is shared with the Opus partner/orchestrator session, so defaulting review to
Sonnet risks starving the orchestrator mid-task. Revisiting this — together with a
`claude_window_burn_threshold` gate symmetric to the codex one — is deferred pending the
2026-08-17 telemetry review. See issue #14.

Field data since supports the deferral rather than the flip: ISO-week 2026-W31 alone shows
**44 claude-backend runs** on gamedaytastic, before any review traffic is added to that lane
(`cost-report.sh` § Subscription-backend weekly burn). The plumbing to *decide* this landed
2026-08 — `claude_window_burn_threshold`, the `claude_review` concurrency lane, and the
partner-session records that close the burn-proxy drift issue #14 names. The default itself
did not move.

---

## Builder Backends (2026-07-15)

Build tasks dispatch through one of three backends, in the project's `builder_backends`
preference order from `PROJECT.md` (default: `codex → claude → opencode`). Rationale:
**exhaust flat-rate subscription capacity before spending metered tokens.**

| Backend | CLI | Billing | Limits | Role |
|---------|-----|---------|--------|------|
| `codex` | `codex exec` | ChatGPT subscription | **weekly** window only | Primary builder lane |
| `claude` | `claude -p` | Claude subscription | 5-hour windows | Second flat pool |
| `opencode` | `opencode run` | OpenRouter per-token | none | Always-available overflow; wide parallel fan-out |

dispatch.sh selects the backend from the model slug (or `--backend`); exit 30 = backend
unavailable → the PM skips to the next backend in order without a `failure_count` event.

### Codex tier column

The gpt-5.6 family is a size ladder — Sol (frontier), Terra (balanced), Luna (fast/cheap)
— a direct Opus/Sonnet/Haiku analog. Reasoning effort rides on the slug as `@<effort>`
(`gpt-5.6-sol@low`); omitted = the model's default. Escalation climbs **model first,
effort second**.

| Tier | Codex model | Notes |
|------|-------------|-------|
| `fast` | `gpt-5.6-luna` | default effort (medium) |
| `standard` | `gpt-5.6-terra` | default effort (medium) |
| `heavy` | `gpt-5.6-sol@low` | sol's default is low ("highly capable at lower efforts"); one retry bump to `gpt-5.6-sol@medium` counts as the heavy rung's second attempt, then one **burn-gated** bump to `gpt-5.6-sol@high` (below) before the backend counts as exhausted |

**Extended heavy rung — `sol@high`, burn-gated (2026-07-17, provisional).** Goal: preserve
the Claude subscription window (the scarcer pool) by letting the more generous codex
allowance absorb more before control falls through to the claude backend. After
`gpt-5.6-sol@medium` fails (exit 20), dispatch attempts `gpt-5.6-sol@high` **once** — but
only if the current ISO-week burn proxy is below `codex_weekly_burn_threshold` (below). If
the week's burn is at/above the threshold, **skip the @high attempt** and fall through to
the claude backend immediately (`backend_escalated`). This keeps the weekly-cliff guard —
we don't spend frontier-effort codex tokens late in a hot week — while shifting more volume
onto codex when there's headroom. `xhigh`/`max`/`ultra` efforts and prior-gen models
(gpt-5.5/5.4/5.4-mini — dominated by the 5.6 ladder) remain **not** auto-dispatched; sol
above high is a Gate 2 user decision. Codex fallback column is empty — backend order *is*
the fallback.

**Provisional — review 2026-08-17.** Mark confirmed only after `cost-report.sh` telemetry
shows the @high rung (a) actually salvages tasks that @medium failed and (b) does not blow
the weekly cliff. Until then the threshold default is conservative.

### `codex_weekly_burn_threshold` (tunable — set per project)

**Set in the project's `PROJECT.md` frontmatter, not here.** The framework value below is the
default a project inherits by omitting the key; `framework/` is a read-only subtree, so tuning
one project by editing this file is a rules violation (see § Project configuration keys).

| Key | Default | Meaning |
|-----|---------|---------|
| `codex_weekly_burn_threshold` | `4000000` tokens (ISO-week, all codex runs incl. `reasoning_tokens`) | Above this current-week burn proxy in `logs/cost.jsonl`, the `sol@high` attempt is skipped and control falls through to the claude backend. Conservative starting value pending telemetry — raise it once `cost-report.sh` shows the real weekly ceiling. |

The proxy is the same per-run token sum `cost-report.sh` already rolls up per ISO-week (see
Weekly-quota burn proxy below); the orchestrator reads the current week's total before
deciding the @high bump.

**Self-evidencing audit:** every `sol@high` dispatch must log the burn-proxy reading it
consulted in its `cost_event` (`burn_proxy:` field — see `.claude/rules/state.md`). An
`@high` dispatch with no burn figure in the log is an auditable violation, so the gate is
enforceable without touching read-only `dispatch.sh` and `cost-report.sh` can flag misses
mechanically.

### Claude tier column (subscription auth only)

`fast`/`standard` → `sonnet`, `heavy` → `opus`. dispatch.sh strips `ANTHROPIC_API_KEY`
from the claude backend's environment so this lane can never silently bill per-token
(the Anthropic-on-subscription-only invariant, enforced structurally).

### Weekly-quota burn proxy (codex)

Codex exposes **no in-band weekly-quota figure** (probed v0.144.4). dispatch.sh records
per-run tokens — including `reasoning_tokens`, the fastest quota-eater — into
`logs/cost.jsonl`; `cost-report.sh` rolls them up per ISO-week as a **burn proxy**.
The proxy paces; the ChatGPT usage UI is the authority — reconcile periodically.

---

## OpenCode Tiers

The Tech Lead recommends a tier (`fast` / `standard` / `heavy`) in its output metadata.
The PM maps that tier to the model below. For Stage 2 Architect-selected tasks, the
Architect classifies the task and picks a tier directly.

The PM passes `model` and `fallback_model` to dispatch.sh. If the primary exits non-zero,
dispatch.sh retries once with the fallback before returning failure to the PM.

**Hard invariant — Anthropic runs on the subscription only.** opencode is a separate process
and cannot use Claude subscription; it can only authenticate to OpenRouter (paid API).
So **no `openrouter/anthropic/*` model may ever appear in these tiers or their fallbacks** —
routing Anthropic through the API bills it at full rate, the exact opposite of "offload cheap
work." The opencode tiers are **non-Anthropic only**. The Anthropic "big gun" lives exclusively
on the subscription side (the Architect / Tech Lead / PM via the Claude Code Agent tool). When
the non-Anthropic ladder is exhausted, control returns there (Gate 2 re-spec on subscription
Opus) — it never falls through to API Opus.

**Escalation ladder:** the tiers below are ordered `fast` → `standard` → `heavy`. A task
starts at its assigned tier; when dispatch.sh's verifier loop is exhausted (exit 20) the PM
bumps to the next tier up and re-resolves `model`/`fallback` from this table. Each rung is a
**different model family** so an escalation is a genuinely fresh attempt, not the same model
retried. Above `heavy`, escalation leaves the API lane entirely → subscription (see Tier
Escalation in `.claude/rules/dispatch.md`).

| Tier | Model | Fallback | Confirmed | Use When |
|------|-------|----------|-----------|----------|
| `fast` | `openrouter/qwen/qwen3-coder-flash` | `openrouter/deepseek/deepseek-v4-pro` | yes (e2e 2026-07-02) | Simple bug fix, isolated change, clear root cause, no API surface changes |
| `standard` | `openrouter/deepseek/deepseek-v4-pro` | `openrouter/z-ai/glm-5.2` | yes (e2e 2026-06-29) | Multi-file feature, new patterns, moderate complexity |
| `heavy` | `openrouter/deepseek/deepseek-v4-pro` | `openrouter/z-ai/glm-5.2` | yes | Complex architecture, new subsystems, large context, significant reasoning. **Swapped 2026-08 on field data** — see below. |

**`fast` re-anchored to a true cost rung (2026-07-02):** gemini-3-flash-preview was dropped —
it priced *above* deepseek-v4-pro on both sides ($0.50/$3.00 vs $0.43/$0.87), so its only
advantage was wall-clock latency, which Tim ruled immaterial. `qwen3-coder-flash`
($0.195/$0.975, 1M ctx) replaces it: coding-specialist, ~55% cheaper than deepseek on input,
and — decisive given the gemini-3.5-flash stall history — a **non-reasoning** model, so the
reasoning-stall failure mode can't occur. **E2e-confirmed 2026-07-02**: multi-file task
through dispatch.sh, verify passed attempt 1, 39s, $0.016 billed, no stall.

All three primaries have **1M context** (not "low-context flash" — verified on OpenRouter
2026-06-29) and were tested end-to-end through opencode (`opencode run` completed a multi-file
task, exit 0). `deepseek-v4-pro` is both the cheapest and strongest SWE-bench model here, so it
anchors `standard`; `glm-5.2` (different family, ~Opus-4.8 FrontierSWE) is the `heavy` last
non-Anthropic attempt before bouncing to subscription.

`deepseek-v4-flash` was removed earlier (it failed on every run). **`gemini-3.5-flash` was
removed as a primary (2026-06-29):** it is a reasoning model that, on large task prompts, spends
its whole token budget in `reasoning` and emits no content/tool-call — the root cause of the
hour-long opencode stall in `hometastic-pm/logs/task-T052-20260628-094058.log` (verify-exhaust →
needless escalation to the old API-Opus heavy tier). `reasoning:{effort:low}` fixes it via the
raw OpenRouter API but did not take through opencode's `provider.models[*].options` passthrough,
so the reasoning models are simply not used as primaries. The reasoning-effort (`--variant`)
knob was also removed earlier: it broke the gemini tiers and opencode provides no per-model
validation for it.

**Slug format note:** Anthropic model slugs on OpenRouter use dots for version numbers
(`claude-opus-4.8`, `claude-sonnet-4.6`). Hyphens cause "model not found" errors.

Mark `confirmed` after testing each tier end-to-end in a real session.

---

## Pricing ($/Mtok)

Used by `cost-report.sh` to turn telemetry token counts into dollar estimates, and by the
PM/you when reasoning about tier choices. **Verify against current pricing before trusting
the dollar figures** — model prices change. Claude prices confirmed 2026-06-29.

**Anthropic rows are subscription-side** (Agent tool) — the $ figures are list API rates shown
for relative reasoning only; under Anthropic plan that work is covered by the subscription, not
billed per-token. The `openrouter/...` rows are the **only** real per-token spend (the cheap
offload lane). Context/prices verified on OpenRouter 2026-06-29.

| Model slug | $ in | $ out | Ctx | Notes |
|------------|------|-------|-----|-------|
| `opus` / `claude-opus-4.8` | 5.00 | 25.00 | 1M | Subscription only (Architect; Gate-2 re-spec). **Never via API/opencode.** |
| `fable` / `claude-fable-5` | 10.00 | 50.00 | — | Adviser-escalation only (§ Orchestrator). **Policy-restricted from security work** — never review or adjudicate a `security: true` task |
| `sonnet` | 2.00 | 10.00 | 1M | Subscription only (PM + Tech Lead default). **Floating alias** — the claude CLI resolves it to the current default Sonnet, so cost.jsonl records written as `sonnet` are not pinned to a version. |
| `claude-sonnet-5` | 2.00 | 10.00 | 1M | Subscription only. Pinned. Observed 3/3 in the field 2026-08. |
| `claude-sonnet-4.6` | 3.00 | 15.00 | 1M | Subscription only. Pinned, previous generation — keep listed so historical `sonnet`-era records and any pinned dispatch still validate. |
| `haiku` / `claude-haiku-4-5` | 1.00 | 5.00 | 200K | Subscription only (commit subagent / mechanical steps) |
| `openrouter/deepseek/deepseek-v4-pro` | 0.43 | 0.87 | 1M | **standard** primary; fast/heavy fallback — cheapest, top SWE-bench |
| `openrouter/z-ai/glm-5.2` | 0.95 | 3.00 | 1M | **heavy fallback** + reviewer/critic family-diversity rung. Demoted from heavy primary 2026-08 — see § Field results |
| `openrouter/qwen/qwen3-coder-flash` | 0.195 | 0.975 | 1M | **fast** primary — non-reasoning coder specialist (e2e confirmed 2026-07-02) |
| `gpt-5.6-sol` / `-terra` / `-luna` | — | — | — | Codex backend, ChatGPT subscription — no per-token spend; tracked as weekly-quota burn proxy (tokens in cost.jsonl) |
| `openrouter/google/gemini-3-flash-preview` | 0.50 | 3.00 | 1M | Dropped 2026-07-02 (priced above deepseek; latency-only advantage) |
| `openrouter/google/gemini-3.5-flash` | 1.50 | 9.00 | 1M | Dropped (reasoning-stall on big tasks); kept for reference |

Aliases (`opus`/`sonnet`/`haiku`/`fable`) are what the Claude Code Agent tool accepts for the
subscription-side planning roles; the `openrouter/...` slugs are what dispatch.sh records for
OpenCode builds. Rows share a price across the aliases that resolve to the same model.

---

## Field results (307 dispatches, 2026-06-29 → 08-06)

Rolled up from `hometastic-pm/logs/cost.jsonl` (64) + `gamedaytastic-pm/logs/cost.jsonl` (243).
This is the evidence base the tier table above should be tuned against; re-run the roll-up in
`framework/cost-report.sh` before changing a rung.

| Primary | n | exit 0 | median duration | Reading |
|---|---|---|---|---|
| `gpt-5.6-terra` | 136 | 131 (96%) | 78–242 s | The workhorse. Cheap lane, fast, reliable. |
| `openrouter/deepseek/deepseek-v4-pro` | 75 | 69 (92%) | 273–338 s | Confirmed `standard` primary; promoted to `heavy` primary. |
| `gpt-5.6-sol@low` | 20 | 20 (100%) | 185 s | Best heavy-class rung observed. |
| `sonnet` / `claude-sonnet-5` (claude backend) | 48 | 45 (94%) | 295 s | **One model, two names** — the CLI alias `sonnet` resolves to `claude-sonnet-5` (verified 2026-08-15), so records written under both slugs are the same lane. Works well; competes with the orchestrator's own window. |
| `openrouter/z-ai/glm-5.2` | 12 | 6 (**50%**) | 1327–1607 s | **Demoted from heavy primary.** |

**Why glm-5.2 was demoted.** 50% success at a ~22–27 minute median, against `sol@low` at
100%/3 min and deepseek at 92%/5 min. It also accounts for the dataset's timeout maxima. Caveat
worth keeping: n=12 is thin and heavy-tier tasks are selected for difficulty, so part of that
rate is task hardness — but a rung that turns a failed task into a lost half-hour is a bad rung
regardless of cause. It stays in the table as the heavy *fallback* and as the non-Anthropic,
non-OpenAI rung for reviewer/critic family diversity, where slowness is tolerable and
independence is the point.

**`fast` tier — unresolved, and it cannot resolve itself.** `qwen3-coder-flash` (the configured
`fast` primary) has **zero runs in all 307 dispatches**; the whole `fast` tier has 8, and those
went to `luna` / `gemini-3-flash`. The standing plan of "let dispatches accumulate, then read
the rescue rate" will never terminate, because nothing routes there — the observed tier split is
standard 60 / heavy 17 / fast 8. Pick one and close it:

- **Sample it deliberately** — force the next 10 genuinely simple tasks to `fast` and read the
  result. This also tests the Tech Lead's tier classifier, which the 60/17/8 split suggests is
  calibrated upward.
- **Or drop `fast`** — if six weeks of real work produced nothing simple enough to route there,
  the tier is theoretical and two rungs are easier to reason about.

A tier classifier that never says "easy" is itself the finding.

**Telemetry hygiene, same dataset.** `tier` was absent on 79% of dispatches and `verify-cmd` on
86% of gamedaytastic's, so both this table and any tier-level cost attribution rest on a
minority of runs. `dispatch.sh` now refuses a build dispatch with no verifier and warns on a
missing tier; expect the next roll-up to be materially more trustworthy than this one.

---

## Candidates

Models to evaluate for future tier assignments. Move to the table above once confirmed.

| Model | Potential tier | Notes |
|-------|---------------|-------|
| `openrouter/openai/gpt-5.4` | standard | 73.9% coding score, cheaper than 5.5 — non-Anthropic alt if a tier needs swapping |
| `openrouter/qwen/qwen3.5-flash-02-23` | fast (budget floor) | $0.065/$0.26, 1M ctx — 3× cheaper than qwen3-coder-flash, but a **reasoning model** (the stall-risk class); only adopt after a stall-free e2e test |
| `openrouter/qwen/qwen3.7-plus` | standard | $0.32/$1.28, 1M ctx — newest Qwen general line; only marginally cheaper than deepseek, no reason to swap today |

(`qwen-2.5-coder-32b-instruct` removed 2026-07-02 — obsolete: $0.66/$1.00, 128K ctx; beaten
on every axis by the qwen3.x line.)

(`deepseek-v4-pro` and `glm-5.2` were promoted into the active tiers on 2026-06-29 — see OpenCode
Tiers. `glm-5.2` actual OpenRouter price is $0.95/$3.00, 1M ctx.)

---

## Previous Tier Models (pre-May 2026)

Retained for reference and rollback. Tag `pre-fallback-models` points to the last commit using these.

| Tier | Model |
|------|-------|
| `fast` | `openrouter/google/gemini-2.5-flash` |
| `standard` | `openrouter/google/gemini-2.5-pro` |
| `heavy` | `openrouter/anthropic/claude-sonnet-4.6` |
