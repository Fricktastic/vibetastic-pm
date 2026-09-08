#!/usr/bin/env python3
"""Provider adapters and durable journal writes for orchestrator telemetry.

Transcript formats are provider-owned and may change.  Parsers therefore return
diagnostics for malformed or unrecognized usage entries instead of inventing zeroes.
The append-only cost journal is the deduplication authority; the state file is retained
only as a compatibility checkpoint for older tooling.
"""

from dataclasses import dataclass, field
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
from typing import List, Optional


STATE_FILE = ".partner-burn-state.json"
LOCK_FILE = ".partner-burn.lock"
USAGE_KEYS = ("input", "output", "cache_read", "cache_creation", "reasoning")
TRANSCRIPT_SCHEMAS = {
    "claude": "claude-transcript-jsonl",
    # Codex documents its transcript as unstable. This adapter supports the local legacy
    # rollout JSONL event stream, not paginated API/export envelopes.
    "codex": "codex-legacy-rollout-jsonl",
}


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_totals():
    return {key: 0 for key in USAGE_KEYS}


def session_namespace(provider, role, session_id):
    return "%s:%s:%s" % (provider, role, session_id)


@dataclass
class TranscriptSnapshot:
    provider: str
    session_id: Optional[str]
    role: Optional[str]
    totals: dict
    model: Optional[str]
    turns: int
    diagnostics: List[str] = field(default_factory=list)


def _nonnegative_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _optional_usage_integer(mapping, key, line_number, diagnostics):
    if key not in mapping:
        return 0
    value = mapping[key]
    if not _nonnegative_integer(value):
        diagnostics.append("line %d has invalid %s" % (line_number, key))
        return None
    return value


def _read_records(path):
    records = []
    diagnostics = []
    with open(path, errors="ignore") as fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                diagnostics.append("line %d is malformed JSON" % line_number)
                continue
            if not isinstance(record, dict):
                diagnostics.append("line %d is not a JSON object" % line_number)
                continue
            records.append((line_number, record))
    return records, diagnostics


def parse_claude_transcript(path, fallback_session_id=None, fallback_model=None):
    records, diagnostics = _read_records(path)
    totals = empty_totals()
    model = fallback_model
    turns = 0

    for line_number, record in records:
        if record.get("isSidechain"):
            continue
        message = record.get("message") or {}
        usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict):
            if record.get("type") == "assistant":
                diagnostics.append("line %d assistant entry is missing usage" % line_number)
            continue
        if not _nonnegative_integer(usage.get("input_tokens")) or not _nonnegative_integer(
            usage.get("output_tokens")
        ):
            diagnostics.append("line %d has unknown Claude usage schema" % line_number)
            continue

        cache_read = _optional_usage_integer(
            usage, "cache_read_input_tokens", line_number, diagnostics
        )
        cache_creation = _optional_usage_integer(
            usage, "cache_creation_input_tokens", line_number, diagnostics
        )
        details = usage.get("output_tokens_details") or {}
        if not isinstance(details, dict):
            diagnostics.append("line %d has invalid output_tokens_details" % line_number)
            continue
        reasoning = _optional_usage_integer(details, "reasoning_tokens", line_number, diagnostics)
        if cache_read is None or cache_creation is None or reasoning is None:
            continue

        values = {
            "input": usage["input_tokens"],
            "output": usage["output_tokens"],
            "cache_read": cache_read,
            "cache_creation": cache_creation,
            "reasoning": reasoning,
        }
        if not any(values.values()):
            diagnostics.append("line %d has zero Claude usage" % line_number)
            continue
        turns += 1
        model = message.get("model") or model
        for key in USAGE_KEYS:
            totals[key] += values[key]

    return TranscriptSnapshot(
        provider="claude",
        session_id=fallback_session_id,
        role="partner",
        totals=totals,
        model=model,
        turns=turns,
        diagnostics=diagnostics,
    )


def _codex_source_role(source):
    if isinstance(source, dict) and "subagent" in source:
        return "subagent"
    if isinstance(source, str) and source:
        normalized = source.lower().replace("-", "_")
        if normalized == "subagent" or normalized.startswith("subagent_"):
            return "subagent"
        return "partner"
    return None


