#!/usr/bin/env python3
"""Defense-in-depth PLAN lint for CI or pre-commit; vocabulary drift is non-blocking."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan',nargs='?',default='PLAN.md')
    parser.add_argument('--staged',action='store_true',help='check the staged root PLAN, only when changed')
    args=parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='pm-plan-check-') as d:
            source=Path(args.plan)
            if args.staged:
                changed=subprocess.run(['git','diff','--cached','--name-only','--diff-filter=ACMR','--','PLAN.md'],capture_output=True,text=True,check=True)
                if not changed.stdout.strip():
                    return 0
                staged=subprocess.run(['git','show',':PLAN.md'],capture_output=True,check=True)
                source=Path(d)/'PLAN.md'
                source.write_bytes(staged.stdout)
            result=subprocess.run(['bash',str(Path(__file__).with_name('plan-lint.sh')),str(source)])
            return 0 if result.returncode in (0,3) else 1
    except (OSError,subprocess.CalledProcessError) as exc:
        print('check-plan: '+str(exc),file=sys.stderr)
        return 1

if __name__=='__main__':
    sys.exit(main())
