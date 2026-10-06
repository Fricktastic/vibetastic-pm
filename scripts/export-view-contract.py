#!/usr/bin/env python3
"""Read-only command-line exporter for the Vibetastic View v1 snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from view_contract import UnrecoverableSourceRead, build_snapshot, write_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pm-dir", type=Path, default=Path.cwd())
    parser.add_argument("--warnings", action="store_true", help="print every warning instead of counts by code")
    args = parser.parse_args()
    pm_dir = args.pm_dir.resolve()
    if not pm_dir.is_dir():
        print(f"export-view-contract: PM directory does not exist or is not a directory: {pm_dir}", file=sys.stderr)
        return 3
    try:
        snapshot = build_snapshot(pm_dir)
        path = write_snapshot(pm_dir, snapshot)
    except (UnrecoverableSourceRead, OSError) as error:
        print(f"export-view-contract: {error}", file=sys.stderr)
        return 3
    except Exception as error:  # defensive CLI boundary; library callers retain tracebacks
        print(f"export-view-contract: {error}", file=sys.stderr)
        return 1
    if args.warnings:
        report = {"path": str(path), "generation": snapshot["generation"], "warnings": snapshot["warnings"]}
    else:
        counts: dict[str, int] = {}
        for warning in snapshot["warnings"]:
            code = str(warning.get("code"))
            counts[code] = counts.get(code, 0) + 1
        report = {"path": str(path), "generation": snapshot["generation"], "warning_counts": counts}
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
