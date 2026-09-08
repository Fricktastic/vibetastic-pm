#!/usr/bin/env python3
"""Command-line cooperative PM ownership and durable-file operations."""
import argparse
import json
import os
from pathlib import Path
import sys
from pm_state import PMState, StateError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pm-dir', default=os.environ.get('PM_DIR', '.'))
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('acquire', 'check', 'renew', 'release', 'handoff', 'active', 'takeover', 'reserve', 'finish', 'reconcile', 'write', 'append'):
        command = sub.add_parser(name)
        if name in ('check', 'renew', 'release', 'handoff', 'reserve', 'reconcile', 'write', 'append'):
            command.add_argument('--token', default=os.environ.get('PM_ORCHESTRATOR_TOKEN'))
        if name in ('acquire', 'handoff', 'takeover', 'check'):
            command.add_argument('--provider', choices=('codex', 'claude'), required=name != 'check')
            command.add_argument('--session', required=name != 'check')
        if name in ('acquire', 'handoff', 'takeover'):
            command.add_argument('--profile', choices=('normal', 'codex-fallback'), default='normal')
        if name in ('acquire', 'handoff', 'takeover', 'reserve', 'finish'):
            command.add_argument('--pid', type=int, default=os.getppid())
        if name in ('takeover', 'reconcile'):
            command.add_argument('--evidence-hash', required=True)
            command.add_argument('--reason', required=True)
        if name in ('reserve', 'finish', 'reconcile'):
            command.add_argument('--run-id', required=True)
        if name == 'reserve':
            command.add_argument('--task-id', required=True)
            command.add_argument('--worktree', required=True)
            command.add_argument('--branch')
        if name in ('write', 'append'):
            command.add_argument('--path', required=True)
            command.add_argument('--source', type=Path, required=True)
            command.add_argument('--operation-id', required=True)
    args = vars(parser.parse_args())
    state = PMState(args.pop('pm_dir'))
    name = args.pop('command')
    if name in ('write', 'append'):
        args['relative'] = args.pop('path')
        args['content'] = args.pop('source').read_text()
    try:
        result = getattr(state, name)(**args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StateError, OSError, ValueError, KeyError, TypeError) as exc:
        print('orchestrator-state: ' + str(exc), file=sys.stderr)
        return 31


if __name__ == '__main__':
    sys.exit(main())
