#!/usr/bin/env python3
"""Structured critic/reviewer verdicts, round caps and the critique build gate (#18, #50).

Before this, the critique gate and its round limit existed only as prose: the critic's verdict
was free text in a log, the adjudication lived in the conversation, and nothing could stop a
fifth critique round (gamedaytastic T211 ran 5) or a tenth reviewer fixup (T211 ran 10). This
module gives each of those a durable, machine-readable record in ``logs/verdicts.jsonl`` and
the checks ``dispatch.sh`` runs against it:

  record       dispatch.sh, after a ``--role critic|reviewer`` run exits 0: parse the role's
               structured result block and append a ``verdict`` row (round-numbered),
               pinned to the reviewed commit (``--head-sha``/``--tree-clean``, issue #35).
               The orchestrator records a subagent review the same way.
  check-cap    dispatch.sh, before a ``--role critic|reviewer`` run: exit 31 when the task has
               used its rounds (critic) or its fixup rounds (reviewer).
  build-gate   dispatch.sh, before a build run: exit 31 when the task needs critique and has no
               ``proceed``/``override`` adjudication newer than its latest critic verdict, or
               when reviewer fixups exceed the cap.
  adjudicate   orchestrator: record the partner's decision on the latest critic verdict.
               ``proceed`` is refused while that verdict carries a BLOCKING-PLAN finding;
               ``override`` is the operator's logged decision to build anyway (needs --reason).
  override-cap orchestrator, on the operator's instruction: grant extra rounds (needs --reason).
  status       print the task's rounds, caps, latest verdicts and gate state as JSON.

Rounds are counted from the ledger, never from prose: a critic round is any recorded critic
verdict except ERROR (the critic could not read the plan — a configuration failure, not a
round); a reviewer fixup round is a recorded review that did not approve (REJECT, a
blocker, or an unparseable result). An unparseable result is recorded as MALFORMED and still
counts, so a non-compliant model cannot slip past the cap.

Exit codes: 0 allowed / recorded; 2 invalid invocation; 31 policy stop (never a task failure,
never a failure_count increment).
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_policy  # noqa: E402

LEDGER = 'verdicts.jsonl'
ROLES = ('critic', 'reviewer')
CRITIC_VERDICTS = ('PROCEED', 'PROCEED-WITH-CHANGES', 'REWORK', 'ERROR')
REVIEWER_VERDICTS = ('APPROVE', 'APPROVE-WITH-FOLLOWUPS', 'REJECT')
CRITIC_COUNTS = ('blocking_plan', 'blocking_preexistent', 'advisory')
REVIEWER_COUNTS = ('blockers', 'followups', 'notes')
BLOCK = {'critic': 'CRITIC_RESULT', 'reviewer': 'REVIEWER_RESULT'}
TASK_ID = re.compile(r'^T[0-9]+[A-Za-z0-9]*$')  # must match dispatch.sh GATE_TASK_ID
# PLAN.md task scalars the gates read. observation / observation_cmd are the merge gate's
# (issue #35, scripts/merge_gate.py).
PLAN_FIELDS = ('risk', 'security', 'verify_tier', 'agent', 'status', 'observation', 'observation_cmd')


class GateError(Exception):
    """A policy stop: exit 31."""


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --- structured verdicts ------------------------------------------------------------------

def parse_result(role, text):
    """Parse the LAST ``<!-- ROLE_RESULT_START --> ... <!-- ROLE_RESULT_END -->`` block.

    Returns a dict with ``verdict`` (MALFORMED when the block is missing or invalid), the
    role's counts, and ``problem`` describing why a result was rejected.
    """
    name = BLOCK[role]
    counts_keys = CRITIC_COUNTS if role == 'critic' else REVIEWER_COUNTS
    allowed = CRITIC_VERDICTS if role == 'critic' else REVIEWER_VERDICTS
    result = {'verdict': 'MALFORMED', 'counts': {}, 'problem': None}
    if role == 'critic':
        result['recommended_verify_tier'] = None
    starts = [m.end() for m in re.finditer(r'<!--\s*' + name + r'_START\s*-->', text)]
    if not starts:
        result['problem'] = f'no {name}_START block in the output'
        return result
    body = text[starts[-1]:]
    end = re.search(r'<!--\s*' + name + r'_END\s*-->', body)
    if not end:
        result['problem'] = f'{name}_START without {name}_END'
        return result
    fields = {}
    for line in body[:end.start()].splitlines():
        match = re.match(r'^\s*([a-z_]+)\s*:\s*(.*?)\s*$', line)
        if match:
            fields[match.group(1)] = match.group(2).split('#')[0].strip().strip('"\'')
    verdict = fields.get('verdict', '').upper()
    if verdict not in allowed:
        result['problem'] = f'verdict {fields.get("verdict")!r} is not one of {"|".join(allowed)}'
        return result
    for key in counts_keys:
        raw = fields.get(key)
        if raw is None or not re.fullmatch(r'[0-9]+', raw):
            result['problem'] = f'{key} must be a non-negative integer, got {raw!r}'
            return result
        result['counts'][key] = int(raw)
    if role == 'critic':
        tier = (fields.get('recommended_verify_tier') or '').upper()
        result['recommended_verify_tier'] = tier if tier in project_policy.TIERS else None
    result['verdict'] = verdict
    return result


def counts_as_round(row):
    """Whether a ledger verdict row consumes a round (critic) / a fixup round (reviewer)."""
    if row.get('event') != 'verdict':
        return False
    if row.get('role') == 'critic':
        return row.get('verdict') != 'ERROR'
    if row.get('role') == 'reviewer':
        return (row.get('verdict') in ('REJECT', 'MALFORMED')
                or (row.get('counts') or {}).get('blockers', 0) > 0)
    return False


# --- ledger -------------------------------------------------------------------------------

def ledger_path(log_dir):
    return Path(log_dir) / LEDGER


def read_ledger(log_dir, task_id=None):
    try:
        lines = ledger_path(log_dir).read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and (task_id is None or row.get('task_id') == task_id):
            rows.append(row)
    return rows


def append_row(log_dir, row):
    path = ledger_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, separators=(',', ':'), sort_keys=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)      # one write of one line: readers never see a partial row
        os.fsync(fd)
    finally:
        os.close(fd)
    return row


def effective_cap(pm_dir, log_dir, role, task_id):
    base = project_policy.cap(pm_dir, role)
    extra = sum(int(row.get('extra', 0)) for row in read_ledger(log_dir, task_id)
                if row.get('event') == 'cap_override' and row.get('role') == role)
    return base + extra, base, extra


def rounds_used(log_dir, role, task_id):
    return sum(1 for row in read_ledger(log_dir, task_id)
               if row.get('role') == role and counts_as_round(row))


# --- PLAN.md: the task's critique requirement ---------------------------------------------

def plan_task(plan_path, task_id):
    """Return the task's scalar fields from PLAN.md, or None when absent/unreadable."""
    try:
        text = Path(plan_path).read_text()
    except OSError:
        return None
    tasks = re.search(r'^tasks:\s*$(.*)', text, re.M | re.S)
    if not tasks:
        return None
    for chunk in re.split(r'(?m)^(?=\s+- id:)', tasks.group(1)):
        head = re.match(r'\s+- id:\s*(\S+)', chunk)
        if not head or head.group(1).strip('"\'') != task_id:
            continue
        fields = {}
        for key in PLAN_FIELDS:
            # A quoted value may carry '#' (an observation_cmd); an unquoted one ends at a comment.
            match = re.search(r'(?m)^\s+' + key + r':[ \t]*(?:"((?:[^"\\\n]|\\.)*)"|\'([^\'\n]*)\'|([^\n#]*))', chunk)
            if not match:
                continue
            double, single, bare = match.groups()
            if double is not None:
                fields[key] = re.sub(r'\\(.)', r'\1', double)
            else:
                fields[key] = single if single is not None else bare.strip()
        return fields
    return None


