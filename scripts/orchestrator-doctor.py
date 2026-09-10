#!/usr/bin/env python3
"""Check installed orchestrator adapters without launching a paid provider session."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


EVENTS = ("PreToolUse", "PostToolUse", "Stop")
BEGIN = "<!-- BEGIN VIBETASTIC ORCHESTRATOR HARNESS -->"


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

    for name in ("CLAUDE.md", "AGENTS.md"):
        try:
            content = (pm_dir / name).read_text()
        except OSError as exc:
            errors.append(f"cannot read {pm_dir / name}: {exc}")
            continue
        if BEGIN not in content or "ORCHESTRATOR.md" not in content:
            errors.append(f"{name} does not contain the managed harness section")
    return {"ok": not errors, "errors": errors}


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


def diagnose(pm_dir, framework_dir):
    pm_dir = pm_dir.resolve()
    framework_dir = framework_dir.resolve()
    configuration = check_configuration(pm_dir, framework_dir)
    selftest = run_adapter_selftest(framework_dir)
    return {
        "ok": configuration["ok"] and selftest["ok"],
        "configuration": configuration,
        "adapter_selftest": selftest,
        "runtime_evidence": runtime_evidence(pm_dir),
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
        for provider, observed in report["runtime_evidence"].items():
            print(f"{provider} runtime hook evidence:", "observed" if observed else "not observed")
        for section in ("configuration", "adapter_selftest"):
            for error in report[section]["errors"]:
                print(f"- {error}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
