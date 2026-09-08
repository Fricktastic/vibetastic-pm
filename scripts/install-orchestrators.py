#!/usr/bin/env python3
"""Add the provider-neutral orchestrator harness to one PM directory."""

import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile


BEGIN = "<!-- BEGIN VIBETASTIC ORCHESTRATOR HARNESS -->"
END = "<!-- END VIBETASTIC ORCHESTRATOR HARNESS -->"
IGNORE_BEGIN = "# BEGIN VIBETASTIC ORCHESTRATOR STATE"
IGNORE_END = "# END VIBETASTIC ORCHESTRATOR STATE"
EVENTS = ("PreToolUse", "PostToolUse", "Stop")


class InstallError(Exception):
    pass


def read_json(path):
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"{path} must contain a JSON object")
    hooks = value.get("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError(f"{path}: hooks must be an object")
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise InstallError(f"{path}: hooks.{event} must be an array")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise InstallError(f"{path}: each hooks.{event} entry must contain a hooks array")
    return value


def read_lease(path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot parse {path}: {exc}") from exc
    if value is not None and not isinstance(value, dict):
        raise InstallError(f"{path} must contain an ownership object or null")
    return value


def hook_command(provider, hook_script, pm_dir):
    return " ".join((
        "python3",
        shlex.quote(str(hook_script)),
        "--provider",
        provider,
        "--pm-dir",
        shlex.quote(str(pm_dir)),
    ))


def is_managed(command_hook, exact_commands):
    return (
        isinstance(command_hook, dict)
        and command_hook.get("type") == "command"
        and isinstance(command_hook.get("command"), str)
        and command_hook["command"] in exact_commands
    )


def command_group(command, matcher=None):
    group = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


def merged_config(original, provider, hook_script, pm_dir, prior_command=None):
    result = copy.deepcopy(original)
    hooks = result.setdefault("hooks", {})
    command = hook_command(provider, hook_script, pm_dir)
    exact_commands = {command}
    if prior_command:
        exact_commands.add(prior_command)
    for event in EVENTS:
        kept_groups = []
        for group in hooks.get(event, []):
            kept_hooks = [item for item in group["hooks"] if not is_managed(item, exact_commands)]
            if kept_hooks:
                kept = copy.deepcopy(group)
                kept["hooks"] = kept_hooks
                kept_groups.append(kept)
        hooks[event] = kept_groups

    mutation_matcher = "Write|Edit|apply_patch|Bash|shell"
    hooks["PreToolUse"].append(command_group(command, mutation_matcher))
    hooks["PostToolUse"].append(command_group(command, mutation_matcher))
    hooks["Stop"].append(command_group(command))
    return result


def managed_document(original, contract_ref):
    plan_update_ref = os.path.join(os.path.dirname(contract_ref), "scripts", "plan-update.py")
    block = f"""{BEGIN}
## Orchestrator harness

Read and follow [`{contract_ref}`]({contract_ref}) before orchestrating work in this PM directory.
The project-local hooks require an active lease for mutating tool calls. Change `PLAN.md`
through `{plan_update_ref}`; direct Write, Edit, and apply_patch changes are rejected.
Shell commands are cooperative because arbitrary shell text cannot be parsed safely; the
post-use hook lints the complete current `PLAN.md`, but cannot undo a command's effects.
Hook configuration does not grant project trust. Provider-native trust still controls whether
these project-local hooks run.
{END}"""
    start = original.find(BEGIN)
    finish = original.find(END)
    if start >= 0 and finish >= start:
        finish += len(END)
        return original[:start] + block + original[finish:]
    if not original:
        return block + "\n"
    separator = "" if original.endswith("\n\n") else "\n" if original.endswith("\n") else "\n\n"
    return original + separator + block + "\n"


def managed_gitignore(original):
    block = f"{IGNORE_BEGIN}\n.orchestrator/\n{IGNORE_END}"
    start = original.find(IGNORE_BEGIN)
    finish = original.find(IGNORE_END)
    if start >= 0 and finish >= start:
        finish += len(IGNORE_END)
        return original[:start] + block + original[finish:]
    separator = "" if not original or original.endswith("\n") else "\n"
    return original + separator + block + "\n"


def validate_managed_document(path, content):
    begins = content.count(BEGIN)
    ends = content.count(END)
    if begins != ends or begins > 1:
        raise InstallError(f"{path} has incomplete or duplicate managed harness markers")
    if begins == 1 and content.find(BEGIN) > content.find(END):
        raise InstallError(f"{path} has managed harness markers in the wrong order")


def prior_hook_commands(marker, marker_path):
    if marker.get("managed_by") != "vibetastic-pm":
        return {}
    required = ("schema_version", "enabled", "pm_dir", "framework_dir", "providers")
    if any(key not in marker for key in required) or marker.get("schema_version") != 1:
        raise InstallError(f"{marker_path} is an invalid managed installation marker")
    try:
        prior_pm = Path(marker["pm_dir"]).resolve()
        prior_hook = Path(marker["framework_dir"]).resolve() / "scripts/orchestrator-hook.py"
        providers = marker["providers"]
    except (TypeError, ValueError) as exc:
        raise InstallError(f"{marker_path} is an invalid managed installation marker") from exc
    if not isinstance(providers, list):
        raise InstallError(f"{marker_path} is an invalid managed installation marker")
    return {
        provider: hook_command(provider, prior_hook, prior_pm)
        for provider in providers if provider in ("claude", "codex")
    }


def encode_json(value):
    return (json.dumps(value, indent=2, sort_keys=False) + "\n").encode()


def atomic_write(path, content):
    if path.exists() and path.read_bytes() == content:
        return
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def install(pm_dir, framework_dir):
    pm_dir = pm_dir.resolve()
    framework_dir = framework_dir.resolve()
    if not pm_dir.is_dir():
        raise InstallError(f"PM directory does not exist: {pm_dir}")
    if not framework_dir.is_dir():
        raise InstallError(f"framework directory does not exist: {framework_dir}")
    required = (
        framework_dir / "ORCHESTRATOR.md",
        framework_dir / "scripts/orchestrator-hook.py",
        framework_dir / "scripts/orchestrator-state.py",
        framework_dir / "scripts/plan-update.py",
        framework_dir / "scripts/pm_state.py",
        framework_dir / "scripts/plan-lint.sh",
        framework_dir / "scripts/log-partner-burn.py",
        framework_dir / "scripts/partner_telemetry.py",
        framework_dir / "scripts/append-cost.py",
        framework_dir / "scripts/orchestrator-routing.py",
        framework_dir / "scripts/dispatch-role.py",
        framework_dir / "orchestrate.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise InstallError("framework is missing required files: " + ", ".join(missing))

    # Preflight every existing input before creating a directory or replacing a file.
    claude_path = pm_dir / ".claude/settings.json"
    codex_path = pm_dir / ".codex/hooks.json"
    marker_path = pm_dir / ".orchestrator/config.json"
    claude = read_json(claude_path)
    codex = read_json(codex_path)
    marker = read_json(marker_path) if marker_path.exists() else {}
    prior_commands = prior_hook_commands(marker, marker_path) if marker else {}
    lease_path = pm_dir / ".orchestrator/lease.json"
    lease = read_lease(lease_path)
    documents = {}
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = pm_dir / name
        try:
            documents[path] = path.read_text() if path.exists() else ""
            validate_managed_document(path, documents[path])
        except OSError as exc:
            raise InstallError(f"cannot read {path}: {exc}") from exc
    ignore_path = pm_dir / ".gitignore"
    try:
        ignore_content = ignore_path.read_text() if ignore_path.exists() else ""
    except OSError as exc:
        raise InstallError(f"cannot read {ignore_path}: {exc}") from exc
    if ignore_content.count(IGNORE_BEGIN) != ignore_content.count(IGNORE_END):
        raise InstallError(f"{ignore_path} has an incomplete managed state marker")

    hook_script = framework_dir / "scripts/orchestrator-hook.py"
    contract_ref = os.path.relpath(framework_dir / "ORCHESTRATOR.md", pm_dir)
    outputs = [
        (claude_path, encode_json(merged_config(
            claude, "claude", hook_script, pm_dir, prior_commands.get("claude")
        ))),
        (codex_path, encode_json(merged_config(
            codex, "codex", hook_script, pm_dir, prior_commands.get("codex")
        ))),
    ]
    outputs.extend(
        (path, managed_document(content, contract_ref).encode())
        for path, content in documents.items()
    )
    outputs.append((ignore_path, managed_gitignore(ignore_content).encode()))
    # Enabling the marker is the final write, after both provider configs and guides exist.
    outputs.append((marker_path, encode_json({
            "schema_version": 1,
            "enabled": True,
            "managed_by": "vibetastic-pm",
            "pm_dir": str(pm_dir),
            "framework_dir": str(framework_dir),
            "providers": ["claude", "codex"],
        })))
    changes = [(path, content) for path, content in outputs
               if not path.exists() or path.read_bytes() != content]
    if lease is not None and changes:
        raise InstallError(
            f"active lease exists in {lease_path}; release or hand off ownership before changing installation"
        )
    for path, content in changes:
        atomic_write(path, content)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pm-dir", required=True, type=Path)
    parser.add_argument("--framework-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        install(args.pm_dir, args.framework_dir)
    except InstallError as exc:
        print(f"install-orchestrators: {exc}", file=sys.stderr)
        return 2
    print(f"Installed Claude and Codex orchestrator adapters in {args.pm_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
