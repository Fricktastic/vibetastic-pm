# Design B — Trustworthy, risk-tiered verify gate

**Status:** spec proposal (2026-06-29). Built into the A1 shape (partner-as-orchestrator). Gate strictness: **tiered by risk** (Tim, 2026-06-29).

## The problem B fixes
The old gate = `xcodebuild` + mock-backed unit tests. hometastic **#72** passed all 57 tests and shipped broken: the `MockRestClient` (the `HistoryFetching` seam) bypassed the real `HARestClient` JSON decode, so a `convertFromSnakeCase`↔CodingKey collision never got exercised. **Green meant "compiles + mocks agree," not "works."** B makes green mean works — proportional to risk.

## Risk tiers
The partner assigns a tier at spec time = the **highest** tier any changed file/behavior touches (bias up when unsure). Recorded as `verify_tier:` in the task spec/PLAN.

| Tier | Applies to | Gate (cumulative) |
|---|---|---|
| **R0 — pure logic** | internal refactor, algorithms, no external I/O, no UI, no (de)serialization boundary | build + unit tests + **Opus diff-review** |
| **R1 — integration boundary** | JSON decode/encode, HTTP, HA service calls, persistence, config parsing | R0 **+ real-path integration test**: a representative **real payload** through the **actual code** (mock only the layer *above* the boundary, never the boundary itself) |
| **R2 — UI / user-visible data path** | SwiftUI views, tiles, glass, anything whose correctness is visual or depends on live data rendering | R1 **+ "run the app and look"**: build to sim/device, launch against live HA, navigate, **screenshot**, partner inspects that the feature actually renders |

## The two rungs that close the #72/#73 gaps
1. **Opus diff-review (all tiers).** Partner (or an Opus subagent) reads the builder diff for *intent and integration traps*, not just compilation — "does it do the thing, match the spec, mock the right layer." Structurally free in A1 (partner is already Opus, already holds the diff). This rung alone would have caught #72.
2. **Real-payload integration tests (R1).** Rule: **never mock the boundary you're testing.** Keep the `HistoryFetching` mock for `TileHistoryStore`'s *cache logic*, but ALSO require a decode test on `HARestClient` with a **captured real HA JSON fixture**. Fixtures come from the live instance — the partner pulls representative payloads via the **HA MCP** and commits them as test fixtures. (#72 fixture = a real `/api/history/period` response.)

## "Run the app and look" (R2) — mechanics
A1 makes this feasible: the partner is in-workspace and can drive the sim + read screenshots.
- Build to sim, `xcrun simctl` launch, navigate to the screen, capture a screenshot, partner reads the image and confirms render (e.g. trendlines visible, hero not clipped).
- Sim is authenticated (long-lived token) → live HA data flows, so data-path correctness is checked without hardware.
- Genuinely device-only checks (Liquid Glass GPU shimmer/perf per #56) remain a flagged human pass, not auto-gated.
- **Signed build required.** Unsigned sim builds hit Keychain `-34018` → demo-mode fallback → real data path/bugs don't reproduce. A demo-mode screenshot is NOT a valid R2 pass. (Learned on #73.)

### R2-flaky — launch-timing / nondeterministic render (the #73 lesson)
A **single screenshot is not a valid R2 pass for a race.** #73's hero band is a launch-layout race (`onScrollGeometryChange` latches a transient offset); the PM verified once, caught a lucky frame, and shipped a still-broken fix that reproduced on Tim's next run.
- **N cold launches:** kill + relaunch **5–10×**, prefer cold starts (transient is launch-layout-induced); pass only if the defect *never* appears.
- **Demand a named mechanism, not a bundle:** a race fix must state *why* it's now deterministic. Shipping 2–3 "complementary" changes hoping one wins is a tell the race wasn't pinned — reject and ask for the mechanism.
- **Scope discipline:** one bug = one mechanism per PR; split unrelated changes (e.g. #75's `ComplicationTile` `scaleEffect`) so the fix's effect is attributable.

### R1 must pin the REAL path, not a replica (the #74 near-miss)
#74's regression test rebuilt its own `JSONDecoder(convertFromSnakeCase)` instead of calling `HARestClient.get()` — so it stays green even if production `get()` later drops the strategy. An R1 test must exercise the **production decode/transport function**, not a hand-rolled copy. For types behind `URLSession.shared` (not injectable — the reason the `HistoryFetching` seam exists), use a **`URLProtocol` stub** feeding the fixture through the real `get()`.

## Where it lives / enforcement
- Encode tiers + rules in a shippable **`framework/VERIFY.md`** (every deployment inherits via subtree); reference from `RULES.md`.
- Task spec carries `verify_tier: R0|R1|R2`; `dispatch.sh`'s verify step becomes **tier-aware** (runs the matching checks). R0/R1 checks run in the backgrounded verify; R2 app-run + Opus review are partner-in-thread (bounded).
- Gate is a **merge gate** the partner enforces in A1 (branch protection only enforces review-count, which #68–#71 showed). Partner does not merge until the tier's checks pass.

## Cost posture
Tiering *is* the cost control: expensive rungs (R2 app-run, Opus review depth) hit only UI/data tasks; R0 stays cheap on the non-Anthropic builder tier. Consistent with the founding goal (subscription-first, offload cheap work).

## Pilot
First real exercise = run the **#72 (R1)** and **#73 (R2)** fixes through A1 + B end-to-end:
- #72: capture a real history JSON fixture via HA MCP → builder fixes decode + adds real-path decode test → Opus diff-review → merge.
- #73: builder fixes glass washout → build to sim → screenshot hero → partner confirms no clipping → merge.
Proves the gate catches the class of bug that motivated it, and shocks down the A1 wiring.

## Open questions for wiring
1. `verify_tier` field: add to the PLAN/TASK schema in `vibetastic-pm` templates.
2. Standardize the R2 sim-launch+screenshot into a reusable script vs. partner ad hoc (lean: a small `framework/scripts/app_screenshot.sh`).
3. Fixture location + capture convention (a `Fixtures/` dir + a "capture from HA MCP" note in VERIFY.md).
