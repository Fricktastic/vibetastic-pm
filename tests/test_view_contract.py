import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import scripts.view_contract as view_contract
from scripts.view_contract import (
    build_snapshot,
    canonical_task_status,
    derive_attention,
    parse_plan,
    sanitize_lease,
    append_view_events,
    diff_snapshots,
    write_snapshot,
)


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
        self.assertIsNone(tasks[0]["id"])
        self.assertEqual(tasks[0]["entry_key"], "invalid-entry:7")
        self.assertFalse(tasks[0]["valid_identity"])
        self.assertIsNone(tasks[0]["source_id"])
        self.assertEqual(tasks[1]["id"], "T001")
        self.assertEqual(tasks[2]["source_id"], "T001")
        self.assertEqual(tasks[2]["id"], "T001")
        self.assertEqual(tasks[2]["entry_key"], "invalid-entry:20")
        self.assertFalse(tasks[2]["valid_identity"])
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


class ViewSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.pm = Path(self.temporary.name)
        fixture = FIXTURES / "view-project"
        (self.pm / "PROJECT.md").write_text((fixture / "PROJECT.md").read_text())
        (self.pm / "PLAN.md").write_text((fixture / "PLAN.md").read_text())
        (self.pm / "TASK_LOG.md").write_text(
            """# Task log

### 2026-09-13T00:02:00Z · user_escalation
```yaml
task_id: T001
reason: \"Approve device verification\"
```
"""
        )
        (self.pm / "HANDOFF.md").write_text("# Current handoff\n")
        (self.pm / ".orchestrator").mkdir()
        (self.pm / ".orchestrator" / "lease.json").write_text(json.dumps({
            "provider": "codex", "session": "session-1", "profile": "normal",
            "token": "lease-secret", "pid": 123, "process_start": "secret-process",
            "acquired_at": "2026-09-13T00:00:00Z",
            "renewed_at": "2026-09-13T00:01:00Z",
        }))
        (self.pm / ".orchestrator" / "runs.json").write_text(json.dumps({
            "run-1": {
                "run_id": "run-1", "task_id": "T001", "status": "active",
                "role": "builder", "backend": "codex", "model": "gpt-5.6-terra",
                "tier": "standard", "heartbeat_at": "2026-09-13T00:01:00Z",
                "worktree": "/secret/source", "dir": "/secret/source",
            }
        }))
        (self.pm / "logs").mkdir()
        (self.pm / "logs" / "runs.jsonl").write_text(json.dumps({
            "event": "run_start", "run_id": "run-1", "task_id": "T001",
            "ts_start": "2026-09-13T00:00:00Z", "role": "builder",
            "backend": "codex", "model": "gpt-5.6-terra", "tier": "standard",
            "prompt": "secret prompt", "log": "raw.log", "dir": "/secret/source",
            "worktree": "/secret/source",
        }) + "\n")
        (self.pm / "logs" / "cost.jsonl").write_text(json.dumps({
            "ts": "2026-09-13T00:05:00Z", "run_id": "run-1", "task_id": "T001",
            "role": "builder", "backend": "codex", "model": "gpt-5.6-terra",
            "tier": "standard", "input_tokens": 100, "output_tokens": 20,
            "quota_proxy_tokens": 120, "cost_usd": None, "log": "raw model output",
        }) + "\n")
        self.now = datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc)

    def test_snapshot_excludes_credentials_and_raw_content(self):
        snapshot = build_snapshot(self.pm, now=self.now)
        encoded = json.dumps(snapshot)
        self.assertNotIn("lease-secret", encoded)
        self.assertNotIn("raw model output", encoded)
        self.assertNotIn("secret prompt", encoded)
        self.assertNotIn("/secret/source", encoded)
        self.assertEqual(snapshot["contract"], "vibetastic-view/v1")
        self.assertEqual(snapshot["project"]["id"], "fixture")
        self.assertEqual(snapshot["ownership"]["provider"], "codex")

    def test_snapshot_has_exact_schema_and_fixed_source_metadata(self):
        snapshot = build_snapshot(self.pm, now=self.now)
        self.assertEqual(set(snapshot), {
            "contract", "schema_version", "generated_at", "generation", "project", "plan",
            "stages", "tasks", "recommended_next", "attention", "ownership", "runs",
            "capacity", "sources", "warnings",
        })
        self.assertEqual(snapshot["generated_at"], "2026-09-13T01:00:00Z")
        self.assertEqual(set(snapshot["sources"]), {
            "PROJECT.md", "PLAN.md", "TASK_LOG.md", "HANDOFF.md", ".orchestrator/lease.json",
            ".orchestrator/runs.json", "logs/runs.jsonl", "logs/cost.jsonl",
        })
        self.assertTrue(all(source["read_at"] == snapshot["generated_at"] for source in snapshot["sources"].values()))
        self.assertEqual(snapshot["recommended_next"], [])

    def test_runs_and_inferred_ids_are_deterministic_and_link_only_authoritative_tasks(self):
        (self.pm / "PLAN.md").write_text((self.pm / "PLAN.md").read_text().replace(
            "  - id: T001\n", "  - id: T001\n", 1) + "")
        runs = json.loads((self.pm / ".orchestrator" / "runs.json").read_text())
        runs["run-2"] = {"run_id": "run-2", "task_id": "T001", "status": "active"}
        runs["orphan"] = {"run_id": "orphan", "task_id": "T404", "status": "active"}
        (self.pm / ".orchestrator" / "runs.json").write_text(json.dumps(runs))
        first = build_snapshot(self.pm, now=self.now)
        second = build_snapshot(self.pm, now=self.now)
        self.assertEqual([run["run_id"] for run in first["runs"]], ["orphan", "run-1", "run-2"])
        self.assertEqual(first["tasks"][0]["runs"], ["run-1", "run-2"])
        self.assertEqual(first["attention"], second["attention"])
        self.assertTrue(any(item["rule"] == "missing_task_linkage" and item["task_id"] == "T404" for item in first["attention"]))

    def test_capacity_never_coerces_numeric_looking_values(self):
        (self.pm / "logs" / "cost.jsonl").write_text(json.dumps({
            "run_id": "x", "task_id": "T001", "input_tokens": "100", "output_tokens": True,
            "quota_proxy_tokens": 1.5, "cost_usd": "2.0",
        }) + "\n")
        usage = build_snapshot(self.pm, now=self.now)["capacity"]["usage"][0]
        self.assertIsNone(usage["input_tokens"])
        self.assertIsNone(usage["output_tokens"])
        self.assertEqual(usage["quota_proxy_tokens"], 1.5)
        self.assertIsNone(usage["cost_usd"])

    def test_write_snapshot_is_valid_complete_json(self):
        path = write_snapshot(self.pm, build_snapshot(self.pm, now=self.now))
        self.assertEqual(json.loads(path.read_text())["generation"], 1)
        self.assertEqual(path, self.pm / ".orchestrator/view/v1/snapshot.json")

    def test_snapshot_does_not_publish_parser_line_maps(self):
        snapshot = build_snapshot(self.pm, now=self.now)
        self.assertTrue(all("field_lines" not in row for row in snapshot["tasks"] + snapshot["stages"]))

    def test_event_rows_are_compact_allowlisted_and_deduplicated(self):
        event = diff_snapshots({}, {"changed": True}, "reserve", None,
                               {"run_id": "run-1", "task_id": "T001", "status": "active",
                                "token": "must-not-appear"})[0]
        append_view_events(self.pm, [event, event])
        rows = (self.pm / ".orchestrator/view/v1/events.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(json.loads(rows[0])), {"event_id", "type", "operation", "run_id", "task_id", "status"})

    def test_attention_distinguishes_explicit_gate_from_inference(self):
        snapshot = build_snapshot(self.pm, now=self.now)
        attention = derive_attention(snapshot, self.now)
        self.assertEqual(attention[0]["classification"], "explicit")
        self.assertEqual(attention[0]["task_id"], "T001")
        self.assertEqual(attention[1]["classification"], "inferred")
        self.assertEqual(attention[1]["rule"], "stale_heartbeat")
        self.assertEqual(attention[1]["evidence"][0]["path"], ".orchestrator/runs.json")

    def test_sanitize_lease_uses_allowlist(self):
        lease = {"provider": "codex", "session": "s", "profile": "normal", "token": "x",
                 "pid": 42, "process_start": "y", "acquired_at": "a", "renewed_at": "r"}
        self.assertEqual(set(sanitize_lease(lease)), {
            "provider", "session", "profile", "acquired_at", "renewed_at"
        })

    def test_malformed_journal_is_partial_and_warned(self):
        with (self.pm / "logs" / "runs.jsonl").open("a") as stream:
            stream.write("{torn\n")
        snapshot = build_snapshot(self.pm, now=self.now)
        self.assertEqual(snapshot["runs"][0]["run_id"], "run-1")
        self.assertTrue(any(warning["code"] == "malformed_jsonl" for warning in snapshot["warnings"]))

    def test_missing_source_is_a_warning_but_permission_error_is_fatal(self):
        (self.pm / "HANDOFF.md").unlink()
        snapshot = build_snapshot(self.pm, now=self.now)
        self.assertTrue(any(warning["code"] == "missing_source" and warning["path"] == "HANDOFF.md" for warning in snapshot["warnings"]))
        original = Path.read_bytes
        def denied(path):
            if path.name == "PROJECT.md":
                raise OSError(13, "Permission denied", str(path))
            return original(path)
        with patch.object(Path, "read_bytes", denied):
            with self.assertRaises(view_contract.UnrecoverableSourceRead) as context:
                build_snapshot(self.pm, now=self.now)
        self.assertEqual(context.exception.path, "PROJECT.md")
        self.assertNotIn(str(self.pm), str(context.exception))

    def test_cli_reports_source_permission_failure_as_exit_three(self):
        result = subprocess.run(
            ["python3", "scripts/export-view-contract.py", "--pm-dir", str(self.pm / "missing")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 3)
        self.assertTrue(result.stderr.startswith("export-view-contract: "))


if __name__ == "__main__":
    unittest.main()
