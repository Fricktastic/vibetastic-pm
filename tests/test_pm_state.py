"""Behavior tests for cooperative ownership and crash-recoverable PM writes."""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
PLAN = '''---
project: demo
tasks:
  - id: T001
    stage: 1
    title: Demo
    agent: codex
    status: pending
    depends_on: []
    failure_count: 0
---
'''


def contender(pm, queue):
    from pm_state import PMState, StateError
    try:
        queue.put(('ok', PMState(pm).acquire('codex', str(os.getpid()), os.getpid())['token']))
    except StateError:
        queue.put(('denied', None))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SCRIPTS / 'pm_state.py').exists(), 'PM state safety module is missing')
        import pm_state
        self.mod = pm_state
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pm = Path(self.tmp.name)
        (self.pm / '.orchestrator').mkdir()
        (self.pm / '.orchestrator/config.json').write_text('{"version":1}')
        (self.pm / 'PLAN.md').write_text(PLAN)
        (self.pm / 'TASK_LOG.md').write_text('# Task log\n')
        self.state = pm_state.PMState(self.pm)

    def own(self):
        return self.state.acquire('codex', 'session', os.getpid())['token']

    def update(self, token, candidate=None, operation='op-1', expected=None):
        return self.state.update_plan(token, expected or hashlib.sha256(PLAN.encode()).hexdigest(),
                                      candidate or PLAN.replace('pending', 'done'), '### task_completed\n', operation)

    def test_competing_acquire_has_exactly_one_winner(self):
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        workers = [ctx.Process(target=contender, args=(str(self.pm), queue)) for _ in range(5)]
        for worker in workers:
            worker.start()
        results = [queue.get(timeout=15)[0] for _ in workers]
        for worker in workers:
            worker.join(15)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(results.count('ok'), 1)
        self.assertEqual(results.count('denied'), 4)

    def test_handoff_fences_old_token_and_renew_release(self):
        token = self.own()
        self.assertEqual(self.state.renew(token)['token'], token)
        replacement = self.state.handoff(token, 'claude', 'next', os.getpid())['token']
        self.assertNotEqual(token, replacement)
        with self.assertRaises(self.mod.StateError):
            self.state.check(token)
        self.state.release(replacement)
        with self.assertRaises(self.mod.StateError):
            self.state.check(replacement)

    def test_absent_or_corrupt_lease_denied(self):
        with self.assertRaises(self.mod.StateError):
            self.state.check('anything')
        (self.pm / '.orchestrator/lease.json').write_text('{')
        with self.assertRaises(self.mod.StateError):
            self.own()

    def test_stale_hash_and_structural_lint_leave_files_untouched(self):
        token = self.own()
        with self.assertRaises(self.mod.StateError):
            self.update(token, expected='0' * 64)
        with self.assertRaises(self.mod.StateError):
            self.update(token, candidate=PLAN.replace('    depends_on: []\n', ''))
        self.assertEqual((self.pm / 'PLAN.md').read_text(), PLAN)
        self.assertEqual((self.pm / 'TASK_LOG.md').read_text(), '# Task log\n')

    def test_vocabulary_drift_is_accepted_and_visible(self):
        result = self.update(self.own(), candidate=PLAN.replace('codex', 'future-provider'))
        self.assertEqual(result['lint_exit'], 3)
        self.assertIn('future-provider', (self.pm / 'PLAN.md').read_text())

    def test_operation_replay_is_idempotent_and_reuse_is_rejected(self):
        token = self.own()
        self.update(token)
        self.update(token)
        self.assertEqual((self.pm / 'TASK_LOG.md').read_text().count('task_completed'), 1)
        with self.assertRaises(self.mod.StateError):
            self.update(token, candidate=PLAN.replace('pending', 'failed'))

    def test_recover_replace_before_log_and_after_log_before_commit(self):
        token = self.own()
        real_replace = self.mod.os.replace
        for target in ('PLAN.md', 'TASK_LOG.md'):
            operation = 'crash-' + target
            candidate = PLAN.replace('pending', 'done') + '\n' + operation
            previous = (self.pm / 'PLAN.md').read_bytes()
            expected = hashlib.sha256(previous).hexdigest()
            def crash_after_replace(src, dst):
                real_replace(src, dst)
                if Path(dst).name == target:
                    raise RuntimeError('simulated process death')
            with patch.object(self.mod.os, 'replace', side_effect=crash_after_replace):
                with self.assertRaises(RuntimeError):
                    self.update(token, candidate=candidate, operation=operation, expected=expected)
            self.state.check(token)
            self.update(token, candidate=candidate, operation=operation, expected=expected)
            self.assertEqual((self.pm / 'TASK_LOG.md').read_text().count(operation), 1)
            self.assertEqual((self.pm / 'PLAN.md').read_text(), candidate)

    def test_guarded_writes_block_traversal_and_preserve_append(self):
        token = self.own()
        self.state.write(token, 'HANDOFF.md', 'hello', 'write-1')
        self.state.append(token, 'TASK_LOG.md', '\nnew event\n', 'append-1')
        self.state.append(token, 'TASK_LOG.md', '\nnew event\n', 'append-1')
        self.assertEqual((self.pm / 'TASK_LOG.md').read_text(), '# Task log\n\nnew event\n')
        for path in ('../escape.md', '.orchestrator/lease.json', 'PLAN.md', 'TASK_LOG.md'):
            with self.assertRaises(self.mod.StateError):
                self.state.write(token, path, 'oops', 'bad-' + path)
        with self.assertRaises(self.mod.StateError):
            self.state.write('stale', 'SPEC.md', 'oops', 'stale')

    def test_dispatch_reservation_survives_handoff_and_blocks_duplicate(self):
        token = self.own()
        self.state.reserve(token, 'run-1', 'T001', os.getpid(), str(self.pm))
        token = self.state.handoff(token, 'claude', 'next', os.getpid())['token']
        with self.assertRaises(self.mod.StateError):
            self.state.reserve(token, 'run-2', 'T001', os.getpid(), str(self.pm))
        self.assertEqual(self.state.active()['runs'][0]['task_id'], 'T001')
        self.state.finish('run-1')
        self.state.reserve(token, 'run-2', 'T001', os.getpid(), str(self.pm))

    def test_takeover_requires_current_evidence_and_dead_owner_preserves_runs(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(lambda: process.poll() is None and process.kill())
        token = self.state.acquire('codex', 'old', process.pid)['token']
        self.state.reserve(token, 'run-1', 'T001', os.getpid(), str(self.pm))
        evidence = self.state.active()
        with self.assertRaises(self.mod.StateError):
            self.state.takeover('claude', 'new', os.getpid(), evidence['evidence_hash'], 'reviewed')
        process.terminate()
        process.wait()
        with self.assertRaises(self.mod.StateError):
            self.state.takeover('claude', 'new', os.getpid(), evidence['evidence_hash'], 'reviewed')
        evidence = self.state.active()
        token = self.state.takeover('claude', 'new', os.getpid(), evidence['evidence_hash'], 'reviewed')['token']
        with self.assertRaises(self.mod.StateError):
            self.state.reserve(token, 'run-2', 'T001', os.getpid(), str(self.pm))
        self.assertEqual((self.pm / 'PLAN.md').read_text(), PLAN)

    def journal(self, rows):
        (self.pm / 'logs').mkdir(exist_ok=True)
        (self.pm / 'logs/runs.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def test_conflicting_worktree_or_branch_is_blocked_but_readonly_is_parallel(self):
        token = self.own()
        self.state.reserve(token, 'run-1', 'T001', os.getpid(), str(self.pm / 'work'), 'feature')
        for worktree, branch in ((str(self.pm / 'work'), 'other'), (str(self.pm / 'other'), 'feature')):
            with self.assertRaises(self.mod.StateError):
                self.state.reserve(token, 'run-2', 'T002', os.getpid(), worktree, branch)
        self.state.reserve(token, 'ro-1', 'review', os.getpid(), '', 'feature')
        self.state.reserve(token, 'ro-2', 'critic', os.getpid(), '', 'feature')

    def test_unmatched_legacy_run_blocks_duplicate_until_explicit_reconciliation(self):
        token = self.own()
        process = subprocess.Popen([sys.executable, '-c', 'pass'])
        process.wait()
        self.journal([{'event': 'run_start', 'run_id': 'legacy', 'task_id': 'T001',
                       'pid': process.pid, 'worktree': str(self.pm / 'old-work'), 'branch': 'old'}])
        evidence = self.state.active()
        self.assertEqual(evidence['runs'][0]['run_id'], 'legacy')
        self.assertFalse(evidence['runs'][0]['process_alive'])
        with self.assertRaises(self.mod.StateError):
            self.state.reserve(token, 'new', 'T001', os.getpid(), '', None)
        self.state.reconcile(token, 'legacy', evidence['evidence_hash'], 'Read run log and checked worktree')
        self.state.reserve(token, 'new', 'T001', os.getpid(), '', None)
        self.assertEqual((self.pm / 'PLAN.md').read_text(), PLAN)

    def test_live_legacy_and_unknown_task_records_conservatively_block_builds(self):
        token = self.own()
        self.journal([{'event': 'run_start', 'run_id': 'legacy', 'task_id': None,
                       'pid': os.getpid(), 'role': 'build', 'worktree': None}])
        with self.assertRaises(self.mod.StateError):
            self.state.reserve(token, 'new', 'T001', os.getpid(), str(self.pm / 'work'), 'new')
        evidence = self.state.active()
        with self.assertRaises(self.mod.StateError):
            self.state.reconcile(token, 'legacy', evidence['evidence_hash'], 'still running')
        self.journal([{'event': 'run_start', 'run_id': 'legacy', 'task_id': 'T001', 'pid': os.getpid()},
                      {'event': 'run_finish', 'run_id': 'legacy', 'exit': 0}])
        self.state.reserve(token, 'new', 'T001', os.getpid(), str(self.pm / 'work'), 'new')
        self.assertEqual(len(self.state.active()['journal']['completed']), 1)

    def test_journal_and_git_content_changes_invalidate_evidence(self):
        token = self.own()
        work = self.pm / 'work'
        work.mkdir()
        def git(*args):
            subprocess.run(['git', '-C', str(work), *args], check=True, capture_output=True)
        git('init')
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.invalid')
        (work / 'tracked').write_text('initial')
        git('add', '.')
        git('commit', '-m', 'initial')
        self.state.reserve(token, 'run-1', 'T001', os.getpid(), str(work), 'feature')
        initial = self.state.active()
        self.assertTrue(initial['runs'][0]['git']['head'])
        self.journal([{'event': 'run_finish', 'run_id': 'old', 'exit': 0}])
        journal_changed = self.state.active()
        self.assertNotEqual(initial['evidence_hash'], journal_changed['evidence_hash'])
        (work / 'tracked').write_text('first change')
        first = self.state.active()['evidence_hash']
        (work / 'tracked').write_text('second change')
        self.assertNotEqual(first, self.state.active()['evidence_hash'])
        (work / 'untracked').write_text('new')
        first = self.state.active()['evidence_hash']
        (work / 'untracked').write_text('different')
        self.assertNotEqual(first, self.state.active()['evidence_hash'])

    def test_truncated_journal_is_visible_and_blocks_reservations(self):
        token = self.own()
        self.journal([])
        (self.pm / 'logs/runs.jsonl').write_text('{')
        self.assertTrue(self.state.active()['journal']['errors'])
        with self.assertRaises(self.mod.StateError):
            self.state.reserve(token, 'new', 'T001', os.getpid(), '', None)

    def test_recovery_before_replace_and_external_edit_conflict(self):
        token = self.own()
        real_replace = self.mod.os.replace
        def crash_before_plan(src, dst):
            if Path(dst).name == 'PLAN.md':
                raise RuntimeError('crash before plan replace')
            real_replace(src, dst)
        with patch.object(self.mod.os, 'replace', side_effect=crash_before_plan):
            with self.assertRaises(RuntimeError):
                self.update(token)
        self.assertEqual((self.pm / 'PLAN.md').read_text(), PLAN)
        (self.pm / 'PLAN.md').write_text('external edit')
        with self.assertRaises(self.mod.StateError):
            self.state.check(token)
        self.assertEqual((self.pm / 'TASK_LOG.md').read_text(), '# Task log\n')
        (self.pm / 'PLAN.md').write_text(PLAN)
        self.state.check(token)
        self.assertIn('done', (self.pm / 'PLAN.md').read_text())

    def test_profile_is_authoritative_and_foreign_finish_is_denied(self):
        lease = self.state.acquire('codex', 'session', os.getpid(), 'codex-fallback')
        self.assertEqual(self.state.check(lease['token'])['profile'], 'codex-fallback')
        self.state.reserve(lease['token'], 'run', 'T001', os.getpid(), '')
        with self.assertRaises(self.mod.StateError):
            self.state.finish('run', os.getppid())
        with self.assertRaises(self.mod.StateError):
            self.state.check(lease['token'], 'claude')

    def test_initial_plan_and_event_file_cli(self):
        token = self.own()
        (self.pm / 'PLAN.md').unlink()
        candidate = self.pm / 'candidate.md'
        candidate.write_text(PLAN)
        event = self.pm / 'event.md'
        event.write_text('### plan_generated\nA multiline event\n')
        result = subprocess.run([sys.executable, str(SCRIPTS / 'plan-update.py'), '--pm-dir', str(self.pm),
            '--token', token, '--expected-hash', 'missing', '--candidate', str(candidate),
            '--event-file', str(event), '--operation-id', 'initial'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.pm / 'PLAN.md').read_text(), PLAN)
        self.assertIn('A multiline event', (self.pm / 'TASK_LOG.md').read_text())

    def test_blank_plan_event_is_rejected(self):
        token = self.own()
        with self.assertRaises(self.mod.StateError):
            self.state.update_plan(token, hashlib.sha256(PLAN.encode()).hexdigest(), PLAN, '  ', 'empty')

    def test_cli_denies_without_lease_and_plan_update_accepts_valid_owner(self):
        state_cli = [sys.executable, str(SCRIPTS / 'orchestrator-state.py'), '--pm-dir', str(self.pm)]
        denied = subprocess.run(state_cli + ['check', '--token', 'missing'], capture_output=True)
        self.assertEqual(denied.returncode, 31)
        token = self.own()
        candidate = self.pm / 'candidate.md'
        candidate.write_text(PLAN.replace('pending', 'done'))
        result = subprocess.run([sys.executable, str(SCRIPTS / 'plan-update.py'), '--pm-dir', str(self.pm),
            '--token', token, '--expected-hash', hashlib.sha256(PLAN.encode()).hexdigest(),
            '--candidate', str(candidate), '--event', '### completed', '--operation-id', 'cli-1'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('done', (self.pm / 'PLAN.md').read_text())


if __name__ == '__main__':
    unittest.main()
