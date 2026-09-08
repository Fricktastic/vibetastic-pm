#!/usr/bin/env python3
"""Lint and atomically update PLAN, recovering its append-only event after crashes."""
import argparse
import json
import os
from pathlib import Path
import sys
from pm_state import PMState, StateError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pm-dir', default=os.environ.get('PM_DIR', '.'))
    parser.add_argument('--token', default=os.environ.get('PM_ORCHESTRATOR_TOKEN'))
    parser.add_argument('--expected-hash', required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    events = parser.add_mutually_exclusive_group(required=True)
    events.add_argument('--event')
    events.add_argument('--event-file', type=Path)
    parser.add_argument('--operation-id', required=True)
    args = parser.parse_args()
    try:
        result = PMState(args.pm_dir).update_plan(args.token, args.expected_hash,
                    args.candidate.read_text(), args.event_file.read_text() if args.event_file else args.event, args.operation_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StateError, OSError, ValueError, KeyError, TypeError) as exc:
        print('plan-update: ' + str(exc), file=sys.stderr)
        return 31


if __name__ == '__main__':
    sys.exit(main())
