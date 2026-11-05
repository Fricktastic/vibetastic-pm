#!/usr/bin/env python3
"""Launch either orchestrator against one installed PM directory, holding a writer lease."""
import argparse
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent


def split_provider_command(argv):
    """Expand a quoted provider command ('claude --name x') into separate arguments."""
    out = list(argv)
    for i, arg in enumerate(out):
        if arg == '--':
            break
        parts = shlex.split(arg) if ' ' in arg.strip() else []
        if parts and parts[0] in ('claude', 'codex'):
            return out[:i] + parts + out[i+1:]
        if not arg.startswith('-') and (i == 0 or out[i-1] not in ('--pm-dir', '--profile')):
            break
    return out


def take_over_dead_owner(state, provider, session, profile, refused):
    """Take over a lease only when its owner is provably dead and no dispatch is open.

    A crashed session leaves its lease behind. Taking it over by hand meant running
    `takeover` from a shell that exits at once, which recorded a dead pid and left a new
    stale lease. The wrapper takes over with its own pid instead, so the lease it takes is
    the one it holds and renews. A live owner or an open reservation still refuses.
    """
    active = subprocess.run(state+['active'], capture_output=True, text=True)
    try:
        evidence = json.loads(active.stdout) if active.returncode == 0 else None
    except ValueError:
        evidence = None
    lease = (evidence or {}).get('lease')
    if not evidence or not lease or evidence.get('owner_alive'):
        print(refused.stderr, end='', file=sys.stderr)
        if lease and evidence.get('owner_alive'):
            print(f"[orchestrate] live {lease['provider']} session {lease['session']} (pid {lease['pid']}) owns this "
                  'project; close it or hand off.', file=sys.stderr)
        return None
    if evidence.get('runs'):
        print(refused.stderr, end='', file=sys.stderr)
        print('[orchestrate] previous owner is dead but dispatch reservations are open: '
              + ', '.join(r['run_id'] for r in evidence['runs'])
              + '. Reconcile them (orchestrator-state.py reconcile), then relaunch.', file=sys.stderr)
        return None
    reason = (f"auto-takeover by orchestrate.py: owner pid {lease['pid']} (session {lease['session']}, "
              f"last renewed {lease.get('renewed_at')}) is not live; no open reservations")
    taken = subprocess.run(state+['takeover','--provider',provider,'--session',session,'--pid',str(os.getpid()),
                                  '--profile',profile,'--evidence-hash',evidence['evidence_hash'],'--reason',reason],
                           capture_output=True, text=True)
    if taken.returncode:
        print(taken.stderr, end='', file=sys.stderr)
        return None
    print(f"[orchestrate] took over stale lease from dead pid {lease['pid']} "
          f"(last renewed {lease.get('renewed_at')}).", file=sys.stderr)
    return taken


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pm-dir', default='.')
    parser.add_argument('--profile', choices=['normal','codex-fallback'])
    parser.add_argument('provider', choices=['claude','codex'])
    parser.add_argument('harness_args', nargs=argparse.REMAINDER, help='arguments passed unchanged to the native CLI')
    args = parser.parse_args(split_provider_command(sys.argv[1:]))
    pm = Path(args.pm_dir).resolve()
    if not (pm/'.orchestrator/config.json').is_file():
        print('Install project orchestration first: python3 framework/scripts/install-orchestrators.py --pm-dir .', file=sys.stderr)
        return 31
    profile = args.profile or ('codex-fallback' if args.provider == 'codex' else 'normal')
    if profile == 'codex-fallback' and args.provider != 'codex':
        print('codex-fallback is a Codex orchestrator session profile', file=sys.stderr)
        return 31
    session = str(uuid.uuid4())
    state = [sys.executable, str(HERE/'scripts/orchestrator-state.py'), '--pm-dir', str(pm)]
    acquired = subprocess.run(state+['acquire','--provider',args.provider,'--session',session,'--pid',str(os.getpid()),'--profile',profile],capture_output=True,text=True)
    if acquired.returncode:
        acquired = take_over_dead_owner(state, args.provider, session, profile, acquired)
        if acquired is None:
            return 31
    token = json.loads(acquired.stdout)['token']
    env = {**os.environ, 'PM_DIR': str(pm), 'PM_ORCHESTRATOR_PROVIDER': args.provider,
           'PM_ORCHESTRATOR_SESSION': session, 'PM_ORCHESTRATOR_TOKEN': token}
    forwarded = args.harness_args
    if forwarded[:1] == ['--']:
        forwarded = forwarded[1:]
    child = None
    prior = {}
    def forward(signum, _frame):
        if child is not None and child.poll() is None:
            child.send_signal(signum)
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            prior[sig] = signal.signal(sig, forward)
        print(f'[orchestrate] {args.provider}, profile {profile}; project {pm}', file=sys.stderr)
        child = subprocess.Popen([args.provider,*forwarded],cwd=pm,env=env)
        while True:
            try:
                return child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                renewed = subprocess.run(state+['renew','--token',token],capture_output=True,text=True)
                if renewed.returncode:
                    print('[orchestrate] ownership lost; stopping old orchestrator. Builders require reconciliation.',file=sys.stderr)
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                    return 31
    except OSError as exc:
        print(f'[orchestrate] {exc}',file=sys.stderr)
        return 30
    finally:
        for sig, handler in prior.items():
            signal.signal(sig, handler)
        released = subprocess.run(state+['release','--token',token],capture_output=True,text=True)
        if released.returncode:
            print('[orchestrate] lease not released: inspect orchestrator-state.py active before takeover.',file=sys.stderr)

if __name__ == '__main__':
    sys.exit(main())
