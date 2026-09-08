#!/usr/bin/env python3
"""Integration tests for the additive Claude/Codex orchestrator harness."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INSTALLER = SCRIPTS / "install-orchestrators.py"
HOOK = SCRIPTS / "orchestrator-hook.py"
DOCTOR = SCRIPTS / "orchestrator-doctor.py"


def run(*args, input=None, env=None):
    return subprocess.run(
        [sys.executable, *map(str, args)],
        input=input,
        text=True,
        capture_output=True,
        env=env,
    )


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.pm = self.base / "sample-pm"
        self.framework = self.pm / "framework"
        self.pm.mkdir()
        self.framework.mkdir()
        (self.framework / "ORCHESTRATOR.md").write_text("# Contract\n")
        scripts = self.framework / "scripts"
        scripts.mkdir()
        for name in (
            "orchestrator-hook.py", "orchestrator-state.py", "plan-update.py", "pm_state.py",
            "plan-lint.sh", "log-partner-burn.py", "partner_telemetry.py", "append-cost.py",
            "orchestrator-routing.py", "dispatch-role.py",
        ):
            (scripts / name).write_text("# fixture\n")
        (self.framework / "orchestrate.py").write_text("# fixture\n")

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        return run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)

    def test_additive_install_preserves_customizations_and_is_byte_idempotent(self):
        (self.pm / ".claude").mkdir()
        (self.pm / ".codex").mkdir()
        claude = {
            "permissions": {"allow": ["Read(*)"]},
            "hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "custom-pre"}]}]},
            "custom": {"theme": "violet"},
        }
        codex = {
            "hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": "custom-stop"},
                {"type": "command", "command": "python3 /custom/orchestrator-hook.py --audit"},
            ]}]},
            "custom": [1, 2, 3],
        }
        (self.pm / ".claude/settings.json").write_text(json.dumps(claude, indent=2) + "\n")
        (self.pm / ".codex/hooks.json").write_text(json.dumps(codex, indent=2) + "\n")
        os.chmod(self.pm / ".claude/settings.json", 0o640)
        (self.pm / "CLAUDE.md").write_text("# Local Claude notes\n\nKeep this exactly.\n")
        (self.pm / "AGENTS.md").write_text("# Local Codex notes\n")

        first = self.install()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        installed = {}
        for rel in (".claude/settings.json", ".codex/hooks.json", "CLAUDE.md", "AGENTS.md", ".gitignore", ".orchestrator/config.json"):
            installed[rel] = (self.pm / rel).read_bytes()

        got_claude = json.loads(installed[".claude/settings.json"])
        got_codex = json.loads(installed[".codex/hooks.json"])
        self.assertEqual(got_claude["permissions"], claude["permissions"])
        self.assertEqual(got_claude["custom"], claude["custom"])
        self.assertEqual(got_claude["hooks"]["PreToolUse"][0], claude["hooks"]["PreToolUse"][0])
        self.assertEqual(got_codex["custom"], codex["custom"])
        self.assertEqual(got_codex["hooks"]["Stop"][0], codex["hooks"]["Stop"][0])
        self.assertEqual((self.pm / ".claude/settings.json").stat().st_mode & 0o777, 0o640)
        for provider, config in (("claude", got_claude), ("codex", got_codex)):
            commands = [
                hook["command"]
                for groups in config["hooks"].values()
                for group in groups
                for hook in group["hooks"]
                if "orchestrator-hook.py" in hook.get("command", "")
                and f"--provider {provider}" in hook.get("command", "")
            ]
            self.assertEqual(len(commands), 3)
            self.assertTrue(all(f"--provider {provider}" in command for command in commands))
        self.assertIn("framework/ORCHESTRATOR.md", (self.pm / "CLAUDE.md").read_text())
        self.assertIn("framework/ORCHESTRATOR.md", (self.pm / "AGENTS.md").read_text())
        self.assertIn("framework/scripts/plan-update.py", (self.pm / "CLAUDE.md").read_text())
        self.assertIn("Keep this exactly.\n\n<!-- BEGIN", (self.pm / "CLAUDE.md").read_text())
        self.assertIn(".orchestrator/", (self.pm / ".gitignore").read_text())
        marker = json.loads(installed[".orchestrator/config.json"])
        self.assertEqual(marker["schema_version"], 1)
        self.assertEqual(marker["pm_dir"], str(self.pm.resolve()))
        self.assertEqual(marker["framework_dir"], str(self.framework.resolve()))

        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        for rel, content in installed.items():
            self.assertEqual((self.pm / rel).read_bytes(), content, rel)

    def test_invalid_existing_json_preflight_writes_nothing(self):
        (self.pm / ".claude").mkdir()
        (self.pm / ".codex").mkdir()
        original = b'{"custom": true}\n'
        invalid = b'{"hooks": '
        (self.pm / ".claude/settings.json").write_bytes(original)
        (self.pm / ".codex/hooks.json").write_bytes(invalid)

        result = self.install()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.pm / ".claude/settings.json").read_bytes(), original)
        self.assertEqual((self.pm / ".codex/hooks.json").read_bytes(), invalid)
        self.assertFalse((self.pm / "CLAUDE.md").exists())
        self.assertFalse((self.pm / "AGENTS.md").exists())
        self.assertFalse((self.pm / ".orchestrator").exists())

    def test_missing_runtime_dependency_writes_nothing(self):
        (self.framework / "scripts/partner_telemetry.py").unlink()

        result = self.install()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.pm / ".claude").exists())
        self.assertFalse((self.pm / ".codex").exists())
        self.assertFalse((self.pm / ".orchestrator").exists())

    def test_incomplete_managed_document_markers_fail_preflight(self):
        original = b"Local notes\n<!-- BEGIN VIBETASTIC ORCHESTRATOR HARNESS -->\nbroken\n"
        (self.pm / "CLAUDE.md").write_bytes(original)

        result = self.install()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.pm / "CLAUDE.md").read_bytes(), original)
        self.assertFalse((self.pm / ".claude").exists())
        self.assertFalse((self.pm / ".orchestrator").exists())

    def test_active_lease_allows_exact_noop_but_refuses_install_changes(self):
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        lease = {"token": "held", "provider": "codex", "session": "active"}
        (self.pm / ".orchestrator/lease.json").write_text(json.dumps(lease))

        noop = self.install()
        self.assertEqual(noop.returncode, 0, noop.stderr)
        path = self.pm / ".claude/settings.json"
        changed = json.loads(path.read_text())
        changed["user_customization"] = True
        path.write_text(json.dumps(changed))
        before = path.read_bytes()

        refused = self.install()

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("active lease", refused.stderr)
        self.assertEqual(path.read_bytes(), before)

        (self.pm / ".orchestrator/lease.json").write_text("null\n")
        released = self.install()
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertTrue(json.loads(path.read_text())["user_customization"])


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.pm = self.base / "project-pm"
        self.framework = self.base / "framework"
        (self.pm / ".orchestrator").mkdir(parents=True)
        (self.pm / ".claude").mkdir()
        (self.pm / ".codex").mkdir()
        (self.pm / ".claude/settings.json").write_text("{}\n")
        (self.pm / ".codex/hooks.json").write_text("{}\n")
        (self.pm / "logs").mkdir()
        (self.framework / "scripts").mkdir(parents=True)
        shutil.copy2(HOOK, self.framework / "scripts/orchestrator-hook.py")
        shutil.copy2(SCRIPTS / "plan-lint.sh", self.framework / "scripts/plan-lint.sh")
        (self.pm / ".orchestrator/config.json").write_text(json.dumps({
            "schema_version": 1,
            "enabled": True,
            "managed_by": "vibetastic-pm",
            "pm_dir": str(self.pm),
            "framework_dir": str(self.framework),
            "providers": ["claude", "codex"],
        }))
        state = textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            def value(flag): return args[args.index(flag) + 1]
            if '--session' in args or value('--token') != 'right-token' or value('--provider') != 'codex':
                print('ownership denied', file=sys.stderr)
                raise SystemExit(31)
            print(json.dumps({'provider':'codex','session':'launcher-session','token':'right-token'}))
        """)
        (self.framework / "scripts/orchestrator-state.py").write_text(state)
        telemetry = textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, pathlib, sys
            provider = sys.argv[sys.argv.index('--provider') + 1]
            payload = json.load(sys.stdin)
            pathlib.Path(payload['cwd'], 'telemetry-call.json').write_text(json.dumps({'provider': provider, 'payload': payload}))
        """)
        (self.framework / "scripts/log-partner-burn.py").write_text(telemetry)

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, provider, payload, token="right-token", selftest=True):
        env = os.environ.copy()
        env.update({
            "PM_ORCHESTRATOR_TOKEN": token,
            "PM_ORCHESTRATOR_PROVIDER": provider,
            "PM_ORCHESTRATOR_SESSION": "session-7",
        })
        if selftest:
            env["ORCHESTRATOR_HOOK_SELFTEST"] = "1"
        return run(
            self.framework / "scripts/orchestrator-hook.py",
            "--provider", provider,
            "--pm-dir", self.pm,
            input=json.dumps(payload),
            env=env,
        )

    def payload(self, event, tool="Edit", tool_input=None):
        return {
            "session_id": "native-session-can-differ",
            "cwd": str(self.pm),
            "transcript_path": str(self.pm / "transcript.jsonl"),
            "model": "gpt-test",
            "hook_event_name": event,
            "tool_name": tool,
            "tool_input": tool_input if tool_input is not None else {},
        }

    def test_uninstalled_project_is_ignored(self):
        (self.pm / ".orchestrator/config.json").unlink()
        result = self.invoke("codex", self.payload("PreToolUse", tool_input={"file_path": str(self.pm / "PLAN.md")}), token="wrong")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.pm / "logs/orchestrator-hooks.jsonl").exists())

    def test_present_invalid_installation_config_blocks(self):
        marker = self.pm / ".orchestrator/config.json"
        valid_marker = marker.read_text()
        for invalid in ("{", json.dumps({"enabled": True, "schema_version": 1})):
            marker.write_text(invalid)
            result = self.invoke("codex", self.payload("PreToolUse"))
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid installation config", result.stderr)
        marker.write_text(valid_marker)
        (self.pm / ".codex/hooks.json").write_text("{")
        invalid_provider = self.invoke("codex", self.payload("PreToolUse"))
        self.assertEqual(invalid_provider.returncode, 2)
        self.assertIn("invalid installation config", invalid_provider.stderr)

    def test_unknown_event_is_not_recorded_as_runtime_evidence(self):
        result = self.invoke("codex", self.payload("FixtureOnly"), selftest=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.pm / "logs/orchestrator-hooks.jsonl").exists())

    def test_pretool_requires_matching_lease_and_rejects_direct_plan_apply_patch(self):
        denied = self.invoke("codex", self.payload("PreToolUse", tool="Edit", tool_input={"file_path": str(self.pm / "HANDOFF.md")}), token="wrong")
        self.assertEqual(denied.returncode, 2)
        self.assertIn("ownership denied", denied.stderr)

        patch = "*** Begin Patch\n*** Update File: PLAN.md\n@@\n-old\n+new\n*** End Patch\n"
        direct = self.invoke("codex", self.payload("PreToolUse", tool="apply_patch", tool_input=patch))
        self.assertEqual(direct.returncode, 2)
        self.assertIn("plan-update.py", direct.stderr)
        move = "*** Begin Patch\n*** Update File: NOTES.md\n*** Move to: PLAN.md\n*** End Patch\n"
        moved = self.invoke("codex", self.payload("PreToolUse", tool="apply_patch", tool_input={"command": move}))
        self.assertEqual(moved.returncode, 2)
        self.assertIn("plan-update.py", moved.stderr)
        records = [json.loads(line) for line in (self.pm / "logs/orchestrator-hooks.jsonl").read_text().splitlines()]
        self.assertEqual([record["evidence"] for record in records], ["selftest", "selftest", "selftest"])
        self.assertEqual(records[-1]["transcript_path"], str(self.pm / "transcript.jsonl"))
        self.assertEqual(records[-1]["hook_event_name"], "PreToolUse")

    def test_posttool_lints_existing_plan_for_claude_and_string_codex_payloads(self):
        (self.pm / "PLAN.md").write_text("not frontmatter\n")
        claude = self.invoke("claude", self.payload("PostToolUse", tool="Write", tool_input={"file_path": "notes.md"}))
        self.assertEqual(claude.returncode, 2)
        self.assertIn("structural lint", claude.stderr)

        codex = self.invoke("codex", self.payload("PostToolUse", tool="apply_patch", tool_input="*** Begin Patch\n*** End Patch"))
        self.assertEqual(codex.returncode, 2)
        self.assertIn("structural lint", codex.stderr)

    def test_posttool_blocks_when_linter_cannot_validate_plan(self):
        (self.pm / "PLAN.md").write_text("anything\n")
        linter = self.framework / "scripts/plan-lint.sh"
        linter.write_text("#!/bin/bash\necho adapter-failed >&2\nexit 7\n")
        unknown = self.invoke("codex", self.payload("PostToolUse", tool="Bash"))
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("could not validate", unknown.stderr)
        self.assertIn("adapter-failed", unknown.stderr)

        linter.unlink()
        missing = self.invoke("codex", self.payload("PostToolUse", tool="Bash"))
        self.assertEqual(missing.returncode, 2)
        self.assertIn("could not validate", missing.stderr)

    def test_stop_forwards_provider_and_payload_to_telemetry(self):
        payload = self.payload("Stop", tool="", tool_input={})
        result = self.invoke("codex", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads((self.pm / "telemetry-call.json").read_text())
        self.assertEqual(call, {"provider": "codex", "payload": payload})


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.pm = base / "doctor-pm"
        self.framework = base / "framework"
        self.pm.mkdir()
        (self.framework / "scripts").mkdir(parents=True)
        (self.framework / "ORCHESTRATOR.md").write_text("# Fixture contract\n")
        shutil.copy2(HOOK, self.framework / "scripts/orchestrator-hook.py")
        shutil.copy2(SCRIPTS / "plan-lint.sh", self.framework / "scripts/plan-lint.sh")
        for name in (
            "orchestrator-state.py", "plan-update.py", "pm_state.py",
            "log-partner-burn.py", "partner_telemetry.py", "append-cost.py",
            "orchestrator-routing.py", "dispatch-role.py",
        ):
            (self.framework / "scripts" / name).write_text("# fixture\n")
        (self.framework / "orchestrate.py").write_text("# fixture\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_doctor_separates_adapter_selftest_from_runtime_evidence(self):
        install = run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(install.returncode, 0, install.stderr)

        (self.pm / "logs").mkdir()
        (self.pm / "logs/orchestrator-hooks.jsonl").write_text(json.dumps({
            "provider": "codex", "evidence": "runtime", "hook_event_name": "PostToolUse"
        }) + "\n")
        first = run(DOCTOR, "--pm-dir", self.pm, "--framework-dir", self.framework, "--json")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        report = json.loads(first.stdout)
        self.assertTrue(report["configuration"]["ok"])
        self.assertTrue(report["adapter_selftest"]["ok"])
        self.assertEqual(report["runtime_evidence"], {"claude": False, "codex": False})
        self.assertNotIn("trusted", first.stdout.lower())

        payload = {
            "session_id": "real-session",
            "cwd": str(self.pm),
            "model": "gpt-real",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
        }
        shutil.copy2(ROOT / "tests/fixtures/plan-good.md", self.pm / "PLAN.md")
        actual = run(self.framework / "scripts/orchestrator-hook.py", "--provider", "codex", "--pm-dir", self.pm, input=json.dumps(payload))
        self.assertEqual(actual.returncode, 0, actual.stderr)
        second = run(DOCTOR, "--pm-dir", self.pm, "--framework-dir", self.framework, "--json")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["runtime_evidence"], {"claude": False, "codex": True})

        config_path = self.pm / ".codex/hooks.json"
        config = json.loads(config_path.read_text())
        config["custom_after_evidence"] = True
        config_path.write_text(json.dumps(config))
        stale = run(DOCTOR, "--pm-dir", self.pm, "--framework-dir", self.framework, "--json")
        self.assertEqual(stale.returncode, 0, stale.stderr)
        self.assertEqual(json.loads(stale.stdout)["runtime_evidence"], {"claude": False, "codex": False})

        records = [json.loads(line) for line in (self.pm / "logs/orchestrator-hooks.jsonl").read_text().splitlines()]
        actual_record = records[-1]
        self.assertEqual(actual_record["outcome"], "passed")
        self.assertEqual(actual_record["exit_code"], 0)
        self.assertEqual(actual_record["config_schema_version"], 1)
        expected_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
        self.assertNotEqual(actual_record["config_sha256"], expected_hash)

    def test_doctor_rejects_a_managed_hook_with_the_wrong_matcher(self):
        install = run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(install.returncode, 0, install.stderr)
        path = self.pm / ".codex/hooks.json"
        config = json.loads(path.read_text())
        config["hooks"]["PreToolUse"][-1]["matcher"] = "Read"
        path.write_text(json.dumps(config))

        result = run(DOCTOR, "--pm-dir", self.pm, "--framework-dir", self.framework, "--json")

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["configuration"]["ok"])
        self.assertTrue(any("matcher" in error for error in report["configuration"]["errors"]))

    def test_doctor_ignores_similarly_named_custom_hook(self):
        install = run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(install.returncode, 0, install.stderr)
        path = self.pm / ".codex/hooks.json"
        config = json.loads(path.read_text())
        config["hooks"]["Stop"].insert(0, {
            "hooks": [{"type": "command", "command": "python3 /custom/orchestrator-hook.py --audit"}]
        })
        path.write_text(json.dumps(config))

        result = run(DOCTOR, "--pm-dir", self.pm, "--framework-dir", self.framework, "--json")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["configuration"]["ok"])


class RealStateHookTests(unittest.TestCase):
    def test_native_session_may_differ_from_launcher_lease_session(self):
        with tempfile.TemporaryDirectory() as raw:
            pm = Path(raw)
            (pm / ".orchestrator").mkdir()
            (pm / ".orchestrator/config.json").write_text(json.dumps({
                "schema_version": 1,
                "enabled": True,
                "managed_by": "vibetastic-pm",
                "pm_dir": str(pm),
                "framework_dir": str(ROOT),
                "providers": ["claude", "codex"],
            }))
            (pm / ".codex").mkdir()
            (pm / ".codex/hooks.json").write_text("{}\n")
            acquired = run(
                SCRIPTS / "orchestrator-state.py", "--pm-dir", pm,
                "acquire", "--provider", "codex", "--session", "launcher-session",
                "--pid", os.getpid(),
            )
            if acquired.returncode == 31 and "Operation not permitted" in acquired.stderr:
                self.skipTest("sandbox does not permit the state layer's process identity check")
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            token = json.loads(acquired.stdout)["token"]
            env = os.environ.copy()
            env.update({
                "PM_ORCHESTRATOR_TOKEN": token,
                "PM_ORCHESTRATOR_PROVIDER": "codex",
                "PM_ORCHESTRATOR_SESSION": "launcher-session",
                "ORCHESTRATOR_HOOK_SELFTEST": "1",
            })
            payload = {
                "session_id": "native-provider-session",
                "cwd": str(pm),
                "model": "gpt-test",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "true"},
            }
            checked = run(HOOK, "--provider", "codex", "--pm-dir", pm, input=json.dumps(payload), env=env)
            self.assertEqual(checked.returncode, 0, checked.stderr)

            env["PM_ORCHESTRATOR_PROVIDER"] = "claude"
            mismatch = run(HOOK, "--provider", "codex", "--pm-dir", pm, input=json.dumps(payload), env=env)
            self.assertEqual(mismatch.returncode, 2)


if __name__ == "__main__":
    unittest.main()
