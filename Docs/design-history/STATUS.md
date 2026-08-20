# STATUS — Vibetastic (framework)

Living state tracker for work on the **build framework itself**. Update as work progresses; read after `CLAUDE.md` each session.

**Last updated:** 2026-07-02

---

## 2026-07-02 — B-implement + A-wiring landed in vibetastic-pm (4 local commits, NOT pushed)
Full framework review → implemented all four recommendations, on local `main` (`db71b59..32aed26`), awaiting Tim's OK to push (shared infra — propagates to deployments on next subtree pull):
1. **Telemetry fixed** (`db71b59`): `dispatch.sh` now records **actual billed cost + tokens** from opencode's SQLite session store (`~/.local/share/opencode/opencode.db`, `session` table — the INFO logs never carried usage, hence the old always-null grep). `verify_passed` is null when no verifier ran; `cost-report.sh` prefers actual `cost_usd` over estimates. Real number: **yesterday's whole dispatch spree cost $0.29.**
2. **B gate shipped** (`6dfbfd8`): `framework/VERIFY.md` (R0/R1/R2 ladder + all pilot rules), `verify_tier` in the PLAN template, `dispatch.sh --read-only` (exit 21 if the run dirtied the tree — structural safety for diagnosis/review dispatches), `scripts/app_screenshot.sh` (N-cold-launch R2 helper). Tested via stubbed opencode: read-only clean/violation, verify pass/exhaust all correct.
3. **Cheap-first review gate** (`bb128a7`): `prompts/reviewer.md` (read-only verdict+findings template) + Reviewer contract in RULES/MODELS. Resolves the design-B "Opus review non-optional" ↔ lesson-3 "review off Opus" tension: first pass on `standard` tier (or Sonnet subagent), **Opus adjudicates the verdict only**.
4. **PM session retired in docs** (`32aed26`): framework CLAUDE.md v2 (partner-orchestrator + A1 ergonomics), MODELS.md orchestrator section, setup.sh messaging, WALKTHROUGH historical banner. `.claude/rules/*` left as-is (mechanics still valid; framed in CLAUDE.md).
**Remaining:** ~~push + subtree-pull~~ (done, through `a98efc5`); A-wiring residue (surface operating rules into hometastic-run's CLAUDE.md); exercise the Reviewer flow on the #51 pilot.
**Also 2026-07-02 (`bd9f15b`, local):** fast tier re-anchored — gemini-3-flash dropped (priced above deepseek, latency-only advantage, Tim ruled latency immaterial), replaced by `qwen3-coder-flash` ($0.195/$0.975, 1M ctx, non-reasoning coder — stall-immune). **Unconfirmed until a real fast-tier e2e dispatch** (deepseek fallback covers meanwhile). Candidates refreshed from live OpenRouter API.

## ▶ NEXT SESSION (start here)
Two tasks, decided 2026-06-29:

1. **A1 wiring** — formalize "partner (Claude, here) drives dispatch + delegates the Tech Lead tier," so it's a deliberate setup, not improvised. Inputs: `design_A_partner_driven_dispatch.md` (the A1 shape, incl. the ergonomics/context-hygiene rules) + the four operating lessons now in `vibetastic-pm/RULES.md` "Operating lessons". Open wiring questions are listed at the bottom of the design_A doc (surface PM rules into the run CLAUDE.md; `dispatch.sh` partner-mode; state location; multi-project generality). **Context:** Tim has retired the separate deployment PM session — partner-as-orchestrator is the model in practice now (A1 confirmed).
2. **Cost review** — first real telemetry now exists from today's ~6 dispatches. Data: `hometastic-pm/logs/cost.jsonl` (per-dispatch records) + run `hometastic-pm/framework/cost-report.sh` for the rollup; pricing table in `vibetastic-pm/MODELS.md`. Most runs were `standard` (deepseek-v4-pro) + one read-only diagnostic. Cross-check against the session's Opus burn (Tim flagged: blew through plan session tokens + ~$20 API). Goal: validate tier choices + the "diagnosis→cheap" routing with real numbers.

Today's outcome (the pilot that produced both): A1+B run on hometastic #72/#73 — #72 merged, #73 functional fixes merged (data+washout+header), polish deferred to hometastic-code #78. The dominant cost was **diagnosis, not building** → the four lessons. See dated entries below for detail.

---

## Why this workspace exists
Stood up 2026-06-29 to keep framework strategy/refactor context out of `vibetastic-pm` (which ships **wholesale** as a `framework/` subtree into every deployment) — the same code↔run split hometastic uses. See `CLAUDE.md` § surfaces.

## What triggered it
A live hometastic build (develop) shipped two regressions that passed CI:
- **#72** — trendlines never render: `convertFromSnakeCase` in `HARestClient.get()` collides with `HAHistoryEntry`'s explicit `last_changed` CodingKey → all history decodes throw, swallowed. **The mock-backed unit tests bypassed the real decode path** — the verify gate's blind spot.
- **#73** — hero Liquid Glass washout/clipping regression.

Lesson: cheap builder tier + weak gate = green CI with real bugs. This is the case for refactor **B**.

## Roadmap (decided with Tim 2026-06-29)
- [x] **A (shape)** — **A1 confirmed**: partner-as-orchestrator. Opus partner in `…-run` drives `dispatch.sh`, specs inline, reviews diffs (Opus rung), manages PRs; `…-pm` demotes to runtime plumbing. Ergonomics/context-hygiene rules locked. See `design_A_partner_driven_dispatch.md`.
- [x] **B (spec)** — trustworthy **risk-tiered** gate (R0 logic / R1 integration / R2 UI). Real-payload integration tests (never mock the boundary under test), Opus diff-review all tiers, "run the app and look" for R2. See `design_B_trustworthy_verify_gate.md`. **Pilot: run #72 (R1) + #73 (R2) through A1+B.**
- [x] **B (implement)** — done 2026-07-02 (`6dfbfd8`/`bb128a7`): `verify_tier` in PLAN template, `framework/VERIFY.md`, `--read-only` dispatch, R2 screenshot script, fixture convention, Reviewer role. Not yet pushed/propagated.
- [~] **A (wiring)** — framework side done 2026-07-02 (`32aed26`: CLAUDE.md v2, setup.sh, MODELS). Remaining: surface operating rules into each `<app>-run` CLAUDE.md (hometastic-run).
- [ ] **Loop (deferred)** — autonomous queue-draining, only after B makes "green" mean "works."

## Decisions locked
- `vibetastic-pm` (source) ↔ `vibetastic-run` (partner): **stay separate** (wholesale-subtree leakage). Memory migrated here 2026-06-29.
- Redundant surface = the deployment PM *session*; collapsed via A1. The `…-pm` dir persists as plumbing.

## B review-overlay findings (2026-06-29, on legacy-PM PRs #74/#75)
First real B exercise — gated the legacy PM's bugfix stack. Results validate B:
- **#74 (#72 trendlines):** fix correct (CodingKeys removed → trendlines render, visually confirmed). Test is a **near-miss** — rebuilds its own `convertFromSnakeCase` decoder instead of calling `HARestClient.get()`, so won't catch future drift in production `get()`. **Mergeable**; follow-up = URLProtocol-stub real-path test.
- **#75 (#73 hero band):** **NOT fixed** — band reproduces on a signed `fix/73` build; expanded hero + collapsed strip both mounted at rest. The 3-change bundle (`onAppear scrollOffset=0`, `expandedOpacity<0.05` gate, ComplicationTile scaleEffect) mitigates a **launch-layout race** without pinning it; the PM's single-screenshot verify caught a lucky frame. **Reopen/redirect** — need a named deterministic mechanism + 5–10 cold-launch verify. ComplicationTile change is scope bleed, split out.
- Lessons written into `design_B_…md`: signed-build required, N-cold-launch for races, "named mechanism not a bundle," scope discipline, real-path (not replica) R1 tests.

## A1 pilot — #73 MERGED (option A), pilot complete (2026-06-29)
Functional fixes merged to `develop` via PR #75 (`4acd50d`), #73 closed, fix/73 deleted. Shipped: empty-hero weather pipeline fixed (entity loads + post-seed re-fetch), Liquid Glass washout removed, collapsed header decoupled & showing real weather. **Deferred** (cosmetic scroll-transition polish) → **issue #78**: tile fade/overlap at the collapsed-header edge (safe-area mask alignment), richer collapsed content (original spec had more than current condition), dead-code cleanup in PulseHero, faster initial weather fetch.
Permissions: broad allowlist added to project `.claude/settings.json` (dispatch/xcodebuild/xcrun/git/gh/osascript/sim-drive/read-only HA) to cut prompts; destructive still ask.

### Pilot conclusion (carry into A1/B framework design)
The A1+B loop *worked* but the session was dominated by **diagnosis cost**, not building. Ordered lessons: (1) **verify the component's data/inputs before its pixels** — the whole #73 saga was an empty data binding mistaken for layout bugs across ~5 cycles; (2) **route open-ended diagnosis to the cheap-but-capable models** (deepseek/glm) — me=Opus burned the plan's session tokens + ~$20 API; deepseek root-caused the data bug for cents; (3) **don't have the partner absorb the Tech Lead role on Opus** — spec-writing + review belong on a cheaper tier; (4) **tight visual tuning ≠ dispatch loop** — do trivial visual nudges directly or human-on-device; synthetic scroll (Quartz) is fine for artifact presence/absence, weak for landing precise frames.

## A1 pilot — #73 ROOT CAUSE CONFIRMED by deepseek diagnostic (2026-06-29)
**Root cause:** the HA weather entity `weather.pirateweather` is **never stored in `entityStore.states`** — it isn't in the config-built `entityMap`, so `HAEntityStore` skips it (`entityMap[result.entityId]` nil → `continue`, HAEntityStore.swift:31). So `WeatherHybridService.fetch()` step 1 (`states...first { $0.entityId == haEntityId }`) finds nothing → `currentTemp`/`condition` stay nil → hero renders only the default sun icon. WeatherKit (sim) always throws, so no gap-fill. Persistent every refresh (entity map never changes).
**Minimal fix (proposed):** in `AppState.swift` after the entity-map merge (~line 253), add the weather entity to the map like background entities do:
```swift
if let weatherEntity = loaded.weather?.haEntity { fullEntityMap[weatherEntity] = weatherEntity }
```
**Secondary (optional):** first-launch timing gap — weather `fetch()` runs before `get_states` seeds the store; add a one-time `refreshWeather()` after the WS `get_states` seed to close it (else on sim the hero is empty until the 15-min loop, but the entity-map fix makes the loop work).
**Status:** NOT implemented — awaiting Tim. Low-risk, ~2-line fix. Overlaps #63 (forecast H/L). Once data flows, the kept collapse/washout/decoupled-header work on `fix/73` can finally be judged. Full report: `hometastic-pm/logs/diag-hero-weather-20260629-162653.log`.

## A1 pilot — earlier framing: hero weather data empty (now root-caused above)
After 3 implementation cycles at the wrong layer (launch-race, drop-strip, pinned-header), the real bug: the home hero's weather (`appState.weatherHybrid.lastFetch`) is **nil/empty** — hero shows only a default sun icon, never temp/condition (proof: shows `sun.max.fill` default, not the real `wind` glyph for condition "windy"). All collapse "bugs" were downstream of empty data. `weather.pirateweather` entity is fine (state windy, temp 76); config `ha_entity` fine. So the data pipeline (`WeatherHybridService.fetch`) isn't delivering. **Read-only diagnostic dispatched to deepseek-v4** (bg) to find why — report → root cause + minimal fix. Overlaps #63. The collapse/washout/decoupled-header work is correct & worth keeping once data flows.

### Pilot lessons (the real deliverable)
- **Verify INPUTS, not just pixels/compilation.** Diagnosing from screenshots + code structure missed an empty data binding for hours. The gate must check that the component is actually receiving the data it should — "run and look" alone is blind to an empty source.
- **Diagnosis quality, not builder quality, was the bottleneck.** Every dispatch was clean/fast; the orchestrator being wrong about *what to build* cost the cycles.
- **Cost correction (Tim, 2026-06-29):** Opus burns hard — this session exhausted the plan's session tokens + ~$20 API overage. "Subscription = free" is FALSE in practice. The opencode cheap tiers (deepseek-v4/glm-5.2) are highly capable (well above Sonnet) and far cheaper per-run. ⇒ Route **open-ended diagnosis to the cheap-but-capable models too**, not reflexively to Opus; reserve Opus for genuine peak-judgment moments. Revisit the "subscription-first" framing in `[[vibetastic-pm-framework]]` MODELS.md with this in mind.
- **A1 flaw (Tim, 2026-06-29): the partner absorbed the Tech Lead role and ran it on Opus.** The framework puts spec/prompt-writing AND code-review on the **Tech Lead (Sonnet default)**, deliberately cheaper. In this pilot I (Opus) wrote every build-prompt and did every diff-review myself → peak-cost Opus on work meant for a cheaper tier = major token burn. **Fix the A1 shape:** partner ORCHESTRATES + makes judgment calls, but DELEGATES spec-writing and code-review to a Tech Lead agent (Sonnet subagent, or cheap opencode tier) — from within the workspace, not a separate session. Reserve Opus-me for decisions, diagnosis-judgment, and human interaction. Update `design_A_partner_driven_dispatch.md` accordingly.
- **Tight visual tuning is wrong for the dispatch loop** (build+test+synthetic-screenshot per nudge too slow); synthetic scroll (Quartz drag) is good for artifact presence/absence, weak for landing a precise scroll frame — human-on-device still wins there.

## A1 pilot — earlier: amendment implemented, washout fixed (superseded by data finding)
Builder implemented the amendment (deepseek-v4-pro, clean diff, Opus-reviewed, build+101 tests green). Scroll-repro screenshots confirm: **dark washout band eliminated; tiles fade at top (mask works)**. Open: synthetic drag can't reliably land the "just-collapsed" frame, so the collapsed-header legibility is a **human-in-the-loop check** — fixed build installed on Tim's booted sim; Tim to scroll + confirm header reads cleanly, then push fix/73 + merge. Commits on fix/73: `205f6c6` (design doc) + builder code (uncommitted until Tim's OK). R2-tooling lesson: synthetic scroll is good for presence/absence of an artifact, weak for landing a precise scroll offset → real finger still wins for fine aesthetic frames.

## A1 pilot — #73 reframed as a DESIGN AMENDMENT (2026-06-29)
Tim's design eye reframed the bug: the frosted strip is a **v5-pulse artifact that misreads its Apple Weather inspiration** (real Apple Weather has no glass bar — header over sky + content fade). Glass-over-dark-scroll-content washout is inherent, not tunable. **Owner approved dropping the strip.** This is the bigger pilot lesson: A1+B reproduced the bug, but the *partner's design judgment* identified it as a design artifact — saving us from polishing something that shouldn't exist.
- Design amended: `DESIGN_REFERENCE_v5-pulse.md` (committed `205f6c6` on fix/73). #73 issue updated.
- New impl: drop `glassEffect`/`glassEffectID` on collapsed hero → compact header over sky + text shadow; add top fade-gradient mask to the bento ScrollView. Dispatched (deepseek-v4-pro, standard, background).
- Gate: corrected R2 = scroll-drag repro (Quartz) screenshots, not static launch.

## A1 pilot — #73 (earlier): FALSE PASS caught, repro built
**Critical pilot lesson:** my first A1 cycle produced a *false pass*. I specced a "launch-layout race" fix (`scrollSettled` gate), dispatched it (clean, 101 tests), and my R2 gate screenshotted 8 **cold launches at rest** → all green. But the bug is **scroll-triggered**: on scroll the pinned collapsed strip's `glassEffect` samples the dark scrolled-up tiles → renders as a dark washed-out band (text ghosted). My gate never performed the triggering action, so it passed a still-broken build — the exact failure B exists to prevent, reproduced by me.
- **Corrected:** the launch fix (`347f90b`) is unpushed, NOT the fix (at most minor hardening). Nothing merged.
- **Repro harness built:** cold launch → synthetic touch-drag via Quartz (throwaway venv `scratchpad/simdrive`, `drag.py`) → `simctl io screenshot`. Reproduces the dark strip washout deterministically. This is the real R2 gate for interaction bugs.
- **B lesson (bigger than the cold-launch one):** R2 "run and look" must reproduce the **triggering interaction**, not a static launch. `simctl` can't swipe → needs synthetic drag (Quartz) or an XCUITest. Writing into VERIFY.md.
- **Root-cause hypothesis:** pinned strip (separate GlassEffectContainer above the scroll content) samples dark tiles, not sky → dark band + ghosted text. Fix is iterative Liquid Glass work. **Next: iterate the strip-glass fix against the repro.**

## A1 pilot — superseded entry (launch-race, was IN FLIGHT)
First real end-to-end A1+B run. **Tim owns #75; legacy PM stood down.** Partner (Claude) orchestrating:
- Reset `fix/73` → clean `origin/develop` (dropped the bad 3-change bundle; ComplicationTile reverts to develop → tracked as #77).
- Diagnosed the named mechanism: launch-layout race in `HomeView.onScrollGeometryChange`; fix = `scrollSettled` gate that ignores the transient spike until the first ≈0 rested reading. Single-file change.
- Builder prompt staged (scratchpad `task-73-hero-race.md`). **Dispatched** `deepseek-v4-pro` (fb glm-5.2), standard tier, background, verify = build + `xcodebuild test`.
- **Pending:** on green → Opus diff-review → R2 gate (5–10 cold launches + screenshots) → force-push `fix/73` to update PR #75.
- Learnings to capture for A-wiring: dispatch invocation ergonomics, R2 cold-launch automation, whether `verify_tier` belongs in the prompt.

## Pilot status
- **#72/#73** — in flight via the **legacy PM** (old process). When PRs land, gate them against B as a *review overlay* (does #72 include a real-payload decode test? was #73 visually confirmed?). No re-dispatch.
- **#51** — **staged** as the first A1+B *dispatch* pilot (`pilot_51_A1B_spec.md`). Re-tiered **R2** (user-visible render). Spec + grounded test cases (live solar 4396.6 W → `4.4 kW`) ready. **HOLD dispatch until `develop` is 0 open PRs and legacy PM idle — Tim signals "pipe clear."** Do not run alongside the legacy PM (two orchestrators on `develop`).

## Next action
On Tim's "pipe clear" signal: dispatch #51 through A1+B (background dispatch → formatter tests → Opus review → sim screenshot → PR). Capture what it reveals about A1 wiring needs.
