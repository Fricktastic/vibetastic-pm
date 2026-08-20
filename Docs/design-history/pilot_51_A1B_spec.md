# Pilot spec — #51 sensor unit formatting (first A1 + B dispatch)

**Staged 2026-06-29. HOLD dispatch until `develop` is clean (0 open PRs) and the legacy PM is idle — Tim signals.** This is the first end-to-end exercise of A1 (partner-driven dispatch) + B (risk-tiered gate).

## Task (issue #51)
Add unit-aware value formatting to sensor-backed tiles (`complication`, `feature`, `metric_pair`, `glance`) so large magnitudes render compactly and on-brand. **Display-only** — entities stay in native units; no config/data change. Formatter keys off the entity's `unit` (config already carries it) / `device_class`, so it generalizes beyond solar.

Rules:
- Power: W → kW at ≥ 1,000 (`4397 W` → `4.4 kW`), 1 decimal.
- Energy: Wh → kWh at ≥ 1,000; native kWh keeps unit but gets thousands separators.
- General: thousands separators + sensible rounding; integer temps unchanged (consistent w/ `spec_homeview_rewrite`).
- Robustness: missing unit or non-numeric state → pass the raw value through, never crash.

## Verify tier: **R2** (user-visible render change)
Bulk of correctness is a pure formatter (R0-style unit tests), but the deliverable is visible, so the gate is R2 = unit tests + Opus diff-review + run-the-app-and-look.

### R0/R1 rung — formatter unit tests (exhaustive, grounded in live values)
| Input state | unit | device_class | Expected |
|---|---|---|---|
| 4396.6 | W | power | `4.4 kW` |
| 3444 | W | power | `3.4 kW` |
| 1000 | W | power | `1.0 kW` |
| 999 | W | power | `999 W` |
| 12500 | W | power | `12.5 kW` |
| 248.0 | Wh | energy | `248 Wh` |
| 1000 | Wh | energy | `1.0 kWh` |
| 2480 | Wh | energy | `2.5 kWh` |
| 108542.1 | kWh | energy | `108,542 kWh` |
| (empty) | W | power | passthrough, no crash |
| "unavailable" | W | power | passthrough, no crash |

Open question for the builder/Tech-Lead: does native kWh scale to MWh past some threshold, or only get separators? Default = separators only unless design says otherwise. (`108,542 kWh` not `108.5 MWh`.)

### Opus diff-review rung (all tiers)
Confirm the formatter is actually **wired into the tile view** for all four tile types (not just defined), keys off config `unit`/`device_class`, and the temperature/integer path is untouched. This is the rung that catches "tests green, tile still shows `4397 W`."

### R2 rung — run the app and look
Build to sim (authenticated → live HA), navigate home, **screenshot the Solar complication tile**, confirm it reads `~4.4 kW` (live ≈ 4397 W), not `4397 W`. Partner inspects the screenshot before merge.

## A1 dispatch plan (when pipe is clear)
1. Confirm `develop` at `0 0`, legacy PM idle.
2. Partner finalizes this as the task spec; classify `verify_tier: R2`.
3. Background-dispatch the implementation via `dispatch.sh` (cheap non-Anthropic tier).
4. On completion: run formatter unit tests → Opus diff-review → sim screenshot.
5. Open PR into `develop`; merge only when all R2 rungs pass.
6. Capture what the run reveals about A1 wiring needs (templates, VERIFY.md, screenshot script) → feeds the B-implement / A-wiring steps.

## Why #51 is a good first pilot
Low-stakes, isolated (display only — no collision with #72/#73's decode/glass files), yet genuinely R2, so it exercises the new "run and look" rung that the old gate never had — on the exact tile (#51 references the same solar complication) where a formatting miss would be invisible to unit tests.
