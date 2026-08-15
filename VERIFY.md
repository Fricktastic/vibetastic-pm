---
framework: claude-pm
version: "1.0"
---

# VERIFY — the risk-tiered merge gate

Why this exists: the old gate (`build + unit tests`) passed code with real integration
bugs. hometastic #72 shipped with all 57 tests green because the mocked seam bypassed the
production JSON decode path; #73 shipped because a single lucky screenshot "verified" a
launch-timing race. **Green must mean "works," proportional to risk.** This file defines
what "verified" means per tier. The orchestrator (partner) enforces it as a merge gate —
branch protection alone does not.

---

## Risk tiers

The orchestrator assigns a tier at spec time = the **highest** tier any changed file or
behavior touches (bias up when unsure). Record it as `verify_tier:` on the task in PLAN.md
and state it in the task prompt.

| Tier | Applies to | Gate (cumulative) |
|---|---|---|
| **R0 — pure logic** | internal refactor, algorithms; no external I/O, no UI, no (de)serialization boundary | build + unit tests + **diff review** (see below) |
| **R1 — integration boundary** | JSON decode/encode, HTTP, service calls, persistence, config parsing | R0 **+ real-path integration test**: a representative **real payload** through the **production code path** |
| **R2 — UI / user-visible data path** | views, tiles, anything whose correctness is visual or depends on live data rendering | R1 **+ "run the app and look"**: build, launch, drive to the state, screenshot, orchestrator inspects |

Mechanical checks (build, tests, R1 fixtures) run inside `dispatch.sh`'s verify loop via
the verify-cmd argument. R2 app-runs and the diff review are orchestrator steps after the
dispatch returns green.

---

## Diff review — cheap-first, Opus adjudicates (all tiers)

Every builder diff gets read for **intent and integration traps** — "does it do the thing,
match the spec, mock the right layer, stay in scope" — before merge. Compilation and tests
do not check intent; this rung is what catches the #72 class of bug.

Cost structure (this is deliberate — see RULES.md operating lesson 3):

1. **First pass runs on a cheap tier.** Dispatch a **read-only** review (`dispatch.sh
   --read-only` with `prompts/reviewer.md` rendered for the task) on the `standard` tier,
   or spawn a Sonnet subagent. The reviewer returns a verdict + findings, changes nothing.
2. **Opus adjudicates only.** The orchestrator (already Opus in the partner model) reads
   the reviewer's findings against the spec and decides merge / reject / re-dispatch. It
   does not perform the line-by-line first pass itself — that is Tech-Lead-tier work and
   burning Opus on it was the single biggest measured cost sink.

A diff merged without this rung is a gate violation regardless of tier.

### Reviewer family diversity (hard rule, 2026-07-17)

**The first-pass Reviewer must be a different model family than the builder backend that
produced the diff.** Same-family review reproduces the builder's blind spots: the reviewer
finds the diff reasonable for exactly the reasons the builder wrote it that way, and the
rung silently degrades into self-review.

| Diff built by | Allowed first-pass Reviewer |
|---|---|
| `claude` backend (sonnet/opus) | opencode `standard` tier (deepseek) — **not** the Sonnet subagent |
| `codex` backend (gpt-5.6-*) | either variant (opencode `standard`, or Sonnet subagent) |
| `opencode` backend (deepseek/glm/qwen) | either variant (Sonnet subagent, or opencode `standard` on a **different** family than the builder used) |

Cost note: the claude backend is the minority lane (second in `builder_backends`, reached
only after the codex ladder is exhausted), so forcing its diffs onto the opencode reviewer
costs ~zero in practice.

### Reviewer lane selection

Which of the *allowed* variants above gets used is the project's call: take the first entry of
`reviewer_backends` in `PROJECT.md` frontmatter (default `[opencode]`) that the diversity table
permits for this diff. **The table is a hard gate and config is only a preference** — if the
configured first choice would be same-family with the builder, skip it and take the next legal
entry. If no configured entry is legal, stop and tell the user; never fall back to same-family
review.

