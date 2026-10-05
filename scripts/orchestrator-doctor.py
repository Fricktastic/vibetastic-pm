#!/usr/bin/env python3
"""Check installed orchestrator adapters without launching a paid provider session."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_policy  # noqa: E402


EVENTS = ("PreToolUse", "PostToolUse", "Stop")
BEGIN = "<!-- BEGIN VIBETASTIC ORCHESTRATOR HARNESS -->"
VOLATILE_SCRIPT = "scripts/handoff-volatile-hook.py"
VOLATILE_HEADING = "## Volatile — re-verify before use"
VIEW_V1_DIR = ".orchestrator/view/v1"
VIEW_SNAPSHOT = VIEW_V1_DIR + "/snapshot.json"
VIEW_EVENTS = VIEW_V1_DIR + "/events.jsonl"
VIEW_INSTALL_FAILED = ".orchestrator/view/install-projection-failed.json"
VIEW_FORBIDDEN_KEYS = (
    "token", "api_key", "secret", "password",
    "credential", "prompt", "transcript", "environment",
)


def load_object(path, errors):
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot parse {path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} is not a JSON object")
        return {}
    return value


def managed_groups(config, event, exact_command):
    hooks = config.get("hooks", {})
    if not isinstance(hooks, dict):
        return []
    groups = hooks.get(event, [])
    if not isinstance(groups, list):
        return []
    matches = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        managed = [
            hook for hook in group["hooks"]
            if (
                isinstance(hook, dict)
                and hook.get("type") == "command"
                and isinstance(hook.get("command"), str)
                and hook["command"] == exact_command
            )
        ]
        if managed:
            matches.append((group, managed))
    return matches


def check_configuration(pm_dir, framework_dir):
    errors = []
    marker = load_object(pm_dir / ".orchestrator/config.json", errors)
    expected = {
        "schema_version": 1,
        "enabled": True,
        "managed_by": "vibetastic-pm",
        "pm_dir": str(pm_dir),
        "framework_dir": str(framework_dir),
        "providers": ["claude", "codex"],
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            errors.append(f"marker {key} is {marker.get(key)!r}; expected {value!r}")

    for provider, relative in (("claude", ".claude/settings.json"), ("codex", ".codex/hooks.json")):
        config = load_object(pm_dir / relative, errors)
        expected_script = str(framework_dir / "scripts/orchestrator-hook.py")
        expected_command = " ".join((
            "python3", shlex.quote(expected_script), "--provider", provider,
            "--pm-dir", shlex.quote(str(pm_dir)),
        ))
        for event in EVENTS:
            groups = managed_groups(config, event, expected_command)
            commands = [hook["command"] for _, managed in groups for hook in managed]
            if len(groups) != 1 or len(commands) != 1:
                errors.append(f"{relative}: expected one managed {event} hook, found {len(commands)}")
                continue
            if event == "PreToolUse":
                expected_matcher = "Read|read_file|Write|Edit|apply_patch|Bash|shell|exec_command"
            elif event == "PostToolUse":
                expected_matcher = "Write|Edit|apply_patch|Bash|shell|exec_command"
            else:
                expected_matcher = None
            actual_matcher = groups[0][0].get("matcher")
            if actual_matcher != expected_matcher:
                errors.append(
                    f"{relative}: managed {event} matcher is {actual_matcher!r}; "
                    f"expected {expected_matcher!r}"
                )
            try:
                words = shlex.split(commands[0])
            except ValueError as exc:
                errors.append(f"{relative}: invalid managed {event} command: {exc}")
                continue
            try:
                command_provider = words[words.index("--provider") + 1]
                command_pm = words[words.index("--pm-dir") + 1]
            except (ValueError, IndexError):
                command_provider = command_pm = None
            if expected_script not in words or command_provider != provider or command_pm != str(pm_dir):
                errors.append(f"{relative}: managed {event} command targets the wrong adapter")

        # Issue #51: the volatile-handoff banner runs at SessionStart for both providers.
        volatile = " ".join((
            "python3", shlex.quote(str(framework_dir / VOLATILE_SCRIPT)),
            "--pm-dir", shlex.quote(str(pm_dir)),
        ))
        groups = managed_groups(config, "SessionStart", volatile)
        count = sum(len(managed) for _, managed in groups)
        if count != 1:
            errors.append(f"{relative}: expected one managed SessionStart hook, found {count}")
        elif groups[0][0].get("matcher") is not None:
            errors.append(
                f"{relative}: managed SessionStart matcher is {groups[0][0].get('matcher')!r}; "
                "expected none (every session source)"
            )

    for name in ("CLAUDE.md", "AGENTS.md"):
        try:
            content = (pm_dir / name).read_text()
        except OSError as exc:
            errors.append(f"cannot read {pm_dir / name}: {exc}")
            continue
        if BEGIN not in content or "ORCHESTRATOR.md" not in content:
            errors.append(f"{name} does not contain the managed harness section")
    return {"ok": not errors, "errors": errors}


def volatile_selftest(framework_dir, pm_dir):
    """The SessionStart banner must be silent without a section and loud with one (#51)."""
    script = framework_dir / VOLATILE_SCRIPT
    if not script.is_file():
        return [f"{VOLATILE_SCRIPT} is missing"]
    errors = []

    def invoke():
        return subprocess.run(
            [sys.executable, str(script), "--pm-dir", str(pm_dir)],
            input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(pm_dir)}),
            capture_output=True, text=True, timeout=30,
        )

    silent = invoke()
    if silent.returncode != 0 or silent.stdout:
        errors.append("volatile-handoff hook was not a silent no-op without HANDOFF.md")
    handoff = pm_dir / "HANDOFF.md"
    handoff.write_text(
        f"# Handoff\n\n{VOLATILE_HEADING}\n"
        "- doctor fixture claim | as-of 2026-01-01 | check: `true`\n"
    )
    loud = invoke()
    handoff.unlink()
    if loud.returncode != 0 or "CLAIMS, NOT FACTS" not in loud.stdout \
            or "doctor fixture claim" not in loud.stdout:
        errors.append("volatile-handoff hook did not print the volatile section with its banner")
    return errors