def critique_required(task):
    """(required, reason). Critique follows the risk flag; verify_tier only for legacy tasks.

    ``risk`` is new in #50. A task written before it has no ``risk:`` field; for those the old
    rule (verify_tier R1/R2) still applies so that upgrading the framework never silently
    drops a critique a task was planned under. ``risk: false`` is the explicit opt-out.
    """
    if (task.get('security') or '').lower() == 'true':
        return True, 'security: true'
    risk = (task.get('risk') or '').lower()
    if risk == 'true':
        return True, 'risk: true'
    if risk == 'false':
        return False, 'risk: false'
    tier = (task.get('verify_tier') or '').upper()
    if tier in ('R1', 'R2'):
        return True, f'legacy task with no risk: field at verify_tier {tier} (pre-#50 default)'
    return False, 'no risk: field and verify_tier ' + (tier or 'unset')


def critique_state(log_dir, task_id):
    """Latest counted critic verdict and whether an adjudication clears the gate after it."""
    latest_verdict, cleared_by = None, None
    for row in read_ledger(log_dir, task_id):
        if row.get('role') == 'critic' and counts_as_round(row):
            latest_verdict, cleared_by = row, None     # a new round voids an older decision
        elif row.get('event') == 'adjudication' and row.get('outcome') in ('proceed', 'override'):
            cleared_by = row
    return latest_verdict, cleared_by


