import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "log-partner-burn.py"
BACKFILL = ROOT / "scripts" / "backfill-partner-burn.py"
APPEND_COST = ROOT / "scripts" / "append-cost.py"


def load_hook_module():
    spec = importlib.util.spec_from_file_location("log_partner_burn", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def claude_rows(session_model="claude-opus-5"):
    return [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": session_model,
                "usage": {
                    "input_tokens": 15,
                    "output_tokens": 30,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 10,
                    "output_tokens_details": {"reasoning_tokens": 4},
                },
            },
        },
        {
            "type": "assistant",
            "isSidechain": True,
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-5",
                "usage": {
                    "input_tokens": 999,
                    "output_tokens": 999,
                    "cache_read_input_tokens": 999,
                },
            },
        },
    ]


def codex_rows(
    session_id="codex-session",
    source="cli",
    input_tokens=150,
    cached_input_tokens=60,
    output_tokens=35,
    reasoning_output_tokens=12,
):
    return [
        {
            "timestamp": "2026-09-07T12:00:00Z",
            "type": "session_meta",
            "payload": {"id": session_id, "source": source},
        },
        {
            "timestamp": "2026-09-07T12:00:01Z",
            "type": "turn_context",
            "payload": {"model": "gpt-6-codex"},
        },
        {
            "timestamp": "2026-09-07T12:00:02Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 8,
                        "total_tokens": 120,
                    }
                },
            },
        },
        {
            "timestamp": "2026-09-07T12:00:03Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_input_tokens,
                        "output_tokens": output_tokens,
                        "reasoning_output_tokens": reasoning_output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                    }
                },
            },
        },
    ]


class PartnerTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.log_dir = self.base / "logs"

    def tearDown(self):
        self.temp.cleanup()

    def run_hook(self, provider, transcript, session_id="hook-session", model=None):
        payload = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(self.base),
        }
        if model is not None:
            payload["model"] = model
        env = dict(os.environ, OPENCODE_DISPATCH_LOG_DIR=str(self.log_dir))
        return subprocess.run(
            [sys.executable, str(HOOK), "--provider", provider],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def cost_rows(self):
        path = self.log_dir / "cost.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def errors(self):
        path = self.log_dir / "telemetry-errors.log"
        return path.read_text() if path.exists() else ""

    def run_append_cost(self, value):
        return subprocess.run(
            [sys.executable, str(APPEND_COST), str(self.log_dir)],
            input=value if isinstance(value, str) else json.dumps(value),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_claude_adapter_preserves_existing_interface_and_accounting(self):
        transcript = self.base / "claude.jsonl"
        write_jsonl(transcript, claude_rows())

        hook = load_hook_module()
        totals, model, turns = hook.read_transcript(str(transcript))
        self.assertEqual(
            totals,
            {"input": 15, "output": 30, "cache_read": 200,
             "cache_creation": 10, "reasoning": 4},
        )
        self.assertEqual((model, turns), ("claude-opus-5", 1))

        result = self.run_hook("claude", transcript, "shared-id")
        self.assertEqual(result.returncode, 0, result.stderr)
        [record] = self.cost_rows()
        self.assertEqual(record["backend"], "claude")
        self.assertEqual(record["role"], "partner")
        self.assertEqual(record["input_tokens"], 15)
        self.assertEqual(record["cache_read_tokens"], 200)
        self.assertEqual(record["output_tokens"], 30)
        self.assertEqual(record["reasoning_tokens"], 4)
        self.assertEqual(record["quota_proxy_tokens"], 45)

    def test_pm_dir_routes_installed_hook_telemetry_ahead_of_provider_cwd(self):
        transcript = self.base / "claude.jsonl"
        pm_dir = self.base / "installed-pm"
        provider_dir = self.base / "provider-project"
        pm_dir.mkdir()
        provider_dir.mkdir()
        write_jsonl(transcript, claude_rows())
        payload = {
            "session_id": "installed-session",
            "transcript_path": str(transcript),
            "cwd": str(provider_dir),
        }
        env = dict(
            os.environ,
            PM_DIR=str(pm_dir),
            CLAUDE_PROJECT_DIR=str(provider_dir),
        )
        env.pop("OPENCODE_DISPATCH_LOG_DIR", None)

        result = subprocess.run(
            [sys.executable, str(HOOK), "--provider", "claude"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((pm_dir / "logs" / "cost.jsonl").is_file())
        self.assertFalse((provider_dir / "logs" / "cost.jsonl").exists())

    def test_codex_uses_latest_cumulative_snapshot_and_separates_cached_input(self):
        transcript = self.base / "codex.jsonl"
        write_jsonl(transcript, codex_rows())

        result = self.run_hook("codex", transcript, "payload-id", model="payload-model")
        self.assertEqual(result.returncode, 0, result.stderr)
        [record] = self.cost_rows()
        self.assertEqual(record["backend"], "codex")
        self.assertEqual(record["role"], "partner")
        self.assertEqual(record["session_id"], "codex-session")
        self.assertEqual(record["model"], "gpt-6-codex")
        self.assertEqual(record["input_tokens"], 90)
        self.assertEqual(record["cache_read_tokens"], 60)
        self.assertEqual(record["cache_creation_tokens"], 0)
        self.assertEqual(record["output_tokens"], 35)
        self.assertEqual(record["reasoning_tokens"], 12)
        self.assertEqual(record["quota_proxy_tokens"], 125)
        self.assertEqual(record["transcript_schema"], "codex-legacy-rollout-jsonl")

    def test_codex_growth_logs_only_normalized_delta(self):
        transcript = self.base / "codex.jsonl"
        rows = codex_rows()
        write_jsonl(transcript, rows)
        self.run_hook("codex", transcript)
        rows.append(codex_rows(
            input_tokens=170,
            cached_input_tokens=65,
            output_tokens=45,
            reasoning_output_tokens=15,
        )[-1])
        write_jsonl(transcript, rows)

        result = self.run_hook("codex", transcript)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.cost_rows()
        self.assertEqual(len(records), 2)
        delta = records[-1]
        self.assertEqual(delta["input_tokens"], 15)
        self.assertEqual(delta["cache_read_tokens"], 5)
        self.assertEqual(delta["output_tokens"], 10)
        self.assertEqual(delta["reasoning_tokens"], 3)
        self.assertEqual(delta["quota_proxy_tokens"], 25)

    def test_duplicate_token_stream_after_model_change_adds_no_record(self):
        transcript = self.base / "codex-model-stream.jsonl"
        rows = codex_rows()
        write_jsonl(transcript, rows)
        self.run_hook("codex", transcript, session_id="parent-hook-id")
        rows.extend([
            {
                "type": "turn_context",
                "payload": {"model": "gpt-6-codex-mini"},
            },
            rows[-1],
        ])
        write_jsonl(transcript, rows)

        duplicate = self.run_hook("codex", transcript, session_id="parent-hook-id")

        self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
        self.assertEqual(len(self.cost_rows()), 1)

        rows.append(codex_rows(
            input_tokens=170,
            cached_input_tokens=65,
            output_tokens=45,
            reasoning_output_tokens=15,
        )[-1])
        write_jsonl(transcript, rows)
        growth = self.run_hook("codex", transcript, session_id="parent-hook-id")
        self.assertEqual(growth.returncode, 0, growth.stderr)
        records = self.cost_rows()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["model"], "gpt-6-codex-mini")

    def test_duplicate_stop_is_deduplicated_by_journal_when_checkpoint_is_stale(self):
        transcript = self.base / "codex.jsonl"
        write_jsonl(transcript, codex_rows())
        self.run_hook("codex", transcript)
        (self.log_dir / ".partner-burn-state.json").write_text("{}")

        result = self.run_hook("codex", transcript)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.cost_rows()), 1)

    def test_append_survives_checkpoint_failure_and_replay_uses_journal(self):
        transcript = self.base / "codex.jsonl"
        write_jsonl(transcript, codex_rows())
        telemetry = load_hook_module().partner_telemetry
        snapshot = telemetry.parse_codex_transcript(
            str(transcript), fallback_session_id="hook-id"
        )

        with mock.patch.object(
            telemetry,
            "_write_compatibility_state",
            side_effect=OSError("injected checkpoint failure"),
        ):
            first, diagnostics = telemetry.append_snapshot(str(self.log_dir), snapshot)

        self.assertIsNotNone(first)
        self.assertIn("could not persist high-water state", "\n".join(diagnostics))
        self.assertFalse((self.log_dir / ".partner-burn-state.json").exists())

        replay, unused_diagnostics = telemetry.append_snapshot(str(self.log_dir), snapshot)
        self.assertIsNone(replay)
        self.assertEqual(len(self.cost_rows()), 1)

    def test_provider_and_role_are_part_of_session_namespace(self):
        claude = self.base / "claude.jsonl"
        codex_partner = self.base / "codex-partner.jsonl"
        codex_child = self.base / "codex-child.jsonl"
        write_jsonl(claude, claude_rows())
        write_jsonl(codex_partner, codex_rows(session_id="same-id"))
        write_jsonl(
            codex_child,
            codex_rows(
                session_id="same-id",
                source={
                    "subagent": {
                        "thread_spawn": {"parent_thread_id": "parent-id", "depth": 1}
                    }
                },
            ),
        )

        self.run_hook("claude", claude, session_id="same-id")
        self.run_hook("codex", codex_partner, session_id="ignored")
        self.run_hook("codex", codex_child, session_id="ignored")

        records = self.cost_rows()
        self.assertEqual(len(records), 3)
        self.assertEqual(
            {(row["backend"], row["role"], row["session_id"]) for row in records},
            {
                ("claude", "partner", "same-id"),
                ("codex", "partner", "same-id"),
                ("codex", "subagent", "same-id"),
            },
        )
        self.assertEqual(len({row["session_namespace"] for row in records}), 3)

    def test_subagent_session_meta_identity_overrides_parent_hook_session(self):
        transcript = self.base / "codex-child.jsonl"
        write_jsonl(
            transcript,
            codex_rows(
                session_id="child-meta-id",
                source={
                    "subagent": {
                        "thread_spawn": {"parent_thread_id": "parent-hook-id", "depth": 1}
                    }
                },
            ),
        )

        result = self.run_hook("codex", transcript, session_id="parent-hook-id")

        self.assertEqual(result.returncode, 0, result.stderr)
        [record] = self.cost_rows()
        self.assertEqual(record["role"], "subagent")
        self.assertEqual(record["session_id"], "child-meta-id")
        self.assertEqual(
            record["session_namespace"], "codex:subagent:child-meta-id"
        )

    def test_codex_session_without_source_is_not_assumed_to_be_partner(self):
        transcript = self.base / "codex-no-source.jsonl"
        rows = codex_rows()
        rows[0] = {
            "type": "session_meta",
            "payload": {"id": "unknown-role-session"},
        }
        write_jsonl(transcript, rows)

        result = self.run_hook("codex", transcript, session_id="parent-hook-id")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cost_rows(), [])
        self.assertIn("session_meta source", self.errors())
        self.assertIn("wrote nothing", self.errors())

    def test_legacy_journal_rows_are_authoritative_and_state_shape_remains_compatible(self):
        transcript = self.base / "claude.jsonl"
        write_jsonl(transcript, claude_rows())
        self.log_dir.mkdir()
        legacy = load_hook_module().build_record(
            {"input": 15, "output": 30, "cache_read": 200,
             "cache_creation": 10, "reasoning": 4},
            "claude-opus-5",
            "legacy-session",
        )
        legacy.pop("session_namespace", None)
        legacy.pop("quota_proxy_tokens", None)
        (self.log_dir / "cost.jsonl").write_text(json.dumps(legacy) + "\n")

        self.run_hook("claude", transcript, "legacy-session")

        self.assertEqual(len(self.cost_rows()), 1)
        state = json.loads((self.log_dir / ".partner-burn-state.json").read_text())
        self.assertIn("legacy-session", state)

    def test_claude_backfill_does_not_collide_with_codex_session_id(self):
        transcripts = self.base / "transcripts"
        transcripts.mkdir()
        write_jsonl(transcripts / "same-id.jsonl", claude_rows())
        self.log_dir.mkdir()
        codex_record = {
            "role": "partner",
            "backend": "codex",
            "session_id": "same-id",
            "input_tokens": 90,
            "output_tokens": 35,
            "cache_read_tokens": 60,
            "cache_creation_tokens": 0,
            "reasoning_tokens": 12,
        }
        (self.log_dir / "cost.jsonl").write_text(json.dumps(codex_record) + "\n")

        result = subprocess.run(
            [
                sys.executable,
                str(BACKFILL),
                "--pm-dir",
                str(self.base),
                "--transcripts",
                str(transcripts),
                "--apply",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.cost_rows()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["backend"], "claude")
        self.assertTrue(records[-1]["backfilled"])
        self.assertEqual(records[-1]["quota_proxy_tokens"], 45)
        self.assertEqual(records[-1]["session_namespace"], "claude:partner:same-id")

    def test_malformed_and_partial_codex_lines_are_diagnosed_but_valid_usage_is_logged(self):
        transcript = self.base / "codex-mixed.jsonl"
        valid = codex_rows()
        transcript.write_text(
            "not-json\n"
            + json.dumps(valid[0]) + "\n"
            + json.dumps({
                "type": "event_msg",
                "payload": {"type": "token_count", "info": {}},
            }) + "\n"
            + "".join(json.dumps(row) + "\n" for row in valid[1:])
        )

        result = self.run_hook("codex", transcript)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.cost_rows()), 1)
        errors = self.errors()
        self.assertIn("malformed JSON", errors)
        self.assertIn("missing total_token_usage", errors)

    def test_unknown_or_zero_codex_usage_writes_no_record_and_visible_error(self):
        transcript = self.base / "codex-unknown.jsonl"
        write_jsonl(
            transcript,
            [
                codex_rows()[0],
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"mystery_tokens": 10}},
                    },
                },
            ],
        )

        result = self.run_hook("codex", transcript)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cost_rows(), [])
        self.assertIn("unknown usage schema", self.errors())

    def test_partial_codex_usage_does_not_invent_cache_or_reasoning_zeroes(self):
        transcript = self.base / "codex-partial.jsonl"
        write_jsonl(
            transcript,
            [
                codex_rows()[0],
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "total_tokens": 15,
                            }
                        },
                    },
                },
            ],
        )

        result = self.run_hook("codex", transcript)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cost_rows(), [])
        self.assertIn("unknown usage schema", self.errors())
        self.assertIn("cached_input_tokens", self.errors())

    def test_paginated_codex_export_is_rejected_with_schema_diagnostic(self):
        transcript = self.base / "codex-page.jsonl"
        transcript.write_text(json.dumps({
            "data": codex_rows(),
            "next_cursor": None,
        }) + "\n")

        result = self.run_hook("codex", transcript, session_id="hook-id")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cost_rows(), [])
        self.assertIn("unsupported paginated Codex transcript", self.errors())
        self.assertIn("legacy rollout JSONL", self.errors())

    def test_concurrent_duplicate_hooks_append_once(self):
        transcript = self.base / "codex.jsonl"
        write_jsonl(transcript, codex_rows())
        payload = json.dumps({
            "session_id": "payload-id",
            "transcript_path": str(transcript),
            "cwd": str(self.base),
        })
        env = dict(os.environ, OPENCODE_DISPATCH_LOG_DIR=str(self.log_dir))
        procs = [
            subprocess.Popen(
                [sys.executable, str(HOOK), "--provider", "codex"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for _ in range(8)
        ]
        results = [proc.communicate(payload) for proc in procs]

        self.assertTrue(all(proc.returncode == 0 for proc in procs), results)
        self.assertEqual(len(self.cost_rows()), 1)

    def test_generic_cost_append_is_locked_durable_and_canonical(self):
        result = self.run_append_cost({"z": 1, "a": "builder"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.log_dir / "cost.jsonl").read_text(),
            '{"a":"builder","z":1}\n',
        )

    def test_generic_cost_append_rejects_non_object_with_visible_error(self):
        result = self.run_append_cost(["not", "a", "record"])

        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.log_dir / "cost.jsonl").exists())
        self.assertIn("JSON object", self.errors())

    def test_generic_append_repairs_torn_tail_without_concatenating_records(self):
        self.log_dir.mkdir()
        (self.log_dir / "cost.jsonl").write_text('{"torn":')

        result = self.run_append_cost({"role": "builder", "run_id": "run-1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = (self.log_dir / "cost.jsonl").read_text().splitlines()
        self.assertEqual(lines[0], '{"torn":')
        self.assertEqual(json.loads(lines[1]), {"role": "builder", "run_id": "run-1"})
        self.assertIn("repaired torn final cost.jsonl line", self.errors())

    def test_adapter_repairs_torn_tail_and_keeps_new_record_parseable(self):
        transcript = self.base / "codex.jsonl"
        write_jsonl(transcript, codex_rows())
        self.log_dir.mkdir()
        (self.log_dir / "cost.jsonl").write_text('{"torn":')

        result = self.run_hook("codex", transcript)

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = (self.log_dir / "cost.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(lines[0])
        self.assertEqual(json.loads(lines[1])["backend"], "codex")
        self.assertIn("repaired torn final cost.jsonl line", self.errors())

    def test_dispatch_style_and_adapter_appends_share_one_lock(self):
        transcript = self.base / "codex.jsonl"
        write_jsonl(transcript, codex_rows())
        payload = json.dumps({
            "session_id": "payload-id",
            "transcript_path": str(transcript),
            "cwd": str(self.base),
        })
        processes = []
        for index in range(8):
            processes.append(subprocess.Popen(
                [sys.executable, str(APPEND_COST), str(self.log_dir)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ))
        environment = dict(os.environ, OPENCODE_DISPATCH_LOG_DIR=str(self.log_dir))
        processes.append(subprocess.Popen(
            [sys.executable, str(HOOK), "--provider", "codex"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        ))

        results = []
        for index, process in enumerate(processes):
            stdin = ({"role": "builder", "run_id": "run-%d" % index}
                     if index < 8 else payload)
            results.append(process.communicate(
                json.dumps(stdin) if isinstance(stdin, dict) else stdin
            ))

        self.assertTrue(all(process.returncode == 0 for process in processes), results)
        records = self.cost_rows()
        self.assertEqual(len(records), 9)
        self.assertEqual(len({row.get("run_id") for row in records if row.get("run_id")}), 8)
        self.assertEqual(sum(row.get("backend") == "codex" for row in records), 1)


if __name__ == "__main__":
    unittest.main()