def parse_codex_transcript(path, fallback_session_id=None, fallback_model=None):
    """Read Codex's cumulative token_count event stream.

    This adapter intentionally recognizes only the bounded schema used here.  Codex
    transcripts are not a stable public interface, so schema drift is diagnosed rather
    than silently interpreted.
    """
    records, diagnostics = _read_records(path)
    session_id = None
    role = None
    model = fallback_model
    turns = 0
    totals = empty_totals()

    for line_number, record in records:
        record_type = record.get("type")
        payload = record.get("payload")
        collection = next(
            (
                key
                for key in ("data", "items", "events")
                if isinstance(record.get(key), list)
            ),
            None,
        )
        if collection and any(
            key in record for key in ("next_cursor", "cursor", "has_more", "next")
        ):
            diagnostics.append(
                "line %d has unsupported paginated Codex transcript format; "
                "expected legacy rollout JSONL" % line_number
            )
            continue
        if record_type == "session_meta":
            if not isinstance(payload, dict):
                diagnostics.append("line %d has invalid session_meta payload" % line_number)
                continue
            identity = payload.get("id")
            if isinstance(identity, str) and identity:
                session_id = identity
            else:
                diagnostics.append("line %d session_meta is missing id" % line_number)
            source_role = _codex_source_role(payload.get("source"))
            if source_role is None:
                diagnostics.append("line %d has unknown session_meta source" % line_number)
            else:
                role = source_role
            continue
        if record_type == "turn_context":
            if not isinstance(payload, dict):
                diagnostics.append("line %d has invalid turn_context payload" % line_number)
                continue
            context_model = payload.get("model")
            if isinstance(context_model, str) and context_model:
                model = context_model
            elif context_model is not None:
                diagnostics.append("line %d has invalid turn_context model" % line_number)
            continue
        if record_type != "event_msg" or not isinstance(payload, dict):
            continue
        if payload.get("type") != "token_count":
            continue

        info = payload.get("info")
        cumulative = info.get("total_token_usage") if isinstance(info, dict) else None
        if not isinstance(cumulative, dict):
            diagnostics.append("line %d token_count is missing total_token_usage" % line_number)
            continue
        required = (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
        missing = [key for key in required if key not in cumulative]
        if missing:
            diagnostics.append(
                "line %d has unknown usage schema; missing %s"
                % (line_number, ", ".join(missing))
            )
            continue
        if any(not _nonnegative_integer(cumulative[key]) for key in required):
            diagnostics.append("line %d has unknown usage schema" % line_number)
            continue
        raw_input = cumulative["input_tokens"]
        cached = cumulative["cached_input_tokens"]
        output = cumulative["output_tokens"]
        reasoning = cumulative["reasoning_output_tokens"]
        if cached > raw_input or reasoning > output:
            diagnostics.append("line %d has unknown usage schema" % line_number)
            continue

        candidate = {
            "input": raw_input - cached,
            "output": output,
            "cache_read": cached,
            "cache_creation": 0,
            "reasoning": reasoning,
        }
        if not any(candidate.values()):
            diagnostics.append("line %d has zero Codex usage" % line_number)
            continue
        totals = candidate
        turns += 1

    if session_id is None:
        session_id = fallback_session_id
        diagnostics.append("Codex transcript is missing session_meta identity; used hook session_id")

    return TranscriptSnapshot(
        provider="codex",
        session_id=session_id,
        role=role,
        totals=totals,
        model=model,
        turns=turns,
        diagnostics=diagnostics,
    )


def parse_transcript(provider, path, fallback_session_id=None, fallback_model=None):
    if provider == "claude":
        return parse_claude_transcript(path, fallback_session_id, fallback_model)
    if provider == "codex":
        return parse_codex_transcript(path, fallback_session_id, fallback_model)
    raise ValueError("unsupported telemetry provider: %s" % provider)


def build_record(
    delta,
    model,
    session_id,
    duration_s=None,
    provider="claude",
    role="partner",
    transcript_schema=None,
):
    """Build the common cost.jsonl shape used by dispatch and partner telemetry."""
    selected_model = model or ("opus" if provider == "claude" else "unknown")
    return {
        "ts": now(),
        "role": role,
        "backend": provider,
        "prompt": None,
        "model": selected_model,
        "primary_model": selected_model,
        "fallback_used": False,
        "stall_retries": 0,
        "tier": None,
        "attempts": 1,
        "verify_passed": None,
        "exit": 0,
        "duration_s": duration_s,
        "cost_usd": None,
        "input_tokens": delta["input"],
        "output_tokens": delta["output"],
        "cache_read_tokens": delta["cache_read"],
        "cache_creation_tokens": delta["cache_creation"],
        "reasoning_tokens": delta["reasoning"] or None,
        "quota_proxy_tokens": delta["input"] + delta["output"],
        "session_id": session_id,
        "session_namespace": session_namespace(provider, role, session_id),
        "transcript_schema": transcript_schema or TRANSCRIPT_SCHEMAS.get(provider),
        "log": None,
    }


def _journal_highwater(cost_path, snapshot, diagnostics):
    seen = empty_totals()
    if not os.path.exists(cost_path):
        return seen
    namespace = session_namespace(snapshot.provider, snapshot.role, snapshot.session_id)
    with open(cost_path, errors="ignore") as fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                diagnostics.append("cost.jsonl line %d is malformed JSON" % line_number)
                continue
            if not isinstance(record, dict):
                continue
            record_namespace = record.get("session_namespace")
            if record_namespace:
                matches = record_namespace == namespace
            else:
                matches = (
                    record.get("backend") == snapshot.provider
                    and record.get("role") == snapshot.role
                    and record.get("session_id") == snapshot.session_id
                )
            if not matches:
                continue
            fields = {
                "input": "input_tokens",
                "output": "output_tokens",
                "cache_read": "cache_read_tokens",
                "cache_creation": "cache_creation_tokens",
                "reasoning": "reasoning_tokens",
            }
            for key, field_name in fields.items():
                value = record.get(field_name) or 0
                if _nonnegative_integer(value):
                    seen[key] += value
                else:
                    diagnostics.append(
                        "cost.jsonl line %d has invalid %s" % (line_number, field_name)
                    )
    return seen


def _write_compatibility_state(state_path, snapshot):
    try:
        with open(state_path) as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}
    state[session_namespace(snapshot.provider, snapshot.role, snapshot.session_id)] = snapshot.totals
    if snapshot.provider == "claude" and snapshot.role == "partner":
        state[snapshot.session_id] = snapshot.totals
    tmp = state_path + ".tmp.%d" % os.getpid()
    with open(tmp, "w") as fh:
        json.dump(state, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, state_path)


