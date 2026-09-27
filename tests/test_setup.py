import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class SetupTests(unittest.TestCase):
    def test_existing_project_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            pm=Path(d)
            original='---\nbuilder_backends: [claude]\n---\ncustom state'
            (pm/'PROJECT.md').write_text(original)
            r=subprocess.run(['bash',str(ROOT/'setup.sh'),'test',d,'org/repo'],cwd=d,capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertEqual((pm/'PROJECT.md').read_text(),original)
            self.assertFalse((pm/'.claude').exists())

    def test_fresh_project_installs_both_harnesses(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run(['bash',str(ROOT/'setup.sh'),'test',d,'org/repo'],cwd=d,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertTrue((Path(d)/'.codex/hooks.json').is_file())
            self.assertTrue((Path(d)/'AGENTS.md').is_file())

    def test_fresh_project_declares_a_valid_review_policy(self):
        # Issue #50: setup writes the round caps as project policy; the commented example
        # sections must not be parsed as policy, so tiers/triggers stay on framework defaults.
        import sys
        sys.path.insert(0,str(ROOT/'scripts'))
        import project_policy
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run(['bash',str(ROOT/'setup.sh'),'test',d,'org/repo'],cwd=d,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            policy=project_policy.load(d)
            self.assertEqual(policy['errors'],[])
            self.assertEqual(policy['sources']['critic_round_cap'],'project')
            self.assertEqual(policy['sources']['reviewer_fixup_round_cap'],'project')
            self.assertEqual(policy['sources']['verify_tiers'],'default')
            self.assertEqual(policy['sources']['risk_triggers'],'default')

    def test_accepts_test_command_as_fifth_argument(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run(
                ['bash',str(ROOT/'setup.sh'),'test',d,'org/repo','python -m compileall .','python -m unittest'],
                cwd=d,capture_output=True,text=True,
            )
            self.assertEqual(r.returncode,0,r.stderr)
            project=(Path(d)/'PROJECT.md').read_text()
            self.assertIn('python -m compileall .',project)
            self.assertIn('python -m unittest',project)
