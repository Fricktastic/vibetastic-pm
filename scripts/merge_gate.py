#!/usr/bin/env python3
"""The merge gate: pin the verified tree, and prove the change took effect (issue #35).

Before this, the merge gate verified *a* tree but never pinned *which* one. gamedaytastic
T078 ran its whole ladder green on one commit and merged the next, which had quietly reverted
the production fix: net production diff empty, every gate reporting success. This module binds
every piece of merge evidence to the exact commit it was produced on, and refuses the merge
when the commit being merged is not the one that evidence is about.

Evidence rows go to the same ledger as the critic/reviewer verdicts, ``logs/verdicts.jsonl``
(scripts/review_gate.py): the gate has to join reviewer verdicts with verification by task and
SHA, and one append-only per-task evidence ledger keeps that a single read. The event names
below are disjoint from review_gate's ``verdict`` / ``adjudication`` / ``cap_override``, so
the round caps and the critique gate never count them.

  verify        run the suite (default: PROJECT.md § Test command) in a CLEAN checkout and
                record a ``verification`` row pinned to its HEAD. Exit 1 when the suite fails.
  fail-on-base  for ``observation: test`` tasks: run the task's observation command on the
                merge base with the branch's test files overlaid (it must FAIL there) and on
                the branch (it must PASS); record a ``fail_on_base`` row. Exit 1 unless both.
  observe       for ``observation: runtime`` tasks: record what the orchestrator/operator
                observed, against a commit (lease owner; what counts is project policy).
  override      waive ONE check for ONE commit with a logged reason (lease owner, operator
                decision). A new commit voids it.
  check         evaluate every check against the candidate commit, record a ``merge_check``
                row, exit 31 on refusal.
  merge         ``check``, then ``gh pr merge <pr> --match-head-commit <sha>`` so GitHub itself
                refuses the merge if the PR head moved after the check.
  status        the task's merge evidence as JSON.

The checks (``check``), all against the candidate commit C and its merge base with --base:
  verification     a passing ``verification`` row at C.
  review           the latest reviewer verdict recorded at C approves (APPROVE /
                   APPROVE-WITH-FOLLOWUPS, no blockers) and was read from a clean tree.
  production_diff  the net diff merge-base..C is non-empty, and contains a production path
                   (PROJECT.md § Test paths / § Non-production paths) unless the task declares
                   ``observation: none``. Net, so a fix reverted later in the branch is empty.
  observation      ``test``: a passing ``fail_on_base`` row at C; ``runtime``: an
                   ``observation`` row at C; ``none``: nothing further. A task with no
                   ``observation:`` field predates #35: skipped with a warning (legacy default).

Exit codes: 0 ok; 1 the command run by verify/fail-on-base failed (recorded); 2 invalid
invocation or configuration error; 31 policy stop (never a task failure, never a
failure_count increment).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_policy  # noqa: E402
import review_gate  # noqa: E402
from review_gate import GateError, append_row, now, read_ledger  # noqa: E402

CHECKS = ('verification', 'review', 'production_diff', 'observation')
OBSERVATION_KINDS = ('test', 'runtime', 'none')
APPROVING = ('APPROVE', 'APPROVE-WITH-FOLLOWUPS')
EVIDENCE_EVENTS = ('verification', 'fail_on_base', 'observation', 'merge_override',
                   'merge_check', 'merge')
OUTPUT_TAIL = 4000


class ConfigError(Exception):
    """An invalid invocation or unusable checkout: exit 2."""


# --- git ----------------------------------------------------------------------------------

def git(cwd, *args, check=True):
    result = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ConfigError(f'git {" ".join(args)} failed in {cwd}: {result.stderr.strip()}')
    return result.stdout


def resolve(cwd, rev):
    return git(cwd, 'rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}').strip()


def head(cwd):
    return resolve(cwd, 'HEAD')


def is_clean(cwd):
    """No staged, unstaged or untracked (non-ignored) change: the checkout IS its HEAD."""
    return git(cwd, 'status', '--porcelain').strip() == ''


def merge_base(cwd, base, sha):
    try:
        return git(cwd, 'merge-base', resolve(cwd, base), sha).strip()
    except ConfigError as exc:
        raise ConfigError(f'cannot find the merge base of {base!r} and {sha[:12]}: {exc}') from exc


def net_diff(cwd, base_sha, sha):
    """[(status, path)] of the NET change base_sha..sha (renames split into D + A)."""
    raw = git(cwd, 'diff', '--name-status', '--no-renames', '-z', base_sha, sha)
    parts = raw.split('\0')
    return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2) if parts[i]]


def commits_between(cwd, old, new):
    out = git(cwd, 'log', '--format=%h %s', f'{old}..{new}', check=False).strip()
    return out.splitlines() if out else []


# --- ledger / plan ------------------------------------------------------------------------

def rows(args, *events):
    return [row for row in read_ledger(args.log_dir, args.task)
            if not events or row.get('event') in events]


def plan_task(args):
    return review_gate.plan_task(Path(args.pm_dir) / 'PLAN.md', args.task)


def observation_kind(task):
    """(kind, note). kind is test|runtime|none, 'legacy' when the field is absent, or 'invalid'.

    Absent is the legacy default: every task written before #35. A task that carries the
    field but leaves it null was written from the new template and simply never decided —
    that is refused, not waved through as legacy.
    """
    if task is None or 'observation' not in task:
        return 'legacy', ('task has no observation: field (a pre-#35 task, or not in PLAN.md): '
                          'legacy default — the fail-on-base / runtime observation is not '
                          'required; the pin and the production-diff check still apply')
    raw = (task.get('observation') or '').strip().lower()
    if raw in ('', 'null', '~'):
        return 'invalid', ('observation: is unset — the spec must name how the change is seen '
                           'to take effect (test | runtime | none)')
    if raw not in OBSERVATION_KINDS:
        return 'invalid', f'observation: {raw!r} is not one of {"|".join(OBSERVATION_KINDS)}'
    return raw, f'observation: {raw}'


def log_path(args, name):
    directory = Path(args.log_dir) / 'merge-gate'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now().replace(':', '').replace('-', '')
    return directory / f'{args.task}-{name}-{stamp}-{os.getpid()}.log'


def run_command(cmd, cwd, log_file, timeout):
    """Run ``bash -c cmd`` in cwd; full output to log_file. Returns (exit, output tail)."""
    with open(log_file, 'w') as handle:
        handle.write(f'# cwd: {cwd}\n# cmd: {cmd}\n')
        handle.flush()
        try:
            code = subprocess.run(['bash', '-c', cmd], cwd=cwd, stdout=handle,
                                  stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            handle.write(f'\n# timed out after {timeout}s\n')
            code = 124
    text = Path(log_file).read_text(errors='replace')
    return code, text[-OUTPUT_TAIL:]


def clean_head(cwd, what):
    sha = head(cwd)
    if not is_clean(cwd):
        raise GateError(
            f'{cwd} has uncommitted or untracked changes, so what {what} would run is not '
            f'commit {sha[:12]} — commit or discard them first. Evidence is pinned to a commit; '
            'a dirty tree has none.')
    return sha


# --- evidence commands --------------------------------------------------------------------

def cmd_verify(args):
    cmd = args.cmd or project_policy.test_command(args.pm_dir)
    if not cmd:
        raise ConfigError('no command: pass --cmd, or declare the suite in PROJECT.md § Test command')
    sha = clean_head(args.dir, 'verify')
    log_file = log_path(args, f'verify-{args.label}')
    code, _ = run_command(cmd, args.dir, log_file, args.timeout)
    moved = head(args.dir) != sha
    row = {'event': 'verification', 'ts': now(), 'task_id': args.task, 'sha': sha,
           'label': args.label, 'cmd': cmd, 'exit': code, 'passed': code == 0 and not moved,
           'head_moved_during_run': moved, 'log': str(log_file)}
    append_row(args.log_dir, row)
    print(json.dumps(row, sort_keys=True))
    if moved:
        print(f'[merge-gate] HEAD moved while the suite ran; nothing was verified.', file=sys.stderr)
    return 0 if row['passed'] else 1


def cmd_fail_on_base(args):
    task = plan_task(args)
    cmd = args.cmd or (task or {}).get('observation_cmd') or ''
    if cmd.strip().lower() in ('', 'null', '~'):
        raise ConfigError(f'no observation command: pass --cmd, or set observation_cmd on '
                          f'{args.task} in PLAN.md')
    policy = project_policy.load(args.pm_dir)
    sha = clean_head(args.dir, 'fail-on-base')
    base_sha = merge_base(args.dir, args.base, sha)
    changes = net_diff(args.dir, base_sha, sha)
    tests = [(status, path) for status, path in changes
             if project_policy.classify(path, policy) == 'test']
    overlay = sorted({path for status, path in tests if status != 'D'} | set(args.overlay or []))
    removed = sorted(path for status, path in tests if status == 'D')
    if not overlay and not removed:
        raise GateError(
            f'the net diff {base_sha[:12]}..{sha[:12]} changes no test path, so there is no new '
            'or changed test to run against the base tree. An observation: test task must add '
            'or change a test (paths: PROJECT.md § Test paths); pass --overlay <path> for a test '
            'outside those paths, or declare observation: runtime.')
    for path in args.overlay or []:
        exists = subprocess.run(['git', '-C', str(args.dir), 'cat-file', '-e', f'{sha}:{path}'],
                                capture_output=True).returncode == 0
        if not exists:
            raise ConfigError(f'--overlay {path} does not exist at {sha[:12]}')

    parent = Path(tempfile.mkdtemp(prefix=f'merge-gate-{args.task}-'))
    base_tree = parent / 'base'
    try:
        git(args.dir, 'worktree', 'add', '--detach', str(base_tree), base_sha)
        if overlay:
            git(base_tree, 'checkout', sha, '--', *overlay)
        for path in removed:
            target = base_tree / path
            if target.exists():
                target.unlink()
        base_log = log_path(args, 'fail-on-base-base')
        base_exit, base_tail = run_command(cmd, base_tree, base_log, args.timeout)
    finally:
        git(args.dir, 'worktree', 'remove', '--force', str(base_tree), check=False)
        shutil.rmtree(parent, ignore_errors=True)
        git(args.dir, 'worktree', 'prune', check=False)
    branch_log = log_path(args, 'fail-on-base-branch')
    branch_exit, _ = run_command(cmd, args.dir, branch_log, args.timeout)
    moved = head(args.dir) != sha

    problems = []
    if base_exit == 0:
        problems.append('the observation PASSES on the base tree with the branch tests overlaid: '
                        'it does not observe this change (an inert test, or the fix is not in '
                        'the production diff)')
    if args.expect_fail_pattern and base_exit != 0 and not re.search(args.expect_fail_pattern, base_tail):
        problems.append(f'the base run failed, but not with --expect-fail-pattern '
                        f'{args.expect_fail_pattern!r} (a build or harness failure is not the '
                        'observation failing)')
    if branch_exit != 0:
        problems.append(f'the observation FAILS on the branch (exit {branch_exit})')
    if moved:
        problems.append('HEAD moved while the observation ran')
    row = {'event': 'fail_on_base', 'ts': now(), 'task_id': args.task, 'sha': sha,
           'base_ref': args.base, 'base_sha': base_sha, 'cmd': cmd, 'overlaid': overlay,
           'removed': removed, 'base_exit': base_exit, 'branch_exit': branch_exit,
           'expect_fail_pattern': args.expect_fail_pattern, 'passed': not problems,
           'problems': problems, 'base_log': str(base_log), 'branch_log': str(branch_log)}
    append_row(args.log_dir, row)
    print(json.dumps(row, sort_keys=True))
    for problem in problems:
        print('[merge-gate] ' + problem, file=sys.stderr)
    return 0 if row['passed'] else 1


def cmd_observe(args):
    review_gate.require_owner(args.pm_dir)
    sha = resolve(args.dir, args.sha or 'HEAD')
    if not sha:
        raise ConfigError(f'{args.sha!r} is not a commit in {args.dir}')
    evidence, summary = (args.evidence or '').strip(), (args.summary or '').strip()
    if not evidence or not summary:
        raise GateError('an observation needs --evidence (the captured artifact: a path or an '
                        'excerpt) and --summary (what was run, the state, what was seen)')
    row = {'event': 'observation', 'ts': now(), 'task_id': args.task, 'sha': sha,
           'kind': args.kind, 'evidence': evidence, 'summary': summary}
    candidate = Path(evidence).expanduser()
    if candidate.is_file():
        row['evidence'] = str(candidate.resolve())
        row['evidence_sha256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    append_row(args.log_dir, row)
    print(json.dumps(row, sort_keys=True))
    return 0


def cmd_override(args):
    review_gate.require_owner(args.pm_dir)
    if not (args.reason or '').strip():
        raise GateError('override requires --reason (the operator decision being recorded)')
    sha = resolve(args.dir, args.sha or 'HEAD')
    if not sha:
        raise ConfigError(f'{args.sha!r} is not a commit in {args.dir}')
    row = {'event': 'merge_override', 'ts': now(), 'task_id': args.task, 'sha': sha,
           'check': args.check, 'reason': args.reason.strip()}
    append_row(args.log_dir, row)
    print(json.dumps(row, sort_keys=True))
    return 0


# --- the gate -----------------------------------------------------------------------------

def _latest(items):
    return items[-1] if items else None


def check_verification(args, sha, _ctx):
    """Every rung (label) ever verified for this task must have passed on THIS commit.

    A rung verified only on an earlier commit does not carry forward: when HEAD moves, the
    whole ladder re-runs. The latest run per rung at this commit wins, so a flake that fails
    after a pass is not verified.
    """
    runs = rows(args, 'verification')
    if not runs:
        return 'fail', 'no verification recorded — run merge_gate.py verify in the task worktree'
    problems, passed = [], []
    for label in dict.fromkeys(r.get('label') for r in runs):
        mine = [r for r in runs if r.get('label') == label]
        latest = _latest([r for r in mine if r.get('sha') == sha])
        if latest and latest.get('passed'):
            passed.append(label)
        elif latest:
            problems.append(f'{label}: failed at {sha[:12]} (exit {latest.get("exit")})')
        else:
            old = _latest([r for r in mine if r.get('passed')]) or mine[-1]
            moved = commits_between(args.dir, old['sha'], sha)
            problems.append(f'{label}: HEAD moved after verification — verified {old["sha"][:12]}, '
                            f'merging {sha[:12]}'
                            + (f' ({len(moved)} commit(s) since: {"; ".join(moved[:5])})' if moved else ''))
    if problems:
        return 'fail', '; '.join(problems) + ' — re-run merge_gate.py verify on the commit being merged'
    return 'pass', f'{", ".join(passed)} passed at {sha[:12]}'


def check_review(args, sha, _ctx):
    reviews = [r for r in rows(args, 'verdict') if r.get('role') == 'reviewer']
    at_sha = [r for r in reviews if r.get('head_sha') == sha]
    latest = _latest(at_sha)
    if latest is None:
        last = _latest(reviews)
        if last is None:
            return 'fail', 'no reviewer verdict recorded for this task'
        if not last.get('head_sha'):
            return 'fail', ('the latest review was recorded before tree pinning (no head_sha): '
                            f're-run the review on {sha[:12]}, or override --check review')
        return 'fail', (f'the latest review read {last["head_sha"][:12]}, not {sha[:12]} — '
                        're-review the commit being merged')
    blockers = (latest.get('counts') or {}).get('blockers', 0)
    if latest.get('verdict') not in APPROVING or blockers:
        return 'fail', (f'the latest review of {sha[:12]} is {latest.get("verdict")} with '
                        f'{blockers} blocker(s)')
    if not latest.get('tree_clean'):
        return 'fail', (f'the review of {sha[:12]} ran in a checkout with uncommitted changes, so '
                        'it did not read that commit — re-review from a clean checkout')
    return 'pass', f'{latest.get("verdict")} at {sha[:12]}'


def check_production_diff(args, sha, ctx):
    changes = ctx['changes']
    if not changes:
        return 'fail', f'the net diff {ctx["base_sha"][:12]}..{sha[:12]} is empty: nothing to merge'
    policy = project_policy.load(args.pm_dir)
    production = sorted({p for _, p in changes if project_policy.classify(p, policy) == 'production'})
    if production:
        shown = ', '.join(production[:5]) + (f' (+{len(production) - 5} more)' if len(production) > 5 else '')
        return 'pass', f'{len(production)} production path(s): {shown}'
    if ctx['kind'] == 'none':
        return 'pass', 'no production change, as declared by observation: none'
    return 'fail', (f'the net diff {ctx["base_sha"][:12]}..{sha[:12]} changes only test / '
                    f'non-production paths ({", ".join(sorted({p for _, p in changes})[:5])}) — '
                    'a green ladder over zero production change (T078). If the task really is '
                    'test- or docs-only, declare observation: none or override --check '
                    'production_diff with the reason')


def check_observation(args, sha, ctx):
    kind = ctx['kind']
    if kind == 'invalid':
        return 'fail', ctx['kind_note']
    if kind == 'legacy':
        return 'skipped', ctx['kind_note']
    if kind == 'none':
        return 'skipped', 'observation: none (no behaviour change declared)'
    if kind == 'test':
        runs = rows(args, 'fail_on_base')
        latest = _latest([r for r in runs if r.get('sha') == sha])
        if latest and latest.get('passed'):
            return 'pass', f'fails on base {latest.get("base_sha", "")[:12]}, passes at {sha[:12]}'
        if latest:
            return 'fail', 'fail-on-base did not hold at this commit: ' + '; '.join(latest.get('problems') or [])
        return 'fail', (f'no fail-on-base run at {sha[:12]} — run merge_gate.py fail-on-base in the '
                        'task worktree' + (' (the last one was at another commit)' if runs else ''))
    observed = [r for r in rows(args, 'observation') if r.get('sha') == sha]
    if observed:
        return 'pass', f'{len(observed)} observation(s) recorded at {sha[:12]}'
    return 'fail', (f'no runtime observation recorded at {sha[:12]} — observe the change on this '
                    'commit and record it with merge_gate.py observe (what counts: PROJECT.md § '
                    'Observations)')


def evaluate(args):
    sha = resolve(args.dir, args.sha or 'HEAD')
    if not sha:
        raise ConfigError(f'{args.sha!r} is not a commit in {args.dir}')
    base_sha = merge_base(args.dir, args.base, sha)
    task = plan_task(args)
    kind, kind_note = observation_kind(task)
    ctx = {'base_sha': base_sha, 'changes': net_diff(args.dir, base_sha, sha),
           'kind': kind, 'kind_note': kind_note}
    overrides = {r.get('check'): r for r in rows(args, 'merge_override') if r.get('sha') == sha}
    results = {}
    for name, check in (('verification', check_verification), ('review', check_review),
                        ('production_diff', check_production_diff),
                        ('observation', check_observation)):
        status, detail = check(args, sha, ctx)
        if status == 'fail' and name in overrides:
            status, detail = 'overridden', f'{detail} [override: {overrides[name]["reason"]}]'
        results[name] = {'status': status, 'detail': detail}
    allowed = all(r['status'] != 'fail' for r in results.values())
    warnings = []
    if not is_clean(args.dir):
        warnings.append(f'{args.dir} has uncommitted changes; they are not part of {sha[:12]} and will not merge')
    if kind == 'legacy':
        warnings.append(kind_note)
    return {'task_id': args.task, 'sha': sha, 'base_ref': args.base, 'base_sha': base_sha,
            'observation': kind, 'allowed': allowed, 'checks': results, 'warnings': warnings}


def cmd_check(args):
    report = evaluate(args)
    append_row(args.log_dir, {'event': 'merge_check', 'ts': now(), 'task_id': args.task,
                              'sha': report['sha'], 'base_sha': report['base_sha'],
                              'allowed': report['allowed'],
                              'checks': {k: v['status'] for k, v in report['checks'].items()}})
    print(json.dumps(report, indent=2, sort_keys=True))
    for warning in report['warnings']:
        print('[merge-gate] warning: ' + warning, file=sys.stderr)
    if not report['allowed']:
        failed = [f'{k}: {v["detail"]}' for k, v in report['checks'].items() if v['status'] == 'fail']
        raise GateError(f'{args.task} may not merge {report["sha"][:12]}:\n  - ' + '\n  - '.join(failed)
                        + '\nEach check is pinned to this commit. Re-run the evidence on it, or '
                        'record an operator decision: merge_gate.py override --check <name> '
                        '--reason "...". See VERIFY.md § Merge gate.')
    return 0


def cmd_merge(args):
    cmd_check(args)
    sha = resolve(args.dir, args.sha or 'HEAD')
    gh = os.environ.get('MERGE_GATE_GH', 'gh')
    # --repo is required and gh runs in the verified checkout: an inferred repo would be the
    # PM directory's own remote, which could merge the same PR number in the wrong repo.
    command = [gh, 'pr', 'merge', str(args.pr), '--match-head-commit', sha, '--repo', args.repo]
    command += [a for a in (args.gh_args or []) if a != '--']
    result = subprocess.run(command, cwd=args.dir)
    append_row(args.log_dir, {'event': 'merge', 'ts': now(), 'task_id': args.task, 'sha': sha,
                              'pr': str(args.pr), 'exit': result.returncode})
    if result.returncode != 0:
        print(f'[merge-gate] gh pr merge exited {result.returncode}; if the PR head moved, '
              'GitHub refused it (--match-head-commit) — re-verify the new head.', file=sys.stderr)
        return 1
    return 0


def cmd_status(args):
    report = {'task_id': args.task,
              'observation': observation_kind(plan_task(args))[0],
              'evidence': rows(args, *EVIDENCE_EVENTS),
              'reviews': [r for r in rows(args, 'verdict') if r.get('role') == 'reviewer']}
    if args.dir:
        report['head'] = head(args.dir)
        report['head_clean'] = is_clean(args.dir)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pm-dir', default=os.environ.get('PM_DIR', '.'))
    parser.add_argument('--log-dir', help='ledger directory (default: <pm-dir>/logs)')
    sub = parser.add_subparsers(dest='command', required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('--cmd', help='default: PROJECT.md § Test command')
    verify.add_argument('--label', default='suite', help='which rung this is (suite, integration, ...)')
    fob = sub.add_parser('fail-on-base')
    fob.add_argument('--cmd', help="default: the task's observation_cmd in PLAN.md")
    fob.add_argument('--overlay', action='append', help='extra branch path to carry onto the base tree')
    fob.add_argument('--expect-fail-pattern', help='regex the base run output must match')
    observe = sub.add_parser('observe')
    observe.add_argument('--sha')
    observe.add_argument('--kind', default='runtime', help='device, simulator, log, response, ...')
    observe.add_argument('--evidence', required=True)
    observe.add_argument('--summary', required=True)
    override = sub.add_parser('override')
    override.add_argument('--sha')
    override.add_argument('--check', choices=CHECKS, required=True)
    override.add_argument('--reason', default='')
    check = sub.add_parser('check')
    merge = sub.add_parser('merge')
    merge.add_argument('--pr', required=True)
    merge.add_argument('--repo', required=True,
                       help='org/repo of the target project; never inferred from the cwd')
    merge.add_argument('gh_args', nargs=argparse.REMAINDER, help='after --: extra gh pr merge flags')
    status = sub.add_parser('status')
    for command in (verify, fob):
        command.add_argument('--timeout', type=int, default=3600)
    for command in (verify, fob, observe, override, check, merge):
        command.add_argument('--dir', required=True, help='the task checkout (its worktree)')
    for command in (fob, check, merge):
        command.add_argument('--base', required=True, help='the branch this merges into, e.g. develop')
    for command in (check, merge):
        command.add_argument('--sha', help='candidate commit (default: HEAD of --dir)')
    status.add_argument('--dir')
    for command in sub.choices.values():
        command.add_argument('--task', required=True)
    args = parser.parse_args(argv)
    if not review_gate.TASK_ID.match(args.task):
        print(f'[merge-gate] invalid task id {args.task!r} (expected T<number>)', file=sys.stderr)
        return 2
    args.log_dir = args.log_dir or str(Path(args.pm_dir) / 'logs')
    handler = {'verify': cmd_verify, 'fail-on-base': cmd_fail_on_base, 'observe': cmd_observe,
               'override': cmd_override, 'check': cmd_check, 'merge': cmd_merge,
               'status': cmd_status}[args.command]
    try:
        return handler(args)
    except GateError as exc:
        print('[merge-gate] ' + str(exc), file=sys.stderr)
        return 31
    except ConfigError as exc:
        print('[merge-gate] configuration error: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
