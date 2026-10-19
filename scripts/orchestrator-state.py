#!/usr/bin/env python3
"""Command-line cooperative PM ownership and durable-file operations."""
import argparse
import json
import os
import secrets
from pathlib import Path
import sys
from pm_state import PMState, StateError


def caller_ownership(lease):
    """Say whether the lease belongs to the session asking.

    The wrapper (orchestrate.py) holds the lease and its PID is the wrapper's, not the CLI's,
    so a session reading `active` saw a live owner with a session name it did not recognise
    and concluded another session held the project. The token in this process's environment
    is the authority: if it matches, the caller IS the owner.
    """
    token = os.environ.get('PM_ORCHESTRATOR_TOKEN')
    owns = bool(lease and token and secrets.compare_digest(str(lease['token']), token))
    if owns:
        note = ('YOU own this lease: your wrapper (orchestrate.py, pid %s) holds it for session %r '
                'and its token is in your environment. Proceed with state commands.'
                % (lease['pid'], lease['session']))
    elif lease:
        note = 'Another session owns this lease; your environment token does not match.'
    else:
        note = 'No lease is held.'
    return {'owns_lease': owns, 'note': note}


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
        if name == 'active':
            result['caller'] = caller_ownership(result.get('lease'))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StateError, OSError, ValueError, KeyError, TypeError) as exc:
        print('orchestrator-state: ' + str(exc), file=sys.stderr)
        return 31


if __name__ == '__main__':
    sys.exit(main())
