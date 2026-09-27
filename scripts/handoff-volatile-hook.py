#!/usr/bin/env python3
"""handoff-volatile-hook — SessionStart hook that surfaces HANDOFF.md's volatile claims.

Issue #51. Handoffs mixed durable decisions with volatile state — "Codex quota is exhausted",
"build installed on device", a branch head, an open PR, a running task — and the next session
read them as facts. Field case (gamedaytastic, 2026-09-24): the handoff said the Codex quota
was exhausted; it had reset; the orchestrator routed a critique and a build to metered
opencode without checking. The operator caught it after the spend.

The format fix (RULES.md § Session Handoff) confines every time-bound claim to one fixed
section whose heading is, EXACTLY:

    ## Volatile — re-verify before use

and whose lines each carry a claim, an as-of date and a check command:

    - <claim> | as-of <YYYY-MM-DD> | check: `<command>`

A rule alone degrades over a long session (RULES.md; issue #39), so this hook puts the
section in front of the fresh session at start-up under a "claims, not facts" banner. The
framework supplies the mechanism; each project writes its own check commands.

Do not rename the heading: a consuming project's interim shim detects this exact string
under framework/ to know when it can retire itself.

Behaviour
    - HANDOFF.md absent, section absent, or section empty  -> silent, exit 0
    - section present -> banner + each line on stdout (both Claude and Codex add SessionStart
      stdout to the session context), with its age in days and a flag on any line that is
      missing its as-of date or check command
    - any internal error -> exit 0. This hook must never fail or block a session.

Wired for both providers by install-orchestrators.py as a SessionStart command hook:
    python3 <framework>/scripts/handoff-volatile-hook.py --pm-dir <pm-dir>
"""
import argparse
import datetime
import os
from pathlib import Path
import re
import sys

HEADING = "## Volatile — re-verify before use"
# A section ends at the next heading of the same or higher level.
SECTION_END_RE = re.compile(r"^#{1,2}\s")
AS_OF_RE = re.compile(r"\|\s*as[- ]of\s+(\S+)", re.IGNORECASE)
CHECK_RE = re.compile(r"\|\s*check:\s*(.+)$", re.IGNORECASE)

BANNER = (
    "[handoff-volatile] CLAIMS, NOT FACTS — HANDOFF.md § Volatile (issue #51)\n"
    "Each line below was true only as of its date. Before acting on one, run its check;\n"
    "never carry one into a new handoff without re-running it (.claude/rules/state.md).\n"
    "Backend availability is never a handoff claim: dispatch and branch on exit 30."
)


def volatile_lines(text):
    """Return the non-blank lines of the volatile section, or None if there is no section."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == HEADING:
            start = index + 1
            break
    if start is None:
        return None
    body = []
    fence = False
    for line in lines[start:]:
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and SECTION_END_RE.match(line):
            break
        if line.strip() and not line.lstrip().startswith("<!--"):
            body.append(line.rstrip())
    return body


def annotate(line, today):
    """Render one claim line with its age, flagging anything that is not well-formed."""
    item = line.strip()
    if item[:2] in ("- ", "* "):
        item = item[2:].strip()
    elif not item.startswith(("-", "*")):
        # Continuation or free prose inside the section: show it, but do not parse it.
        return f"    {line.strip()}"
    # Search for the fields rather than splitting on "|": a check command may itself be a
    # shell pipeline.
    problems = []
    age = ""
    as_of = AS_OF_RE.search(item)
    if as_of:
        try:
            dated = datetime.date.fromisoformat(as_of.group(1))
            days = (today - dated).days
            age = f" [{days} day{'s' if days != 1 else ''} old]" if days >= 0 else " [future-dated]"
        except ValueError:
            problems.append("invalid as-of date")
    else:
        problems.append("no as-of date")
    check = CHECK_RE.search(item)
    if not check or not check.group(1).strip("` "):
        problems.append("no check command — treat as unverified")
    flag = f"  <-- MALFORMED: {', '.join(problems)}" if problems else ""
    return f"  - {item}{age}{flag}"


def render(pm_dir, today=None):
    handoff = pm_dir / "HANDOFF.md"
    if not handoff.is_file():
        return ""
    body = volatile_lines(handoff.read_text(errors="replace"))
    if not body:
        return ""
    today = today or datetime.date.today()
    rendered = [annotate(line, today) for line in body]
    return BANNER + "\n\n" + "\n".join(rendered) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pm-dir", type=Path, default=None)
    args, _ = parser.parse_known_args(argv)
    pm_dir = args.pm_dir or Path(os.environ.get("PM_DIR") or os.getcwd())
    output = render(pm_dir.resolve())
    if output:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 — never fail the session, whatever happened
        pass
    sys.exit(0)
