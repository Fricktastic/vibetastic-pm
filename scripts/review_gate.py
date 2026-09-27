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
               used its rounds (critic) or its fixup rounds (reviewer). With ``--lock-fd`` it
               first takes the task+role lock on that descriptor (issue #57), which the
               caller holds until the verdict is recorded, so two concurrent runs on one task
               cannot both pass the cap; a run already holding it is exit 31.
  build-gate   dispatch.sh, before a build run: exit 31 when the task needs critique and has no
               ``proceed``/``override`` adjudication newer than its latest critic verdict, when
               the task spec changed since that adjudication, when a ``security: true`` task's
               ``proceed`` was not recorded by an Opus-class model, or when reviewer fixups
               exceed the cap.
  adjudicate   orchestrator: record the partner's decision on the latest critic verdict, with
               the adjudicating model (``--model``) and the spec's SHA-256 (issue #57).
               ``proceed`` is refused while that verdict carries a BLOCKING-PLAN finding, and
               on a ``security: true`` task unless ``--model`` is Opus-class; ``override`` is
               the operator's logged decision to build anyway (needs --reason).
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
import errno
import fcntl
import hashlib
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
        # Parse the leading tier token: 'R1 (bumped)' is R1 (issue #57); garbage stays None.
        token = re.match(r'([A-Za-z0-9]+)', fields.get('recommended_verify_tier') or '')
        tier = token.group(1).upper() if token else ''
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
    return plan_tasks(text).get(task_id)


def plan_tasks(text):
    """Every task's scalar fields (PLAN_FIELDS) from PLAN.md text, keyed by task id, in order."""
    found = {}
    tasks = re.search(r'^tasks:\s*$(.*)', text or '', re.M | re.S)
    if not tasks:
        return found
    for chunk in re.split(r'(?m)^(?=\s+- id:)', tasks.group(1)):
        head = re.match(r'\s+- id:\s*(\S+)', chunk)
        if not head:
            continue
        task_id = head.group(1).strip('"\'')
        if task_id in found:
            continue        # a duplicate id is plan-lint's to report; the first one wins here
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
        found[task_id] = fields
    return found


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


# --- [issue #57] security floor: who adjudicated -------------------------------------------

def is_opus_class(model):
    """Whether a recorded model name is an Opus-class Anthropic model on subscription Claude.

    Same test orchestrator-routing.py applies to a security adjudicator ('opus' in the name),
    restricted to the Anthropic family and never via OpenRouter (Anthropic never routes
    through it). Fable is not Opus-class: it is policy-restricted from security work.
    """
    name = (model or '').strip().lower()
    if not name or name.startswith(('openrouter/', 'anthropic/')) or 'fable' in name:
        return False
    return name == 'opus' or name.startswith('opus@') or (name.startswith('claude-') and 'opus' in name)


def security_floor_problem(row):
    """Why an adjudication row does NOT satisfy the security Opus floor, or None when it does.

    Reusable by any gate that must check a security task's adjudicator: the build gate here,
    and merge_gate.check_security on the merge-time ``merge_adjudication`` row (issue #58).
    Rules:
      - ``override`` is the operator's logged decision (it needs --reason); it stands whatever
        model recorded it.
      - a row with no ``model`` key predates issue #57 (legacy): accepted, with a warning from
        the caller — the Claude partner's standing model was already Opus (MODELS.md).
      - otherwise the recorded ``model`` must be Opus-class.
    """
    if row.get('outcome') == 'override':
        return None
    if 'model' not in row:
        return None
    if is_opus_class(row.get('model')):
        return None
    return (f'the adjudication was recorded by {row.get("model") or "an unnamed model"}, not an '
            'Opus-class model')


def is_legacy_adjudication(row):
    return row.get('event') == 'adjudication' and 'model' not in row


# --- [issue #57] the adjudication is about a specific spec ---------------------------------

def default_spec_path(pm_dir, task_id):
    return Path(pm_dir) / 'prompts' / f'task-{task_id}.md'


def relative_to_pm(path, pm_dir):
    path, pm = Path(path).resolve(), Path(pm_dir).resolve()
    try:
        return str(path.relative_to(pm))
    except ValueError:
        return str(path)


def file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def spec_drift_problem(row, pm_dir):
    """Why the spec an adjudication was about has changed since, or None when it has not.

    Rows with no ``spec_sha256`` (legacy, or no spec file existed at adjudication) are not
    checked. ``spec_path`` is stored relative to the PM dir when it lies inside it.
    """
    recorded = row.get('spec_sha256')
    if not recorded:
        return None
    current = file_sha256(Path(pm_dir) / (row.get('spec_path') or ''))
    if current == recorded:
        return None
    what = 'is missing' if current is None else 'has changed'
    return f'the spec it adjudicated ({row.get("spec_path")}) {what} since'


# --- [issue #57] one in-flight critic/reviewer round per task ------------------------------

def take_round_lock(fd, role, task_id):
    """Lock the task+role on an inherited descriptor, non-blocking.

    dispatch.sh opens the lock file on a descriptor it keeps for the whole run, so the lock is
    held from this cap check through the verdict record and released by the kernel if the
    dispatch dies — there is no stale lock to clean up.
    """
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            raise GateError(
                f'another {role} run for {task_id} is in flight; its verdict must be recorded '
                'before the next round can be counted against the cap. Wait for it to finish.'
            ) from exc
        raise


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
    if args.lock_fd is not None:
        take_round_lock(args.lock_fd, args.role, args.task)
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
        # Issue #57: an unregistered task id is not gated. Say so where the operator sees it.
        print(f'[review-gate] warning: {args.task} is not in PLAN.md, so no review gate applies '
              'to this build (critique, security floor, fixup cap). Register the task first.',
              file=sys.stderr)
        print(json.dumps({'allowed': True, 'task_id': args.task, 'reason': 'task not in PLAN.md'}))
        return 0
    required, why = critique_required(task)
    security = (task.get('security') or '').lower() == 'true'
    if required:
        verdict, cleared = critique_state(args.log_dir, args.task)
        if cleared is not None:
            drift = spec_drift_problem(cleared, args.pm_dir)
            if drift:
                raise GateError(
                    f'{args.task} was adjudicated ({cleared.get("outcome")}, {cleared.get("ts")}) but '
                    f'{drift}. Re-run the critic on the changed spec, or re-record the decision '
                    f'on it: review_gate.py adjudicate --task {args.task} --outcome proceed|override.')
            if security:
                floor = security_floor_problem(cleared)
                if floor:
                    raise GateError(
                        f'{args.task} is security: true and {floor}. Security adjudication is '
                        'mandatory Opus (VERIFY.md § Security-sensitive tasks): re-record it from an '
                        f'Opus session with review_gate.py adjudicate --task {args.task} --outcome '
                        'proceed --model <opus model>, or record the operator\'s decision with '
                        '--outcome override --reason ...')
                if is_legacy_adjudication(cleared):
                    print(f'[review-gate] warning: {args.task} is security: true and its adjudication '
                          'predates issue #57 (no model recorded); accepted as legacy.', file=sys.stderr)
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
    task = plan_task(Path(args.pm_dir) / 'PLAN.md', args.task) or {}
    security = (task.get('security') or '').lower() == 'true'
    if args.outcome == 'proceed':
        if verdict is None:
            raise GateError(f'{args.task} has no recorded critic verdict to proceed on')
        if security and not is_opus_class(args.model):
            raise GateError(
                f'{args.task} is security: true; its proceed adjudication must be recorded by an '
                f'Opus-class model (--model, got {args.model or "none"}). If Opus is unavailable '
                "the task stays blocked, or the operator's decision is recorded with --outcome "
                'override --reason ... (VERIFY.md § Security-sensitive tasks).')
        blocking = (verdict.get('counts') or {}).get('blocking_plan', 0)
        if verdict.get('verdict') not in ('PROCEED', 'PROCEED-WITH-CHANGES') or blocking:
            raise GateError(
                f'the latest critique of {args.task} is {verdict.get("verdict")} with {blocking} '
                'BLOCKING-PLAN finding(s); resolve them and re-run the critic, or record the '
                "operator's decision with --outcome override --reason ...")
    elif not (args.reason or '').strip():
        raise GateError('--outcome override requires --reason (the operator decision being recorded)')
    spec = Path(args.spec) if args.spec else default_spec_path(args.pm_dir, args.task)
    if args.spec and not spec.is_file():
        raise GateError(f'--spec {args.spec} is not a readable file')
    spec_sha = file_sha256(spec)
    # Issue #57: who decided (the security floor) and which spec the decision is about (a
    # changed spec voids it). 'model' is always written, so a row without the key is legacy.
    row = {'event': 'adjudication', 'ts': now(), 'task_id': args.task, 'role': 'critic',
           'outcome': args.outcome, 'reason': args.reason or None,
           'critic_run_id': verdict.get('run_id') if verdict else None,
           'critic_round': verdict.get('round') if verdict else None,
           'model': args.model or None,
           'spec_path': relative_to_pm(spec, args.pm_dir) if spec_sha else None,
           'spec_sha256': spec_sha}
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
              'latest_critic_verdict': verdict, 'cleared_by': cleared,
              'spec_drift': spec_drift_problem(cleared, args.pm_dir) if cleared else None,
              'security_floor': (security_floor_problem(cleared)
                                 if cleared and task and (task.get('security') or '').lower() == 'true'
                                 else None)}
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
    sub.choices['check-cap'].add_argument(
        '--lock-fd', type=int, default=None,
        help='an open descriptor on the task+role lock file, held by the caller until the '
             'verdict is recorded (issue #57); a lock already held elsewhere is exit 31')
    override = sub.choices['override-cap']
    override.add_argument('--extra', type=int, default=1)
    override.add_argument('--reason', default='')
    adjudicate = sub.add_parser('adjudicate')
    adjudicate.add_argument('--outcome', choices=('proceed', 'override'), required=True)
    adjudicate.add_argument('--reason', default='')
    adjudicate.add_argument('--model', default='',
                            help='the model making this decision (the partner session); a '
                                 'security: true proceed needs an Opus-class model (issue #57)')
    adjudicate.add_argument('--spec', default=None,
                            help='the spec the critique read (default prompts/task-<id>.md); its '
                                 'SHA-256 is recorded and a later change voids the adjudication')
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
