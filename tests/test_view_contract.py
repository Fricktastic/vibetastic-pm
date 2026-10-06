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
            "stages", "tasks", "artifacts", "recommended_next", "attention", "ownership", "runs",
            "capacity", "sources", "warnings",
        })
        self.assertEqual(snapshot["generated_at"], "2026-09-13T01:00:00Z")
        self.assertEqual(set(snapshot["sources"]), {
            "PROJECT.md", "PLAN.md", "TASK_LOG.md", "HANDOFF.md", ".orchestrator/lease.json",
            ".orchestrator/runs.json", "logs/runs.jsonl", "logs/cost.jsonl",
        })
        self.assertTrue(all(source["read_at"] == snapshot["generated_at"] for source in snapshot["sources"].values()))
        self.assertEqual(snapshot["recommended_next"], [])

    def test_artifacts_come_only_from_clean_allowlisted_outputs(self):
        plan = (self.pm / "PLAN.md").read_text()
        block = (
            "    outputs:\n      - prompts/design-spec.md\n      - docs/report.md\n"
            "      - prompts/task-T001.md\n      - logs/run.log\n      - ../escape.md\n"
            "      - /etc/passwd\n      - .orchestrator/lease.json\n      - linked/x.md\n"
        )
        plan = plan.replace("  - id: T001\n", "  - id: T001\n" + block, 1)
        self.assertIn("docs/report.md", plan)
        (self.pm / "PLAN.md").write_text(plan)
        (self.pm / "docs").mkdir()
        (self.pm / "docs" / "report.md").write_text("# r\n")
        os.symlink("/", self.pm / "linked")
        snapshot = build_snapshot(self.pm, now=self.now)
        paths = [artifact["path"] for artifact in snapshot["artifacts"]]
        self.assertEqual(paths, ["docs/report.md", "prompts/design-spec.md"])
        report = snapshot["artifacts"][0]
        self.assertEqual(report["kind"], "document")
        self.assertEqual(report["media_type"], "text/markdown")
        self.assertTrue(report["exists"])
        self.assertFalse(snapshot["artifacts"][1]["exists"])
        self.assertEqual(report["task_ids"], ["T001"])
        task = next(task for task in snapshot["tasks"] if task["id"] == "T001")
        self.assertEqual(sorted(task["artifacts"]), sorted(a["id"] for a in snapshot["artifacts"]))
        self.assertNotIn("outputs", task)
        rejected = [w for w in snapshot["warnings"] if w.get("code") == "artifact_path_rejected"]
        self.assertEqual(len(rejected), 6)
        encoded = json.dumps(snapshot)
        for leak in ("/etc/passwd", "escape.md", "task-T001.md", "logs/run.log", "linked/x.md"):
            self.assertNotIn(leak, encoded)
        self.assertEqual(report["id"], build_snapshot(self.pm, now=self.now)["artifacts"][0]["id"])

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

    def test_capacity_projects_duration_fallback_and_exit(self):
        (self.pm / "logs" / "cost.jsonl").write_text(json.dumps({
            "run_id": "x", "duration_s": 42, "primary_model": "terra", "model": "luna",
            "fallback_used": True, "stall_retries": 1, "exit": 30, "log": "raw",
        }) + "\n" + json.dumps({"run_id": "y", "duration_s": "42", "fallback_used": "true"}) + "\n")
        usage = {row["run_id"]: row for row in build_snapshot(self.pm, now=self.now)["capacity"]["usage"]}
        self.assertEqual((usage["x"]["duration_s"], usage["x"]["primary_model"], usage["x"]["fallback_used"],
                          usage["x"]["stall_retries"], usage["x"]["exit"]), (42, "terra", True, 1, 30))
        self.assertIsNone(usage["y"]["duration_s"])
        self.assertIsNone(usage["y"]["fallback_used"])
        self.assertNotIn("log", usage["x"])

    def test_write_snapshot_is_valid_complete_json(self):
        path = write_snapshot(self.pm, build_snapshot(self.pm, now=self.now))
        self.assertEqual(json.loads(path.read_text())["generation"], 1)
        self.assertEqual(path, self.pm / ".orchestrator/view/v1/snapshot.json")

    def test_unstable_three_attempt_capture_returns_last_complete_capture_once_per_source(self):
        original = view_contract._fingerprint_source
        def unstable(pm, relative):
            present, source_hash = original(pm, relative)
            return (present, "changed" if relative == "PLAN.md" else source_hash)
        with patch.object(view_contract, "_fingerprint_source", side_effect=unstable):
            snapshot = build_snapshot(self.pm, now=self.now)
        warnings = [warning for warning in snapshot["warnings"]
                    if warning["code"] == "unstable_source_read"]
        self.assertEqual(warnings, [{"code": "unstable_source_read",
                                     "message": "Source changed during capture", "path": "PLAN.md"}])
        self.assertEqual(snapshot["plan"]["provenance"]["sha256"],
                         hashlib.sha256((self.pm / "PLAN.md").read_bytes()).hexdigest())

    def test_snapshot_replace_failure_preserves_old_bytes_and_cleans_temp(self):
        target = self.pm / ".orchestrator/view/v1/snapshot.json"
        target.parent.mkdir(parents=True)
        target.write_bytes(b'{"old":true}\n')
        with patch.object(view_contract.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                write_snapshot(self.pm, {"new": True})
        self.assertEqual(target.read_bytes(), b'{"old":true}\n')
        self.assertEqual(list(target.parent.glob(".snapshot-*")), [])

    def test_invalid_task_identity_never_links_matching_run(self):
        plan = (self.pm / "PLAN.md").read_text()
        duplicate = """  - id: T001
    stage: 1
    title: Duplicate
    agent: codex
    status: pending
    depends_on: []
    failure_count: 0
"""
        opening, closing = plan.rsplit("---\n", 1)
        (self.pm / "PLAN.md").write_text(opening + duplicate + "---\n" + closing)
        snapshot = build_snapshot(self.pm, now=self.now)
        invalid = next(task for task in snapshot["tasks"] if task["id"] == "T001" and not task["valid_identity"])
        self.assertEqual(invalid["runs"], [])
        self.assertTrue(any(item["rule"] == "missing_task_linkage" and item["task_id"] == "T001"
                            for item in snapshot["attention"]))

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
        row = json.loads(rows[0])
        self.assertEqual(set(row), {"event_id", "type", "operation", "run_id", "task_id", "status", "recorded_at"})
        self.assertRegex(row["recorded_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        append_view_events(self.pm, [event])  # a retried append still deduplicates
        self.assertEqual(len((self.pm / ".orchestrator/view/v1/events.jsonl").read_text().splitlines()), 1)

    def test_event_mapping_allowlists_and_renewal_identity_are_fixed(self):
        mappings = {
            "acquire": "lease_acquired", "renew": "lease_renewed", "release": "lease_released",
            "handoff": "lease_handed_off", "takeover": "lease_taken_over", "reserve": "run_started",
            "finish": "run_finished", "reconcile": "run_reconciled", "update_plan": "plan_updated",
            "write": "document_written", "append": "document_appended",
        }
        entity = {"provider": "codex", "session": "s", "profile": "normal", "renewed_at": "one",
                  "run_id": "r", "task_id": "T001", "status": "active", "role": "builder",
                  "backend": "codex", "model": "m", "tier": "fast", "plan_hash": "h", "lint_exit": 0,
                  "path": "HANDOFF.md", "hash": "h", "field_lines": {"secret": 1}}
        for command, event_type in mappings.items():
            row = diff_snapshots({}, {"changed": command}, command, None, entity)[0]
            self.assertEqual(row["type"], event_type)
            self.assertNotIn("field_lines", row)
        one = diff_snapshots({}, {"changed": 1}, "renew", None, entity)[0]
        entity["renewed_at"] = "two"
        two = diff_snapshots({}, {"changed": 2}, "renew", None, entity)[0]
        self.assertNotEqual(one["event_id"], two["event_id"])
        self.assertNotIn("renewed_at", two)

    def test_event_id_formulas_and_parent_fsync(self):
        entity = {"run_id": "run-1", "task_id": "T001", "status": "active"}
        cmd = diff_snapshots({}, {"after": 1}, "reserve", None, entity)[0]
        expected = hashlib.sha256(json.dumps(entity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
        self.assertEqual(cmd["event_id"], f"cmd:reserve:run-1:{expected}:0")
        op = diff_snapshots({}, {"after": 1}, "write", "transaction-id", {"path": "HANDOFF.md", "hash": "h"})[0]
        self.assertEqual(op["event_id"], "op:" + hashlib.sha256(b"transaction-id").hexdigest()[:32] + ":0")
        with patch.object(view_contract.os, "fsync", wraps=view_contract.os.fsync) as sync:
            append_view_events(self.pm, [cmd])
        self.assertGreaterEqual(sync.call_count, 2)  # event file and its parent directory

    def test_attention_distinguishes_explicit_gate_from_inference(self):
        snapshot = build_snapshot(self.pm, now=self.now)
        attention = derive_attention(snapshot, self.now)
        self.assertEqual(attention[0]["classification"], "explicit")
        self.assertEqual(attention[0]["task_id"], "T001")
        self.assertEqual(attention[1]["classification"], "inferred")
        self.assertEqual(attention[1]["rule"], "stale_heartbeat")
        self.assertEqual(attention[1]["evidence"][0]["path"], ".orchestrator/runs.json")

    def test_journal_run_links_to_gate_task_when_reservation_key_is_absent(self):
        (self.pm / ".orchestrator" / "runs.json").write_text("{}")
        (self.pm / "logs" / "runs.jsonl").write_text(json.dumps({
            "event": "run_start", "run_id": "run-2", "task_id": None, "gate_task_id": "T001",
            "ts_start": "2026-09-13T00:00:00Z", "role": "read-only",
        }) + "\n")
        snapshot = build_snapshot(self.pm, now=self.now)
        self.assertEqual(snapshot["runs"][0]["task_id"], "T001")
        task = next(task for task in snapshot["tasks"] if task["id"] == "T001")
        self.assertEqual(task["runs"], ["run-2"])
        self.assertFalse(any(w["code"] == "missing_task_linkage" for w in snapshot["warnings"]))

    def test_operator_action_request_needs_no_task(self):
        with (self.pm / "TASK_LOG.md").open("a") as log:
            log.write("\n### 2026-09-13T00:03:00Z · operator_action_requested\n```yaml\n"
                      "task_id: null\nagent: pm\nreason: \"Renew the signing certificate\"\n```\n")
        attention = build_snapshot(self.pm, now=self.now)["attention"]
        request = next(item for item in attention if item["kind"] == "operator_action_requested")
        self.assertIsNone(request["task_id"])
        self.assertEqual(request["reason"], "Renew the signing certificate")

    def test_recommended_next_carries_title_state_and_why(self):
        with (self.pm / "TASK_LOG.md").open("a") as log:
            log.write("\n### 2026-09-13T00:03:00Z · next_recommended\n```yaml\ntask_id: null\nagent: pm\n"
                      "items:\n  - task_id: T001\n    why: \"Unblocks the device pass\"\n"
                      "  - task_id: T999\n    why: gone\n```\n")
        snapshot = build_snapshot(self.pm, now=self.now)
        self.assertEqual(snapshot["recommended_next"], [{
            "rank": 1, "task_id": "T001", "title": "Fixture task", "state": "ready",
            "why": "Unblocks the device pass", "recommended_at": "2026-09-13T00:03:00Z",
            "evidence": snapshot["recommended_next"][0]["evidence"],
        }])
        self.assertTrue(any(w["code"] == "unknown_recommended_task" for w in snapshot["warnings"]))

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
