import hashlib
import unittest
from pathlib import Path

from scripts.view_contract import canonical_task_status, parse_plan


FIXTURES = Path(__file__).parent / "fixtures"


class CanonicalTaskStatusTests(unittest.TestCase):
    def test_canonical_status_separates_phase_and_resolution(self):
        self.assertEqual(canonical_task_status("building", False), ("active", None))
        self.assertEqual(canonical_task_status("pending", True), ("ready", None))
        self.assertEqual(canonical_task_status("split", False), ("closed", "split"))
        self.assertEqual(canonical_task_status("wontfix", False), ("closed", "wontfix"))

    def test_canonical_status_preserves_dependency_readiness_and_unknown_fallback(self):
        self.assertEqual(canonical_task_status("pending", False), ("backlog", None))
        self.assertEqual(canonical_task_status("in_progress", True), ("active", None))
        self.assertEqual(canonical_task_status("unrecognized", True), ("backlog", None))


class ParsePlanTests(unittest.TestCase):
    def setUp(self):
        self.text = (FIXTURES / "view-plan-drift.md").read_text()
        self.plan, self.warnings = parse_plan(self.text)

    def test_parse_plan_preserves_drift_and_provenance(self):
        task = self.plan["tasks"][0]
        self.assertEqual(task["source_status"], "building")
        self.assertEqual(task["state"], "active")
        self.assertEqual(task["phase"], "building")
        self.assertEqual(task["provenance"]["path"], "PLAN.md")
        self.assertEqual(task["provenance"]["sha256"], hashlib.sha256(self.text.encode()).hexdigest())
        self.assertEqual(task["field_lines"]["status"], 14)
        self.assertEqual([task["id"] for task in self.plan["tasks"]], ["T001", "T002", "T003"])
        self.assertTrue(any(warning["code"] == "unknown_task_status" for warning in self.warnings))

    def test_parse_plan_keeps_partial_tasks_and_identifies_missing_fields(self):
        missing = [warning for warning in self.warnings if warning["code"] == "missing_required_field"]
        self.assertEqual(
            missing,
            [{
                "code": "missing_required_field",
                "message": "Task T003 is missing required field 'failure_count'",
                "path": "PLAN.md",
                "line": 26,
                "task_id": "T003",
                "field": "failure_count",
            }],
        )
        partial = self.plan["tasks"][2]
        self.assertIsNone(partial["failure_count"])
        self.assertEqual(partial["state"], "ready")

    def test_parse_plan_sets_ready_only_when_all_known_dependencies_are_done(self):
        tasks = {task["id"]: task for task in self.plan["tasks"]}
        self.assertEqual(tasks["T002"]["dependencies"], ["T001"])
        self.assertEqual(tasks["T002"]["state"], "backlog")


if __name__ == "__main__":
    unittest.main()
