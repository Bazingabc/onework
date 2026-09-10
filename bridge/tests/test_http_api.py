import json
import threading
import unittest
import urllib.error
import urllib.request

from agent_views.bridge import AgentViewsError
from agent_views.bridge.http_api import (
    CLIENT_HEADER,
    CLIENT_HEADER_VALUE,
    HOOK_HEADER,
    make_server,
)


class StubService:
    def __init__(self):
        self.sent = []
        self.created = []
        self.approvals = []
        self.hook_events = []
        self.dismissed = []

    def health(self):
        return {"ok": True, "codexVersion": "0.144.1"}

    def list_sessions(self):
        return [{"id": "thread-1", "title": "测试会话"}]

    def get_session(self, thread_id):
        if thread_id == "missing":
            raise AgentViewsError("thread_not_found", "会话不存在", 404)
        return {"id": thread_id, "messages": []}

    def create_session(self, initial_text=None):
        self.created.append(initial_text)
        return {"id": "managed-new", "title": "未命名会话"}

    def send(
        self,
        thread_id,
        text=None,
        *,
        answers=None,
        expected_message_id=None,
        expected_updated_at=None,
    ):
        if thread_id == "stale":
            raise AgentViewsError(
                "session_changed", "会话已有新进展，旧答案未发送", 409
            )
        self.sent.append(
            (
                thread_id,
                text,
                answers,
                expected_message_id,
                expected_updated_at,
            )
        )
        return {"action": "answered", "threadId": thread_id}

    def respond_approval(self, thread_id, decision):
        self.approvals.append((thread_id, decision))
        return {"action": "approval_resolved", "threadId": thread_id}

    def handle_hook_event(self, payload):
        self.hook_events.append(payload)
        return {"action": "recorded", "threadId": payload.get("session_id")}

    def dismiss_attention(self, thread_id, message_id=None):
        self.dismissed.append((thread_id, message_id))
        return {"action": "attention_dismissed", "threadId": thread_id}


class HttpApiTest(unittest.TestCase):
    def setUp(self):
        self.service = StubService()
        self.hook_token = "test-hook-token-with-sufficient-entropy"
        self.server = make_server(
            self.service, "127.0.0.1", 0, self.hook_token
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, *, method="GET", payload=None, headers=None):
        data = None
        values = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            values.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=values
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def android_headers(self):
        return {CLIENT_HEADER: CLIENT_HEADER_VALUE}

    def test_unavailable_health_exposes_actionable_error(self):
        self.service.health = lambda: {"ok": False, "lastError": "App Server 单行输出超过安全上限"}
        status, payload = self.request("/api/health")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "codex_unavailable")
        self.assertEqual(payload["error"]["message"], payload["lastError"])

    def test_health_list_and_detail_contract(self):
        health_status, health = self.request("/api/health")
        list_status, listed = self.request("/api/sessions")
        detail_status, detail = self.request("/api/sessions/thread-1")

        self.assertEqual((health_status, health["ok"]), (200, True))
        self.assertEqual((list_status, listed["sessions"][0]["id"]), (200, "thread-1"))
        self.assertEqual((detail_status, detail["session"]["id"]), (200, "thread-1"))

    def test_write_requires_android_header_and_rejects_browser_origin(self):
        missing_status, missing = self.request(
            "/api/sessions/thread-1/messages",
            method="POST",
            payload={"text": "继续"},
        )
        browser_status, browser = self.request(
            "/api/sessions/thread-1/messages",
            method="POST",
            payload={"text": "继续"},
            headers={**self.android_headers(), "Origin": "https://example.test"},
        )

        self.assertEqual(missing_status, 403)
        self.assertEqual(missing["error"]["code"], "client_header_required")
        self.assertEqual(browser_status, 403)
        self.assertEqual(browser["error"]["code"], "browser_write_forbidden")
        self.assertEqual(self.service.sent, [])

    def test_create_send_answers_and_approval_contract(self):
        created_status, created = self.request(
            "/api/sessions",
            method="POST",
            payload={"initialText": "开始"},
            headers=self.android_headers(),
        )
        send_status, sent = self.request(
            "/api/sessions/thread-1/messages",
            method="POST",
            payload={
                "text": "1",
                "answers": {"choice": "1"},
                "expectedMessageId": "message-1",
                "expectedUpdatedAt": 123,
            },
            headers=self.android_headers(),
        )
        approval_status, approval = self.request(
            "/api/sessions/thread-1/approvals",
            method="POST",
            payload={"decision": "decline"},
            headers=self.android_headers(),
        )

        self.assertEqual((created_status, created["session"]["id"]), (201, "managed-new"))
        self.assertEqual((send_status, sent["action"]), (202, "answered"))
        self.assertEqual((approval_status, approval["action"]), (202, "approval_resolved"))
        self.assertEqual(self.service.created, ["开始"])
        self.assertEqual(
            self.service.sent,
            [("thread-1", "1", {"choice": "1"}, "message-1", 123)],
        )
        self.assertEqual(self.service.approvals, [("thread-1", "decline")])

    def test_session_change_error_status_is_preserved(self):
        status, payload = self.request(
            "/api/sessions/stale/messages",
            method="POST",
            payload={"text": "继续"},
            headers=self.android_headers(),
        )

        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "session_changed")

    def test_hook_endpoint_uses_separate_token_authentication(self):
        missing_status, missing = self.request(
            "/api/hooks/events",
            method="POST",
            payload={"hook_event_name": "Stop", "session_id": "thread-1"},
        )
        accepted_status, accepted = self.request(
            "/api/hooks/events",
            method="POST",
            payload={"hook_event_name": "Stop", "session_id": "thread-1"},
            headers={HOOK_HEADER: self.hook_token},
        )

        self.assertEqual(missing_status, 403)
        self.assertEqual(missing["error"]["code"], "hook_token_invalid")
        self.assertEqual((accepted_status, accepted["action"]), (202, "recorded"))
        self.assertEqual(len(self.service.hook_events), 1)

    def test_attention_dismiss_contract(self):
        status, payload = self.request(
            "/api/sessions/thread-1/attention/dismiss",
            method="POST",
            payload={"messageId": "message-1"},
            headers=self.android_headers(),
        )

        self.assertEqual((status, payload["action"]), (200, "attention_dismissed"))
        self.assertEqual(self.service.dismissed, [("thread-1", "message-1")])

    def test_non_loopback_binding_is_rejected(self):
        with self.assertRaises(AgentViewsError) as context:
            make_server(self.service, "0.0.0.0", 8765)
        self.assertEqual(context.exception.code, "loopback_required")


if __name__ == "__main__":
    unittest.main()
