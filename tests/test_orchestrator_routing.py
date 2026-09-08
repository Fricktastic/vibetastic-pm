"""Policy regressions: a fallback session must preserve its orchestrator quota and gates."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class RoutingTests(unittest.TestCase):
    def call(self, *args):
        return subprocess.run([sys.executable, str(ROOT/'scripts/orchestrator-routing.py'), *args], capture_output=True, text=True)

    def test_fallback_refuses_subscription_builders(self):
        for backend, model in [('codex', 'gpt-5.6-terra'), ('claude', 'sonnet')]:
            r = self.call('check', '--profile', 'codex-fallback', '--backend', backend, '--model', model, '--role', 'build')
            self.assertEqual(r.returncode, 31, r.stderr)

    def test_diversity_applies_to_primary_and_fallback(self):
        for model, fallback in [('openrouter/minimax/minimax-m3', ''), ('openrouter/moonshotai/kimi-k2.6', 'openrouter/minimax/minimax-m3')]:
            r = self.call('check', '--profile', 'codex-fallback', '--backend', 'opencode', '--model', model,
                          '--fallback-model', fallback, '--role', 'reviewer', '--author-model', 'openrouter/minimax/minimax-m3')
            self.assertEqual(r.returncode, 31, r.stderr)

    def test_diverse_openrouter_review_allowed(self):
        r = self.call('check', '--profile', 'codex-fallback', '--backend', 'opencode', '--model', 'openrouter/moonshotai/kimi-k2.6',
                      '--role', 'critic', '--author-model', 'gpt-5.6-terra')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_review_without_author_and_unknown_family_refused(self):
        for author in ['', 'mystery-model']:
            r = self.call('check', '--profile', 'normal', '--backend', 'opencode', '--model', 'openrouter/minimax/minimax-m3',
                          '--role', 'reviewer', '--author-model', author)
            self.assertEqual(r.returncode, 31, r.stderr)

    def test_security_review_and_adjudication_do_not_lower_floor(self):
        for role in ['reviewer', 'adjudicator']:
            r = self.call('check', '--profile', 'codex-fallback', '--backend', 'opencode', '--model', 'openrouter/moonshotai/kimi-k2.6',
                          '--role', role, '--security', '--author-model', 'gpt-5.6-terra')
            self.assertEqual(r.returncode, 31, r.stderr)
        r = self.call('check', '--profile', 'codex-fallback', '--backend', 'claude', '--model', 'opus', '--role', 'adjudicator', '--security', '--exceptional-adjudication')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_anthropic_never_routes_via_openrouter(self):
        r = self.call('check', '--profile', 'normal', '--backend', 'opencode', '--model', 'openrouter/anthropic/claude-opus', '--role', 'build')
        self.assertEqual(r.returncode, 31, r.stderr)

    def test_normal_order_preserved_fallback_is_session_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'PROJECT.md'
            content = '---\nbuilder_backends: [claude, codex, opencode]\nreviewer_backends: [opencode]\n---\n'
            p.write_text(content)
            for profile, expected in [('normal', ['claude','codex','opencode']), ('codex-fallback',['opencode'])]:
                r = self.call('order', '--profile', profile, '--project', str(p), '--role', 'build')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(json.loads(r.stdout), expected)
            self.assertEqual(p.read_text(), content)

if __name__ == '__main__':
    unittest.main()

class DispatchGuardTests(unittest.TestCase):
    def test_installed_project_refuses_dispatch_without_lease_before_any_run(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            pm = Path(d)
            (pm/'.orchestrator').mkdir()
            (pm/'.orchestrator/config.json').write_text('{}')
            (pm/'prompts').mkdir()
            prompt = pm/'prompts/task-T001.md'
            prompt.write_text('test')
            r = subprocess.run(['bash', str(ROOT/'dispatch.sh'), '--backend', 'opencode', 'openrouter/minimax/minimax-m3',
                                d, str(prompt), '', 'true'], env={**os.environ, 'PM_DIR': d, 'PM_ORCHESTRATOR_TOKEN': ''},
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 31, r.stderr)
            self.assertFalse((pm/'logs/runs.jsonl').exists())

class ManagedDispatchTests(unittest.TestCase):
    def test_managed_dispatch_finishes_reservation_strips_token_and_normalizes_usage(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            pm=Path(d).resolve()
            (pm/'.orchestrator').mkdir()
            (pm/'.orchestrator/config.json').write_text('{}')
            (pm/'prompts').mkdir()
            prompt=pm/'prompts/investigate-T001.md'
            prompt.write_text('read-only task')
            code=pm/'code';code.mkdir()
            subprocess.run(['git','init','-q',str(code)],check=True)
            bin_dir=pm/'bin';bin_dir.mkdir()
            fake=bin_dir/'codex'
            fake.write_text('''#!/bin/sh
if [ -n "${PM_ORCHESTRATOR_TOKEN:-}" ]; then echo leaked > "$CHECK_ENV"; else echo stripped > "$CHECK_ENV"; fi
printf '%s\\n' '{"type":"thread.started","thread_id":"test"}' '{"type":"item.completed","item":{"type":"agent_message","text":"read-only result"}}' '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":60,"output_tokens":20,"reasoning_output_tokens":8}}'
''')
            fake.chmod(0o755)
            own=subprocess.run([sys.executable,str(ROOT/'scripts/orchestrator-state.py'),'--pm-dir',str(pm),'acquire','--provider','codex','--session','test','--pid',str(os.getpid()),'--profile','normal'],capture_output=True,text=True)
            self.assertEqual(own.returncode,0,own.stderr)
            token=json.loads(own.stdout)['token']
            env={**os.environ,'PM_DIR':str(pm),'PM_ORCHESTRATOR_TOKEN':token,'PM_ORCHESTRATOR_PROVIDER':'codex','PM_ORCHESTRATOR_SESSION':'test',
                 'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'CHECK_ENV':str(pm/'env-check')}
            r=subprocess.run(['bash',str(ROOT/'dispatch.sh'),'--read-only','--role','investigator','--backend','codex','gpt-5.6-terra',str(code),str(prompt)],env=env,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual((pm/'env-check').read_text().strip(),'stripped')
            reservations=json.loads((pm/'.orchestrator/runs.json').read_text())
            self.assertEqual([run['status'] for run in reservations.values()],['finished'])
            rows=[json.loads(line) for line in (pm/'logs/cost.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['input_tokens'],40)
            self.assertEqual(rows[0]['output_tokens'],20)
            self.assertEqual(rows[0]['quota_proxy_tokens'],60)
            self.assertEqual(rows[0]['role'],'investigator')
