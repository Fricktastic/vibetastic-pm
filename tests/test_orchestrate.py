"""Launcher must hold the lease for the harness lifetime and cleanly release it."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class LauncherTests(unittest.TestCase):
    def test_launch_passes_session_scope_and_releases_after_cli_failure(self):
        with tempfile.TemporaryDirectory() as d:
            pm = Path(d)
            (pm/'.orchestrator').mkdir()
            (pm/'.orchestrator/config.json').write_text('{}')
            (pm/'bin').mkdir()
            cli=pm/'bin/codex'
            cli.write_text('#!/usr/bin/env python3\nimport os,json,sys\nfrom pathlib import Path\nPath("seen.json").write_text(json.dumps({k:os.environ.get(k) for k in ["PM_ORCHESTRATOR_TOKEN","PM_ORCHESTRATOR_PROVIDER","PM_ORCHESTRATOR_SESSION","PM_DIR"]}))\nsys.exit(7)\n')
            cli.chmod(0o755)
            r = subprocess.run([sys.executable,str(ROOT/'orchestrate.py'),'--pm-dir',d,'codex'],
                               env={**os.environ,'PATH':str(pm/'bin')+os.pathsep+os.environ['PATH']},capture_output=True,text=True)
            self.assertEqual(r.returncode,7,r.stderr)
            env=json.loads((pm/'seen.json').read_text())
            self.assertTrue(env['PM_ORCHESTRATOR_TOKEN'])
            self.assertEqual(env['PM_ORCHESTRATOR_PROVIDER'],'codex')
            check=subprocess.run([sys.executable,str(ROOT/'scripts/orchestrator-state.py'),'--pm-dir',d,'check','--token',env['PM_ORCHESTRATOR_TOKEN']],capture_output=True,text=True)
            self.assertEqual(check.returncode,31,check.stdout)

    def test_uninstalled_project_not_silently_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run([sys.executable,str(ROOT/'orchestrate.py'),'--pm-dir',d,'claude'],capture_output=True,text=True)
            self.assertEqual(r.returncode,31,r.stderr)
            self.assertFalse((Path(d)/'.orchestrator').exists())
