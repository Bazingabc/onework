import sys
import threading
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess


from agent_views.bridge.codex_protocol import (
    CLIENT_METHODS,
    CodexAppServerClient,
    CodexProtocolError,
    normalize_notification,
    normalize_server_request,
    parse_codex_version,
    sanitize_protocol_value,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_app_server.py"


def version_result(version="0.144.1", returncode=0):
    return CompletedProcess(
        ["codex", "--version"], returncode, "codex-cli %s" % version, ""
    )


class AgentProtocolTest(unittest.TestCase):
    def make_client(self, mode="normal", **kwargs):
        return CodexAppServerClient(
            "codex",
            command=[sys.executable, str(FIXTURE), mode],
            version_runner=lambda *args, **options: version_result(),
            request_timeout=2,
            **kwargs,
        )

    def test_version_gate_accepts_supported_minor_and_fails_closed(self):
        self.assertEqual(parse_codex_version("codex-cli 0.144.1"), (0, 144, 1))
        client = CodexAppServerClient(
            "codex",
            version_runner=lambda *args, **kwargs: version_result("0.145.0"),
        )
        with self.assertRaises(CodexProtocolError) as context:
            client.probe_version()
        self.assertEqual(context.exception.code, "protocol_version_unsupported")
        self.assertIn("0.145.0", str(context.exception))

    def test_initialize_and_correlate_fragmented_out_of_order_jsonl(self):
        client = self.make_client("reorder")
        try:
            initialized = client.initialize()
            results = {}

            def request(marker):
                results[marker] = client.request("model/list", {"marker": marker})

            threads = [threading.Thread(target=request, args=(marker,)) for marker in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

            self.assertEqual(initialized["platformOs"], "test")
            self.assertEqual(results["a"]["data"][0]["id"], "a")
            self.assertEqual(results["b"]["data"][0]["id"], "b")
        finally:
            client.close()

    def test_approval_and_notifications_are_normalized_and_redacted(self):
        events = []
        approvals = []
        approval_seen = threading.Event()
        client = None

        def handle_approval(approval):
            approvals.append(approval)
            client.respond_server_request(
                approval["request_id"], {"decision": "decline"}
            )
            approval_seen.set()

        client = self.make_client(
            event_handler=events.append,
            server_request_handler=handle_approval,
        )
        try:
            client.initialize()
            result = client.request("thread/start", {"cwd": "/tmp", "ephemeral": True})
            self.assertTrue(approval_seen.wait(1))
        finally:
            client.close()

        self.assertEqual(result["thread"]["id"], "thread-fixture")
        self.assertEqual(approvals[0]["kind"], "command")
        self.assertEqual(approvals[0]["params"]["environment"], "[REDACTED]")
        self.assertEqual([event["kind"] for event in events], ["message.delta"])
        self.assertEqual(
            events[0]["payload"]["environmentVariables"], "[REDACTED]"
        )
        self.assertNotIn("private chain of thought", str(events))

    def test_unknown_methods_are_rejected_at_both_protocol_edges(self):
        events = []
        client = self.make_client(
            "unknown-server-request", event_handler=events.append
        )
        try:
            client.initialize()
            with self.assertRaises(CodexProtocolError) as context:
                client.request("thread/shellCommand", {})
            self.assertEqual(context.exception.code, "protocol_method_unsupported")

            client.request("thread/read", {"threadId": "thread-fixture"})
            deadline = time.time() + 1
            while not events and time.time() < deadline:
                time.sleep(0.01)
        finally:
            client.close()

        self.assertEqual(events[0]["kind"], "protocol.error")
        self.assertEqual(
            events[0]["payload"]["code"], "protocol_server_method_unsupported"
        )

    def test_server_request_preserves_numeric_json_rpc_identity(self):
        request = normalize_server_request(
            {
                "id": 7,
                "method": "item/tool/requestUserInput",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "questions": [],
                },
            }
        )
        self.assertEqual(request["request_id"], 7)

    def test_output_is_bounded_and_raw_reasoning_is_dropped(self):
        sanitized = sanitize_protocol_value(
            {
                "text": "x" * 9000,
                "apiKey": "secret",
                "accessToken": "also-secret",
                "nested": ["ok"],
            }
        )
        self.assertTrue(sanitized["text"].endswith("[truncated]"))
        self.assertEqual(sanitized["apiKey"], "[REDACTED]")
        self.assertEqual(sanitized["accessToken"], "[REDACTED]")
        self.assertIsNone(
            normalize_notification(
                "item/reasoning/summaryTextDelta", {"delta": "hidden"}
            )
        )
        completed = normalize_notification(
            "turn/completed",
            {
                "threadId": "thread-safe",
                "turn": {
                    "id": "turn-safe",
                    "status": "completed",
                    "items": [
                        {
                            "type": "reasoning",
                            "content": ["private chain of thought"],
                            "summary": ["private summary"],
                        },
                        {
                            "type": "commandExecution",
                            "aggregatedOutput": "TOKEN=private",
                        },
                        {"type": "agentMessage", "text": "safe answer"},
                    ],
                },
            },
        )
        rendered = str(completed)
        self.assertNotIn("private chain of thought", rendered)
        self.assertNotIn("private summary", rendered)
        self.assertNotIn("TOKEN=private", rendered)
        self.assertIn("safe answer", rendered)

    def test_subprocess_crash_fails_pending_request_with_diagnostic(self):
        events = []
        client = self.make_client("crash", event_handler=events.append)
        try:
            client.initialize()
            with self.assertRaises(CodexProtocolError) as context:
                client.request("model/list", {})
        finally:
            client.close()
        self.assertEqual(context.exception.code, "protocol_remote_error")
        self.assertTrue(any(event["kind"] == "protocol.error" for event in events))

    def test_allowlist_is_exactly_the_planned_surface(self):
        self.assertEqual(
            CLIENT_METHODS,
            {
                "initialize",
                "model/list",
                "account/rateLimits/read",
                "thread/list",
                "thread/start",
                "thread/read",
                "thread/turns/list",
                "thread/resume",
                "thread/fork",
                "turn/start",
                "turn/steer",
                "turn/interrupt",
            },
        )


if __name__ == "__main__":
    unittest.main()