Note the default is deliberately *not* the flat-rate claude lane even for codex-built diffs,
where the table permits it — review volume against the shared 5h window would compete with the
orchestrator. Deferred pending the 2026-08-17 telemetry review; see `MODELS.md` § Project
configuration keys and issue #14.

---

## Pre-build critique — shift-left review (R1+ / security, 2026-07-20)

Diff review reacts: it runs *after* the builder has already burned budget, and it can only
find the gotcha once it is in the diff. The **pre-build critique** is the mirror rung — it
reads the **plan** for blast radius, lost behavior, and underspecification **before** dispatch,
which is the cheapest place to fix a design-level gotcha. It exists to catch the "just talk to
the Partner and let it go" failure: a technically-correct diff that passes verify and still
does the wrong thing or breaks something adjacent.

**Applies to** any build task at **R1 / R2 or `security: true`** — whatever its origin (a Tech
Lead task spec, an Architect Stage-2 task, or a change the Partner talked itself into
conversationally). **R0 / isolated tasks skip it** (bias up when unsure — the rung is cheap, a
missed consequence is not). Because every target-code change flows through `dispatch.sh` (the
Partner never writes target code), wiring the rung to the dispatch boundary catches the
conversational path for free — see `.claude/rules/dispatch.md` § Pre-Build Critique.

Cost structure — identical cheap-first / Opus-adjudicates split as diff review:

1. **The critique runs on a cheap read-only tier.** Dispatch `dispatch.sh --read-only` with
   `prompts/critic.md` rendered for the task. The critic returns a verdict + findings, changes
   nothing.
2. **The Partner adjudicates only.** It reads the findings against SPEC and decides
   dispatch / rework / escalate — it does not perform the plan critique itself at Opus rates.

### Critic family diversity (hard rule)

**The critic must be a different model family than whoever authored the plan** — the Tech Lead
(Sonnet) or the Partner (Opus). Same-family critique reproduces the author's blind spots, the
same failure the Reviewer's diversity rule guards against. Route the critique to the **codex**
backend (gpt-5.6-terra, `standard`) or **opencode** `standard` — never an Anthropic critic of an
Anthropic-authored plan. (This is the genuinely good use of the flat-rate codex lane in a review
role.)

Lane selection follows `critic_backends` in `PROJECT.md` frontmatter (default
`[codex, opencode]`), with the same precedence as the Reviewer: diversity is the hard gate,
config only orders the legal choices.

### Security floor

For `security: true` the critique runs at a **capable rung**, not the cheap tier: a non-Anthropic
reviewer at or above Sonnet capability (opencode `heavy`, glm-5.2) or a family-diverse Sonnet
subagent. **Fable is never used** (policy-restricted from security work — MODELS.md § Orchestrator).

### Adjudication

The Partner reads the critic's output:

- **BLOCKING findings must be resolved before dispatch** — fold them into the spec / re-plan via
  the Tech Lead, or record an explicit **user override** in TASK_LOG (`critic_override`, with the
  finding and the reason). No silent proceed.
- **ADVISORY findings** are logged (`critic_returned`); fold in at the Partner's discretion.
- **`RECOMMENDED_VERIFY_TIER`**, if higher than the task's stated `verify_tier`, **raises it**
  (bias up) before dispatch.

**A R1+/security task dispatched to a builder with an unresolved BLOCKING finding is a gate
violation**, exactly as a diff merged without the diff-review rung is.

---

## Security-sensitive tasks — the one place cheap-first is wrong (2026-07-17)

A task carries `security: true` when its diff touches **auth, credentials, keychain,
entitlements, network trust, sandboxing, or input validation on data from outside the
app**. The Tech Lead sets the flag in its result metadata; the Architect sets it for
Stage-2 tasks. The orchestrator records it on the task in PLAN.md alongside `verify_tier`
and states it in the task prompt. **Bias up when unsure** — the flag is cheap, a miss is not.

Effect — the review rung is forced up, in two places:

1. **First-pass review runs on Sonnet minimum.** The cheap opencode tier is not an
   acceptable first pass for a security diff. Use a Sonnet subagent (or higher), or an
   opencode reviewer only *in addition to*, never *instead of*, the Sonnet rung.
2. **Adjudication is mandatory Opus, and is never delegated.** The orchestrator reads the
   security diff itself. This is an explicit, deliberate exception to RULES.md operating
   lesson 3 and to the pm-scope delegation defaults. **Fable must never be used** for
   security adjudication or security review — it is policy-restricted from security work
   (MODELS.md § Orchestrator).

Where this collides with the family-diversity rule above (a claude-built security diff),
the **security floor wins**: satisfy diversity by picking a non-Anthropic reviewer at or
above the Sonnet capability rung (opencode `heavy`, glm-5.2) *in addition to* the Sonnet
pass, or record in the TASK_LOG verdict that diversity was consciously traded for the
security floor. Never resolve the collision by dropping to the cheap tier.

**Rationale:** a missed security bug does not fail a verify loop — it ships, silently, and
the cost lands later and outside the project. Every other rung in this file assumes a
failure surfaces as a red build or a wrong pixel; security failures surface as nothing at
all. That asymmetry is why this is the one category where cheap-first review is the wrong
bias, and why the token cost of an Opus read is not a consideration here.

---

## R1 rules — real payloads through the real path

- **Never mock the boundary under test.** Mocks above the boundary (to isolate logic that
  *consumes* it) are fine; the decode/transport/persistence code itself must run for real.
- **No replicas.** A test that rebuilds its own decoder/client "equivalent to" production
  stays green when production drifts (the #74 near-miss). The test must call the
  production function. If the dependency isn't injectable (e.g. code hard-wired to
  `URLSession.shared`), stub the transport underneath it (`URLProtocol` stub) and feed the
  fixture through the real call.
- **Fixtures are captured, not invented.** Pull representative payloads from the live
  system (e.g. via the project's MCP integration or a curl against the real API) and
  commit them under the target project's test fixtures directory (convention:
  `<Tests>/Fixtures/<source>-<endpoint>.json`, with a comment noting capture date/source).

## R2 rules — run the app and look

- **Reproduce the triggering interaction**, not just a static launch. A scroll bug needs a
  scroll; a tap bug needs a tap. Synthetic input (e.g. Quartz drag scripts) is valid for
  artifact presence/absence, weak for landing a precise frame — flag precise-frame checks
  as a human pass.
- **Races need N cold launches.** For launch-timing / nondeterministic renders: kill and
  relaunch **5–10×** (cold starts); pass only if the defect never appears. A single
  screenshot is not a valid pass for a race. `scripts/app_screenshot.sh` automates this
  for iOS simulators.
- **Demand a named mechanism, not a bundle.** A race fix must state *why* it is now
  deterministic. Two or three "complementary" changes shipped hoping one wins is a tell
  the race wasn't pinned — reject and ask for the mechanism.
- **Verify the data source before the pixels.** Before judging rendering, confirm the
  component actually receives the data it should (trace the binding/fetch). An empty data
  source mimics layout bugs and burns cycles (RULES.md operating lesson 1).
- **Use a build configuration where the real data path runs.** e.g. unsigned iOS sim
  builds can fail Keychain access and silently fall back to demo data — a demo-mode
  screenshot is NOT a valid R2 pass.
- **Scope discipline:** one bug = one mechanism per PR. Split unrelated changes so the
  fix's effect is attributable.

---

## Enforcement

- Task specs and PLAN.md tasks carry `verify_tier: R0|R1|R2` and `security: true|false`.
  A `security: true` task forces the review rung up (Sonnet-minimum first pass, mandatory
  Opus adjudication — see Security-sensitive tasks above).
- The orchestrator does not merge until the tier's full ladder has passed and the diff
  review verdict is recorded (TASK_LOG event or PR comment).
- Genuinely device-only checks (GPU effects, haptics, perf feel) are flagged as an
  explicit human pass — never silently skipped, never auto-passed.
