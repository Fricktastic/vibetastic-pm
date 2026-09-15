"""Tolerant, dependency-free parsing for the Vibetastic View v1 contract."""

from __future__ import annotations

import hashlib
import errno
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_STATUS = {
    "in_progress": ("active", None),
    "building": ("active", None),
    "blocked": ("blocked", None),
    "failed": ("blocked", None),
    "done": ("done", None),
    "superseded": ("closed", "superseded"),
    "split": ("closed", "split"),
    "wontfix": ("closed", "wontfix"),
}

_TASK_REQUIRED_FIELDS = (
    "stage",
    "title",
    "agent",
    "status",
    "depends_on",
    "failure_count",
)
_STAGE_STATES = {
    "pending": "pending",
    "in_progress": "active",
    "building": "active",
    "done": "done",
}
_LIST_ITEM = re.compile(r"^\s+-\s*(?P<body>.*)$")
_ITEM_START = re.compile(r"^\s+-\s*id:\s*(?P<value>.*)$")
_FIELD = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_-]*):\s*(?P<value>.*)$")

_SOURCE_PATHS = (
    "PROJECT.md", "PLAN.md", "TASK_LOG.md", "HANDOFF.md",
    ".orchestrator/lease.json", ".orchestrator/runs.json", "logs/runs.jsonl",
    "logs/cost.jsonl",
)
_LEASE_FIELDS = ("provider", "session", "profile", "acquired_at", "renewed_at")
_RUN_FIELDS = ("run_id", "task_id", "status", "phase", "role", "backend", "model", "tier", "started_at", "finished_at", "heartbeat_at")
_CAPACITY_FIELDS = ("run_id", "task_id", "role", "backend", "model", "tier", "ts", "input_tokens", "output_tokens", "quota_proxy_tokens", "cost_usd")
_SCALARS = (str, int, float, bool)
_EXPLICIT_TYPES = {"user_escalation", "gate_requested", "device_evidence_requested", "verification_requested", "operator_action_requested"}


class UnrecoverableSourceRead(RuntimeError):
    """An expected fixed source could not be safely read."""

    def __init__(self, path: str, error: BaseException):
        self.path = path
        self.error_text = str(error)
        super().__init__(f"{path}: {self.error_text}")


def canonical_task_status(source: str, dependencies_done: bool) -> tuple[str, str | None]:
    """Map a source PLAN status to the View task state and optional resolution."""
    if source == "pending":
        return ("ready" if dependencies_done else "backlog", None)
    return TASK_STATUS.get(source, ("backlog", None))


