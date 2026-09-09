"""Small JSONL App Server used by protocol contract tests."""

import json
import sys
import time


MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"


def send(message, fragmented=False):
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    if fragmented:
        midpoint = max(1, len(encoded) // 2)
        sys.stdout.write(encoded[:midpoint])
        sys.stdout.flush()
        time.sleep(0.01)
        sys.stdout.write(encoded[midpoint:])
    else:
        sys.stdout.write(encoded)
    sys.stdout.flush()


pending = []
for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        send(
            {
                "id": request_id,
                "result": {
                    "userAgent": "fake-app-server/0.144.1",
                    "platformFamily": "unix",
                    "platformOs": "test",
                    "codexHome": "/tmp/fake-codex-home",
                },
            },
            fragmented=True,
        )
    elif method == "initialized":
        continue
    elif MODE == "crash" and method == "model/list":
        sys.exit(17)
    elif MODE == "reorder" and method == "model/list":
        pending.append(message)
        if len(pending) == 2:
            for item in reversed(pending):
                send(
                    {
                        "id": item["id"],
                        "result": {"data": [{"id": item["params"]["marker"]}]},
                    }
                )
            pending = []
    elif method == "model/list":
        send(
            {
                "id": request_id,
                "result": {
                    "data": [
                        {"id": "gpt-5.6-terra", "displayName": "Terra"},
                        {"id": "gpt-5.6-luna", "displayName": "Luna"},
                    ]
                },
            }
        )
    elif method == "thread/start":
        send(
            {
                "method": "item/reasoning/textDelta",
                "params": {"delta": "private chain of thought"},
            }
        )
        send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "delta": "safe message",
                    "environmentVariables": {"ACCESS_TOKEN": "secret-value"},
                },
            }
        )
        send(
            {
                "id": "server-approval-1",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thread-fixture",
                    "turnId": "turn-fixture",
                    "itemId": "item-fixture",
                    "command": ["git", "status"],
                    "reason": "inspect worktree",
                    "environment": {"TOKEN": "must-not-leak"},
                },
            }
        )
        send({"id": request_id, "result": {"thread": {"id": "thread-fixture"}}})
    elif MODE == "unknown-server-request" and method == "thread/read":
        send(
            {
                "id": "server-unknown-1",
                "method": "thread/shellCommand",
                "params": {"command": "dangerous"},
            }
        )
        send({"id": request_id, "result": {"thread": {"id": "thread-fixture"}}})
    elif request_id is not None and method:
        send({"id": request_id, "result": {"method": method, "params": message.get("params")}})
    elif request_id is not None and ("result" in message or "error" in message):
        # Client response to a fixture-created server request.
        continue
