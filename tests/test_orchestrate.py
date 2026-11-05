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

    def _project(self, d, exit_code=0):
        pm = Path(d)
        (pm/'.orchestrator').mkdir()
        (pm/'.orchestrator/config.json').write_text('{}')
        (pm/'bin').mkdir()
        cli = pm/'bin/claude'
        cli.write_text(f'#!/bin/sh\nexit {exit_code}\n')
        cli.chmod(0o755)
        return pm

    def _state(self, d, *args):
        return subprocess.run([sys.executable,str(ROOT/'scripts/orchestrator-state.py'),'--pm-dir',d,*args],capture_output=True,text=True)

    def _launch(self, d, pm):
        return subprocess.run([sys.executable,str(ROOT/'orchestrate.py'),'--pm-dir',d,'claude'],
                              env={**os.environ,'PATH':str(pm/'bin')+os.pathsep+os.environ['PATH']},capture_output=True,text=True)

    def _lease_held_by(self, d, proc, reserve=False):
        acquired = self._state(d,'acquire','--provider','claude','--session','old','--pid',str(proc.pid))
        self.assertEqual(acquired.returncode,0,acquired.stderr)
        if reserve:
            token = json.loads(acquired.stdout)['token']
            r = self._state(d,'reserve','--token',token,'--run-id','run-1','--task-id','T001','--pid',str(proc.pid),'--worktree',d)
            self.assertEqual(r.returncode,0,r.stderr)

    def test_dead_owner_without_reservations_is_taken_over_and_released(self):
        with tempfile.TemporaryDirectory() as d:
            pm = self._project(d)
            proc = subprocess.Popen(['sleep','30'])
            self._lease_held_by(d, proc)
            proc.kill(); proc.wait()
            r = self._launch(d, pm)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertIn('took over stale lease',r.stderr)
            self.assertIsNone(json.loads(self._state(d,'active').stdout)['lease'])

    def test_live_owner_is_never_taken_over(self):
        with tempfile.TemporaryDirectory() as d:
            pm = self._project(d)
            proc = subprocess.Popen(['sleep','30'])
            try:
                self._lease_held_by(d, proc)
                r = self._launch(d, pm)
                self.assertEqual(r.returncode,31,r.stderr)
                self.assertIn('owns this project',r.stderr)
                self.assertEqual(json.loads(self._state(d,'active').stdout)['lease']['session'],'old')
            finally:
                proc.kill(); proc.wait()

    def test_dead_owner_with_open_reservation_is_not_taken_over(self):
        with tempfile.TemporaryDirectory() as d:
            pm = self._project(d)
            proc = subprocess.Popen(['sleep','30'])
            self._lease_held_by(d, proc, reserve=True)
            proc.kill(); proc.wait()
            r = self._launch(d, pm)
            self.assertEqual(r.returncode,31,r.stderr)
            self.assertIn('run-1',r.stderr)
            self.assertEqual(json.loads(self._state(d,'active').stdout)['lease']['session'],'old')

    def test_uninstalled_project_not_silently_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run([sys.executable,str(ROOT/'orchestrate.py'),'--pm-dir',d,'claude'],capture_output=True,text=True)
            self.assertEqual(r.returncode,31,r.stderr)
            self.assertFalse((Path(d)/'.orchestrator').exists())
