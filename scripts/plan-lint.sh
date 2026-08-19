#!/bin/bash
# plan-lint — structural validator for PLAN.md.
#
# The whole dispatch loop branches on PLAN.md fields; one malformed write (wrong indent,
# dropped depends_on, bad status) silently corrupts the loop. Run this after every PLAN.md
# write to convert silent corruption into an immediate, loud error.
#
# Usage: bash framework/scripts/plan-lint.sh [PLAN.md]     (default: ./PLAN.md)
# Exit:  0 = clean
#        1 = STRUCTURAL corruption (missing required field, duplicate id, unknown or cyclic
#            dependency, unparseable tasks: section). This is the case CLAUDE.md means by
#            "fix it now, before anything reads the file".
#        2 = cannot read the file
#        3 = VOCABULARY drift only (an unrecognised agent/status/tier value, structure fine)
#
# [0b] Why 3 exists: the enums below predated the multi-backend design and the SUPERSEDED
# convention, so a structurally perfect PLAN.md exited non-zero — 52 lines on gamedaytastic,
# every one vocabulary, none structural. A rule that cannot be satisfied gets ignored, and an
# ignored linter is not a safety net. Separating the exit codes keeps the hard rule hard
# while letting vocabulary drift be visible without being blocking.
#
# Checks (parser is regex-based on purpose — no PyYAML dependency on a stock macOS python3):
#   - YAML frontmatter block exists (--- ... ---)
#   - every task has id, stage, title, agent, status, depends_on, failure_count
#   - status ∈ {pending, in_progress, done, failed, superseded}
#     (superseded = replaced by a later task; terminal, like done)
#   - agent ∈ {designer, architect, opencode, codex, claude, pm, user, null},
#     optionally qualified with the model that ran it ("codex gpt-5.6-sol@low")
#     (user = explicit human pass, e.g. a VERIFY.md device-only check;
#      codex/claude/opencode = the builder backend per MODELS.md § Builder Backends;
#      null = registered but not yet assigned a backend)
#   - task ids unique; every depends_on target exists; dependency graph has no cycles
#   - failure_count is a non-negative integer
#   - tier (if present) ∈ {fast, standard, heavy}; verify_tier (if present) ∈ {R0, R1, R2}
set -u

PLAN_FILE="${1:-PLAN.md}"
[ -r "$PLAN_FILE" ] || { echo "plan-lint: cannot read $PLAN_FILE" >&2; exit 2; }

python3 - "$PLAN_FILE" <<'PY'
import re, sys

path = sys.argv[1]
text = open(path).read()
errors = []          # structural — exit 1
vocab  = []          # vocabulary drift — exit 3, never blocking

m = re.match(r"\A---\n(.*?)\n---\s*(\n|\Z)", text, re.S)
if m:
    fm = m.group(1)
else:
    # Tolerate an unclosed frontmatter block (observed in long-running real PLAN.md files:
    # YAML tasks flow into markdown prose with no closing ---). Lint the whole region; the
    # '- id:' chunking below ignores non-task prose.
    m2 = re.match(r"\A---\n(.*)", text, re.S)
    if not m2:
        print(f"plan-lint: {path}: no YAML frontmatter block (--- ... ---)", file=sys.stderr)
        sys.exit(1)
    print(f"plan-lint: {path}: note — frontmatter never closed with ---; linting whole file")
    fm = m2.group(1)

# Split the tasks: section into per-task chunks on "  - id:" list items.
tasks_m = re.search(r"^tasks:\s*$(.*)", fm, re.M | re.S)
if not tasks_m:
    print(f"plan-lint: {path}: no tasks: section in frontmatter", file=sys.stderr)
    sys.exit(1)

# Double-quoted YAML scalars have a deliberately small escape vocabulary. A live PLAN.md
# contained Swift's @Environment(\.dismiss) in notes:, where \. makes the whole YAML
# document unparseable even though this regex linter previously accepted it. Check the
# simple one-line scalars we support so authors use a legal escape, single quotes, or a
# block scalar for code-heavy text.
simple_escapes = set('0abtnvfre"\\N_LP/ ')
in_tasks = False
current_task = None
for line_no, line in enumerate(fm.splitlines(), 1):
    if re.match(r"^tasks:\s*$", line):
        in_tasks = True
        continue
    if in_tasks:
        task_id_match = re.match(r"^\s+- id:\s*(\S+)", line)
        if task_id_match:
            current_task = task_id_match.group(1).strip("\"'")
    scalar_match = re.match(
        r'^\s*(?P<field>[A-Za-z_][A-Za-z0-9_-]*):\s*"'
        r'(?P<value>(?:[^"\\]|\\.)*)"', line)
    if not scalar_match:
        continue
    value = scalar_match.group("value")
    i = 0
    while i < len(value):
        if value[i] != "\\":
            i += 1
            continue
        if i + 1 == len(value):
            escape, width = "\\", 1
        else:
            escape = value[i + 1]
            width = 2
            required_digits = {"x": 2, "u": 4, "U": 8}.get(escape)
            if required_digits is not None:
                digits = value[i + 2:i + 2 + required_digits]
                width += required_digits
                if len(digits) != required_digits or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
                    escape = "\\" + escape + digits
                else:
                    i += width
                    continue
            elif escape in simple_escapes:
                i += width
                continue
            else:
                escape = "\\" + escape
        context = current_task or f"line {line_no}"
        errors.append(
            f"{context}: {scalar_match.group('field')} contains illegal YAML escape "
            f"'{escape}'; use a legal YAML escape or a single-quoted/block scalar")
        i += width