# --- lease (managed projects) -------------------------------------------------------------

def require_owner(pm_dir):
    """Decisions are durable state: in a managed project only the lease owner records them."""
    if not (Path(pm_dir) / '.orchestrator/config.json').is_file():
        return
    from pm_state import PMState, StateError
    try:
        PMState(pm_dir).check(os.environ.get('PM_ORCHESTRATOR_TOKEN'))
    except (StateError, OSError, ValueError, KeyError, TypeError) as exc:
        raise GateError(f'recording a decision requires the orchestrator lease: {exc}') from exc


# --- commands -----------------------------------------------------------------------------

def cmd_record(args):
    text = Path(args.output).read_text(errors='replace') if Path(args.output).is_file() else ''
    parsed = parse_result(args.role, text)
    row = {'event': 'verdict', 'ts': now(), 'task_id': args.task, 'role': args.role,
           'run_id': args.run_id, 'prompt': args.prompt, 'model': args.model, **parsed}
    if args.head_sha:
        # Issue #35: pin the tree the verdict is about. The merge gate accepts a review only
        # for the exact commit being merged, read from a clean checkout.
        row['head_sha'] = args.head_sha
        row['tree_clean'] = args.tree_clean == 'true'
    row['round'] = rounds_used(args.log_dir, args.role, args.task) + 1 if counts_as_round(row) else None
    append_row(args.log_dir, row)
    if parsed['problem']:
        print(f'[review-gate] {args.role} result for {args.task} is MALFORMED ({parsed["problem"]}); '
              f'recorded, and it still counts as a round.', file=sys.stderr)
    print(parsed['verdict'])
    return 0


def cmd_check_cap(args):
    limit, base, extra = effective_cap(args.pm_dir, args.log_dir, args.role, args.task)
    used = rounds_used(args.log_dir, args.role, args.task)
    # critic: rounds so far must be below the cap for one more round to start.
    # reviewer: a review evaluates the previous fixup, so it may run while fixups <= cap
    #           (with cap 3: review 4 checks fixup 3; review 5 would follow an illegal fixup 4).
    over = used >= limit if args.role == 'critic' else used > limit
    if over:
        what = 'critique rounds' if args.role == 'critic' else 'reviewer fixup rounds'
        raise GateError(
            f'{args.task} has used {used} {what}; the cap is {limit} ({base} from policy'
            f'{f" + {extra} granted" if extra else ""}). Escalate to the operator: redesign '
            f'(re-spec as a new task), override (review_gate.py adjudicate --outcome override / '
            f'override-cap --reason ...), or abort. See .claude/rules/dispatch.md § Round caps.')
    print(json.dumps({'allowed': True, 'task_id': args.task, 'role': args.role,
                      'rounds_used': used, 'cap': limit}))
    return 0


def cmd_build_gate(args):
    task = plan_task(Path(args.pm_dir) / 'PLAN.md', args.task)
    if task is None:
        print(json.dumps({'allowed': True, 'task_id': args.task, 'reason': 'task not in PLAN.md'}))
        return 0
    required, why = critique_required(task)
    if required:
        verdict, cleared = critique_state(args.log_dir, args.task)
        if cleared is None:
            if verdict is None:
                detail = 'no pre-build critique has been recorded'
            else:
                detail = (f'latest critique (round {verdict.get("round")}, {verdict.get("verdict")}) '
                          'has no proceed/override adjudication')
            raise GateError(
                f'{args.task} requires pre-build critique ({why}) and {detail}. Run the critic '
                f'(dispatch.sh --role critic), then record the decision with review_gate.py '
                f'adjudicate --task {args.task} --outcome proceed (or --outcome override '
                f'--reason "<operator decision>").')
    fixups = rounds_used(args.log_dir, 'reviewer', args.task)
    limit, _, _ = effective_cap(args.pm_dir, args.log_dir, 'reviewer', args.task)
    if fixups > limit:
        raise GateError(
            f'{args.task} has had {fixups} non-approving reviews; the reviewer fixup cap is '
            f'{limit}. Another fixup build needs the operator: redesign, override '
            f'(review_gate.py override-cap --role reviewer --reason ...), or abort.')
    print(json.dumps({'allowed': True, 'task_id': args.task, 'critique_required': required,
                      'reason': why, 'reviewer_fixups': fixups, 'reviewer_cap': limit}))
    return 0


