# Field test — GPT-6 codex models vs the 5.6 ladder (issue #69)

One fixed task (`task.md`: write `scripts/tier-table.py`), run N times per model, each run in
its own worktree branched from `main`. Runs alternate between the two models of a pair.

```sh
bash Docs/field-tests/issue-69/run.sh fast  5   # gpt-6-luna      vs gpt-5.6-luna
bash Docs/field-tests/issue-69/run.sh heavy 5   # gpt-6.1-sol@low vs gpt-5.6-sol@low
```

- **Verifier (builder-visible):** `verify.sh` — compiles and emits JSON. Drives the
  self-correct loop only.
- **Correctness (hidden):** `check.py` — 18 checks against a tier map it derives from
  MODELS.md, including mutated copies (missing codex section, `Previous Tier Models` moved
  first, unreadable path) and a one-file diff. Validated 18/18 on a reference solution that
  also agrees with `dispatch.sh`'s `tier_expected_model`.
- **Measured:** `out/results.jsonl` (exit, wall, checks passed) + `logs/cost.jsonl` rows for
  prompts `ft69-*` (attempts, `quota_proxy_tokens` / fresh input + output).
- MODELS.md is untouched: candidates go into a temp copy via `MODELS_FILE`; no tier is passed
  (`DISPATCH_ALLOW_NO_TIER=1`).
- Cleanup: `git worktree list | grep ft69`, then remove those worktrees and `ft69/*` branches.

Decision rule: adopt a candidate only if it matches the incumbent's correctness and verify
pass rate, and is lower on quota-proxy tokens per run or wall time. Benchmarks don't count.
