#!/usr/bin/env python3
"""Install or remove the Agent Views observer without replacing existing hooks."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict


EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "PermissionRequest",
    "PostToolUse",
    "Interrupt",
    "SessionEnd",
)
STATUS_MESSAGE = "Agent Views 同步状态"


def _read_config(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"description": "User-level Codex lifecycle hooks.", "hooks": {}}
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("现有 hooks.json 无法解析，未进行覆盖") from exc
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise RuntimeError("现有 hooks.json 结构无效，未进行覆盖")
    value.setdefault("hooks", {})
    return value


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".hooks-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _is_agent_views_handler(handler: Any, installed_script: Path) -> bool:
    if not isinstance(handler, dict):
        return False
    command = str(handler.get("command") or "")
    return (
        handler.get("statusMessage") == STATUS_MESSAGE
        and str(installed_script) in command
    )


def _remove_existing(payload: Dict[str, Any], installed_script: Path) -> None:
    hooks = payload.setdefault("hooks", {})
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            kept_handlers = [
                item
                for item in handlers
                if not _is_agent_views_handler(item, installed_script)
            ]
            if kept_handlers:
                kept_groups.append({**group, "hooks": kept_handlers})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)


def install(codex_home: Path, state_home: Path, source_script: Path) -> Dict[str, Any]:
    codex_home = codex_home.expanduser().resolve()
    state_home = state_home.expanduser().resolve()
    config_path = codex_home / "hooks.json"
    installed_script = state_home / "hooks" / "forward_event.py"
    payload = _read_config(config_path)
    original = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    _remove_existing(payload, installed_script)

    installed_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_script, installed_script)
    os.chmod(installed_script, 0o700)
    command = "%s %s" % (
        shlex.quote(sys.executable),
        shlex.quote(str(installed_script)),
    )
    for event in EVENTS:
        payload.setdefault("hooks", {}).setdefault(event, []).append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 2,
                        "statusMessage": STATUS_MESSAGE,
                    }
                ]
            }
        )

    changed = original != json.dumps(payload, ensure_ascii=False, sort_keys=True)
    backup = None
    if changed and config_path.exists():
        backup = config_path.with_name(
            "hooks.json.agent-views-backup-%d" % int(time.time())
        )
        shutil.copy2(config_path, backup)
    if changed:
        _atomic_write(config_path, payload)
    return {
        "changed": changed,
        "config": str(config_path),
        "script": str(installed_script),
        "backup": str(backup) if backup else None,
        "events": list(EVENTS),
    }


def uninstall(codex_home: Path, state_home: Path) -> Dict[str, Any]:
    codex_home = codex_home.expanduser().resolve()
    state_home = state_home.expanduser().resolve()
    config_path = codex_home / "hooks.json"
    installed_script = state_home / "hooks" / "forward_event.py"
    payload = _read_config(config_path)
    original = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    _remove_existing(payload, installed_script)
    changed = original != json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if changed:
        _atomic_write(config_path, payload)
    try:
        installed_script.unlink()
    except FileNotFoundError:
        pass
    return {"changed": changed, "config": str(config_path)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--uninstall", action="store_true")
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument(
        "--state-home", type=Path, default=Path.home() / ".agent-views"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.install:
        result = install(args.codex_home, args.state_home, Path(__file__).with_name("forward_event.py"))
    else:
        result = uninstall(args.codex_home, args.state_home)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
