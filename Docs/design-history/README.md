# Design history

Historical design documents from the framework's own development, recovered from the retired
`vibetastic-run/` iCloud workspace on 2026-08-20. They were never under version control; this
directory is the archive.

**These are rationale, not rules.** Where any of them disagrees with the operative docs, the
operative docs win — without exception:

| Question | Authority today |
|---|---|
| How the orchestrator works | `CLAUDE.md`, `.claude/rules/*` |
| What the merge gate requires | `VERIFY.md` |
| Which model/tier to route to | `MODELS.md` |
| Operating lessons from the field | `RULES.md` |

## What each file is

| File | Written | What it captures |
|---|---|---|
| `design_A_partner_driven_dispatch.md` | 2026-06-29 | The proposal to collapse the standalone PM session into the partner workspace. Holds the **A1/A2/A3 comparison** and the reasoning for choosing A1 — the argument for the current architecture exists nowhere else. Wired 2026-07-02 (`32aed26`); the `-run` half of the model was itself retired 2026-08-17. |
| `design_B_trustworthy_verify_gate.md` | 2026-06-29 | The design that became `VERIFY.md`. Records **why** the R0/R1/R2 ladder exists: hometastic #72 passed all 57 tests and shipped broken because `MockRestClient` bypassed the real decode path, so green meant "compiles + mocks agree," not "works." Source of the "never mock the boundary you're testing" rule. |
| `pilot_51_A1B_spec.md` | 2026-06-29 | The first end-to-end A1+B exercise (hometastic #51, sensor unit formatting). Useful as a worked example of a task spec under the original gate. |
| `STATUS.md` | through 2026-07-02 | The framework's living status tracker before `HANDOFF.md` replaced it. Dated entries are the origin of the four operating lessons now in `RULES.md`, and record the first real telemetry (the whole 2026-07-01 dispatch spree: $0.29). |

## Known drift

`design_A` and `STATUS.md` both describe the orchestrator as running in `<project>-run/`. That
workspace was retired on 2026-08-17 — the orchestrator now runs in `<project>-pm/`. `design_B`'s
tier table also names Opus diff-review at every tier; the cheap-first Reviewer lane superseded
that on 2026-07-02 (`bb128a7`), with Opus adjudicating the verdict only.
