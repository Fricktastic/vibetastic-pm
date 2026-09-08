import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "cost-report.sh"


class CostReportTests(unittest.TestCase):
    def run_report(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            logs = base / "logs"
            logs.mkdir()
            (logs / "cost.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )
            models = base / "MODELS.md"
            models.write_text("# Models\n")
            task_log = base / "TASK_LOG.md"
            task_log.write_text("# Task log\n")
            return subprocess.run(
                ["bash", str(REPORT), str(logs), str(models), str(task_log)],
                text=True,
                capture_output=True,
                check=False,
            )

    def test_same_provider_model_keeps_builder_partner_and_subagent_roles_separate(self):
        common = {
            "ts": "2026-09-08T12:00:00Z",
            "backend": "codex",
            "model": "gpt-6-codex",
            "primary_model": "gpt-6-codex",
            "attempts": 1,
            "verify_passed": True,
            "cache_read_tokens": 0,
        }
        rows = [
            dict(common, role="builder", input_tokens=10, output_tokens=5,
                 reasoning_tokens=2),
            dict(common, role="partner", input_tokens=90, output_tokens=35,
                 reasoning_tokens=12, quota_proxy_tokens=125),
            dict(common, role="subagent", input_tokens=7, output_tokens=3,
                 reasoning_tokens=1, quota_proxy_tokens=10),
        ]

        result = self.run_report(rows)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "codex: gpt-6-codex [partner]: sessions=1  in=90  out=35",
            result.stdout,
        )
        self.assertIn(
            "codex: gpt-6-codex [subagent]: sessions=1  in=7  out=3",
            result.stdout,
        )
        self.assertIn("codex: gpt-6-codex [builder]", result.stdout)
        self.assertIn("runs=1  verify-fails=0", result.stdout)
        self.assertIn("(subscription — see weekly burn)", result.stdout)
        self.assertIn("dispatches=1", result.stdout)

    def test_weekly_quota_prefers_explicit_proxy_and_falls_back_for_history(self):
        rows = [
            {
                "ts": "2026-09-08T12:00:00Z",
                "role": "partner",
                "backend": "codex",
                "model": "gpt-6-codex",
                "input_tokens": 90,
                "output_tokens": 35,
                "reasoning_tokens": 12,
                "cache_read_tokens": 60,
                "quota_proxy_tokens": 125,
            },
            {
                "ts": "2026-09-08T12:10:00Z",
                "role": "builder",
                "backend": "codex",
                "model": "gpt-6-codex",
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_tokens": 2,
                "cache_read_tokens": 4,
            },
        ]

        result = self.run_report(rows)

        self.assertEqual(result.returncode, 0, result.stderr)
        weekly_line = next(
            line for line in result.stdout.splitlines()
            if "2026-W37" in line and "codex" in line
        )
        self.assertIn("quota=142", weekly_line)
        self.assertIn("reasoning=14", weekly_line)
        self.assertIn("cache=64", weekly_line)


if __name__ == "__main__":
    unittest.main()
