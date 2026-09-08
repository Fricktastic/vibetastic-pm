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
