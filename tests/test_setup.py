import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
DOCTOR=ROOT/'scripts/orchestrator-doctor.py'

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

    def test_template_prose_is_written_not_executed(self):
        # The PROJECT.md heredoc is unquoted so ${VARS} expand; an unescaped backtick in its
        # prose became command substitution and ran `xcodebuild build-for-testing` at setup.
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run(['bash',str(ROOT/'setup.sh'),'test',d,'org/repo'],cwd=d,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertNotIn('command not found',r.stderr)
            self.assertNotIn('xcodebuild:',r.stderr)
            project=(Path(d)/'PROJECT.md').read_text()
            self.assertIn('`xcodebuild build-for-testing`, never a bare `build`',project)

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

    def test_setup_fresh_project_initializes_view_contract(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run(['bash',str(ROOT/'setup.sh'),'test',d,'org/repo'],cwd=d,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            snapshot=Path(d)/'.orchestrator/view/v1/snapshot.json'
            self.assertTrue(snapshot.is_file(), f'missing view snapshot at {snapshot}')
            payload=json.loads(snapshot.read_text())
            self.assertEqual(payload.get('contract'),'vibetastic-view/v1')
            self.assertIsInstance(payload.get('schema_version'),int)
            self.assertGreaterEqual(payload.get('generation',0),1)
            self.assertFalse((Path(d)/'.orchestrator/view/install-projection-failed.json').exists())

    def test_doctor_reports_view_contract(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run(['bash',str(ROOT/'setup.sh'),'test',d,'org/repo'],cwd=d,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            result=subprocess.run([sys.executable,str(DOCTOR),'--pm-dir',d,'--framework-dir',str(ROOT),'--json'],
                                  cwd=d,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads(result.stdout)
            checks=report.get('checks')
            self.assertIsInstance(checks,dict,'doctor report missing checks dict')
            view=checks.get('view_contract_v1')
            self.assertIsInstance(view,dict,'doctor report missing view_contract_v1 check')
            self.assertEqual(view.get('status'),'ok')
            self.assertTrue(view.get('assertions'))
            self.assertTrue((Path(d)/'.orchestrator/view/v1/snapshot.json').is_file())
