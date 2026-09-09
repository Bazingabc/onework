#!/usr/bin/env python3
"""Forward a minimal Codex lifecycle event to the loopback Agent Views Bridge."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Mapping


MAX_STDIN_BYTES = 64 * 1024
MAX_MESSAGE_TEXT = 8_000
ALLOWED_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "PermissionRequest",
    "PostToolUse",
    "Interrupt",
    "SessionEnd",
}


def sanitize_event(value: Mapping[str, Any]) -> Dict[str, str]:
    event = str(value.get("hook_event_name") or "")
    session_id = str(value.get("session_id") or "")
    if event not in ALLOWED_EVENTS or not session_id or len(session_id) > 200:
        return {}
    result = {
        "hook_event_name": event,
        "session_id": session_id,
    }
    limits = {
        "turn_id": 200,
        "cwd": 2_000,
        "model": 200,
        "permission_mode": 100,
        "tool_name": 200,
        "last_assistant_message": MAX_MESSAGE_TEXT,
    }
    for key, limit in limits.items():
        if value.get(key) is not None:
            result[key] = str(value.get(key))[:limit]
    return result


def deliver(
    payload: Mapping[str, Any],
    *,
    url: str,
    token_path: Path,
    timeout: float = 0.6,
) -> bool:
    sanitized = sanitize_event(payload)
    if not sanitized:
        return False
    try:
        token = token_path.expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if len(token) < 32:
        return False
    encoded = json.dumps(
        sanitized, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Agent-Views-Hook": token,
            "User-Agent": "agent-views-codex-hook/0.2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1)
            return 200 <= response.status < 300
    except Exception:
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--url",
        default=os.environ.get(
            "AGENT_VIEWS_HOOK_URL", "http://127.0.0.1:8765/api/hooks/events"
        ),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path.home() / ".agent-views" / "hook-token",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) <= MAX_STDIN_BYTES:
            value = json.loads(raw.decode("utf-8"))
            if isinstance(value, dict):
                deliver(value, url=args.url, token_path=args.token_file)
    except Exception:
        pass
    # All configured events accept an empty JSON result. Emitting valid JSON
    # keeps the synchronous observer from adding accidental prompt context or
    # changing Codex behavior. Delivery itself is capped at 0.6 seconds.
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
