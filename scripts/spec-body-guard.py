#!/usr/bin/env python3
"""spec-body-guard — PreToolUse hook on Read/Bash, enforcing dispatch.md rule [0g].

The rule, already written down and already bold:

    The Tech Lead writes the spec file itself — the orchestrator never holds the body.
    [...] Never read the spec body into context. The critic and the builder both read it
    from disk.

Measured on gamedaytastic (35 partner transcripts, issue #39): 217 reads of
prompts/task-T0XX.md totalling ~367K tokens — 37% of everything the orchestrator ingested,
and the single largest line item in partner burn. One `Read` of task-T065.md cost 9,402
tokens by itself. The rule was correct, stated its own rationale, and was ignored 217 times.

That is the plan-lint story again: instruction-based discipline degrades over a long
session. So this makes it mechanical, in the same shape as plan-lint-hook.py — a hook that
BLOCKS (exit 2) and tells the model what to do instead.

What is blocked
    A whole-file read of <pm-dir>/prompts/task-T*.md or critic-T*.md — `Read` with no
    limit, or a Bash `cat`/`head -n <big>` of one.

What is explicitly allowed, because [0g] step 1-2 require them
    - existence / non-empty checks (`test -s`, `wc -c`, `ls`)
    - bounded greps and tail slices that pull the YAML block after the delimiter
    - `Read` with an explicit small `limit`
    - anything the builder or critic does — this hook only ever runs in the PM session

Escape hatch
    SPEC_BODY_GUARD_OFF=1 in the environment, mirroring DISPATCH_ALLOW_NO_VERIFY=1.
    Deliberate exceptions exist; silent ones should not. Justify it in TASK_LOG.

Wired by setup.sh:
  "PreToolUse": [{"matcher": "Read|Bash", "hooks": [{"type": "command",
    "command": "python3 \"<pm-dir>/framework/scripts/spec-body-guard.py\""}]}]

Any internal failure exits 0. This hook must never block unrelated work.
"""
import json
import os
import re
import sys

# A spec body is task-T<digits> or critic-T<digits> under a prompts/ directory.
SPEC_RE = re.compile(r"(?:^|/)prompts/((?:task|critic)-T\d+[A-Za-z0-9._-]*\.md)$")
SPEC_IN_CMD_RE = re.compile(r"prompts/((?:task|critic)-T\d+[A-Za-z0-9._-]*\.md)")

# Bash verbs that pull a whole file into context. `grep`, `awk`, `sed -n Np`, `wc`, `test`
# and `ls` are deliberately absent — [0g] needs them.
BULK_RE = re.compile(r"\b(cat|bat|less|more)\b")
HEAD_TAIL_RE = re.compile(r"\b(head|tail)\b\s+(?:-n\s*)?-?(\d+)")

# A bounded slice is fine; the point is not to hold the body. 60 lines is roughly the YAML
# block plus context, and well under the ~1,700-token average of the reads being prevented.
MAX_LINES = 60

ADVICE = (
    "[spec-body-guard] Blocked: reading a task-spec body into the orchestrator's context.\n"
    "\n"
    "  file: {name}\n"
    "\n"
    ".claude/rules/dispatch.md [0g]: \"The Tech Lead writes the spec file itself — the\n"
    "orchestrator never holds the body. [...] Never read the spec body into context. The\n"
    "critic and the builder both read it from disk.\"\n"
    "\n"
    "Measured cost of ignoring this: 217 such reads, ~367K tokens, 37% of orchestrator\n"
    "intake on gamedaytastic (issue #39).\n"
    "\n"
    "Do one of these instead:\n"
    "  - Register the task: grep out only the YAML block, e.g.\n"
    "      sed -n '/TECH_LEAD_RESULT_START/,$p' <file>\n"
    "  - Confirm the Tech Lead actually wrote it:  test -s <file>\n"
    "  - Need a specific fact from the spec? Read with an explicit small limit, or ask the\n"
    "    critic/reviewer dispatch — both already read it from disk at cheap-tier cost.\n"
    "  - Genuinely need the whole body: set SPEC_BODY_GUARD_OFF=1 and say why in TASK_LOG."
)


def blocked_read(tool_input):
    path = tool_input.get("file_path") or ""
    m = SPEC_RE.search(path.replace("\\", "/"))
    if not m:
        return None
    limit = tool_input.get("limit")
    if isinstance(limit, int) and 0 < limit <= MAX_LINES:
        return None
    return m.group(1)


def blocked_bash(tool_input):
    cmd = tool_input.get("command") or ""
    m = SPEC_IN_CMD_RE.search(cmd.replace("\\", "/"))
    if not m:
        return None
    ht = HEAD_TAIL_RE.search(cmd)
    if ht:
        return None if int(ht.group(2)) <= MAX_LINES else m.group(1)
    if BULK_RE.search(cmd):
        return m.group(1)
    return None


def main():
    if os.environ.get("SPEC_BODY_GUARD_OFF") == "1":
        sys.exit(0)
    data = json.load(sys.stdin)
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    if tool == "Read":
        name = blocked_read(tool_input)
    elif tool == "Bash":
        name = blocked_bash(tool_input)
    else:
        sys.exit(0)
    if not name:
        sys.exit(0)
    print(ADVICE.format(name=name), file=sys.stderr)
    sys.exit(2)


try:
    main()
except SystemExit:
    raise
except Exception:
    sys.exit(0)
