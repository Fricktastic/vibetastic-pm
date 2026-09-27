"""Planning artifact registration must never return full spec bodies to the partner."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]

class RoleResultTests(unittest.TestCase):
    def setUp(self):
        path=ROOT/'scripts/dispatch-role.py'
        self.assertTrue(path.exists(),'role artifact wrapper is missing')
        spec=importlib.util.spec_from_file_location('dispatch_role',path)
        self.module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_tech_lead_returns_metadata_and_rewrites_promoted_path(self):
        reply='commentary should stay on disk\n<!-- TECH_LEAD_RESULT_START -->\n```yaml\nspec_path: "/tmp/staged.md"\ntask_title: "A task"\nsuggested_tier: standard\nsecurity: false\nverify_tier: R1\nrisk: false\nobservation: none\n```\n<!-- TECH_LEAD_RESULT_END -->\n'
        result=self.module.metadata(reply,'TECH_LEAD','/tmp/staged.md','/pm/prompts/task-T001.md')
        self.assertIn('/pm/prompts/task-T001.md',result)
        self.assertNotIn('commentary',result)
        self.assertNotIn('/tmp/staged.md',result)

    def test_rejects_missing_or_duplicate_metadata_delimiter(self):
        for reply in ['full spec body', '<!-- TECH_LEAD_RESULT_START -->'*2]:
            with self.assertRaises(ValueError):
                self.module.metadata(reply,'TECH_LEAD','/tmp/spec.md','/pm/prompts/task-T001.md')

    def test_rejects_metadata_pointing_at_an_unregistered_artifact(self):
        reply='<!-- TECH_LEAD_RESULT_START -->\n```yaml\nspec_path: "/wrong.md"\ntask_title: x\nsuggested_tier: fast\nsecurity: false\nverify_tier: R0\nrisk: false\nobservation: none\n```\n<!-- TECH_LEAD_RESULT_END -->'
        with self.assertRaises(ValueError):
            self.module.metadata(reply,'TECH_LEAD','/tmp/staged.md','/pm/prompts/task-T001.md')

    def test_rejects_metadata_without_the_risk_decision(self):
        # Issue #50: risk (critique) and verify_tier (evidence) are separate required decisions.
        for dropped in ('risk', 'verify_tier', 'observation'):
            fields={'task_title':'x','suggested_tier':'fast','security':'false','verify_tier':'R2','risk':'false',
                    'observation':'runtime'}
            fields.pop(dropped)
            body='\n'.join(f'{k}: {v}' for k,v in fields.items())
            reply=f'<!-- TECH_LEAD_RESULT_START -->\n```yaml\nspec_path: "/tmp/staged.md"\n{body}\n```\n<!-- TECH_LEAD_RESULT_END -->'
            with self.assertRaises(ValueError):
                self.module.metadata(reply,'TECH_LEAD','/tmp/staged.md','/pm/prompts/task-T001.md')

    def test_observation_must_be_a_known_kind_and_test_needs_its_command(self):
        # Issue #35: the merge gate's fail-on-base run needs the command that runs the test.
        def reply(extra):
            return ('<!-- TECH_LEAD_RESULT_START -->\n```yaml\nspec_path: "/tmp/staged.md"\n'
                    'task_title: x\nsuggested_tier: fast\nsecurity: false\nverify_tier: R0\n'
                    f'risk: false\n{extra}\n```\n<!-- TECH_LEAD_RESULT_END -->')
        for bad in ('observation: tests', 'observation: test', 'observation: test\nobservation_cmd: null',
                    'observation: test\nobservation_cmd: ""'):
            with self.assertRaises(ValueError, msg=bad):
                self.module.metadata(reply(bad), 'TECH_LEAD', '/tmp/staged.md', '/pm/prompts/task-T001.md')
        good = self.module.metadata(reply('observation: test\nobservation_cmd: "make test ONLY=X"'),
                                    'TECH_LEAD', '/tmp/staged.md', '/pm/prompts/task-T001.md')
        self.assertIn('observation_cmd', good)

    def test_shipped_tech_lead_template_satisfies_the_parser(self):
        """The result block tech-lead.md asks for, once filled, must register (issue #35)."""
        import re
        text=(ROOT/'prompts/tech-lead.md').read_text()
        end=text.index('<!-- TECH_LEAD_RESULT_END -->')
        block=text[text.rindex('<!-- TECH_LEAD_RESULT_START -->',0,end):end+len('<!-- TECH_LEAD_RESULT_END -->')]
        filled=block.replace('{{SPEC_OUTPUT_PATH}}','/tmp/staged.md')
        filled=re.sub(r'^observation: <.*>$','observation: test',filled,flags=re.M)
        filled=re.sub(r'^observation_cmd: ".*"$','observation_cmd: "make test ONLY=X"',filled,flags=re.M)
        filled=re.sub(r'^(\w+): <[^>]*>$',r'\1: x',filled,flags=re.M)
        self.module.metadata(filled,'TECH_LEAD','/tmp/staged.md','/pm/prompts/task-T001.md')
        for name in ('architect.md','critic.md','reviewer.md'):
            self.assertIn('fails on', (ROOT/'prompts'/name).read_text(), name)

    def test_defect_evidence_contract_spans_author_and_critic(self):
        """The fields the Tech Lead writes are the ones the critic blocks on (issue #46)."""
        spec=(ROOT/'prompts/tech-lead.md').read_text()
        critic=(ROOT/'prompts/critic.md').read_text()
        for field in ('**Symptom:**','**Mechanism:**','**Evidence:**'):
            self.assertIn(field, spec, field)
            self.assertIn(field.strip('*:'), critic, field)
        self.assertIn('## Defect evidence check', critic)
        check=critic.split('## Defect evidence check',1)[1].split('\n## ',1)[0]
        self.assertIn('[BLOCKING-PLAN]', check)
        self.assertIn('reasoning from source', check)
        # the finding must use a tag the result block counts, or it never blocks the build
        self.assertIn('blocking_plan: <number of [BLOCKING-PLAN] findings>', critic)

    def test_architect_separates_spec_body_from_registration_metadata(self):
        reply='long build spec\n<!-- ARCHITECT_RESULT_START -->\n```yaml\nselected_tier: standard\n```\n'
        spec, meta=self.module.architect_result(reply)
        self.assertEqual(spec,'long build spec\n')
        self.assertNotIn('long build spec',meta)
