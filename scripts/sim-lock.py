#!/usr/bin/env python3
"""Run a command while holding the machine-wide simulator lock.

Usage: python3 framework/scripts/sim-lock.py [--timeout SECONDS] -- <command> [args...]

One simulator-driving command at a time per machine: two concurrent xcodebuild test runs
wedge CoreSimulator. Wrap every simulator use with this -- a test-running verify command
(dispatch.sh runs it out of the sandbox, in parallel across dispatches) and the PM's own
Test command (merge_gate.py verify) -- so they queue instead of colliding.

The lock is ~/.cache/vibetastic/simulator.lock (override: VIBETASTIC_SIM_LOCK). A nested
call (a wrapped command that itself calls sim-lock.py) runs directly instead of deadlocking.
Exits with the command's status, or 75 if the lock was not acquired within --timeout.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

HELD_ENV = "VIBETASTIC_SIM_LOCK_HELD"
LOCK_TIMEOUT_EXIT = 75


def lock_path() -> Path:
    override = os.environ.get("VIBETASTIC_SIM_LOCK")
    return Path(override) if override else Path.home() / ".cache" / "vibetastic" / "simulator.lock"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=3600.0,
                        help="seconds to wait for the lock (default 3600)")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    if os.environ.get(HELD_ENV) == "1":
        return subprocess.call(command)

    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as handle:
        deadline = time.monotonic() + args.timeout
        announced = False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print(f"sim-lock: timed out after {args.timeout:g}s waiting for {path}", file=sys.stderr)
                    return LOCK_TIMEOUT_EXIT
                if not announced:
                    print(f"sim-lock: waiting for {path}", file=sys.stderr)
                    announced = True
                time.sleep(0.2)
        # The kernel releases the lock when this process exits, even on a crash.
        return subprocess.call(command, env={**os.environ, HELD_ENV: "1"})


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
