---
# Example project policy for an iOS / iPadOS app (issues #50, #35).
# Copy the two keys below into your PROJECT.md frontmatter and the sections below into
# its body, then edit them to fit the project. Every part is optional: whatever you leave out
# falls back to the generic framework default (framework/scripts/project_policy.py).
# Check the result with:
#   python3 framework/scripts/project_policy.py --pm-dir . validate
#   python3 framework/scripts/orchestrator-doctor.py --pm-dir . --framework-dir framework
critic_round_cap: 2
reviewer_fixup_round_cap: 3
---

<!-- The framework owns the mechanisms (the critique gate, round caps, verdict ledger); this
     file is POLICY — what the tiers and triggers mean for one project. Tier names stay
     R0/R1/R2 because plan-lint and the gates key on them; their meaning is yours. -->

## Verify tiers

- R0: Pure logic — algorithms, model/state code, refactors with no I/O, no view and no
  (de)serialization boundary. Evidence: `xcodebuild build-for-testing` in the verify loop,
  then the orchestrator runs the unit tests on a simulator (`PROJECT.md § Test command`).
- R1: Integration boundary — JSON decode/encode, URLSession/HTTP, persistence (SwiftData,
  Core Data, files, Keychain), config parsing, third-party SDKs. Evidence: R0 plus a
  real-path test that feeds a captured payload (`<Tests>/Fixtures/<source>-<endpoint>.json`)
  through the production decoder/client; stub `URLProtocol` underneath, never the decoder.
- R2: User-visible data path — views, tiles, animation, anything whose correctness is visual
  or depends on live data rendering. Evidence: R1 plus run-and-observe: build, launch on a
  simulator or device, drive to the state, screenshot, and the orchestrator inspects it. Races
  need 5-10 cold launches (`framework/scripts/app_screenshot.sh`); use a build configuration
  where the real data path runs (an unsigned simulator build that falls back to demo data is
  not a valid R2 pass). GPU effects, haptics and performance feel are an explicit human
  device pass, never auto-passed.

## Risk triggers

- Audio session or playback ownership changes (who holds the session, interruption and
  route-change handling, background audio).
- Timing, concurrency or actor-isolation changes (timers, Task cancellation, main-actor hops,
  anything that races a user action).
- Shared playback or app state that more than one feature writes (a shared writer, a
  singleton, persisted preferences).
- A persisted data model or migration change (SwiftData/Core Data schema, stored JSON).
- A design decision the spec leaves open that the builder would have to guess.

<!-- Not a risk trigger on its own: "it is a UI change". A view tweak whose only risk is how
     it looks is R2 (it needs a run-and-look) but risk: false (it needs no pre-build
     critique). That split is the point of issue #50: verify_tier says what evidence proves
     the change; risk says whether the plan needs a second pair of eyes before it is built. -->

## Observations

- Run it on the exact commit being merged (the SHA `merge_gate.py` pins): a simulator build
  of that commit in a configuration where the real data path runs, or a device build for
  device-only behaviour.
- Cite the artifact: a screenshot from `framework/scripts/app_screenshot.sh`, a
  `log stream` / Console excerpt showing the branch taken and its values, or a recording —
  saved under the PM dir and passed as `--evidence`.
- Before and after: the same interaction on the base build (or the defect report's
  reproduction) and on the branch. Races: 5-10 cold launches on the branch, defect absent
  every time.
- GPU effects, haptics and performance feel are the operator's device pass; the operator
  records it (`merge_gate.py observe --kind device`).

## Test paths

- *Tests/
- *UITests/
- Fixtures/
- *Tests.swift
- *Test.swift

## Non-production paths

- *.md
- Docs/
- fastlane/metadata/

## Test support paths

<!-- Production files that carry test wiring. A new XCTest file only compiles into the test
target once the project file lists it, so fail-on-base overlays the branch's project file
with the tests; without it the base run fails on "no such test", not on the assertion. Pair
it with --expect-fail-pattern: the overlaid project file may also reference new production
sources the base tree lacks. With XcodeGen, list project.yml instead. -->

- *.pbxproj
