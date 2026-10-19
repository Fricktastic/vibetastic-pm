"""A wrapper-launched session must be told the wrapper's lease is its own."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'scripts/orchestrator-state.py'
HOOK = ROOT / 'scripts/handoff-volatile-hook.py'


def run(args, **env):
    full = {k: v for k, v in os.environ.items() if not k.startswith('PM_ORCHESTRATOR_')}
    full.update(env)
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, env=full)


class LeaseSelfRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pm = Path(self.tmp.name).resolve()
        (self.pm / '.orchestrator').mkdir()
        (self.pm / '.orchestrator/config.json').write_text('{}')
        own = run([STATE, '--pm-dir', self.pm, 'acquire', '--provider', 'claude',
                   '--session', 'wrapper-session', '--pid', os.getpid()])
        self.assertEqual(own.returncode, 0, own.stderr)
        self.token = json.loads(own.stdout)['token']

    def tearDown(self):
        self.tmp.cleanup()

    def caller(self, **env):
        out = run([STATE, '--pm-dir', self.pm, 'active'], **env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)['caller']

    def test_active_reports_ownership_from_environment_token(self):
        self.assertTrue(self.caller(PM_ORCHESTRATOR_TOKEN=self.token)['owns_lease'])
        self.assertFalse(self.caller(PM_ORCHESTRATOR_TOKEN='stale')['owns_lease'])
        self.assertFalse(self.caller()['owns_lease'])

    def test_session_start_announces_ownership_only_to_token_holder(self):
        out = run([HOOK, '--pm-dir', self.pm], PM_ORCHESTRATOR_TOKEN=self.token,
                  PM_ORCHESTRATOR_SESSION='wrapper-session')
        self.assertIn('YOU own the project lease', out.stdout)
        self.assertNotIn(self.token, out.stdout)
        self.assertEqual(run([HOOK, '--pm-dir', self.pm]).stdout, '')


if __name__ == '__main__':
    unittest.main()
