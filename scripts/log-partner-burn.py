#!/usr/bin/env python3
"""log-partner-burn.py — record the orchestrator session's own token burn into cost.jsonl.

Wired as a `Stop` hook by setup.sh.

Why this exists: every cost gate in the framework reads `logs/cost.jsonl`, but that file
only ever contained *dispatched* work. The partner/orchestrator session — the most expensive
context in the system, and the one sharing the claude 5-hour window with claude-backend
builders and (per issue #14) first-pass reviewers — was invisible in it. Both burn gates
(`codex_weekly_burn_threshold`, and the proposed `claude_window_burn_threshold`) therefore
ran on a proxy that drifts by exactly the amount the orchestrator spent.

**Why it is written this way (issue #30).** The first revision read usage straight off the
hook payload (`payload["usage"]`) and bailed when absent. Claude Code's real Stop payload is
`{session_id, transcript_path, stop_hook_active, cwd}` — it carries no usage at all — so the
guard fired every time and the hook silently wrote nothing for five days across 28 sessions.
Reconstructing from the transcripts afterwards put the orchestrator at ~71% of every token
the system had ever consumed, none of it logged. Usage lives in the **transcript**: one
`message.usage` object per assistant turn. So read it from there.

Two invariants that bug taught us:

1. **Never write zeros.** A record of zeros is worse than no record, because the gates trust
   it. Preserved from the original.
2. **Never fail silently.** The original honored (1) and violated (2), which is why nobody
   noticed. Every bail-out path now leaves a line in `logs/telemetry-errors.log`.

Stop fires once per assistant turn, so a cumulative transcript total would be re-counted on
every turn. State in `logs/.partner-burn-state.json` holds a per-session high-water mark and
only the delta is appended.

Never fatal: a hook that fails must not interrupt the session. Every error path exits 0.
"""
import datetime
import json
import os
import sys

STATE_FILE = ".partner-burn-state.json"
ERROR_LOG = "telemetry-errors.log"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _note(log_dir, msg):
    """Leave a trace when we decline to write. Invariant 2: silence must be visible."""
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
    base = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    return os.path.join(base, "logs")


def read_transcript(path):
    """Sum usage over the session's own assistant turns.

    Sidechain entries are subagent turns; the Agent PostToolUse hook owns those
    (logs/agent-spawns.jsonl), so counting them here would double-count.
    """
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "reasoning": 0}
    model = None
    turns = 0
    with open(path, errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("isSidechain"):
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            turns += 1
            model = msg.get("model") or model
            totals["input"] += usage.get("input_tokens") or 0
            totals["output"] += usage.get("output_tokens") or 0
            totals["cache_read"] += usage.get("cache_read_input_tokens") or 0
            totals["cache_creation"] += usage.get("cache_creation_input_tokens") or 0
            details = usage.get("output_tokens_details") or {}
            if isinstance(details, dict):
                totals["reasoning"] += details.get("reasoning_tokens") or 0
    return totals, model, turns


def build_record(delta, model, session_id, duration_s=None):
    """Same shape as a dispatch record so cost-report.sh needs no special case."""
    return {
        "ts": _now(),
        "role": "partner",
        "backend": "claude",
        "prompt": None,
        "model": model or "opus",
        "primary_model": model or "opus",
        "fallback_used": False,
        "stall_retries": 0,
        "tier": None,
        "attempts": 1,
        "verify_passed": None,
        "exit": 0,
        "duration_s": duration_s,
        "cost_usd": None,  # subscription lane, like the codex/claude builders
        "input_tokens": delta["input"],
        "output_tokens": delta["output"],
        "cache_read_tokens": delta["cache_read"],
        "cache_creation_tokens": delta["cache_creation"],
        "reasoning_tokens": delta["reasoning"] or None,
        "session_id": session_id,
        "log": None,
    }


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _note(os.path.join(os.getcwd(), "logs"), "stdin was not JSON; wrote nothing")
        return 0

    log_dir = _resolve_log_dir(payload)
    session_id = payload.get("session_id")
    transcript = payload.get("transcript_path")

    if not transcript or not os.path.exists(transcript):
        _note(log_dir, "no readable transcript_path (%r) for session %s" % (transcript, session_id))
        return 0

    try:
        totals, model, turns = read_transcript(transcript)
    except Exception as exc:
        _note(log_dir, "could not read transcript %s: %s" % (transcript, exc))
        return 0

    if turns == 0 or not any(totals.values()):
        _note(log_dir, "transcript %s carried no usage; wrote nothing" % transcript)
        return 0

    # Per-session high-water mark: Stop fires per turn, so log only what is new.
    state_path = os.path.join(log_dir, STATE_FILE)
    try:
        with open(state_path) as fh:
            state = json.load(fh)
    except Exception:
        state = {}
    seen = state.get(session_id) or {}
    delta = {k: totals[k] - (seen.get(k) or 0) for k in totals}

    if all(v <= 0 for v in delta.values()):
        return 0  # nothing new since the last turn; not an error, no note
    delta = {k: max(v, 0) for k, v in delta.items()}

    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "cost.jsonl"), "a") as fh:
            fh.write(json.dumps(build_record(delta, model, session_id)) + "\n")
    except Exception as exc:
        _note(log_dir, "could not append to cost.jsonl: %s" % exc)
        return 0

    state[session_id] = totals
    try:
        tmp = state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, state_path)
    except Exception as exc:
        # The record is already written; a lost high-water mark double-counts next turn.
        _note(log_dir, "could not persist high-water state: %s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
