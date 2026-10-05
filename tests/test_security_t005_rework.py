"""Opus-prescribed fail-first regression tests for T005 security rework.

These tests cover the four remedies adjudicated as binding amendments to the
T005 security review. They start red against commit 275c7a8 and turn green
after the four prescribed fixes are applied to
``scripts/orchestrator-doctor.py`` and ``scripts/install-orchestrators.py``.

Remedies (one test each):

1. ``test_doctor_echoes_no_snapshot_or_event_values`` — content-free doctor
   diagnostics with allowlisted warning codes. A tampered snapshot carrying a
   secret in ``contract``, ``type``, or in a warning message must NEVER appear
   in text output or in the JSON report's diagnostics.
2. ``test_doctor_fail_closed_when_view_contract_import_fails`` — fail-closed
   missing ``view_contract`` import plus forbidden-key scan on every dict row.
   Even when the module is unavailable, dict rows that carry a forbidden key
   (``token``) must trip ``status: error``.
3. ``test_installer_reason_omits_non_oserror_exception_text`` — non-OSError
   installer reason is the exception class name only; OSError is class plus
   short ``strerror``. A sentinel string carried by a non-OSError must be
   absent from the marker and stdout.
4. ``test_installer_noop_reports_skipped_with_exporter_pointer`` — truthful
   skipped/no-op stdout. When no installer writes are required, stdout must
   print the skipped line and a pointer to ``export-view-contract.py``; the
   old ``view contract: unavailable`` failure line must not appear.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INSTALLER = SCRIPTS / "install-orchestrators.py"
DOCTOR = SCRIPTS / "orchestrator-doctor.py"


def _run(*args, input=None, env=None):
    return subprocess.run(
        [sys.executable, *map(str, args)],
        input=input,
        text=True,
        capture_output=True,
        env=env,
    )


def _make_pm():
    import shutil
    temp = tempfile.TemporaryDirectory()
    base = Path(temp.name)
    pm = base / "sample-pm"
    framework = pm / "framework"
    pm.mkdir()
    framework.mkdir()
    (framework / "ORCHESTRATOR.md").write_text("# Contract\n")
    scripts = framework / "scripts"
    scripts.mkdir()
    # Real hook + linter so the doctor selftest can actually run; the other
    # scripts can be stubs because the selftest only invokes orchestrator-hook.py
    # and the embedded plan-lint.sh shell path.
    shutil.copy2(SCRIPTS / "orchestrator-hook.py", scripts / "orchestrator-hook.py")
    shutil.copy2(SCRIPTS / "plan-lint.sh", scripts / "plan-lint.sh")
    (scripts / "plan-lint.sh").chmod(0o755)
    for name in (
        "orchestrator-state.py", "plan-update.py",
        "pm_state.py", "log-partner-burn.py",
        "partner_telemetry.py", "append-cost.py", "orchestrator-routing.py",
        "dispatch-role.py", "spec-body-guard.py",
        "view_contract.py", "export-view-contract.py",
    ):
        (scripts / name).write_text("# fixture\n")
    (framework / "orchestrate.py").write_text("# fixture\n")
    return temp, pm, framework


class SecretShapeTestMixin:
    """Helpers shared by secret-echo tests."""

    SECRET_CONTRACT = "<SECRET-CONTRACT-VALUE-XYZ>"
    SECRET_TYPE = "<SECRET-TYPE-VALUE-XYZ>"
    SECRET_WARNING = "<SECRET-WARNING-MESSAGE-XYZ>"

    def _secret_snapshot(self):
        return {
            "contract": self.SECRET_CONTRACT,
            "schema_version": 1,
            "generation": 1,
            "warnings": [
                {"code": "events_jsonl_empty", "message": self.SECRET_WARNING},
            ],
            "project": {"id": "fixture", "display_name": "fixture"},
        }


class DoctorNoSecretEchoTests(SecretShapeTestMixin, unittest.TestCase):
    """Remedy 1 — content-free doctor diagnostics with allowlisted warning codes."""

    def setUp(self):
        self.temp, self.pm, self.framework = _make_pm()
        install = _run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(install.returncode, 0, install.stderr)
        snapshot = self.pm / ".orchestrator/view/v1/snapshot.json"
        snapshot.write_text(json.dumps(self._secret_snapshot(), sort_keys=True))

    def tearDown(self):
        self.temp.cleanup()

    def _assert_secret_absent(self, surface, name):
        for token in (self.SECRET_CONTRACT, self.SECRET_TYPE, self.SECRET_WARNING):
            self.assertNotIn(token, surface, f"{name} must not echo {token!r}")

    def test_doctor_echoes_no_snapshot_or_event_values(self):
        # Force a fresh snapshot under our control, then read text + json outputs.
        result = _run(DOCTOR, "--pm-dir", self.pm, "--framework-dir", self.framework, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._assert_secret_absent(result.stdout, "doctor --json stdout")
        self._assert_secret_absent(result.stderr, "doctor --json stderr")

        text = _run(DOCTOR, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(text.returncode, 0, text.stdout + text.stderr)
        self._assert_secret_absent(text.stdout, "doctor text stdout")
        self._assert_secret_absent(text.stderr, "doctor text stderr")

        # The JSON report's check section must also be free of the secrets.
        report = json.loads(result.stdout)
        view = report["checks"]["view_contract_v1"]
        rendered = json.dumps(view, sort_keys=True)
        self._assert_secret_absent(rendered, "doctor JSON checks dict")

        # And warnings reported from inside the snapshot must be allowlisted.
        codes = [w.get("code") for w in view.get("warnings", [])]
        pattern = re.compile(r"^[a-z0-9_]{1,64}$")
        for code in codes:
            self.assertIsNotNone(
                pattern.match(code or ""),
                f"warning code {code!r} is not allowlisted",
            )


class DoctorImportFailClosedTests(unittest.TestCase):
    """Remedy 2 — fail-closed missing view_contract import + scan every dict row."""

    def setUp(self):
        self.temp, self.pm, self.framework = _make_pm()
        install = _run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(install.returncode, 0, install.stderr)
        snapshot = self.pm / ".orchestrator/view/v1/snapshot.json"
        # Sanity: install with the real view_contract must have written a snapshot.
        self.assertTrue(snapshot.is_file(), "fixture install should have written a snapshot")

    def tearDown(self):
        self.temp.cleanup()

    def test_doctor_fail_closed_when_view_contract_import_fails(self):
        # Importing the doctor as a module lets us inspect its event-check
        # function in-process, where we can force the view_contract import to
        # fail by hiding it from sys.modules. The subprocess boundary makes
        # this impossible to control via PYTHONPATH alone because the doctor's
        # sys.path[0] (its own scripts dir) always resolves the real module
        # first; the library path is the only honest way to exercise the
        # import-failure branch end-to-end.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "orchestrator_doctor", str(SCRIPTS / "orchestrator-doctor.py")
        )
        doctor_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(doctor_mod)

        # Hide view_contract so the doctor's ``_import_view_contract`` returns None.
        saved = sys.modules.pop("view_contract", None)
        # Force the doctor's script-dir lookup to also miss view_contract.
        saved_path_first = None
        try:
            sys.modules["view_contract"] = None  # causes import to raise ImportError
            events_path = self.pm / ".orchestrator/view/v1/events.jsonl"
            events_path.parent.mkdir(parents=True, exist_ok=True)
            forbidden_row = json.dumps({
                "event_id": "cmd:lease_acquired:abc:def2:0",
                "type": "lease_acquired",
                "operation": "acquire",
                "provider": "codex",
                "session": "test",
                "profile": "normal",
                "token": "<SENTINEL-TOKEN-VALUE>",
            }, sort_keys=True, separators=(",", ":"))
            events_path.write_text(forbidden_row + "\n")

            view = doctor_mod.check_view_contract_v1(self.pm, self.framework)
            self.assertEqual(
                view["status"], "error",
                f"expected status=error when view_contract import fails with a "
                f"forbidden row, got status={view['status']!r} errors={view.get('errors')!r}",
            )
            joined = "\n".join(view.get("errors", []))
            self.assertIn("token", joined, f"expected forbidden key 'token' error, got {joined!r}")
            self.assertNotIn("<SENTINEL-TOKEN-VALUE>", joined, "forbidden row value must not be echoed")
        finally:
            sys.modules.pop("view_contract", None)
            if saved is not None:
                sys.modules["view_contract"] = saved


class InstallerReasonContentFreeTests(unittest.TestCase):
    """Remedy 3 — non-OSError installer reason is exception class only."""

    def setUp(self):
        self.temp, self.pm, self.framework = _make_pm()

    def tearDown(self):
        self.temp.cleanup()

    def test_installer_reason_omits_non_oserror_exception_text(self):
        # First, install a clean baseline so a real snapshot exists.
        install = _run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(install.returncode, 0, install.stderr)

        # Force a non-OSError from build_snapshot by replacing logs/runs.jsonl
        # with a directory; that triggers UnrecoverableSourceRead (RuntimeError
        # subclass) carrying a sentinel string we will assert never appears.
        codex_path = self.pm / ".codex/hooks.json"
        config = json.loads(codex_path.read_text())
        config["user_customization"] = True
        codex_path.write_text(json.dumps(config))
        runs_path = self.pm / "logs/runs.jsonl"
        if runs_path.exists() or runs_path.is_symlink():
            runs_path.unlink()
        runs_path.mkdir(parents=True, exist_ok=True)

        second = _run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        marker = self.pm / ".orchestrator/view/install-projection-failed.json"
        self.assertTrue(marker.is_file(), "expected install-projection-failed marker")
        payload = json.loads(marker.read_text())
        # The marker may legitimately carry the class name; it must NOT carry
        # any inner exception message text. The sentinel-only UnrecoverableSourceRead
        # variant is the canonical non-OSError here.
        marker_text = marker.read_text()
        self.assertNotIn("UnrecoverableSourceRead: logs/runs.jsonl", marker_text,
                         "marker must not embed UnrecoverableSourceRead args")
        # Reason in stdout must be content-free too.
        self.assertNotIn("UnrecoverableSourceRead: logs/runs.jsonl", second.stdout,
                         "stdout must not embed UnrecoverableSourceRead args")
        self.assertNotIn("logs/runs.jsonl", second.stdout,
                         "stdout must not echo the failing path")
        # The class name alone is acceptable as a content-free reason.
        warning = payload.get("warning", {})
        message = warning.get("message", "")
        self.assertTrue(
            re.match(r"^[A-Za-z_][A-Za-z0-9_]*(:\s.+)?$", message),
            f"warning.message {message!r} must be class name only or class name + short strerror",
        )


class InstallerNoopTruthfulStdoutTests(unittest.TestCase):
    """Remedy 4 — truthful skipped/no-op stdout with exporter pointer."""

    def setUp(self):
        self.temp, self.pm, self.framework = _make_pm()

    def tearDown(self):
        self.temp.cleanup()

    def test_installer_noop_reports_skipped_with_exporter_pointer(self):
        first = _run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(first.returncode, 0, first.stderr)
        # No-op rerun: no writes required.
        second = _run(INSTALLER, "--pm-dir", self.pm, "--framework-dir", self.framework)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertNotIn(
            "view contract: unavailable", second.stdout,
            "no-op rerun must not print the unavailable failure line",
        )
        self.assertNotIn(
            "view_projection_dirty", second.stdout,
            "no-op rerun must not cite a marker that was never written",
        )
        # The skipped outcome must mention the exporter so operators know how
        # to recover a missing snapshot.
        self.assertIn("export-view-contract", second.stdout,
                      "no-op rerun must point at the exporter")


if __name__ == "__main__":
    unittest.main()