def run_adapter_selftest(framework_dir):
    errors = []
    hook = framework_dir / "scripts/orchestrator-hook.py"
    linter = framework_dir / "scripts/plan-lint.sh"
    if not hook.is_file() or not linter.is_file():
        return {"ok": False, "errors": ["hook or PLAN linter is missing"]}
    with tempfile.TemporaryDirectory(prefix="orchestrator-doctor-") as raw:
        pm_dir = Path(raw)
        (pm_dir / ".orchestrator").mkdir()
        marker = {
            "schema_version": 1,
            "enabled": True,
            "managed_by": "vibetastic-pm",
            "pm_dir": str(pm_dir),
            "framework_dir": str(framework_dir),
            "providers": ["claude", "codex"],
        }
        (pm_dir / ".orchestrator/config.json").write_text(json.dumps(marker))
        (pm_dir / ".claude").mkdir()
        (pm_dir / ".codex").mkdir()
        (pm_dir / ".claude/settings.json").write_text("{}\n")
        (pm_dir / ".codex/hooks.json").write_text("{}\n")
        environment = os.environ.copy()
        environment["ORCHESTRATOR_HOOK_SELFTEST"] = "1"

        errors.extend(volatile_selftest(framework_dir, pm_dir))

        base_payload = {
            "session_id": "doctor-fixture",
            "cwd": str(pm_dir),
            "transcript_path": str(pm_dir / "fixture.jsonl"),
            "model": "doctor-fixture",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(pm_dir / "notes.md")},
        }
        no_plan = subprocess.run(
            [sys.executable, str(hook), "--provider", "claude", "--pm-dir", str(pm_dir)],
            input=json.dumps(base_payload), capture_output=True, text=True, env=environment,
        )
        if no_plan.returncode != 0:
            errors.append(f"Claude adapter fixture returned {no_plan.returncode} without PLAN.md")

        (pm_dir / "PLAN.md").write_text("malformed fixture\n")
        string_payload = dict(base_payload)
        string_payload.update({"tool_name": "apply_patch", "tool_input": "*** Begin Patch\n*** End Patch"})
        bad_plan = subprocess.run(
            [sys.executable, str(hook), "--provider", "codex", "--pm-dir", str(pm_dir)],
            input=json.dumps(string_payload), capture_output=True, text=True, env=environment,
        )
        if bad_plan.returncode != 2:
            errors.append(f"Codex adapter fixture returned {bad_plan.returncode} for corrupt PLAN.md")

        try:
            evidence = [
                json.loads(line)
                for line in (pm_dir / "logs/orchestrator-hooks.jsonl").read_text().splitlines()
            ]
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"adapter fixture evidence was unreadable: {exc}")
        else:
            if len(evidence) != 2 or any(item.get("evidence") != "selftest" for item in evidence):
                errors.append("adapter fixtures were not labeled selftest evidence")
            elif [item.get("outcome") for item in evidence] != ["skipped", "blocked"]:
                errors.append("adapter fixtures did not record their observed outcomes")
            elif any(not item.get("config_sha256") or item.get("config_schema_version") != 1
                     for item in evidence):
                errors.append("adapter fixtures did not bind evidence to their effective config")
    return {"ok": not errors, "errors": errors}


