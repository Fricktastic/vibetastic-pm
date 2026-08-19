#!/usr/bin/env python3
"""plan-lint-hook — PostToolUse hook on Write/Edit, gating PLAN.md.

`.claude/rules/state.md` says to run plan-lint.sh after every PLAN.md write and treat
exit 1 as corruption to fix "before anything reads the file". That was instruction-based
discipline, and instruction-based discipline degrades over a long session — the same
reason log-agent-spawn.py exists. This makes it mechanical.

Wired by setup.sh into .claude/settings.json:
  "hooks": {"PostToolUse": [{"matcher": "Write|Edit", "hooks": [{"type": "command",
    "command": "python3 \"<pm-dir>/framework/scripts/plan-lint-hook.py\""}]}]}

Behaviour, mirroring plan-lint's exit taxonomy ([0b] — see plan-lint.sh):
  exit 1 (STRUCTURAL) -> hook exit 2: the linter output is fed back to the model as a
                         blocking error it must fix before doing anything else.
  exit 3 (VOCABULARY)  -> hook exit 0 with a one-line note on stderr. Non-blocking on
                         purpose: a linter that is permanently red gets ignored, and
                         gamedaytastic's 52 vocabulary-only errors are exactly that case.
  exit 0 / 2 / other   -> silent exit 0.

Only fires for writes whose path basename is PLAN.md. Any internal failure exits 0 —
this hook must never block unrelated work.
"""
import json
import os
import subprocess
import sys

try:
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    if os.path.basename(path) != "PLAN.md":
        sys.exit(0)

    # plan-lint.sh is always a sibling of this hook, in both layouts: a project's
    # <pm-dir>/framework/scripts/ and the framework repo's own scripts/. Do NOT try to
    # derive the pm dir by walking up — the walk differs between the two layouts, and
    # getting it wrong resolves to a nonexistent linter that this hook then silently
    # skips, leaving the gate looking installed while enforcing nothing.
    linter = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan-lint.sh")
    if not os.path.exists(linter):
        print("[plan-lint-hook] plan-lint.sh not found next to the hook — not linting.",
              file=sys.stderr)
        sys.exit(0)

    r = subprocess.run(
        ["bash", linter, path], capture_output=True, text=True, timeout=60
    )
    out = (r.stdout or "") + (r.stderr or "")

    if r.returncode == 1:
        print(
            "PLAN.md failed structural lint after this write. Per "
            ".claude/rules/state.md this is corruption: fix it now, before anything "
            "reads the file.\n\n" + out.strip(),
            file=sys.stderr,
        )
        sys.exit(2)
    if r.returncode == 3:
        print("[plan-lint] vocabulary drift only (exit 3) — not blocking.", file=sys.stderr)
except Exception:
    pass
sys.exit(0)
