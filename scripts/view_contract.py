"""Tolerant, dependency-free parsing for the Vibetastic View v1 contract."""

from __future__ import annotations

import hashlib
import re
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
_ITEM_START = re.compile(r"^\s+- id:\s*(?P<value>\S+)")
_FIELD = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_-]*):\s*(?P<value>.*)$")


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
    task_ids = {task["id"] for task in raw_tasks}
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
        if lines[index] == "---":
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
    for item in _items(section):
        fields, field_lines = _fields(item)
        stage_id = fields.get("id")
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
        for field in ("id", "name", "status"):
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
    for item in _items(section):
        fields, field_lines = _fields(item)
        task_id = fields.get("id")
        if task_id is None:
            # _items only starts at an ``- id:`` line, but retain a defensive
            # fallback so malformed future callers cannot turn drift into a
            # parser exception.
            task_id = "<unknown>"
        for field in _TASK_REQUIRED_FIELDS:
            if fields.get(field) is None:
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
                "stage": fields.get("stage"),
                "title": fields.get("title"),
                "agent": fields.get("agent"),
                "source_status": fields.get("status"),
                "dependencies": _dependencies(fields.get("depends_on")),
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
        if task["source_status"] == "done"
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
        dependencies_done = not missing_dependencies and all(
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
        if line == f"{name}:":
            start = index + 1
            break
    if start is None:
        return None
    if stop_at is None:
        return lines[start:]
    for index in range(start, len(lines)):
        if lines[index][1] == f"{stop_at}:":
            return lines[start:index]
    return lines[start:]


def _items(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    items: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] | None = None
    for line_no, line in lines:
        if _ITEM_START.match(line):
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
    if value[0:1] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def _dependencies(value: str | None) -> list[str]:
    if value is None or value == "[]":
        return []
    return [part.strip().strip("\"'") for part in value.strip("[]").split(",") if part.strip()]


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
