#!/usr/bin/env python3
"""Record Claude or Codex orchestrator token usage in ``logs/cost.jsonl``.

Stop hooks provide a transcript path rather than usage. Provider adapters read the
transcript, while the shared append-only journal prevents duplicate cumulative usage from
being recorded on repeated Stop events or after a lost compatibility checkpoint.

Telemetry is never allowed to block the session. Missing or unrecognized usage writes a
diagnostic to ``logs/telemetry-errors.log`` and never writes a zero-valued cost record.
"""

import argparse
import datetime
import json
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import partner_telemetry


STATE_FILE = partner_telemetry.STATE_FILE
ERROR_LOG = "telemetry-errors.log"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _note(log_dir, msg):
    """Leave a trace when telemetry is incomplete, without breaking the hook."""
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, ERROR_LOG), "a") as fh:
            fh.write("%s log-partner-burn: %s\n" % (_now(), msg))
    except Exception:
        pass


def _resolve_log_dir(payload):
    env = os.environ.get("OPENCODE_DISPATCH_LOG_DIR")
    if env:
        return env
    base = (
        os.environ.get("PM_DIR")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or payload.get("cwd")
        or os.getcwd()
    )
    return os.path.join(base, "logs")


def read_transcript(path):
    """Legacy Claude adapter interface retained for selftests and backfill callers."""
    snapshot = partner_telemetry.parse_claude_transcript(path)
    return snapshot.totals, snapshot.model, snapshot.turns


def build_record(delta, model, session_id, duration_s=None):
    """Legacy record-builder interface with Claude defaults."""
    return partner_telemetry.build_record(delta, model, session_id, duration_s)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("claude", "codex"), default="claude")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _note(os.path.join(os.getcwd(), "logs"), "stdin was not JSON; wrote nothing")
        return 0
    if not isinstance(payload, dict):
        _note(os.path.join(os.getcwd(), "logs"), "stdin was not a JSON object; wrote nothing")
        return 0

    log_dir = _resolve_log_dir(payload)
    session_id = payload.get("session_id")
    transcript = payload.get("transcript_path")
    if not transcript or not os.path.exists(transcript):
        _note(log_dir, "no readable transcript_path (%r) for session %s" % (transcript, session_id))
        return 0

    try:
        snapshot = partner_telemetry.parse_transcript(
            args.provider,
            transcript,
            fallback_session_id=session_id,
            fallback_model=payload.get("model"),
        )
    except Exception as exc:
        _note(log_dir, "could not read transcript %s: %s" % (transcript, exc))
        return 0

    if snapshot.turns == 0 or not any(snapshot.totals.values()):
        for diagnostic in snapshot.diagnostics:
            _note(log_dir, diagnostic)
        _note(log_dir, "transcript %s carried no usage; wrote nothing" % transcript)
        return 0

    try:
        unused_record, diagnostics = partner_telemetry.append_snapshot(log_dir, snapshot)
    except Exception as exc:
        _note(log_dir, "could not append to cost.jsonl: %s" % exc)
        return 0
    for diagnostic in diagnostics:
        _note(log_dir, diagnostic)
    return 0


if __name__ == "__main__":
    sys.exit(main())