def cmd_adjudicate(args):
    require_owner(args.pm_dir)
    verdict, _ = critique_state(args.log_dir, args.task)
    if args.outcome == 'proceed':
        if verdict is None:
            raise GateError(f'{args.task} has no recorded critic verdict to proceed on')
        blocking = (verdict.get('counts') or {}).get('blocking_plan', 0)
        if verdict.get('verdict') not in ('PROCEED', 'PROCEED-WITH-CHANGES') or blocking:
            raise GateError(
                f'the latest critique of {args.task} is {verdict.get("verdict")} with {blocking} '
                'BLOCKING-PLAN finding(s); resolve them and re-run the critic, or record the '
                "operator's decision with --outcome override --reason ...")
    elif not (args.reason or '').strip():
        raise GateError('--outcome override requires --reason (the operator decision being recorded)')
    row = {'event': 'adjudication', 'ts': now(), 'task_id': args.task, 'role': 'critic',
           'outcome': args.outcome, 'reason': args.reason or None,
           'critic_run_id': verdict.get('run_id') if verdict else None,
           'critic_round': verdict.get('round') if verdict else None}
    append_row(args.log_dir, row)
    print(json.dumps(row, sort_keys=True))
    return 0


def cmd_override_cap(args):
    require_owner(args.pm_dir)
    if not (args.reason or '').strip():
        raise GateError('override-cap requires --reason (the operator decision being recorded)')
    if args.extra < 1:
        raise GateError('--extra must be at least 1')
    row = {'event': 'cap_override', 'ts': now(), 'task_id': args.task, 'role': args.role,
           'extra': args.extra, 'reason': args.reason}
    append_row(args.log_dir, row)
    print(json.dumps(row, sort_keys=True))
    return 0


def cmd_status(args):
    task = plan_task(Path(args.pm_dir) / 'PLAN.md', args.task)
    required, why = critique_required(task) if task else (False, 'task not in PLAN.md')
    verdict, cleared = critique_state(args.log_dir, args.task)
    report = {'task_id': args.task, 'critique_required': required, 'reason': why,
              'latest_critic_verdict': verdict, 'cleared_by': cleared}
    for role in ROLES:
        limit, _, _ = effective_cap(args.pm_dir, args.log_dir, role, args.task)
        report[role] = {'rounds_used': rounds_used(args.log_dir, role, args.task), 'cap': limit}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pm-dir', default=os.environ.get('PM_DIR', '.'))
    parser.add_argument('--log-dir', help='ledger directory (default: <pm-dir>/logs)')
    sub = parser.add_subparsers(dest='command', required=True)
    record = sub.add_parser('record')
    record.add_argument('--output', required=True, help='file holding the role run\'s final report')
    record.add_argument('--run-id', default=None)
    record.add_argument('--prompt', default=None)
    record.add_argument('--model', default=None)
    record.add_argument('--head-sha', default=None,
                        help='commit the reviewed/critiqued checkout was at (issue #35)')
    record.add_argument('--tree-clean', choices=('true', 'false'), default='false',
                        help='whether that checkout had no uncommitted or untracked changes')
    for name in ('record', 'check-cap', 'override-cap'):
        command = sub.choices.get(name) or sub.add_parser(name)
        command.add_argument('--role', choices=ROLES, required=True)
    override = sub.choices['override-cap']
    override.add_argument('--extra', type=int, default=1)
    override.add_argument('--reason', default='')
    adjudicate = sub.add_parser('adjudicate')
    adjudicate.add_argument('--outcome', choices=('proceed', 'override'), required=True)
    adjudicate.add_argument('--reason', default='')
    sub.add_parser('build-gate')
    sub.add_parser('status')
    for command in sub.choices.values():
        command.add_argument('--task', required=True)
    args = parser.parse_args(argv)
    if not TASK_ID.match(args.task):
        print(f'[review-gate] invalid task id {args.task!r} (expected T<number>)', file=sys.stderr)
        return 2
    args.log_dir = args.log_dir or str(Path(args.pm_dir) / 'logs')
    handler = {'record': cmd_record, 'check-cap': cmd_check_cap, 'build-gate': cmd_build_gate,
               'adjudicate': cmd_adjudicate, 'override-cap': cmd_override_cap,
               'status': cmd_status}[args.command]
    try:
        return handler(args)
    except GateError as exc:
        print('[review-gate] ' + str(exc), file=sys.stderr)
        return 31


if __name__ == '__main__':
    sys.exit(main())
