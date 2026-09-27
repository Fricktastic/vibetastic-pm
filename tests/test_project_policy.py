#!/usr/bin/env python3
"""Project-owned review policy in PROJECT.md: parsing, validation, doctor, installer (#50)."""

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import project_policy  # noqa: E402

POLICY = SCRIPTS / "project_policy.py"
DOCTOR = SCRIPTS / "orchestrator-doctor.py"


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pm = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_project(self, frontmatter="", body=""):
        (self.pm / "PROJECT.md").write_text(f"---\nproject: x\n{frontmatter}---\n\n{body}")

    def test_missing_project_uses_generic_defaults(self):
        policy = project_policy.load(self.pm)
        self.assertEqual((policy["critic_round_cap"], policy["reviewer_fixup_round_cap"]), (2, 3))
        self.assertEqual(policy["errors"], [])
        self.assertEqual(set(policy["verify_tiers"]), {"R0", "R1", "R2"})
        # Framework defaults describe evidence kinds, never a platform.
        text = " ".join(policy["verify_tiers"].values()).lower()
        for word in ("ios", "simulator", "xcode", "view", "screenshot"):
            self.assertNotIn(word, text)

    def test_project_declares_caps_tiers_and_triggers(self):
        self.write_project(
            "critic_round_cap: 1\nreviewer_fixup_round_cap: 5   # busy project\n",
            "## Verify tiers\n\n- R0: unit tests\n- R1: fixture through the decoder\n"
            "  continued on a second line\n- R2: device check\n\n"
            "## Risk triggers\n\n- audio ownership\n- shared playback state\n\n## Notes\n- not policy\n")
        policy = project_policy.load(self.pm)
        self.assertEqual(policy["errors"], [])
        self.assertEqual((policy["critic_round_cap"], policy["reviewer_fixup_round_cap"]), (1, 5))
        self.assertEqual(policy["verify_tiers"]["R1"], "fixture through the decoder continued on a second line")
        self.assertEqual(policy["risk_triggers"], ["audio ownership", "shared playback state"])
        rendered = project_policy.render(policy)
        self.assertIn("audio ownership", rendered)
        self.assertNotIn("framework default", rendered)
        self.assertNotIn("not policy", rendered)

    def test_malformed_declarations_are_errors_and_fall_back(self):
        self.write_project(
            "critic_round_cap: two\nreviewer_fixup_round_cap: 0\n",
            "## Verify tiers\n- R0: a\n- R3: b\n\n## Risk triggers\n\n## Risk triggers\n- x\n")
        policy = project_policy.load(self.pm)
        joined = "\n".join(policy["errors"])
        self.assertIn("critic_round_cap", joined)
        self.assertIn("reviewer_fixup_round_cap", joined)
        self.assertIn("unknown verify tier R3", joined)
        self.assertIn("missing R1, R2", joined)
        self.assertIn("2 times", joined)
        self.assertEqual((policy["critic_round_cap"], policy["reviewer_fixup_round_cap"]), (2, 3))
        self.assertEqual(policy["verify_tiers"], project_policy.DEFAULT_VERIFY_TIERS)

    def test_commented_example_sections_are_not_policy(self):
        self.write_project(body="<!--\n## Risk triggers\n- example only\n-->\n")
        policy = project_policy.load(self.pm)
        self.assertEqual(policy["sources"]["risk_triggers"], "default")
        self.assertEqual(policy["errors"], [])

    def test_validate_cli_exit_codes(self):
        self.write_project("critic_round_cap: -1\n")
        bad = subprocess.run([sys.executable, POLICY, "--pm-dir", self.pm, "validate"],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.write_project("critic_round_cap: 3\n")
        good = subprocess.run([sys.executable, POLICY, "--pm-dir", self.pm, "validate"],
                              capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)

    def test_example_ios_policy_is_valid_when_copied_into_a_project(self):
        example = (ROOT / "Docs/examples/policy-ios.md").read_text()
        (self.pm / "PROJECT.md").write_text(example)
        policy = project_policy.load(self.pm)
        self.assertEqual(policy["errors"], [])
        self.assertEqual(policy["sources"]["verify_tiers"], "project")
        self.assertEqual(policy["sources"]["risk_triggers"], "project")

    def test_doctor_reports_invalid_policy(self):
        self.write_project("reviewer_fixup_round_cap: lots\n")
        report = subprocess.run([sys.executable, DOCTOR, "--pm-dir", self.pm, "--framework-dir",
                                 ROOT, "--json"], capture_output=True, text=True)
        data = json.loads(report.stdout)
        self.assertFalse(data["policy"]["ok"])
        self.assertFalse(data["ok"])
        self.assertIn("reviewer_fixup_round_cap", " ".join(data["policy"]["errors"]))
        self.write_project("reviewer_fixup_round_cap: 4\n")
        data = json.loads(subprocess.run([sys.executable, DOCTOR, "--pm-dir", self.pm, "--framework-dir",
                                          ROOT, "--json"], capture_output=True, text=True).stdout)
        self.assertTrue(data["policy"]["ok"])
        self.assertEqual(data["policy"]["reviewer_fixup_round_cap"], 4)

    def test_installer_never_touches_project_policy(self):
        framework = self.pm / "framework"
        (framework / "scripts").mkdir(parents=True)
        (framework / "ORCHESTRATOR.md").write_text("# Contract\n")
        (framework / "orchestrate.py").write_text("# fixture\n")
        for name in ("orchestrator-hook.py", "orchestrator-state.py", "plan-update.py", "pm_state.py",
                     "plan-lint.sh", "log-partner-burn.py", "partner_telemetry.py", "append-cost.py",
                     "orchestrator-routing.py", "dispatch-role.py", "spec-body-guard.py",
                     "handoff-volatile-hook.py"):
            (framework / "scripts" / name).write_text("# fixture\n")
        self.write_project("critic_round_cap: 1\n", "## Risk triggers\n- audio ownership\n")
        before = (self.pm / "PROJECT.md").read_bytes()
        result = subprocess.run([sys.executable, SCRIPTS / "install-orchestrators.py", "--pm-dir",
                                 self.pm, "--framework-dir", framework], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.pm / "PROJECT.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
