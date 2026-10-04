from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class PlanDefenseTests(unittest.TestCase):
    def test_ci_accepts_vocab_but_rejects_structure(self):
        for fixture, expected in [('plan-vocab.md',0),('plan-missing-field.md',1)]:
            r=subprocess.run([sys.executable,str(ROOT/'scripts/check-plan.py'),str(ROOT/'tests/fixtures'/fixture)],capture_output=True,text=True)
            self.assertEqual(r.returncode,expected,r.stderr)

    def test_precommit_checks_staged_version_not_repaired_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(['git','init','-q',d],check=True)
            p=Path(d)/'PLAN.md'
            p.write_text('broken staged plan')
            subprocess.run(['git','-C',d,'add','PLAN.md'],check=True)
            p.write_text((ROOT/'tests/fixtures/plan-good.md').read_text())
            r=subprocess.run([sys.executable,str(ROOT/'scripts/check-plan.py'),'--staged'],cwd=d,capture_output=True,text=True)
            self.assertEqual(r.returncode,1,r.stderr)

    def test_lint_bounds_tasks_body_at_new_keys_both_orders(self):
        # plan-good.md: recommended_next before attention.
        # plan-good-attention-first.md: attention before recommended_next.
        for fixture in ['plan-good.md', 'plan-good-attention-first.md']:
            r = subprocess.run(['bash', str(ROOT / 'scripts/plan-lint.sh'),
                                str(ROOT / 'tests/fixtures' / fixture)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_lint_attention_failures_are_attention_specific(self):
        # Must exit 1 for the attention-specific missing-field reason, NOT because the
        # attention item was mis-chunked as a task (which would say it misses 'stage' etc.).
        r = subprocess.run(['bash', str(ROOT / 'scripts/plan-lint.sh'),
                            str(ROOT / 'tests/fixtures' / 'plan-attention-missing-field.md')],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn('attention item', r.stderr)
        self.assertNotIn("missing required field 'stage'", r.stderr)

    def test_lint_preserves_malformed_task_checks(self):
        # A malformed task before the new keys (and old-format fixtures) must fail exactly as
        # before; the Step 2a truncation never masks a real task chunk.
        for fixture in ['plan-missing-field.md', 'plan-bad-dep.md',
                        'plan-nested-depends.md', 'plan-bad-escape.md']:
            r = subprocess.run(['bash', str(ROOT / 'scripts/plan-lint.sh'),
                                str(ROOT / 'tests/fixtures' / fixture)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stderr)
