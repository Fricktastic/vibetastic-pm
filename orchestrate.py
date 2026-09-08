#!/usr/bin/env python3
"""Launch either orchestrator against one installed PM directory, holding a writer lease."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pm-dir', default='.')
    parser.add_argument('--profile', choices=['normal','codex-fallback'])
    parser.add_argument('provider', choices=['claude','codex'])
    parser.add_argument('harness_args', nargs=argparse.REMAINDER, help='arguments passed unchanged to the native CLI')
    args = parser.parse_args()
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
        print(acquired.stderr, end='', file=sys.stderr)
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