def runtime_evidence(pm_dir):
    seen = {"claude": False, "codex": False}
    try:
        marker = json.loads((pm_dir / ".orchestrator/config.json").read_text())
        schema_version = marker["schema_version"]
        config_hashes = {
            "claude": hashlib.sha256((pm_dir / ".claude/settings.json").read_bytes()).hexdigest(),
            "codex": hashlib.sha256((pm_dir / ".codex/hooks.json").read_bytes()).hexdigest(),
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return seen
    try:
        lines = (pm_dir / "logs/orchestrator-hooks.jsonl").read_text().splitlines()
    except OSError:
        return seen
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        provider = record.get("provider")
        outcome = record.get("outcome")
        exit_code = record.get("exit_code")
        successful_outcome = (
            (outcome in ("passed", "warning") and exit_code == 0)
            or (outcome == "blocked" and exit_code == 2)
        )
        if (
            record.get("evidence") == "runtime"
            and provider in seen
            and record.get("hook_event_name") in EVENTS
            and successful_outcome
            and record.get("config_schema_version") == schema_version
            and record.get("config_sha256") == config_hashes[provider]
        ):
            seen[provider] = True
    return seen


def check_policy(pm_dir):
    """Issue #50: the project's review policy in PROJECT.md must parse as declared.

    Absent keys/sections are fine (framework defaults apply); a malformed declaration is an
    error, because enforcement would silently fall back to a default the project did not pick.
    """
    policy = project_policy.load(pm_dir)
    return {
        "ok": not policy["errors"],
        "errors": policy["errors"],
        "sources": policy["sources"],
        "critic_round_cap": policy["critic_round_cap"],
        "reviewer_fixup_round_cap": policy["reviewer_fixup_round_cap"],
    }


def diagnose(pm_dir, framework_dir):
    pm_dir = pm_dir.resolve()
    framework_dir = framework_dir.resolve()
    configuration = check_configuration(pm_dir, framework_dir)
    selftest = run_adapter_selftest(framework_dir)
    policy = check_policy(pm_dir)
    return {
        "ok": configuration["ok"] and selftest["ok"] and policy["ok"],
        "configuration": configuration,
        "adapter_selftest": selftest,
        "policy": policy,
        "runtime_evidence": runtime_evidence(pm_dir),
        "checks": {"view_contract_v1": check_view_contract_v1(pm_dir, framework_dir)},
    }


def run_doctor(pm_dir, framework_dir):
    """Library entry point returning the same dict `diagnose` builds."""
    return diagnose(pm_dir, framework_dir)


def _import_view_contract(framework_dir):
    """Resolve view_contract via sys.path[0] (sibling-module pattern used by pm_state.py:20).

    Falls back to the framework's scripts directory when sys.path[0] lacks it. The doctor
    is diagnostic only; missing/empty contracts are still reported through the checks dict
    even when the module cannot be imported.
    """
    scripts_dir = (framework_dir / "scripts").resolve()
    try:
        import view_contract  # noqa: WPS433 (lazy import for sibling module)
    except Exception:
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        try:
            import view_contract  # noqa: WPS433 (lazy import for sibling module)
        except Exception:
            return None
    return view_contract


def _scan_forbidden(value, key_name, location, errors):
    """Recursively check a JSON-shaped value for the view contract's forbidden keys."""
    def walk(node, path):
        if isinstance(node, dict):
            for key, child in node.items():
                next_path = f"{path}.{key}" if path else key
                if key in VIEW_FORBIDDEN_KEYS:
                    errors.append(f"forbidden key '{key}' in {location} {next_path}")
                else:
                    walk(child, next_path)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
    walk(value, "")


def _is_allowlisted_code(value):
    """Allow only short, low-charset codes so the doctor can echo them safely."""
    return bool(value) and bool(re.match(r"^[a-z0-9_]{1,64}$", value))


def check_view_contract_v1(pm_dir, framework_dir):
    """Read-only validation of `.orchestrator/view/v1/`. Never writes."""
    errors: list[str] = []
    warnings: list[dict] = []
    assertions: list[str] = []
    snapshot_path = pm_dir / VIEW_SNAPSHOT
    events_path = pm_dir / VIEW_EVENTS

    snapshot = None
    if snapshot_path.is_file():
        try:
            raw = snapshot_path.read_text()
        except OSError as exc:
            errors.append(f"cannot read {snapshot_path}: {exc.__class__.__name__}")
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{snapshot_path} is not valid JSON: {exc.msg}")
            else:
                if not isinstance(parsed, dict):
                    errors.append(f"{snapshot_path} must be a JSON object")
                else:
                    snapshot = parsed
                    contract = parsed.get("contract")
                    schema_version = parsed.get("schema_version")
                    generation = parsed.get("generation")
                    if contract == "vibetastic-view/v1":
                        assertions.append("snapshot.contract == vibetastic-view/v1")
                    else:
                        errors.append(
                            "snapshot.contract has unexpected value (expected 'vibetastic-view/v1')"
                        )
                    if isinstance(schema_version, int) and not isinstance(schema_version, bool):
                        assertions.append("snapshot.schema_version is an integer")
                    else:
                        errors.append("snapshot.schema_version has unexpected value (expected integer)")
                    if (
                        isinstance(generation, int)
                        and not isinstance(generation, bool)
                        and generation >= 1
                    ):
                        assertions.append("snapshot.generation >= 1")
                    else:
                        errors.append("snapshot.generation has unexpected value (expected integer >= 1)")
                    _scan_forbidden(parsed, "snapshot", str(snapshot_path), errors)
                    for warning in parsed.get("warnings", []) or []:
                        if isinstance(warning, dict) and warning.get("code"):
                            code = str(warning["code"])
                            if _is_allowlisted_code(code):
                                warnings.append({
                                    "code": code,
                                    "path": str(snapshot_path),
                                })
    else:
        errors.append(
            "no view projection; run the installer or export-view-contract.py "
            f"(expected {snapshot_path})"
        )

    view_contract = _import_view_contract(framework_dir)
    allowed_event_types = getattr(view_contract, "_EVENT_FIELDS", None)
    if view_contract is None and events_path.is_file():
        errors.append(
            f"{events_path}: view_contract module unavailable; event rows unvalidated"
        )

    if events_path.is_file():
        try:
            raw = events_path.read_text()
        except OSError as exc:
            errors.append(f"cannot read {events_path}: {exc.__class__.__name__}")
        else:
            lines = raw.splitlines()
            non_empty = [line for line in lines if line.strip()]
            if not non_empty:
                warnings.append({
                    "code": "events_jsonl_empty",
                    "message": f"{events_path} is present but contains no events",
                    "path": str(events_path),
                })
            else:
                for line_no, line in enumerate(non_empty, 1):
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        errors.append(
                            f"{events_path} line {line_no} is not valid JSON: {exc.msg}"
                        )
                        continue
                    if not isinstance(row, dict):
                        errors.append(
                            f"{events_path} line {line_no} is not an object"
                        )
                        continue
                    if allowed_event_types is not None:
                        event_id = row.get("event_id")
                        event_type = row.get("type")
                        operation = row.get("operation")
                        if not (isinstance(event_id, str) and isinstance(event_type, str)
                                and isinstance(operation, str)):
                            errors.append(
                                f"{events_path} line {line_no}: event_id, type, operation must be strings"
                            )
                            _scan_forbidden(row, "event", f"{events_path} line {line_no}", errors)
                            continue
                        if event_type not in allowed_event_types:
                            errors.append(
                                f"{events_path} line {line_no}: unknown event type"
                            )
                            _scan_forbidden(row, "event", f"{events_path} line {line_no}", errors)
                            continue
                        expected_keys = {"event_id", "type", "operation", *allowed_event_types[event_type]}
                        extra = set(row) - expected_keys
                        if extra:
                            sample = sorted(extra)[0]
                            errors.append(
                                f"{events_path} line {line_no}: unexpected key"
                            )
                    _scan_forbidden(row, "event", f"{events_path} line {line_no}", errors)
    else:
        warnings.append({
            "code": "events_jsonl_absent",
            "message": f"{events_path} is absent; the installer and exporter do not create it",
            "path": str(events_path),
        })

    for path in (snapshot_path, events_path):
        if path.is_file():
            try:
                mode = path.stat().st_mode
            except OSError as exc:
                errors.append(f"cannot stat {path}: {exc.__class__.__name__}")
                continue
            if mode & 0o002:
                errors.append(
                    f"{path} mode is world-writable (0{mode & 0o777:o}); tighten file permissions"
                )
            elif mode & 0o020:
                warnings.append({
                    "code": "group_writable",
                    "message": (
                        f"{path} mode is 0{mode & 0o777:o} (group-writable); "
                        "review whether group write access is required"
                    ),
                    "path": str(path),
                })

    stale_marker = pm_dir / VIEW_INSTALL_FAILED
    if stale_marker.is_file() or stale_marker.is_dir():
        warnings.append({
            "code": "install_projection_failed",
            "message": (
                "installer initial view projection failed; reconstruct with "
                "export-view-contract.py or re-run install-orchestrators.py with a "
                "real change and confirm"
            ),
            "path": str(stale_marker),
        })

    return {
        "status": "error" if errors else "ok",
        "assertions": assertions,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pm-dir", required=True, type=Path)
    parser.add_argument("--framework-dir", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = diagnose(args.pm_dir, args.framework_dir)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("configuration:", "ok" if report["configuration"]["ok"] else "failed")
        print("adapter selftest:", "ok" if report["adapter_selftest"]["ok"] else "failed")
        policy = report["policy"]
        print("project policy:", "ok" if policy["ok"] else "failed",
              f"(critic rounds {policy['critic_round_cap']}, reviewer fixups "
              f"{policy['reviewer_fixup_round_cap']}; "
              + ", ".join(f"{k} {v}" for k, v in sorted(policy["sources"].items())) + ")")
        for provider, observed in report["runtime_evidence"].items():
            print(f"{provider} runtime hook evidence:", "observed" if observed else "not observed")
        view = report.get("checks", {}).get("view_contract_v1", {})
        if view:
            status = view.get("status", "ok")
            print(f"view contract v1: {status if status == 'ok' else 'failed'}")
        for section in ("configuration", "adapter_selftest", "policy"):
            for error in report[section]["errors"]:
                print(f"- {error}", file=sys.stderr)
        view_errors = view.get("errors", []) if isinstance(view, dict) else []
        for error in view_errors:
            print(f"- {error}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
