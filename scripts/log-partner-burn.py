#!/usr/bin/env python3
"""log-partner-burn.py — record the orchestrator session's own token burn into cost.jsonl.

Wired as a `Stop` hook by setup.sh.

Why this exists: every cost gate in the framework reads `logs/cost.jsonl`, but that file
only ever contained *dispatched* work. The partner/orchestrator session — the most expensive
context in the system, and the one sharing the claude 5-hour window with claude-backend
builders and (per issue #14) first-pass reviewers — was invisible in it. Both burn gates
(`codex_weekly_burn_threshold`, and the proposed `claude_window_burn_threshold`) therefore
ran on a proxy that drifts by exactly the amount the orchestrator spent.

The record is deliberately the same shape as a dispatch record so `cost-report.sh` needs no
special case; `role` distinguishes it. `cost_usd` stays null — the partner runs on
subscription auth, like the codex and claude builder lanes.

Never fatal: a hook that fails must not interrupt the session. Every error path exits 0.
"""
import json
import os
import sys
import datetime


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    # Claude Code passes the session record on stdin. Field names have moved between
    # versions, so probe rather than assume, and emit nothing if usage isn't present —
    # a record of zeros would be worse than no record, because the gates would trust it.
    usage = payload.get("usage") or payload.get("total_usage") or {}
    if not isinstance(usage, dict) or not usage:
        return 0

    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    cache = usage.get("cache_read_input_tokens")
    if inp is None and out is None:
        return 0

    log_dir = os.environ.get("OPENCODE_DISPATCH_LOG_DIR") or os.path.join(os.getcwd(), "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        return 0

    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "role": "partner",
        "backend": "claude",
        "prompt": None,
        "model": payload.get("model") or "opus",
        "primary_model": payload.get("model") or "opus",
        "fallback_used": False,
        "stall_retries": 0,
        "tier": None,
        "attempts": 1,
        "verify_passed": None,
        "exit": 0,
        "duration_s": payload.get("duration_s"),
        "cost_usd": None,          # subscription lane, like the codex/claude builders
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache,
        "reasoning_tokens": usage.get("reasoning_output_tokens"),
        "session_id": payload.get("session_id"),
        "log": None,
    }

    try:
        with open(os.path.join(log_dir, "cost.jsonl"), "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