@contextmanager
def locked_journal(log_dir):
    """Hold the cooperative lock shared by every framework cost writer."""
    os.makedirs(log_dir, exist_ok=True)
    lock_path = os.path.join(log_dir, LOCK_FILE)
    with open(lock_path, "a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        yield


def _append_record_unlocked(cost_path, record):
    """Append one complete record, separating it from a torn final line if necessary."""
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    diagnostics = []
    with open(cost_path, "a+b") as cost_fh:
        cost_fh.seek(0, os.SEEK_END)
        size = cost_fh.tell()
        if size:
            cost_fh.seek(-1, os.SEEK_END)
            if cost_fh.read(1) != b"\n":
                cost_fh.seek(0, os.SEEK_END)
                cost_fh.write(b"\n")
                diagnostics.append(
                    "repaired torn final cost.jsonl line before durable append"
                )
        cost_fh.seek(0, os.SEEK_END)
        cost_fh.write(encoded)
        cost_fh.flush()
        os.fsync(cost_fh.fileno())
    return diagnostics


def append_journal_record(log_dir, record):
    """Validate and durably append one arbitrary cost record under the shared lock."""
    if not isinstance(record, dict):
        raise ValueError("cost record must be a JSON object")
    cost_path = os.path.join(log_dir, "cost.jsonl")
    with locked_journal(log_dir):
        return _append_record_unlocked(cost_path, record)


def append_snapshot(log_dir, snapshot):
    """Append an unseen delta while holding the journal lock.

    Returns ``(record_or_none, diagnostics)``.  State checkpoint failure does not undo a
    durable journal append; replay remains safe because the journal is rescanned next time.
    """
    diagnostics = list(snapshot.diagnostics)
    if not snapshot.session_id:
        diagnostics.append("transcript has no session identity; wrote nothing")
        return None, diagnostics
    if snapshot.role not in ("partner", "subagent"):
        diagnostics.append("transcript has no trusted provider role; wrote nothing")
        return None, diagnostics
    if snapshot.turns == 0 or not any(snapshot.totals.values()):
        diagnostics.append("transcript carried no usage; wrote nothing")
        return None, diagnostics

    cost_path = os.path.join(log_dir, "cost.jsonl")
    state_path = os.path.join(log_dir, STATE_FILE)
    with locked_journal(log_dir):
        seen = _journal_highwater(cost_path, snapshot, diagnostics)
        delta = {key: max(snapshot.totals[key] - seen[key], 0) for key in USAGE_KEYS}
        record = None
        if any(delta.values()):
            record = build_record(
                delta,
                snapshot.model,
                snapshot.session_id,
                provider=snapshot.provider,
                role=snapshot.role,
                transcript_schema=TRANSCRIPT_SCHEMAS.get(snapshot.provider),
            )
            diagnostics.extend(_append_record_unlocked(cost_path, record))
        try:
            _write_compatibility_state(state_path, snapshot)
        except Exception as exc:
            diagnostics.append("could not persist high-water state: %s" % exc)
        return record, diagnostics
