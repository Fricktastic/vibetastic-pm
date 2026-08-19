# HANDOFF — vibetastic-pm (framework repo)

**Stage:** framework maintenance. Branch `phase-0-field-lessons`, 3 commits ahead of `main`,
**not yet merged or pushed**.

## Done this session (2026-08-15)

Phase 0 of a planned 4-phase rework. Specs, patches and rationale live in
`~/Developer/vibe-rework/` — read `NEXT.md` there first, then `SPEC-00-field-lessons.md`.

Evidence base: 307 real dispatches (hometastic 64, gamedaytastic 243) + `PROPOSALS.md` +
open issues #9–#14.

- `3d4d825` PROJECT.md tunable frontmatter keys (incl. the previously-uncommitted 2026-07-31 work)
- `8d014c4` Phase 0 — dispatch.sh, plan-lint.sh, critic/tech-lead prompts, rules, partner-burn hook
- `c75aa48` MODELS.md — § Field results, glm-5.2 demotion, Sonnet 5 alias + pricing

Verified before commit: all patches `git apply --check` clean; `bash -n` on every script;
`plan-lint.sh` on gamedaytastic's real PLAN.md went **52 errors → OK (74 tasks)** while still
exiting 1 on three synthetic corruptions; `cost-report.sh` output on the real 243-record
cost.jsonl unchanged except the new partner section; codex smoke dispatch passed with the
watchdog both disabled and live, leaving no `.turn` files.

## Facts established (don't re-derive)

- **`HANDOFF.md`'s old claim that the tracker was empty was wrong.** Six issues are open:
  #9 (critic convergence), #10 (codex stdin hang — **fixed here**), #11 (critic {{PLAN}}
  path — **fixed here**), #12 (parsing ≠ type-checking), #13 (external iOS sim verification
  = SPEC-02), #14 (route review to claude — **deliberately not done**, see below).
- `sonnet` is a **floating CLI alias** and resolves to `claude-sonnet-5` (verified
  2026-08-15). The 45 `sonnet` + 3 `claude-sonnet-5` records are one lane: 48 runs, 94%.
- **`reviewer_backends` stays `[opencode]`.** A mid-session patch flipped it to
  `[claude, opencode]`; that contradicted the existing prose in `MODELS.md` and `VERIFY.md`,
  which defer the flip to the **2026-08-17** telemetry review. Reverted. W31 alone shows 44
  claude-backend runs *before* any review traffic — the field data supports the deferral.
  The plumbing (`claude_window_burn_threshold`, `claude_review` lane, partner-burn hook)
  landed; only the default did not move.
- **`PROPOSALS.md` had 7 entries and 0 adoptions.** Phase 0 resolves or partly resolves all
  seven. They have **not** been marked harvested yet.
- The framework subtree is byte-identical across vibetastic / hometastic / gamedaytastic —
  **but neither project has pulled Phase 0 yet**, so both still run the old framework.
- `gh` write needs USER auth: `unset GH_TOKEN GITHUB_TOKEN` first. Push branches yourself.
- **Do not run git in these folders from a Cowork device-bridge session** — it cannot unlink
  `.git/index.lock` and will wedge the repo. Use a local shell.

## NEXT ACTION

1. Merge `phase-0-field-lessons` → `main` and push.
2. `git subtree pull` into **gamedaytastic-pm only**. Leave hometastic on the old framework
   for a week — that is a free A/B on identical projects.
3. Expect the first build dispatch after the pull to **fail with exit 2** if the orchestrator
   omits the verify command. That is the intended behaviour, not a regression.
4. After a week: re-run `cost-report.sh` and check the `verify_passed: null` and `tier: None`
   rates first. They were 86% and 79%.
5. Close #10 and #11 with commit refs; comment partial progress on #9 and #12; mark the
   `PROPOSALS.md` entries harvested.
6. Then Phase 1 (herdr) — `vibe-rework/SPEC-01-herdr-panes.md`. Confirm the stdout format of
   `herdr workspace create` / `herdr pane create` before the first real dispatch.

The **SessionStart hook** (the old next action) is deliberately deferred: Phase 1's persistent
panes make roughly half of it moot. The `SessionEnd`/`Stop` write-side hook for the
hometastic `handoff_gap` is unaffected and still worth doing — note a `Stop` hook now exists
for partner-burn telemetry, so that wiring is already in `setup.sh`.

## Open data threads

- **`fast` tier cannot resolve itself.** `qwen3-coder-flash`: zero runs in 307 dispatches;
  the whole tier has 8. Either force the next 10 simple tasks to `fast`, or drop the tier.
  The observed 60/17/8 split suggests the Tech Lead's tier classifier is calibrated upward.
- **glm-5.2 demotion rests on n=12** (6/12, 22–27 min median). Revisit if the next roll-up
  disagrees; two-line revert in `MODELS.md`.
- **Untested in the field:** the `{{SPEC_OUTPUT_PATH}}` Tech Lead contract (nothing enforces
  the injection mechanically) and `log-partner-burn.py`'s payload field names (it writes
  nothing rather than something wrong if they've moved — if `cost-report.sh` keeps saying
  "no partner records", dump the Stop payload and adjust the probe).