chunks = re.split(r"(?m)^(?=\s+- id:)", tasks_m.group(1))
tasks = {}
order = []
for chunk in chunks:
    idm = re.match(r"\s+- id:\s*(\S+)", chunk)
    if not idm:
        continue
    tid = idm.group(1).strip("\"'")
    if tid in tasks:
        errors.append(f"duplicate task id {tid}")
        continue

    def field(name):
        fm_ = re.search(rf"(?m)^\s+{name}:\s*(.*)$", chunk)
        return fm_.group(1).strip() if fm_ else None

    t = {k: field(k) for k in
         ("stage", "title", "agent", "status", "depends_on", "failure_count",
          "tier", "verify_tier")}
    tasks[tid] = t
    order.append(tid)

    for req in ("stage", "title", "agent", "status", "depends_on", "failure_count"):
        if t[req] is None:
            errors.append(f"{tid}: missing required field '{req}'")
    if t["status"] and t["status"] not in (
            "pending", "in_progress", "done", "failed", "superseded"):
        vocab.append(f"{tid}: unrecognised status '{t['status']}'")
    # An agent may be a bare role/backend, or a backend qualified with the model that ran
    # it ("codex gpt-5.6-sol@low") — MODELS.md allows an @effort suffix, so match on the
    # first token only.
    if t["agent"]:
        _agent_head = t["agent"].split()[0] if t["agent"].split() else t["agent"]
        if _agent_head not in ("designer", "architect", "opencode", "codex", "claude",
                               "pm", "user", "null", "~"):
            vocab.append(f"{tid}: unrecognised agent '{t['agent']}'")
    if t["failure_count"] is not None and not re.fullmatch(r"\d+", t["failure_count"]):
        errors.append(f"{tid}: failure_count must be a non-negative integer, got '{t['failure_count']}'")
    if t["tier"] and t["tier"] not in ("fast", "standard", "heavy", "null", "~"):
        vocab.append(f"{tid}: unrecognised tier '{t['tier']}'")
    if t["verify_tier"] and t["verify_tier"].split()[0].rstrip("#").strip() not in ("R0", "R1", "R2", "null", "~"):
        vt = t["verify_tier"].split("#")[0].strip()
        if vt not in ("R0", "R1", "R2", "null", "~", ""):
            vocab.append(f"{tid}: unrecognised verify_tier '{vt}'")

if not tasks:
    errors.append("tasks: section contains no parseable '- id:' entries")

# depends_on: existence + cycle detection
# A live corpus used Obsidian wikilinks as [[T055]]. YAML accepts that as a list of lists,
# but consumers expect a flat list of task IDs; spotting it before bracket stripping keeps the
# dependency graph from being silently corrupted.
deps = {}
for tid, t in tasks.items():
    raw = t.get("depends_on") or "[]"
    if raw.startswith("[") and "[" in raw[1:]:
        errors.append(
            f"{tid}: depends_on has nested-list value '{raw}'; use a flat list like "
            "[T001, T002] (or []) — [[T001]] is not valid")
    ids = [d.strip().strip("\"'") for d in raw.strip("[]").split(",") if d.strip()]
    deps[tid] = ids
    for d in ids:
        if d not in tasks:
            errors.append(f"{tid}: depends_on references unknown task '{d}'")

WHITE, GRAY, BLACK = 0, 1, 2
color = {t: WHITE for t in tasks}
def visit(n, stack):
    color[n] = GRAY
    for d in deps.get(n, []):
        if d not in tasks:
            continue
        if color[d] == GRAY:
            errors.append(f"dependency cycle: {' -> '.join(stack + [n, d])}")
        elif color[d] == WHITE:
            visit(d, stack + [n])
    color[n] = BLACK
for t in order:
    if color[t] == WHITE:
        visit(t, [])

if errors:
    for e in errors:
        print(f"plan-lint: {path}: STRUCTURAL: {e}", file=sys.stderr)
    for e in vocab:
        print(f"plan-lint: {path}: vocabulary: {e}", file=sys.stderr)
    sys.exit(1)

if vocab:
    for e in vocab:
        print(f"plan-lint: {path}: vocabulary: {e}", file=sys.stderr)
    print(f"plan-lint: {path}: structure OK ({len(tasks)} tasks); "
          f"{len(vocab)} vocabulary note(s) — not blocking (exit 3)")
    sys.exit(3)

print(f"plan-lint: {path}: OK ({len(tasks)} tasks)")
PY
