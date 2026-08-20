#!/usr/bin/env python3
"""backfill-partner-burn — reconstruct historical orchestrator burn into cost.jsonl.

`log-partner-burn.py` never wrote a record between 2026-08-15 and 2026-08-20 (issue #30):
it read usage from the Stop payload, which does not carry any. The sessions themselves are
not lost — Claude Code keeps a full transcript per session under
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, and every assistant turn in one
carries a `message.usage` object. This replays those into `logs/cost.jsonl` so the burn
gates and `cost-report.sh` see the system's largest consumer instead of guessing at it.

One record per session (not per turn): the per-turn granularity of the live hook exists to
avoid double-counting a growing transcript, which is not a concern replaying finished ones.

Records carry `"backfilled": true` so they are always separable from live telemetry, and
`ts` is the session's last assistant turn — not now — so week/window bucketing stays honest.

Also seeds `logs/.partner-burn-state.json` with each session's totals, so the live hook
treats them as already-counted and logs only genuinely new turns.

Usage:
  python3 backfill-partner-burn.py --pm-dir /path/to/<project>-pm            # dry run
  python3 backfill-partner-burn.py --pm-dir /path/to/<project>-pm --apply
  ... --transcripts <extra-dir>   # repeatable; e.g. a retired -run workspace's transcripts
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

def encode_project_dir(path):
    """Claude Code's transcript dir name: the absolute cwd with non-alphanumerics hyphenated."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def read_session(path):
    """Sum a finished session's own assistant turns. Mirrors log-partner-burn.read_transcript."""
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "reasoning": 0}
    model = None
    turns = 0
    last_ts = None
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
                continue  # subagent turns belong to agent-spawns.jsonl
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            turns += 1
            model = msg.get("model") or model
            last_ts = rec.get("timestamp") or last_ts
            totals["input"] += usage.get("input_tokens") or 0
            totals["output"] += usage.get("output_tokens") or 0
            totals["cache_read"] += usage.get("cache_read_input_tokens") or 0
            totals["cache_creation"] += usage.get("cache_creation_input_tokens") or 0
            details = usage.get("output_tokens_details") or {}
            if isinstance(details, dict):
                totals["reasoning"] += details.get("reasoning_tokens") or 0
    return totals, model, turns, last_ts


def normalize_ts(ts, fallback_path):
    if ts:
        try:
            cleaned = ts.replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(cleaned).astimezone(
                datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    mtime = datetime.datetime.fromtimestamp(
        os.path.getmtime(fallback_path), datetime.timezone.utc
    )
    return mtime.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pm-dir", required=True, help="the <project>-pm directory")
    ap.add_argument("--transcripts", action="append", default=[],
                    help="extra transcript directory (repeatable)")
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    args = ap.parse_args()

    pm_dir = os.path.abspath(args.pm_dir)
    log_dir = os.path.join(pm_dir, "logs")
    cost_path = os.path.join(log_dir, "cost.jsonl")

    projects_root = os.path.expanduser("~/.claude/projects")
    dirs = [os.path.join(projects_root, encode_project_dir(pm_dir))] + args.transcripts
    dirs = [d for d in dirs if os.path.isdir(d)]
    if not dirs:
        print("no transcript directories found", file=sys.stderr)
        return 1

    # Never double-write: skip sessions cost.jsonl already carries.
    existing = set()
    if os.path.exists(cost_path):
        for line in open(cost_path, errors="ignore"):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("role") == "partner" and rec.get("session_id"):
                existing.add(rec["session_id"])

    records, state, skipped, empty = [], {}, 0, 0
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
            sid = os.path.splitext(os.path.basename(path))[0]
            if sid in existing:
                skipped += 1
                continue
            totals, model, turns, last_ts = read_session(path)
            if turns == 0 or not any(totals.values()):
                empty += 1
                continue
            records.append({
                "ts": normalize_ts(last_ts, path),
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
                "duration_s": None,
                "cost_usd": None,
                "input_tokens": totals["input"],
                "output_tokens": totals["output"],
                "cache_read_tokens": totals["cache_read"],
                "cache_creation_tokens": totals["cache_creation"],
                "reasoning_tokens": totals["reasoning"] or None,
                "session_id": sid,
                "turns": turns,
                "backfilled": True,
                "log": None,
            })
            state[sid] = totals

    records.sort(key=lambda r: r["ts"])
    tot = {k: sum(r[k] or 0 for r in records) for k in
           ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")}
    grand = sum(tot.values())
    print("transcript dirs : %d" % len(dirs))
    print("sessions to add : %d  (skipped %d already present, %d with no usage)"
          % (len(records), skipped, empty))
    print("input           : %15s" % format(tot["input_tokens"], ","))
    print("output          : %15s" % format(tot["output_tokens"], ","))
    print("cache_read      : %15s" % format(tot["cache_read_tokens"], ","))
    print("cache_creation  : %15s" % format(tot["cache_creation_tokens"], ","))
    print("TOTAL           : %15s" % format(grand, ","))
    if records:
        print("range           : %s .. %s" % (records[0]["ts"], records[-1]["ts"]))

    if not args.apply:
        print("\ndry run — re-run with --apply to write")
        return 0
    if not records:
        print("\nnothing to write")
        return 0

    os.makedirs(log_dir, exist_ok=True)
    with open(cost_path, "a") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")

    state_path = os.path.join(log_dir, ".partner-burn-state.json")
    try:
        with open(state_path) as fh:
            prior = json.load(fh)
    except Exception:
        prior = {}
    prior.update(state)
    tmp = state_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(prior, fh)
    os.replace(tmp, state_path)

    print("\nwrote %d records to %s" % (len(records), cost_path))
    print("seeded high-water state for %d sessions" % len(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