def parse_plan(text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse a PLAN.md frontmatter block without requiring valid YAML.

    The framework has historically accepted YAML-like PLAN files that are not always valid
    YAML. This parser intentionally follows the linter's line-oriented model: it extracts
    supported list items, retains source facts and provenance, and reports drift in warnings.
    """
    digest = hashlib.sha256(text.encode()).hexdigest()
    lines = text.splitlines()
    warnings: list[dict[str, Any]] = []
    frontmatter = _frontmatter_lines(lines, warnings)
    top_level = _top_level_fields(frontmatter)

    stages = _parse_stages(frontmatter, digest, warnings)
    raw_tasks = _parse_task_fields(frontmatter, digest, warnings)
    task_ids = {task["id"] for task in raw_tasks if task["valid_identity"]}
    tasks = _normalize_tasks(raw_tasks, task_ids, warnings)

    plan = {
        "project": top_level.get("project"),
        "created": top_level.get("created"),
        "updated": top_level.get("updated"),
        "stages": stages,
        "tasks": tasks,
        "provenance": {"path": "PLAN.md", "sha256": digest},
    }
    return plan, warnings


def _frontmatter_lines(
    lines: list[str], warnings: list[dict[str, Any]]
) -> list[tuple[int, str]]:
    if not lines or lines[0] != "---":
        _warning(
            warnings,
            "missing_frontmatter",
            "PLAN.md has no YAML frontmatter opening delimiter",
            line=1,
        )
        return list(enumerate(lines, 1))

    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return [(line_no, line) for line_no, line in enumerate(lines[1:index], 2)]

    _warning(
        warnings,
        "unclosed_frontmatter",
        "PLAN.md frontmatter is not closed; parsing the remaining lines",
        line=1,
    )
    return [(line_no, line) for line_no, line in enumerate(lines[1:], 2)]


def _top_level_fields(lines: list[tuple[int, str]]) -> dict[str, str | None]:
    fields: dict[str, str | None] = {}
    for _, line in lines:
        if line.startswith((" ", "\t", "-")):
            continue
        match = _FIELD.match(line)
        if match:
            fields[match.group("name")] = _scalar(match.group("value"))
    return fields


def _parse_stages(
    lines: list[tuple[int, str]], digest: str, warnings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    section = _section(lines, "stages", stop_at="tasks")
    if section is None:
        _warning(warnings, "missing_stages_section", "PLAN.md has no stages section")
        return []

    stages: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    for ordinal, item in enumerate(_items(section), 1):
        fields, field_lines = _fields(item)
        source_id = fields.get("id")
        stage_id, valid_identity, entry_key = _unique_id(
            source_id, seen_ids, "stage", ordinal, item[0][0], warnings
        )
        source_status = fields.get("status")
        state = _STAGE_STATES.get(source_status) if source_status is not None else None
        if source_status is not None and state is None:
            _warning(
                warnings,
                "unknown_stage_status",
                f"Stage {stage_id or '<unknown>'} has unknown status '{source_status}'",
                line=field_lines.get("status"),
                stage_id=stage_id,
                field="status",
            )
        for field in ("name", "status"):
            if fields.get(field) is None:
                _warning(
                    warnings,
                    "missing_required_field",
                    f"Stage {stage_id or '<unknown>'} is missing required field '{field}'",
                    line=item[0][0],
                    stage_id=stage_id,
                    field=field,
                )
        stages.append(
            {
                "id": stage_id,
                "source_id": source_id,
                "entry_key": entry_key,
                "valid_identity": valid_identity,
                "name": fields.get("name"),
                "source_status": source_status,
                "state": state,
                "field_lines": field_lines,
                "provenance": _provenance(digest, item[0][0]),
            }
        )
    return stages


def _parse_task_fields(
    lines: list[tuple[int, str]], digest: str, warnings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    section = _section(lines, "tasks")
    if section is None:
        _warning(warnings, "missing_tasks_section", "PLAN.md has no tasks section")
        return []

    tasks: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    for ordinal, item in enumerate(_items(section), 1):
        fields, field_lines = _fields(item)
        source_id = fields.get("id")
        task_id, valid_identity, entry_key = _unique_id(
            source_id, seen_ids, "task", ordinal, item[0][0], warnings
        )
        depends_value = fields.get("depends_on")
        dependencies, dependencies_valid = _parse_dependencies(depends_value)
        if not dependencies_valid and depends_value is not None:
            _warning(
                warnings,
                "nested_dependency_list",
                f"Task {task_id} has malformed nested depends_on value '{depends_value}'",
                line=field_lines.get("depends_on"),
                task_id=task_id,
                field="depends_on",
            )
        for field in _TASK_REQUIRED_FIELDS:
            if field not in fields or fields.get(field) is None:
                _warning(
                    warnings,
                    "missing_required_field",
                    f"Task {task_id} is missing required field '{field}'",
                    line=item[0][0],
                    task_id=task_id,
                    field=field,
                )
        tasks.append(
            {
                "id": task_id,
                "source_id": source_id,
                "entry_key": entry_key,
                "valid_identity": valid_identity,
                "stage": fields.get("stage"),
                "title": fields.get("title"),
                "agent": fields.get("agent"),
                "source_status": fields.get("status"),
                "dependencies": dependencies,
                "dependencies_valid": dependencies_valid,
                "failure_count": fields.get("failure_count"),
                "tier": fields.get("tier"),
                "verify_tier": fields.get("verify_tier"),
                "field_lines": field_lines,
                "provenance": _provenance(digest, item[0][0]),
            }
        )
    return tasks


def _normalize_tasks(
    tasks: list[dict[str, Any]], task_ids: set[str], warnings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # Dependency readiness must not depend on whether a prerequisite happens
    # to appear earlier in PLAN.md. Only a source ``done`` status satisfies a
    # dependency; ``ready`` means the task is eligible, not completed.
    completed = {
        task["id"]
        for task in tasks
        if task["valid_identity"] and task["source_status"] == "done"
    }

    normalized: list[dict[str, Any]] = []
    for task in tasks:
        missing_dependencies = [dep for dep in task["dependencies"] if dep not in task_ids]
        for dependency in missing_dependencies:
            _warning(
                warnings,
                "unknown_dependency",
                f"Task {task['id']} depends on unknown task '{dependency}'",
                line=task["field_lines"].get("depends_on"),
                task_id=task["id"],
                field="depends_on",
                dependency=dependency,
            )
        dependencies_done = task["dependencies_valid"] and not missing_dependencies and all(
            dependency in completed for dependency in task["dependencies"]
        )
        state, resolution = canonical_task_status(task["source_status"], dependencies_done)
        if task["source_status"] is not None and task["source_status"] not in {
            "pending",
            *TASK_STATUS,
        }:
            _warning(
                warnings,
                "unknown_task_status",
                f"Task {task['id']} has unknown status '{task['source_status']}'",
                line=task["field_lines"].get("status"),
                task_id=task["id"],
                field="status",
            )
        task["state"] = state
        task["resolution"] = resolution
        task["phase"] = task["source_status"] if state == "active" else None
        normalized.append(task)
    return normalized


def _section(
    lines: list[tuple[int, str]], name: str, stop_at: str | None = None
) -> list[tuple[int, str]] | None:
    start = None
    for index, (_, line) in enumerate(lines):
        if line.rstrip() == f"{name}:":
            start = index + 1
            break
    if start is None:
        return None
    if stop_at is None:
        return lines[start:]
    for index in range(start, len(lines)):
        if lines[index][1].rstrip() == f"{stop_at}:":
            return lines[start:index]
    return lines[start:]


def _items(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    items: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] | None = None
    item_indent: int | None = None
    for line_no, line in lines:
        list_match = _LIST_ITEM.match(line)
        indent = len(line) - len(line.lstrip()) if list_match else None
        if list_match and (item_indent is None or indent == item_indent):
            if item_indent is None:
                item_indent = indent
            current = [(line_no, line)]
            items.append(current)
        elif current is not None:
            current.append((line_no, line))
    return items


def _fields(item: list[tuple[int, str]]) -> tuple[dict[str, str | None], dict[str, int]]:
    fields: dict[str, str | None] = {}
    lines: dict[str, int] = {}
    for line_no, line in item:
        item_match = _ITEM_START.match(line)
        if item_match:
            fields["id"] = _scalar(item_match.group("value"))
            lines["id"] = line_no
            continue
        list_match = _LIST_ITEM.match(line)
        if list_match:
            # Retain malformed entries such as ``- title: ...`` so callers
            # can display a partial record and a structured missing-id warning.
            line = list_match.group("body")
        match = _FIELD.match(line)
        if not match:
            continue
        name = match.group("name")
        value = _scalar(match.group("value"))
        fields[name] = value
        lines[name] = line_no
    return fields, lines


def _scalar(value: str) -> str | None:
    value = value.strip()
    if not value or value in {"null", "~"}:
        return None
    if value[0:1] in {"'", '"'}:
        quote = value[0]
        for index in range(1, len(value)):
            if value[index] != quote or value[index - 1] == "\\":
                continue
            remainder = value[index + 1 :].strip()
            if not remainder or remainder.startswith("#"):
                return value[1:index]
            break
    return value.split(" #", 1)[0].strip()


def _dependencies(value: str | None) -> list[str]:
    return _parse_dependencies(value)[0]


def _parse_dependencies(value: str | None) -> tuple[list[str], bool]:
    if value is None or value == "[]":
        return [], value is not None
    stripped = value.strip()
    nested_list = stripped.startswith("[[") and stripped[-2:] == "]]"
    if nested_list:
        return [], False
    return [part.strip().strip("\"'") for part in stripped.strip("[]").split(",") if part.strip()], True


def _unique_id(
    source_id: str | None,
    seen_ids: dict[str, int],
    kind: str,
    ordinal: int,
    line: int,
    warnings: list[dict[str, Any]],
) -> tuple[str | None, bool, str | None]:
    if not source_id:
        entry_key = f"invalid-entry:{line}"
        _warning(
            warnings,
            "missing_required_field",
            f"{kind.title()} entry {entry_key} is missing required field 'id'",
            line=line,
            **{f"{kind}_id": None, "entry_key": entry_key},
            field="id",
        )
        return None, False, entry_key
    count = seen_ids.get(source_id, 0) + 1
    seen_ids[source_id] = count
    if count == 1:
        return source_id, True, None
    entry_key = f"invalid-entry:{line}"
    _warning(
        warnings,
        f"duplicate_{kind}_id",
        f"{kind.title()} {source_id} is duplicated; retaining partial entry {entry_key}",
        line=line,
        **{f"{kind}_id": source_id},
        entry_key=entry_key,
        field="id",
    )
    return source_id, False, entry_key


def _provenance(digest: str, line: int) -> dict[str, Any]:
    return {"path": "PLAN.md", "sha256": digest, "line": line}


def _warning(
    warnings: list[dict[str, Any]], code: str, message: str, line: int | None = None, **details: Any
) -> None:
    warning: dict[str, Any] = {"code": code, "message": message, "path": "PLAN.md"}
    if line is not None:
        warning["line"] = line
    warning.update(details)
    warnings.append(warning)


def sanitize_lease(lease: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the deliberately small, non-secret lease projection."""
    if not isinstance(lease, dict):
        return None
    return {key: lease.get(key) if _is_scalar(lease.get(key)) else None for key in _LEASE_FIELDS}


def build_snapshot(pm_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    """Build the stable, sanitized View v1 read projection without mutating PM state."""
    generated = _utc_timestamp(now or datetime.now(timezone.utc))
    captures, sources, capture_warnings = _capture_sources(Path(pm_dir), generated)
    warnings = list(capture_warnings)
    plan_text = captures["PLAN.md"]["text"] if captures["PLAN.md"]["present"] else ""
    plan, plan_warnings = parse_plan(plan_text)
    warnings.extend(plan_warnings)
    project_id, project_line = _project_id(captures["PROJECT.md"]["text"])
    project_provenance = _provenance_for(captures["PROJECT.md"], "PROJECT.md", project_line)
    project = {"id": project_id, "display_name": project_id, "provenance": project_provenance}
    lease = _json_object(captures[".orchestrator/lease.json"], ".orchestrator/lease.json", warnings)
    ownership = sanitize_lease(lease)
    reservations = _reservation_rows(captures[".orchestrator/runs.json"], warnings)
    journals = _journal_rows(captures["logs/runs.jsonl"], warnings)
    runs = _join_runs(reservations, journals)
    valid_task_ids = {task["id"] for task in plan["tasks"] if task["valid_identity"] and task["id"]}
    task_runs: dict[str, set[str]] = {task_id: set() for task_id in valid_task_ids}
    for run in runs:
        task_id = run["task_id"]
        if task_id in valid_task_ids:
            task_runs[task_id].add(run["run_id"])
        else:
            _view_warning(warnings, "missing_task_linkage", "Run has no authoritative PLAN task", ".orchestrator/runs.json" if run["source"] != "journal" else "logs/runs.jsonl", task_id=task_id)
    tasks = []
    for task in plan["tasks"]:
        copy = dict(task)
        copy["runs"] = sorted(task_runs.get(task["id"], set())) if task["valid_identity"] else []
        tasks.append(copy)
    capacity = {"usage": _capacity_rows(captures["logs/cost.jsonl"], warnings), "provenance": _provenance_for(captures["logs/cost.jsonl"], "logs/cost.jsonl", 1)}
    snapshot = {
        "contract": "vibetastic-view/v1", "schema_version": 1, "generated_at": generated,
        "generation": _prior_generation(Path(pm_dir), warnings), "project": project,
        "plan": {key: plan[key] for key in ("project", "created", "updated", "provenance")},
        "stages": plan["stages"], "tasks": tasks, "recommended_next": [], "attention": [],
        "ownership": ownership, "runs": runs, "capacity": capacity, "sources": sources,
        "warnings": [],
    }
    explicit = _explicit_attention(captures["TASK_LOG.md"], warnings)
    snapshot["warnings"] = _sort_warnings(warnings)
    snapshot["attention"] = _derive_attention(snapshot, generated, explicit, captures["HANDOFF.md"].get("mtime"))
    return snapshot


def derive_attention(snapshot: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Return the deterministic attention list for an already-built snapshot."""
    if snapshot.get("attention") is not None:
        return list(snapshot["attention"])
    return _derive_attention(snapshot, _utc_timestamp(now), [])


def write_snapshot(pm_dir: Path, snapshot: dict[str, Any]) -> Path:
    """Atomically write the sole View v1 output; no events are produced here."""
    target = Path(pm_dir) / ".orchestrator" / "view" / "v1" / "snapshot.json"
    _atomic_replace(target, (json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
    return target


def _capture_sources(pm_dir: Path, read_at: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    last: tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]] | None = None
    last_after: dict[str, tuple[bool, str | None]] | None = None
    for attempt in range(3):
        warnings: list[dict[str, Any]] = []
        captures = {path: _read_source(pm_dir, path, warnings) for path in _SOURCE_PATHS}
        before = {path: (item["present"], item["sha256"]) for path, item in captures.items()}
        after = {path: _fingerprint_source(pm_dir, path) for path in _SOURCE_PATHS}
        sources = {path: {"present": item["present"], "sha256": item["sha256"], "read_at": read_at} for path, item in captures.items()}
        result = (captures, sources, warnings)
        if before == after:
            return result
        last = result
        last_after = after
    assert last is not None
    captures, sources, warnings = last
    assert last_after is not None
    for path in _SOURCE_PATHS:
        if (captures[path]["present"], captures[path]["sha256"]) != last_after[path]:
            _view_warning(warnings, "unstable_source_read", "Source changed during capture", path)
    return captures, sources, warnings


def _read_source(pm_dir: Path, relative: str, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        data = (pm_dir / relative).read_bytes()
        text = data.decode("utf-8", errors="replace")
        if "\ufffd" in text:
            _view_warning(warnings, "text_replacement", "UTF-8 replacement character used while decoding source", relative)
        return {"present": True, "data": data, "text": text, "sha256": hashlib.sha256(data).hexdigest(), "mtime": (pm_dir / relative).stat().st_mtime}
    except OSError as error:
        if _absent_error(error):
            _view_warning(warnings, "missing_source", "Expected source is absent", relative)
            return {"present": False, "data": b"", "text": "", "sha256": None, "mtime": None}
        raise UnrecoverableSourceRead(relative, _safe_os_error(error)) from error


def _fingerprint_source(pm_dir: Path, relative: str) -> tuple[bool, str | None]:
    try:
        data = (pm_dir / relative).read_bytes()
        return True, hashlib.sha256(data).hexdigest()
    except OSError as error:
        if _absent_error(error):
            return False, None
        raise UnrecoverableSourceRead(relative, _safe_os_error(error)) from error


def _absent_error(error: OSError) -> bool:
    return isinstance(error, FileNotFoundError) or error.errno in {errno.ENOENT, errno.ENOTDIR}


def _safe_os_error(error: OSError) -> RuntimeError:
    return RuntimeError(f"[Errno {error.errno}] {error.strerror or 'source read failed'}")


def _json_object(capture: dict[str, Any], path: str, warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not capture["present"]:
        return None
    try:
        value = json.loads(capture["text"])
    except json.JSONDecodeError:
        _view_warning(warnings, "malformed_json", "JSON source could not be decoded", path)
        return None
    if not isinstance(value, dict):
        _view_warning(warnings, "invalid_source_shape", "JSON source must be an object", path)
        return None
    return value


def _reservation_rows(capture: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    value = _json_object(capture, ".orchestrator/runs.json", warnings)
    if value is None:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    records = value.values() if all(isinstance(v, dict) for v in value.values()) else [value]
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("run_id"), str) or not record["run_id"]:
            _view_warning(warnings, "invalid_run_record", "Reservation lacks a run_id", ".orchestrator/runs.json")
            continue
        rows[record["run_id"]] = {field: record.get(field) if _is_scalar(record.get(field)) else None for field in _RUN_FIELDS}
        rows[record["run_id"]]["provenance"] = [_provenance_for(capture, ".orchestrator/runs.json", 1)]
    return rows


def _journal_rows(capture: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not capture["present"]:
        return rows
    for line_no, line in enumerate(capture["text"].splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            _view_warning(warnings, "malformed_jsonl", "JSONL line could not be decoded", "logs/runs.jsonl", line_no)
            continue
        if not isinstance(record, dict) or not isinstance(record.get("run_id"), str) or record.get("event") not in {"run_start", "run_finish"}:
            _view_warning(warnings, "invalid_run_record", "Invalid run journal record", "logs/runs.jsonl", line_no)
            continue
        run_id = record["run_id"]
        row = rows.setdefault(run_id, {field: None for field in _RUN_FIELDS} | {"provenance": []})
        if record["event"] == "run_start":
            mapping = {"ts_start": "started_at"}
        else:
            mapping = {"ts_finish": "finished_at"}
        for field in ("run_id", "task_id", "role", "backend", "model", "tier", "status", "phase", "heartbeat_at"):
            if field in record and _is_scalar(record.get(field)):
                row[field] = record[field]
        for source, target in mapping.items():
            if source in record and _is_scalar(record.get(source)):
                row[target] = record[source]
        row["provenance"].append(_provenance_for(capture, "logs/runs.jsonl", line_no))
    return rows


def _join_runs(reservations: dict[str, dict[str, Any]], journals: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for run_id in sorted(set(reservations) | set(journals)):
        reservation, journal = reservations.get(run_id), journals.get(run_id)
        row = {field: None for field in _RUN_FIELDS}
        if journal:
            row.update({field: journal.get(field) for field in _RUN_FIELDS})
        if reservation:
            for field in _RUN_FIELDS:
                if reservation.get(field) is not None:
                    row[field] = reservation[field]
        row["run_id"] = run_id
        row["source"] = "reservation+journal" if reservation and journal else "reservation" if reservation else "journal"
        row["provenance"] = (reservation or {}).get("provenance", []) + (journal or {}).get("provenance", [])
        result.append(row)
    return result


def _capacity_rows(capture: dict[str, Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    if not capture["present"]:
        return result
    for line_no, line in enumerate(capture["text"].splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            _view_warning(warnings, "malformed_jsonl", "JSONL line could not be decoded", "logs/cost.jsonl", line_no)
            continue
        if not isinstance(record, dict):
            _view_warning(warnings, "invalid_cost_record", "Invalid cost record", "logs/cost.jsonl", line_no)
            continue
        row = {field: record.get(field) if _is_scalar(record.get(field)) else None for field in _CAPACITY_FIELDS}
        for field in ("input_tokens", "output_tokens", "quota_proxy_tokens", "cost_usd"):
            if not isinstance(row[field], (int, float)) or isinstance(row[field], bool):
                row[field] = None
        row["provenance"] = [_provenance_for(capture, "logs/cost.jsonl", line_no)]
        result.append(row)
    return sorted(result, key=lambda row: tuple(str(row.get(key) or "") for key in ("backend", "model", "role", "task_id", "run_id")))


def _explicit_attention(capture: dict[str, Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    lines = capture["text"].splitlines()
    heading = re.compile(r"^###\s+(\S+)\s+·\s+([a-z_]+)\s*$")
    for index, line in enumerate(lines):
        match = heading.match(line)
        if not match or match.group(2) not in _EXPLICIT_TYPES:
            continue
        end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith("### ")), len(lines))
        fields = {m.group(1): m.group(2).strip().strip("\"'") for line in lines[index + 1:end] if (m := re.match(r"^\s*(task_id|reason):\s*(.*?)\s*$", line))}
        task_id, reason = fields.get("task_id"), fields.get("reason")
        observed = _parse_timestamp(match.group(1))
        if not task_id or not reason or observed is None:
            _view_warning(warnings, "malformed_explicit_attention", "Explicit attention block is incomplete or has an invalid timestamp", "TASK_LOG.md", index + 1)
            continue
        evidence = [_provenance_for(capture, "TASK_LOG.md", index + 1)]
        event = match.group(2)
        result.append({"id": f"explicit:{event}:{task_id}:{index + 1}", "classification": "explicit", "task_id": task_id, "kind": event, "reason": reason, "rule": None, "observed_at": observed, "evidence": evidence})
    return sorted(result, key=lambda item: item["id"])


def _derive_attention(snapshot: dict[str, Any], now: str, explicit: list[dict[str, Any]], handoff_mtime: float | None = None) -> list[dict[str, Any]]:
    inferred: list[dict[str, Any]] = []
    existing = {(item["task_id"], item["kind"]) for item in explicit if item.get("task_id") is not None}
    def add(rule: str, task_id: str | None, kind: str, reason: str, evidence: list[dict[str, Any]]) -> None:
        if task_id is not None and (task_id, kind) in existing:
            return
        payload = {"rule": rule, "task_id": task_id, "kind": kind, "evidence": evidence}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
        inferred.append({"id": f"inferred:{rule}:{task_id or 'none'}:{fingerprint}", "classification": "inferred", "task_id": task_id, "kind": kind, "reason": reason, "rule": rule, "observed_at": now, "evidence": evidence})
    now_dt = _datetime_timestamp(now)
    for run in snapshot.get("runs", []):
        evidence = run.get("provenance") or [{"path": "logs/runs.jsonl", "sha256": None, "line": 1}]
        if run.get("source") == "journal":
            add("orphaned_run", run.get("task_id"), "orphaned_run", "Run is present only in the journal", evidence)
        if run.get("source") in {"reservation", "reservation+journal"} and run.get("status") == "active":
            heartbeat = _datetime_timestamp(run.get("heartbeat_at")) if isinstance(run.get("heartbeat_at"), str) else None
            if heartbeat and now_dt and (now_dt - heartbeat).total_seconds() > 900:
                add("stale_heartbeat", run.get("task_id"), "stale_heartbeat", "Active reservation heartbeat is over 15 minutes old", evidence)
    for warning in snapshot.get("warnings", []):
        path = warning.get("path") if isinstance(warning.get("path"), str) else "PLAN.md"
        evidence = [{"path": path, "sha256": snapshot.get("sources", {}).get(path, {}).get("sha256"), "line": warning.get("line", 1) or 1}]
        code = warning.get("code")
        if code == "missing_task_linkage":
            add("missing_task_linkage", warning.get("task_id"), "missing_task_linkage", warning.get("message", "Missing task linkage"), evidence)
        elif code in {"unknown_task_status", "unknown_stage_status"}:
            add("unknown_vocabulary", warning.get("task_id") or warning.get("stage_id"), "unknown_vocabulary", warning.get("message", "Unknown vocabulary"), evidence)
        else:
            add("contract_error", warning.get("task_id"), "contract_error", warning.get("message", "Contract warning"), evidence)
    handoff = snapshot.get("sources", {}).get("HANDOFF.md", {})
    if handoff.get("present") and now_dt:
        # The capture keeps mtime internal; source freshness uses a conservative read-at-only fallback.
        # build_snapshot adds the mtime marker below when it can observe it.
        mtime = handoff_mtime
        if isinstance(mtime, (int, float)) and now_dt.timestamp() - mtime > 86400:
            add("stale_handoff", None, "stale_handoff", "HANDOFF.md is over 24 hours old", [{"path": "HANDOFF.md", "sha256": handoff.get("sha256"), "line": 1}])
    return sorted(explicit, key=lambda item: item["id"]) + sorted(inferred, key=lambda item: item["id"])


def _project_id(text: str) -> tuple[str | None, int | None]:
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.startswith((" ", "\t", "-")):
            continue
        match = re.match(r"^project:\s*(.*?)\s*$", line)
        if match:
            return _scalar(match.group(1)), line_no
    return None, None


def _provenance_for(capture: dict[str, Any], path: str, line: int | None) -> dict[str, Any]:
    return {"path": path, "sha256": capture.get("sha256"), "line": line or 1}


def _view_warning(warnings: list[dict[str, Any]], code: str, message: str, path: str, line: int | None = None, **details: Any) -> None:
    warning: dict[str, Any] = {"code": code, "message": message, "path": path}
    if line is not None:
        warning["line"] = line
    warning.update(details)
    warnings.append(warning)


def _sort_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(warnings, key=lambda warning: (str(warning.get("code", "")), str(warning.get("path", "")), warning.get("line") or 0, str(warning.get("message", ""))))


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, _SCALARS)


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str | None) -> str | None:
    parsed = _datetime_timestamp(value)
    return _utc_timestamp(parsed) if parsed else None


def _datetime_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _prior_generation(pm_dir: Path, warnings: list[dict[str, Any]]) -> int:
    target = pm_dir / ".orchestrator" / "view" / "v1" / "snapshot.json"
    try:
        value = json.loads(target.read_text())
        generation = value.get("generation") if isinstance(value, dict) else None
        if isinstance(generation, int) and not isinstance(generation, bool) and generation >= 0:
            return generation + 1
    except FileNotFoundError:
        return 1
    except (OSError, json.JSONDecodeError):
        pass
    _view_warning(warnings, "invalid_prior_generation", "Prior snapshot generation is invalid", ".orchestrator/view/v1/snapshot.json")
    return 1


def _atomic_replace(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode & 0o7777 if target.exists() else None
    descriptor, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
