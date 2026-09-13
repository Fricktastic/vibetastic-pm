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

    def test_pending_task_with_missing_dependencies_is_partial_and_not_ready(self):
        text = """---
stages:
  - id: 1
    name: Build
    status: pending
tasks:
  - id: T001
    stage: 1
    title: Missing dependency field
    agent: codex
    status: pending
failure_count: 0
---""" + "   \n"
        plan, warnings = parse_plan(text)
        task = plan["tasks"][0]
        self.assertEqual(task["dependencies"], [])
        self.assertFalse(task["dependencies_valid"])
        self.assertEqual(task["state"], "backlog")
        self.assertTrue(
            any(
                warning["code"] == "missing_required_field"
                and warning["field"] == "depends_on"
                for warning in warnings
            )
        )
        self.assertFalse(any(warning["code"] == "unclosed_frontmatter" for warning in warnings))

    def test_nested_dependencies_warn_and_do_not_become_valid(self):
        text = """---
stages:
  - id: 1
    name: Build
    status: pending
tasks:
  - id: T001
    stage: 1
    title: Done task
    agent: codex
    status: done
    depends_on: []
    failure_count: 0
  - id: T002
    stage: 1
    title: Nested dependency syntax
    agent: codex
    status: pending
    depends_on: [[T001]]
    failure_count: 0
---
"""
        plan, warnings = parse_plan(text)
        task = plan["tasks"][1]
        self.assertEqual(task["dependencies"], [])
        self.assertFalse(task["dependencies_valid"])
        self.assertEqual(task["state"], "backlog")
        self.assertTrue(any(warning["code"] == "nested_dependency_list" for warning in warnings))

    def test_missing_and_duplicate_ids_are_retained_as_invalid_entries(self):
        text = """---
stages:
  - id: 1
    name: Build
    status: pending
tasks:
  - title: Missing ID
    stage: 1
    agent: codex
    status: pending
    depends_on: []
    failure_count: 0
  - id: T001
    stage: 1
    title: First copy
    agent: codex
    status: done
    depends_on: []
    failure_count: 0
  - id: T001
    stage: 1
    title: Duplicate copy
    agent: codex
    status: pending
    depends_on: []
    failure_count: 0
---
"""
        plan, warnings = parse_plan(text)
        tasks = plan["tasks"]
        self.assertEqual(len(tasks), 3)
        self.assertTrue(tasks[0]["id"].startswith("__invalid_task_"))
        self.assertFalse(tasks[0]["id_valid"])
        self.assertIsNone(tasks[0]["source_id"])
        self.assertEqual(tasks[1]["id"], "T001")
        self.assertEqual(tasks[2]["source_id"], "T001")
        self.assertEqual(tasks[2]["id"], "T001~duplicate-2")
        self.assertFalse(tasks[2]["id_valid"])
        self.assertTrue(any(warning["field"] == "id" for warning in warnings))
        self.assertTrue(any(warning["code"] == "duplicate_task_id" for warning in warnings))

    def test_plan_parser_normalizes_stage_and_positive_dependency_readiness(self):
        text = (FIXTURES / "plan-good.md").read_text()
        plan, warnings = parse_plan(text)
        self.assertFalse(warnings)
        self.assertEqual(plan["stages"][0]["state"], "active")
        self.assertEqual(plan["tasks"][1]["state"], "ready")

    def test_plan_parser_warns_on_unknown_dependencies(self):
        text = (FIXTURES / "plan-bad-dep.md").read_text()
        plan, warnings = parse_plan(text)
        self.assertEqual(plan["tasks"][1]["state"], "backlog")
        self.assertTrue(any(warning["code"] == "unknown_dependency" for warning in warnings))


if __name__ == "__main__":
    unittest.main()
