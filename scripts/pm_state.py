"""Cooperative PM fencing and durable, recoverable Markdown transactions.

All participants must use this API: flock is not a filesystem security boundary.
Pending intents roll forward on the next command; unexpected external edits fail
closed for manual reconciliation. No clock expiry steals ownership or fails tasks.
"""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile


class StateError(RuntimeError):
    """State/policy denial (CLI exit 31), never a builder failure."""


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def serialized(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def process_identity(pid):
    """PID plus OS start time distinguishes recycled process IDs on macOS/Linux."""
    if not isinstance(pid, int) or pid <= 0:
        raise StateError('pid must be a positive integer')
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'stat='],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StateError('cannot inspect owner process: ' + str(exc)) from exc
    fields = result.stdout.strip().rsplit(None, 1)
    if result.returncode == 1 and not fields:
        return None
    if result.returncode != 0 or len(fields) != 2:
        raise StateError('cannot establish process identity')
    return None if fields[1].startswith('Z') else fields[0].strip()


class PMState:
    def __init__(self, pm_dir):
        self.pm = Path(pm_dir).resolve()
        self.meta = self.pm / '.orchestrator'

    def _read_json(self, path, default=None):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise StateError('invalid state JSON: ' + str(path)) from exc

    def _fsync_dir(self, path):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _replace(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.pm-state-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
            self._fsync_dir(path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _save(self, path, value):
        self._replace(path, serialized(value) + b'\n')

    @contextmanager
    def _lock(self):
        config = self._read_json(self.meta / 'config.json')
        if not isinstance(config, dict) or config.get('enabled') is False:
            raise StateError('project orchestration is not enabled or config is invalid')
        with (self.meta / 'state.lock').open('a+b') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                self._recover()
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _lease(self):
        lease = self._read_json(self.meta / 'lease.json')
        if lease is not None and (not isinstance(lease, dict) or not all(
                lease.get(key) for key in ('token', 'provider', 'session', 'pid', 'process_start', 'profile'))):
            raise StateError('invalid lease; inspect state before proceeding')
        return lease

    def _check(self, token, provider=None, session=None):
        lease = self._lease()
        if not token or not lease or not secrets.compare_digest(str(lease['token']), str(token)):
            raise StateError('missing or stale ownership token; acquire or reconcile ownership')
        if provider is not None and lease['provider'] != provider:
            raise StateError('lease provider mismatch')
        if session is not None and lease['session'] != session:
            raise StateError('lease session mismatch')
        if process_identity(lease['pid']) != lease['process_start']:
            raise StateError('owner process is no longer live; inspect active and explicitly take over')
        return lease

    def _new_lease(self, provider, session, pid, profile):
        if provider not in ('codex', 'claude') or not session or profile not in ('normal', 'codex-fallback'):
            raise StateError('provider/session/profile is invalid')
        identity = process_identity(pid)
        if identity is None:
            raise StateError('new owner process must be live')
        return {'provider': provider, 'session': session, 'pid': pid, 'process_start': identity,
                'profile': profile, 'token': secrets.token_hex(32), 'acquired_at': stamp(), 'renewed_at': stamp()}

    def acquire(self, provider, session, pid, profile='normal'):
        with self._lock():
            if self._lease() is not None:
                raise StateError('project already owned; age never authorizes stealing a lease')
            lease = self._new_lease(provider, session, pid, profile)
            self._save(self.meta / 'lease.json', lease)
            return lease

    def check(self, token, provider=None, session=None):
        with self._lock():
            return self._check(token, provider, session)

    def renew(self, token):
        with self._lock():
            lease = self._check(token)
            lease['renewed_at'] = stamp()
            self._save(self.meta / 'lease.json', lease)
            return lease

    def release(self, token):
        with self._lock():
            self._check(token)
            self._save(self.meta / 'lease.json', None)
            return {'released': True}

    def handoff(self, token, provider, session, pid, profile='normal'):
        with self._lock():
            self._check(token)
            lease = self._new_lease(provider, session, pid, profile)
            self._save(self.meta / 'lease.json', lease)
            return lease

    def _runs(self):
        runs = self._read_json(self.meta / 'runs.json', {})
        if not isinstance(runs, dict) or any(not isinstance(run, dict) or not all(
                key in run for key in ('run_id', 'task_id', 'pid', 'process_start', 'status', 'worktree'))
                for run in runs.values()):
            raise StateError('invalid dispatch reservation state')
        return runs

    def _journal(self):
        path = self.pm / 'logs/runs.jsonl'
        raw = path.read_bytes() if path.exists() else b''
        starts, finishes, errors = {}, {}, []
        for number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get('run_id'), str):
                    raise ValueError('missing run_id')
                if row.get('event') == 'run_start':
                    if row['run_id'] in starts and starts[row['run_id']] != row:
                        raise ValueError('conflicting run_start')
                    starts[row['run_id']] = row
                elif row.get('event') == 'run_finish':
                    finishes[row['run_id']] = row
                else:
                    raise ValueError('unknown run event')
            except (ValueError, UnicodeError) as exc:
                errors.append({'line': number, 'error': str(exc)})
        return {'path': str(path), 'hash': digest(raw), 'errors': errors,
                'unmatched': [starts[key] for key in sorted(starts.keys() - finishes.keys())],
                'completed': [finishes[key] for key in sorted(finishes)]}

    def _git(self, directory):
        if not directory or not Path(directory).is_dir():
            return {'available': False}
        def git(*args):
            return subprocess.run(['git', '--no-optional-locks', '-C', str(directory), *args],
                                  capture_output=True, timeout=15)
        try:
            head = git('rev-parse', '--verify', 'HEAD')
            if head.returncode:
                return {'available': False, 'error': head.stderr.decode(errors='replace').strip()}
            status = git('status', '--porcelain=v1', '-z', '--untracked-files=all')
            diff = git('diff', '--no-ext-diff', '--binary', 'HEAD', '--')
            untracked = git('ls-files', '--others', '--exclude-standard', '-z')
            inventory = git('worktree', 'list', '--porcelain')
            if any(r.returncode for r in (status, diff, untracked, inventory)):
                return {'available': False, 'error': 'git evidence command failed'}
            extra = []
            root = Path(directory)
            for name in untracked.stdout.split(b'\0'):
                if not name:
                    continue
                file = root / os.fsdecode(name)
                content_hash = hashlib.sha256()
                if file.is_symlink():
                    content_hash.update(os.fsencode(os.readlink(file)))
                elif file.is_file():
                    with file.open('rb') as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            content_hash.update(chunk)
                else:
                    content_hash.update(b'<missing-or-nonregular>')
                extra.append([os.fsdecode(name), content_hash.hexdigest()])
            return {'available': True, 'head': head.stdout.decode().strip(),
                    'status': status.stdout.decode(errors='replace').replace('\0', '\n'),
                    'diff_hash': digest(diff.stdout), 'untracked': extra,
                    'worktree_inventory': inventory.stdout.decode(errors='replace')}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {'available': False, 'error': str(exc)}

    def _active(self, include_git=True):
        lease = self._lease()
        owner_alive = bool(lease and process_identity(lease['pid']) == lease['process_start'])
        known = self._runs()
        journal = self._journal()
        runs = [dict(run) for run in known.values() if run['status'] == 'active']
        for legacy in journal['unmatched']:
            if legacy['run_id'] not in known:
                runs.append(dict(legacy, status='active', source='legacy-journal',
                                 process_start=None, worktree=legacy.get('worktree') or '',
                                 task_id=legacy.get('task_id'), pid=legacy.get('pid')))
        for run in runs:
            try:
                identity = process_identity(run['pid'])
                run['process_alive'] = (identity == run['process_start'] if run['process_start'] else
                                        (None if identity else False))
            except StateError:
                run['process_alive'] = None
            run['worktree_exists'] = bool(run['worktree'] and Path(run['worktree']).exists())
            run['required_action'] = ('wait for dispatch finish' if run['process_alive'] else
                'inspect run logs/worktree; explicitly reconcile dead process; do not redispatch automatically')
            if include_git:
                run['git'] = self._git(run['worktree'] or run.get('dir'))
        evidence = {'lease': lease, 'owner_alive': owner_alive, 'runs': sorted(runs, key=lambda r: r['run_id']),
                    'journal': journal, 'plan_hash': self._file_hash(self.pm / 'PLAN.md'),
                    'task_log_hash': self._file_hash(self.pm / 'TASK_LOG.md')}
        evidence['evidence_hash'] = digest(serialized(evidence))
        return evidence

    def active(self):
        with self._lock():
            return self._active()

    def takeover(self, provider, session, pid, evidence_hash, reason, profile='normal'):
        with self._lock():
            evidence = self._active()
            if not reason.strip() or evidence_hash != evidence['evidence_hash']:
                raise StateError('takeover requires a reason and current active evidence hash')
            if evidence['owner_alive']:
                raise StateError('live owner must hand off or release; takeover refused')
            if not evidence['lease']:
                raise StateError('no previous lease; use acquire')
            lease = self._new_lease(provider, session, pid, profile)
            lease['takeover'] = {'previous_owner': evidence['lease'], 'evidence_hash': evidence_hash,
                                 'reason': reason, 'unreconciled_runs': [r['run_id'] for r in evidence['runs']]}
            self._save(self.meta / 'lease.json', lease)
            return lease

    def reserve(self, token, run_id, task_id, pid, worktree, branch=None):
        with self._lock():
            lease = self._check(token)
            if not run_id or not task_id:
                raise StateError('run-id and task-id are required')
            runs = self._runs()
            if run_id in runs:
                raise StateError('run-id already registered')
            evidence = self._active(include_git=False)
            if evidence['journal']['errors']:
                raise StateError('runs journal is incomplete or invalid; inspect active and reconcile it first')
            worktree = str(Path(worktree).resolve()) if worktree else ''
            for run in evidence['runs']:
                if run['task_id'] == task_id:
                    raise StateError('task already has an unreconciled dispatch; inspect active')
                if worktree and run['worktree'] and (
                        str(Path(run['worktree']).resolve()) == worktree or
                        (branch and branch == run.get('branch'))):
                    raise StateError('worktree or branch already has an unreconciled dispatch')
                if (worktree and run.get('source') == 'legacy-journal'
                        and run.get('role') != 'read-only' and not run['worktree']):
                    raise StateError('legacy build has unknown worktree scope; explicitly reconcile first')
            identity = process_identity(pid)
            if identity is None:
                raise StateError('dispatch process must be live')
            run = {'run_id': run_id, 'task_id': task_id, 'pid': pid, 'process_start': identity,
                   'worktree': worktree, 'branch': branch, 'status': 'active', 'started_at': stamp(),
                   'owner': lease}
            runs[run_id] = run
            self._save(self.meta / 'runs.json', runs)
            return run

    def finish(self, run_id, pid=None):
        """Dispatch's own process may finish after ownership moves to another session."""
        with self._lock():
            runs = self._runs()
            if run_id not in runs:
                raise StateError('unknown run-id')
            run = runs[run_id]
            pid = os.getpid() if pid is None else pid
            if pid != run['pid'] or process_identity(pid) != run['process_start']:
                raise StateError('only recorded dispatch process can finish; use explicit reconcile for dead runs')
            if run['status'] == 'active':
                run.update(status='finished', finished_at=stamp())
                self._save(self.meta / 'runs.json', runs)
            return run

    def reconcile(self, token, run_id, evidence_hash, reason):
        with self._lock():
            self._check(token)
            evidence = self._active()
            if not reason.strip() or evidence_hash != evidence['evidence_hash']:
                raise StateError('reconciliation requires current active evidence and a reason')
            run = next((r for r in evidence['runs'] if r['run_id'] == run_id), None)
            if not run or run['process_alive'] is not False:
                raise StateError('only an unresolved dead dispatch can be reconciled')
            runs = self._runs()
            if run_id not in runs:
                runs[run_id] = run
            runs[run_id].update(status='reconciled', reconciled_at=stamp(), reason=reason,
                                evidence_hash=evidence_hash)
            self._save(self.meta / 'runs.json', runs)
            return runs[run_id]

    def _file_hash(self, path):
        return digest(path.read_bytes()) if path.exists() else None

    def _target(self, relative, allow_plan=False, allow_log=False):
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise StateError('durable path must be relative to PM directory')
        if (relative not in ('HANDOFF.md', 'SPEC.md') and path.parts[0] != 'prompts'
                and not (allow_plan and relative == 'PLAN.md')
                and not (allow_log and relative == 'TASK_LOG.md')):
            raise StateError('unsupported durable path; PLAN requires update_plan; TASK_LOG is append-only')
        target = self.pm / path
        if not target.resolve().is_relative_to(self.pm) or any(
                (self.pm / Path(*path.parts[:index])).is_symlink() for index in range(1, len(path.parts) + 1)):
            raise StateError('symlink durable paths are not supported')
        return target

    def _operation_path(self, operation_id):
        if not operation_id or len(operation_id) > 200 or '\n' in operation_id or '-->' in operation_id:
            raise StateError('invalid operation-id')
        return self.meta / 'operations' / (digest(operation_id.encode()) + '.json')

    def _replay(self, operation_id, request):
        prior = self._read_json(self._operation_path(operation_id))
        if prior is not None:
            if prior['request_hash'] != digest(serialized(request)):
                raise StateError('operation-id reused with different content')
            return prior['result']
        return None

    def _recover(self):
        for path in sorted((self.meta / 'operations').glob('*.json')):
            intent = self._read_json(path)
            if not isinstance(intent, dict) or not all(k in intent for k in ('done', 'files', 'result', 'request_hash')):
                raise StateError('invalid durable intent: ' + str(path))
            if not intent['done']:
                self._apply(path, intent)

    def _apply(self, operation_path, intent):
        # Inspect every target before mutating any: unrelated writes cannot be clobbered.
        targets = []
        for entry in intent['files']:
            target = self._target(entry['path'], allow_plan=True, allow_log=True)
            current = self._file_hash(target)
            after = base64.b64decode(entry['after'], validate=True)
            if current not in (entry['before_hash'], digest(after)):
                raise StateError('unresolved intent conflicts with external change: ' + str(target))
            targets.append((target, after, current))
        for target, after, current in targets:
            if current != digest(after):
                self._replace(target, after)
        intent['done'] = True
        self._save(operation_path, intent)

    def _transaction(self, operation_id, request, files, result):
        path = self._operation_path(operation_id)
        intent = {'done': False, 'operation_id': operation_id, 'request_hash': digest(serialized(request)),
                  'result': result, 'files': [{'path': str(p.relative_to(self.pm)),
                  'before_hash': self._file_hash(p), 'after': base64.b64encode(data).decode()}
                  for p, data in files]}
        self._save(path, intent)
        self._apply(path, intent)
        return result

    def update_plan(self, token, expected_hash, candidate, event, operation_id):
        with self._lock():
            self._check(token)
            if not event.strip():
                raise StateError('PLAN update requires a nonempty TASK_LOG event')
            if expected_hash in ('missing', 'none'):
                expected_hash = None
            request = {'kind': 'plan', 'expected_hash': expected_hash, 'candidate': candidate, 'event': event}
            replay = self._replay(operation_id, request)
            if replay is not None:
                return replay
            plan = self._target('PLAN.md', allow_plan=True)
            log = self._target('TASK_LOG.md', allow_log=True)
            if self._file_hash(plan) != expected_hash:
                raise StateError('PLAN hash changed; reread before applying')
            with tempfile.TemporaryDirectory(prefix='pm-plan-') as temporary:
                source = Path(temporary) / 'PLAN.md'
                source.write_text(candidate)
                lint = subprocess.run(['bash', str(Path(__file__).with_name('plan-lint.sh')), str(source)],
                                      capture_output=True, text=True)
            if lint.returncode not in (0, 3):
                raise StateError('candidate PLAN rejected: ' + lint.stderr.strip())
            result = {'operation_id': operation_id, 'plan_hash': digest(candidate.encode()),
                      'lint_exit': lint.returncode, 'lint_output': lint.stdout + lint.stderr}
            marker = '\n<!-- pm-operation: ' + operation_id + ' -->\n'
            log_bytes = log.read_bytes() if log.exists() else b''
            return self._transaction(operation_id, request,
                [(plan, candidate.encode()), (log, log_bytes + (marker + event.rstrip() + '\n').encode())], result)

    def _write(self, token, relative, content, operation_id, append):
        with self._lock():
            self._check(token)
            target = self._target(relative, allow_log=append)
            request = {'kind': 'append' if append else 'write', 'path': relative, 'content': content}
            replay = self._replay(operation_id, request)
            if replay is not None:
                return replay
            before = target.read_bytes() if append and target.exists() else b''
            after = before + content.encode()
            return self._transaction(operation_id, request, [(target, after)],
                                     {'operation_id': operation_id, 'path': relative, 'hash': digest(after)})

    def write(self, token, relative, content, operation_id):
        return self._write(token, relative, content, operation_id, False)

    def append(self, token, relative, content, operation_id):
        return self._write(token, relative, content, operation_id, True)
