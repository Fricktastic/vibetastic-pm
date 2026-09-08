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
        reply='commentary should stay on disk\n<!-- TECH_LEAD_RESULT_START -->\n```yaml\nspec_path: "/tmp/staged.md"\ntask_title: "A task"\nsuggested_tier: standard\nsecurity: false\n```\n<!-- TECH_LEAD_RESULT_END -->\n'
        result=self.module.metadata(reply,'TECH_LEAD','/tmp/staged.md','/pm/prompts/task-T001.md')
        self.assertIn('/pm/prompts/task-T001.md',result)
        self.assertNotIn('commentary',result)
        self.assertNotIn('/tmp/staged.md',result)

    def test_rejects_missing_or_duplicate_metadata_delimiter(self):
        for reply in ['full spec body', '<!-- TECH_LEAD_RESULT_START -->'*2]:
            with self.assertRaises(ValueError):
                self.module.metadata(reply,'TECH_LEAD','/tmp/spec.md','/pm/prompts/task-T001.md')

    def test_rejects_metadata_pointing_at_an_unregistered_artifact(self):
        reply='<!-- TECH_LEAD_RESULT_START -->\n```yaml\nspec_path: "/wrong.md"\ntask_title: x\nsuggested_tier: fast\nsecurity: false\n```\n<!-- TECH_LEAD_RESULT_END -->'
        with self.assertRaises(ValueError):
            self.module.metadata(reply,'TECH_LEAD','/tmp/staged.md','/pm/prompts/task-T001.md')

    def test_architect_separates_spec_body_from_registration_metadata(self):
        reply='long build spec\n<!-- ARCHITECT_RESULT_START -->\n```yaml\nselected_tier: standard\n```\n'
        spec, meta=self.module.architect_result(reply)
        self.assertEqual(spec,'long build spec\n')
        self.assertNotIn('long build spec',meta)
