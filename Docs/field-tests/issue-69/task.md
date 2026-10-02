# Field-test task (issue #69) — `scripts/tier-table.py`

You are in the vibetastic-pm framework repo. Write **one new file**, `scripts/tier-table.py`
(Python 3 standard library only, executable, no other file changes). Do not commit;
the orchestrator commits.

## What it does

`python3 scripts/tier-table.py [path-to-MODELS.md]` (default: `MODELS.md` in the repo root)
prints, on stdout, one JSON object mapping each builder backend to its tier ladder:

```json
{
  "codex":    {"fast": {"model": "...", "effort": null, "fallback": null}, "standard": {...}, "heavy": {...}},
  "opencode": {"fast": {"model": "...", "effort": null, "fallback": "..."}, ...},
  "claude":   {"fast": {"model": "sonnet", ...}, "standard": {...}, "heavy": {...}}
}
```

Rules:

1. **codex** comes from the table under the `### Codex tier column` heading only. `model` is
   the slug in the second column with any `@<effort>` suffix removed; `effort` is that suffix
   (`"low"`) or `null` when there is none. `fallback` is always `null`.
2. **opencode** comes from the table under the `## OpenCode Tiers` heading only (stop at the
   next heading of the same or higher level). `model` is the second column, `fallback` the
   third, `effort` always `null`.
3. **claude** is fixed by the prose in MODELS.md: `fast` and `standard` are `sonnet`,
   `heavy` is `opus`; `effort` and `fallback` are `null`.
4. MODELS.md also has a `## Previous Tier Models` section whose tables use the same row shape.
   **It must never be read as a live tier.** Neither may any table outside the two named
   sections.
5. Slugs are the backtick-quoted text in the cell, without the backticks. Ignore any cell text
   outside the first backtick pair.
6. Output keys in the order shown, `json.dumps(..., indent=2)`, trailing newline.
7. Exit 1 with a one-line message on stderr if the file is unreadable, or if either named
   section is missing or does not yield all three tiers. Exit 0 otherwise.
8. `--tier <backend> <tier>` prints just that rung's `model` (plain text, one line) and exits 0;
   an unknown backend or tier exits 1.

## Done when

- `python3 scripts/tier-table.py | python3 -m json.tool` succeeds.
- For every backend and tier, `--tier <backend> <tier>` agrees with `dispatch.sh`'s
  `tier_expected_model` for the same pair.
- A MODELS.md copy with the `### Codex tier column` heading removed makes it exit 1.

End your report with exactly one line: `ACCEPTANCE: MET` or `ACCEPTANCE: UNMET — <why>`.
