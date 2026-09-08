#!/usr/bin/env python3
"""Provider-neutral project hook for lease checks, PLAN lint, and telemetry."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


DIRECT_PLAN_PATCH = re.compile(
    r"^\*\*\*\s+(?:(?:Update|Add|Delete) File|Move to):\s*(.+?)\s*$", re.MULTILINE
)


class ConfigError(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def installed_config(pm_dir, provider):
    marker = pm_dir / ".orchestrator/config.json"
    if not marker.exists():
        return None
    try:
        value = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid installation config {marker}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"invalid installation config {marker}: expected a JSON object")
    if value.get("enabled") is False:
        return None
    required = ("enabled", "schema_version", "managed_by", "pm_dir", "framework_dir", "providers")
    missing = [key for key in required if key not in value]
    if missing or value.get("enabled") is not True or value.get("schema_version") != 1:
        raise ConfigError(
            f"invalid installation config {marker}: missing or unsupported fields {missing}"
        )
    if value.get("managed_by") != "vibetastic-pm":
        raise ConfigError(f"invalid installation config {marker}: unknown manager")
    if not isinstance(value.get("providers"), list) or provider not in value["providers"]:
        raise ConfigError(f"invalid installation config {marker}: provider {provider} is not enabled")
    try:
        configured_pm = Path(value["pm_dir"]).resolve()
        framework_dir = Path(value["framework_dir"]).resolve()
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid installation config {marker}: invalid paths") from exc
    if configured_pm != pm_dir.resolve():
        raise ConfigError(f"invalid installation config {marker}: pm_dir does not match invocation")
    provider_config = pm_dir / (".claude/settings.json" if provider == "claude" else ".codex/hooks.json")
    try:
        config_bytes = provider_config.read_bytes()
        parsed_provider = json.loads(config_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid installation config {provider_config}: {exc}") from exc
    if not isinstance(parsed_provider, dict):
        raise ConfigError(f"invalid installation config {provider_config}: expected a JSON object")
    return value, framework_dir, hashlib.sha256(config_bytes).hexdigest()


def append_evidence(pm_dir, provider, payload, outcome, exit_code, config_sha256, schema_version):
    record = {
        "ts": now(),
        "provider": provider,
        "event": payload.get("hook_event_name"),
        "hook_event_name": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "transcript_path": payload.get("transcript_path"),
        "model": payload.get("model"),
        "evidence": "selftest" if os.environ.get("ORCHESTRATOR_HOOK_SELFTEST") == "1" else "runtime",
        "outcome": outcome,
        "exit_code": exit_code,
        "config_sha256": config_sha256,
        "config_schema_version": schema_version,
    }
    log_dir = pm_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        fd = os.open(log_dir / "orchestrator-hooks.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)
    except OSError:
        pass


def normalized_tool_input(payload):
    value = payload.get("tool_input")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"command": value}
    return {}


def is_direct_plan_write(payload):
    tool = str(payload.get("tool_name") or "")
    tool_input = normalized_tool_input(payload)
    if tool in ("Write", "Edit"):
        candidate = tool_input.get("file_path") or tool_input.get("path") or ""
        return Path(str(candidate)).name == "PLAN.md"
    if tool == "apply_patch":
        patch = tool_input.get("command") or tool_input.get("patch") or ""
        return any(Path(match).name == "PLAN.md" for match in DIRECT_PLAN_PATCH.findall(str(patch)))
    return False


def check_owner(pm_dir, framework_dir, provider):
    token = os.environ.get("PM_ORCHESTRATOR_TOKEN")
    claimed_provider = os.environ.get("PM_ORCHESTRATOR_PROVIDER") or provider
    if not token or claimed_provider != provider:
        print(
            "[orchestrator-hook] Blocked: no matching active orchestrator lease identity. "
            "Launch through the orchestrator wrapper; hooks never acquire a lease automatically.",
            file=sys.stderr,
        )
        return False
    state_script = framework_dir / "scripts/orchestrator-state.py"
    command = [
        sys.executable,
        str(state_script),
        "--pm-dir",
        str(pm_dir),
        "check",
        "--token",
        token,
        "--provider",
        provider,
    ]
    try:
        checked = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[orchestrator-hook] Blocked: lease check could not run: {exc}", file=sys.stderr)
        return False
    if checked.returncode != 0:
        detail = (checked.stderr or checked.stdout or "ownership denied").strip()
        print(f"[orchestrator-hook] Blocked: {detail}", file=sys.stderr)
        return False
    try:
        lease = json.loads(checked.stdout)
    except json.JSONDecodeError:
        print("[orchestrator-hook] Blocked: lease check returned invalid JSON", file=sys.stderr)
        return False
    if (
        lease.get("token") != token
        or lease.get("provider") != provider
    ):
        print("[orchestrator-hook] Blocked: lease identity did not match this orchestrator", file=sys.stderr)
        return False
    return True


def pre_tool_use(pm_dir, framework_dir, provider, payload):
    if not check_owner(pm_dir, framework_dir, provider):
        return 2, "blocked"
    if is_direct_plan_write(payload):
        print(
            "[orchestrator-hook] Blocked direct PLAN.md edit. Use framework/scripts/plan-update.py "
            "so the expected hash, lint, atomic replace, and TASK_LOG update are one transaction.",
            file=sys.stderr,
        )
        return 2, "blocked"
    return 0, "passed"


def post_tool_use(pm_dir, framework_dir):
    plan = pm_dir / "PLAN.md"
    if not plan.exists():
        return 0, "skipped"
    linter = framework_dir / "scripts/plan-lint.sh"
    try:
        checked = subprocess.run(
            ["bash", str(linter), str(plan)], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[orchestrator-hook] Blocked: PLAN lint could not validate PLAN.md: {exc}", file=sys.stderr)
        return 2, "error"
    detail = ((checked.stdout or "") + (checked.stderr or "")).strip()
    if checked.returncode == 1:
        print(
            "[orchestrator-hook] PLAN.md failed structural lint after tool use. "
            "The tool's effects already happened; repair PLAN.md transactionally before continuing."
            + ("\n\n" + detail if detail else ""),
            file=sys.stderr,
        )
        return 2, "blocked"
    if checked.returncode == 3:
        print("[orchestrator-hook] PLAN.md has vocabulary drift (non-blocking).", file=sys.stderr)
        return 0, "warning"
    if checked.returncode == 0:
        return 0, "passed"
    print(
        f"[orchestrator-hook] Blocked: PLAN linter could not validate PLAN.md "
        f"(exit {checked.returncode})." + ("\n\n" + detail if detail else ""),
        file=sys.stderr,
    )
    return 2, "error"


def stop(framework_dir, provider, pm_dir, payload):
    logger = framework_dir / "scripts/log-partner-burn.py"
    environment = os.environ.copy()
    environment["PM_DIR"] = str(pm_dir)
    try:
        checked = subprocess.run(
            [sys.executable, str(logger), "--provider", provider],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=environment,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[orchestrator-hook] telemetry could not run: {exc}", file=sys.stderr)
        return 0, "error"
    if checked.returncode != 0:
        detail = (checked.stderr or checked.stdout or f"exit {checked.returncode}").strip()
        print(f"[orchestrator-hook] telemetry reported: {detail}", file=sys.stderr)
        return 0, "error"
    return 0, "passed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=("claude", "codex"))
    parser.add_argument("--pm-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    pm_dir = args.pm_dir.resolve()
    try:
        installed = installed_config(pm_dir, args.provider)
    except ConfigError as exc:
        print(f"[orchestrator-hook] Blocked: {exc}", file=sys.stderr)
        return 2
    if installed is None:
        return 0
    marker, framework_dir, config_sha256 = installed
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name")
    if event not in ("PreToolUse", "PostToolUse", "Stop"):
        return 0
    if event == "PreToolUse":
        exit_code, outcome = pre_tool_use(pm_dir, framework_dir, args.provider, payload)
    elif event == "PostToolUse":
        exit_code, outcome = post_tool_use(pm_dir, framework_dir)
    else:
        exit_code, outcome = stop(framework_dir, args.provider, pm_dir, payload)
    append_evidence(
        pm_dir, args.provider, payload, outcome, exit_code,
        config_sha256, marker["schema_version"],
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
