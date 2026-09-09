import json
import tempfile
import unittest
from pathlib import Path

from agent_views.bridge import AgentViewsError, AgentViewsService, ManagedThreadStore


NOW = 1_700_000_000


class FakeProtocol:
    def __init__(self, threads=None, resume_error=None):
        self.cli_version = "0.144.1"
        self.threads = list(threads or [])
        self.calls = []
        self.responses = []
        self.event_handler = lambda event: None
        self.server_request_handler = lambda request: None
        self.closed = False
        self.next_thread = 1
        self.next_turn = 1
        self.resume_error = resume_error

    def initialize(self):
        self.calls.append(("initialize", {}))
        return {"userAgent": "fake-app-server/0.144.1"}

    def request(self, method, params=None):
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "model/list":
            return {
                "data": [
                    {"id": "gpt-test", "displayName": "Test", "isDefault": True}
                ]
            }
        if method == "thread/list":
            return {"data": [dict(item) for item in self.threads], "nextCursor": None}
        if method == "thread/resume":
            if self.resume_error is not None:
                raise RuntimeError(self.resume_error)
            thread = self._thread(params["threadId"])
            return {"thread": {**thread, "status": {"type": "idle"}}}
        if method == "thread/read":
            return {"thread": dict(self._thread(params["threadId"]))}
        if method == "thread/start":
            thread_id = "managed-new-%d" % self.next_thread
            self.next_thread += 1
            return {
                "thread": {
                    "id": thread_id,
                    "createdAt": NOW,
                    "updatedAt": NOW,
                    "cwd": params.get("cwd"),
                    "status": {"type": "idle"},
                }
            }
        if method == "turn/start":
            turn_id = "turn-%d" % self.next_turn
            self.next_turn += 1
            return {"turn": {"id": turn_id, "status": "inProgress", "items": []}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        raise AssertionError("unexpected method: %s" % method)

    def _thread(self, thread_id):
        for thread in self.threads:
            if thread.get("id") == thread_id:
                return thread
        raise RuntimeError("no rollout found")

    def respond_server_request(self, request_id, result=None, error=None):
        self.responses.append(
            {"requestId": request_id, "result": result, "error": error}
        )

    def close(self):
        self.closed = True


class AgentViewsServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def make_service(self, protocol, managed=()):
        ManagedThreadStore(self.state).save(managed)
        service = AgentViewsService(
            protocol,
            workspace=self.root,
            state_path=self.state,
            clock=lambda: NOW,
        )
        service.start()
        return service

    @staticmethod
    def thread(thread_id, age=0, status="notLoaded", **extra):
        return {
            "id": thread_id,
            "name": extra.pop("name", None),
            "preview": extra.pop("preview", "preview %s" % thread_id),
            "createdAt": NOW - age,
            "updatedAt": NOW - age,
            "cwd": extra.pop("cwd", "/workspace/%s" % thread_id),
            "source": extra.pop("source", {"type": "vscode"}),
            "status": {"type": status},
            **extra,
        }

    def test_lists_all_recent_codex_threads_with_separate_control_state(self):
        protocol = FakeProtocol(
            [
                self.thread("managed", age=10, name="受管任务"),
                self.thread(
                    "ide",
                    age=20,
                    name="IDE 任务",
                    turns=[
                        {
                            "id": "turn-ide",
                            "status": "completed",
                            "items": [
                                {
                                    "id": "a-ide",
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": "请确认是否继续。",
                                }
                            ],
                        }
                    ],
                ),
                self.thread("old", age=3_601, name="过期任务"),
            ]
        )
        service = self.make_service(protocol, managed={"managed"})

        sessions = service.list_sessions()

        self.assertEqual([item["id"] for item in sessions], ["managed", "ide"])
        self.assertEqual(sessions[0]["ownership"], "managed")
        self.assertEqual(sessions[0]["status"]["type"], "idle")
        self.assertEqual(sessions[1]["ownership"], "managed")
        self.assertEqual(sessions[1]["status"]["type"], "waiting_input")
        self.assertEqual(sessions[1]["attention"]["type"], "required_inferred")
        self.assertEqual(sessions[1]["control"]["type"], "attachable")
        resumes = [params for method, params in protocol.calls if method == "thread/resume"]
        self.assertEqual(resumes, [{"threadId": "managed"}])

    def test_active_writer_resume_conflict_is_running_but_not_direct(self):
        protocol = FakeProtocol(
            [
                self.thread(
                    "managed",
                    turns=[],
                )
            ],
            resume_error="thread managed already has an active writer",
        )
        service = self.make_service(protocol, managed={"managed"})

        session = service.list_sessions()[0]

        self.assertEqual(session["runtimeState"], "running")
        self.assertEqual(session["status"]["type"], "running")
        self.assertNotIn("message", session["status"])
        self.assertEqual(session["control"]["type"], "busy_elsewhere")

    def test_request_user_input_turns_literal_shortcut_into_structured_answer(self):
        protocol = FakeProtocol()
        service = self.make_service(protocol)
        session = service.create_session()
        thread_id = session["id"]
        service.handle_server_request(
            {
                "request_id": 7,
                "kind": "user_input",
                "method": "item/tool/requestUserInput",
                "params": {
                    "threadId": thread_id,
                    "turnId": "turn-waiting",
                    "questions": [
                        {
                            "id": "choice",
                            "header": "选择",
                            "question": "请选择",
                            "options": [
                                {"label": "1", "description": "第一项"},
                                {"label": "2", "description": "第二项"},
                            ],
                        }
                    ],
                },
            }
        )

        waiting = next(item for item in service.list_sessions() if item["id"] == thread_id)
        result = service.send(thread_id, "1")

        self.assertEqual(waiting["status"]["type"], "waiting_input")
        self.assertEqual(waiting["pending"]["questions"][0]["question"], "请选择")
        self.assertEqual(result["action"], "answered")
        self.assertEqual(
            protocol.responses,
            [
                {
                    "requestId": 7,
                    "result": {"answers": {"choice": {"answers": ["1"]}}},
                    "error": None,
                }
            ],
        )

    def test_final_message_does_not_become_attachable_until_turn_settles(self):
        protocol = FakeProtocol(
            [
                self.thread(
                    "ide-active",
                    status="active",
                    turns=[
                        {
                            "id": "turn-active",
                            "status": "inProgress",
                            "items": [
                                {
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": "请确认是否继续。",
                                }
                            ],
                        }
                    ],
                )
            ]
        )
        service = self.make_service(protocol)

        session = service.list_sessions()[0]

        self.assertEqual(session["runtimeState"], "running")
        self.assertEqual(session["attention"]["type"], "none")
        self.assertEqual(session["control"]["type"], "busy_elsewhere")

    def test_send_starts_then_steers_and_starts_again_after_completion(self):
        protocol = FakeProtocol()
        service = self.make_service(protocol)
        thread_id = service.create_session()["id"]

        first = service.send(thread_id, "继续")
        second = service.send(thread_id, "A")
        service.handle_protocol_event(
            {
                "kind": "turn.completed",
                "payload": {
                    "threadId": thread_id,
                    "turn": {"id": first["turnId"], "status": "completed"},
                },
            }
        )
        third = service.send(thread_id, "B")

        self.assertEqual(first["action"], "started")
        self.assertEqual(second["action"], "steered")
        self.assertEqual(third["action"], "started")
        methods = [method for method, _ in protocol.calls]
        self.assertEqual(methods.count("turn/start"), 2)
        self.assertEqual(methods.count("turn/steer"), 1)

    def test_first_task_uses_plan_mode_and_continue_switches_to_default(self):
        protocol = FakeProtocol()
        service = self.make_service(protocol)
        thread_id = service.create_session()["id"]

        first = service.send(thread_id, "先分析需求")
        service.handle_protocol_event(
            {
                "kind": "turn.completed",
                "payload": {
                    "threadId": thread_id,
                    "turn": {"id": first["turnId"], "status": "completed"},
                },
            }
        )
        service.send(thread_id, "继续")

        starts = [params for method, params in protocol.calls if method == "turn/start"]
        self.assertEqual(starts[0]["collaborationMode"]["mode"], "plan")
        self.assertEqual(starts[0]["collaborationMode"]["settings"]["model"], "gpt-test")
        self.assertEqual(starts[1]["collaborationMode"]["mode"], "default")

    def test_completed_ide_thread_is_resumed_and_continued(self):
        protocol = FakeProtocol(
            [
                self.thread(
                    "ide",
                    cwd="/workspace/original",
                    turns=[
                        {
                            "id": "turn-old",
                            "status": "completed",
                            "items": [
                                {
                                    "id": "a-old",
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": "请确认是否继续。",
                                }
                            ],
                        }
                    ],
                )
            ]
        )
        service = self.make_service(protocol)
        waiting = service.list_sessions()[0]

        result = service.send(
            "ide",
            "继续",
            expected_message_id=waiting["attention"]["messageId"],
            expected_updated_at=waiting["updatedAt"],
        )

        self.assertEqual(result["action"], "started")
        resumes = [params for method, params in protocol.calls if method == "thread/resume"]
        starts = [params for method, params in protocol.calls if method == "turn/start"]
        self.assertEqual(resumes, [{"threadId": "ide"}])
        self.assertEqual(starts[-1]["threadId"], "ide")
        self.assertEqual(starts[-1]["cwd"], "/workspace/original")

    def test_stale_inferred_answer_is_rejected_before_resume(self):
        protocol = FakeProtocol(
            [
                self.thread(
                    "ide",
                    turns=[
                        {
                            "id": "turn-old",
                            "status": "completed",
                            "items": [
                                {
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": "请确认是否继续。",
                                }
                            ],
                        }
                    ],
                )
            ]
        )
        service = self.make_service(protocol)

        with self.assertRaises(AgentViewsError) as context:
            service.send("ide", "继续", expected_message_id="stale-message")

        self.assertEqual(context.exception.code, "session_changed")
        self.assertFalse(any(method == "thread/resume" for method, _ in protocol.calls))

    def test_thread_detail_projects_only_user_and_agent_messages(self):
        protocol = FakeProtocol(
            [
                self.thread(
                    "external",
                    turns=[
                        {
                            "id": "turn-1",
                            "items": [
                                {
                                    "id": "u1",
                                    "type": "userMessage",
                                    "content": [{"type": "text", "text": "你好"}],
                                },
                                {
                                    "id": "r1",
                                    "type": "reasoning",
                                    "content": ["private reasoning"],
                                },
                                {
                                    "id": "c1",
                                    "type": "commandExecution",
                                    "aggregatedOutput": "TOKEN=secret",
                                },
                                {
                                    "id": "a1",
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": "已完成",
                                },
                            ],
                        }
                    ],
                )
            ]
        )
        service = self.make_service(protocol)

        detail = service.get_session("external")

        self.assertEqual(
            [(item["role"], item["text"]) for item in detail["messages"]],
            [("user", "你好"), ("agent", "已完成")],
        )
        self.assertEqual(detail["messages"][-1]["phase"], "final_answer")
        self.assertNotIn("private reasoning", json.dumps(detail, ensure_ascii=False))
        self.assertNotIn("TOKEN=secret", json.dumps(detail, ensure_ascii=False))

    def test_server_request_arriving_on_bridge_connection_is_claimed(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)

        service.handle_server_request(
            {
                "request_id": "server-1",
                "kind": "user_input",
                "params": {
                    "threadId": "ide",
                    "questions": [{"id": "choice", "question": "请选择"}],
                },
            }
        )

        waiting = service.list_sessions()[0]
        self.assertEqual(protocol.responses, [])
        self.assertEqual(waiting["control"]["type"], "direct")
        self.assertEqual(waiting["attention"]["type"], "required_protocol")

    def test_user_prompt_for_new_turn_evicts_stale_approval(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)
        service.handle_server_request(
            {
                "request_id": "approval-old",
                "kind": "file_change",
                "params": {"threadId": "ide", "turnId": "turn-old"},
            }
        )

        service.handle_hook_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "ide",
                "turn_id": "turn-new",
            }
        )
        session = service.list_sessions()[0]

        self.assertIsNone(session["pending"])
        self.assertEqual(session["runtimeState"], "running")
        self.assertEqual(session["status"]["type"], "running")
        self.assertEqual(
            session["pendingDiagnostic"]["lastEvictionReason"],
            "user_prompt_submitted",
        )

    def test_delayed_same_turn_start_does_not_evict_approval(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)
        service.handle_server_request(
            {
                "request_id": "approval-current",
                "kind": "command",
                "params": {"threadId": "ide", "turnId": "turn-current"},
            }
        )

        service.handle_protocol_event(
            {
                "kind": "turn.started",
                "payload": {
                    "threadId": "ide",
                    "turn": {"id": "turn-current", "status": "inProgress"},
                },
            }
        )
        session = service.list_sessions()[0]

        self.assertEqual(session["pending"]["requestId"], "approval-current")
        self.assertEqual(session["status"]["type"], "waiting_approval")

    def test_new_turn_start_evicts_old_turn_approval(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)
        service.handle_server_request(
            {
                "request_id": "approval-old",
                "kind": "command",
                "params": {"threadId": "ide", "turnId": "turn-old"},
            }
        )

        service.handle_protocol_event(
            {
                "kind": "turn.started",
                "payload": {
                    "threadId": "ide",
                    "turn": {"id": "turn-new", "status": "inProgress"},
                },
            }
        )
        session = service.list_sessions()[0]

        self.assertIsNone(session["pending"])
        self.assertEqual(session["status"]["type"], "running")
        self.assertEqual(
            session["pendingDiagnostic"]["lastEvictionReason"], "new_turn_started"
        )

    def test_post_tool_use_evicts_same_turn_approval(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)
        service.handle_server_request(
            {
                "request_id": "approval-tool",
                "kind": "command",
                "params": {"threadId": "ide", "turnId": "turn-tool"},
            }
        )

        service.handle_hook_event(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "ide",
                "turn_id": "turn-tool",
            }
        )
        session = service.list_sessions()[0]

        self.assertIsNone(session["pending"])
        self.assertEqual(
            session["pendingDiagnostic"]["lastEvictionReason"], "tool_continued"
        )

    def test_thread_detail_evicts_pending_from_older_turn(self):
        protocol = FakeProtocol(
            [
                self.thread(
                    "ide",
                    turns=[
                        {
                            "id": "turn-new",
                            "status": "inProgress",
                            "items": [
                                {
                                    "id": "user-new",
                                    "type": "userMessage",
                                    "content": [{"type": "text", "text": "继续"}],
                                }
                            ],
                        }
                    ],
                )
            ]
        )
        service = self.make_service(protocol)
        service.handle_server_request(
            {
                "request_id": "approval-old",
                "kind": "file_change",
                "params": {"threadId": "ide", "turnId": "turn-old"},
            }
        )

        session = service.list_sessions()[0]

        self.assertIsNone(session["pending"])
        self.assertEqual(session["runtimeState"], "running")
        self.assertEqual(
            session["pendingDiagnostic"]["lastEvictionReason"],
            "latest_turn_changed",
        )

    def test_pending_for_current_in_progress_turn_survives_reconciliation(self):
        protocol = FakeProtocol(
            [
                self.thread(
                    "ide",
                    status="active",
                    turns=[
                        {"id": "turn-current", "status": "inProgress", "items": []}
                    ],
                )
            ]
        )
        service = self.make_service(protocol)
        service.handle_server_request(
            {
                "request_id": "approval-current",
                "kind": "file_change",
                "params": {"threadId": "ide", "turnId": "turn-current"},
            }
        )

        session = service.list_sessions()[0]

        self.assertEqual(session["pending"]["requestId"], "approval-current")
        self.assertEqual(session["status"]["type"], "waiting_approval")

    def test_repeated_server_request_preserves_original_evidence(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)
        request = {
            "request_id": "approval-repeat",
            "kind": "file_change",
            "params": {"threadId": "ide", "turnId": "turn-current"},
        }

        service.handle_server_request(request)
        first = service.list_sessions()[0]["pendingDiagnostic"]
        service.handle_server_request(request)
        second = service.list_sessions()[0]["pendingDiagnostic"]

        self.assertEqual(first["receivedAt"], second["receivedAt"])
        self.assertEqual(service.health()["pending"]["active"], 1)

    def test_pending_without_turn_id_uses_first_detail_as_baseline(self):
        protocol = FakeProtocol(
            [
                self.thread(
                    "ide",
                    turns=[{"id": "turn-one", "status": "inProgress", "items": []}],
                )
            ]
        )
        service = self.make_service(protocol)
        service.handle_server_request(
            {
                "request_id": "approval-no-turn",
                "kind": "file_change",
                "params": {"threadId": "ide"},
            }
        )

        first = service.list_sessions()[0]
        protocol.threads[0]["turns"] = [
            {"id": "turn-two", "status": "inProgress", "items": []}
        ]
        second = service.list_sessions()[0]

        self.assertEqual(first["pending"]["requestId"], "approval-no-turn")
        self.assertIsNone(second["pending"])
        self.assertEqual(
            second["pendingDiagnostic"]["lastEvictionReason"],
            "latest_turn_changed",
        )

    def test_same_turn_user_prompt_does_not_evict_later_approval(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)
        service.handle_server_request(
            {
                "request_id": "approval-current",
                "kind": "command",
                "params": {"threadId": "ide", "turnId": "turn-current"},
            }
        )

        service.handle_hook_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "ide",
                "turn_id": "turn-current",
            }
        )
        session = service.list_sessions()[0]

        self.assertEqual(session["pending"]["requestId"], "approval-current")

    def test_terminal_hooks_evict_pending(self):
        for event_name in ("Stop", "Interrupt", "SessionEnd"):
            with self.subTest(event_name=event_name):
                protocol = FakeProtocol([self.thread("ide")])
                service = self.make_service(protocol)
                service.handle_server_request(
                    {
                        "request_id": "approval-terminal",
                        "kind": "file_change",
                        "params": {"threadId": "ide", "turnId": "turn-current"},
                    }
                )

                service.handle_hook_event(
                    {
                        "hook_event_name": event_name,
                        "session_id": "ide",
                        "turn_id": "turn-current",
                        "last_assistant_message": "已结束",
                    }
                )

                self.assertIsNone(service.list_sessions()[0]["pending"])

    def test_stop_hook_marks_question_and_user_prompt_clears_it(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)

        recorded = service.handle_hook_event(
            {
                "hook_event_name": "Stop",
                "session_id": "ide",
                "turn_id": "turn-hook",
                "last_assistant_message": "请描述一下你的使用场景。",
            }
        )
        waiting = service.list_sessions()[0]
        service.handle_hook_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "ide",
                "turn_id": "turn-next",
            }
        )
        running = service.list_sessions()[0]

        self.assertEqual(recorded["action"], "recorded")
        self.assertEqual(waiting["attention"]["type"], "required_inferred")
        self.assertEqual(waiting["control"]["type"], "attachable")
        self.assertEqual(running["attention"]["type"], "none")
        self.assertEqual(running["runtimeState"], "running")

        service.handle_hook_event(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "ide",
                "turn_id": "turn-new",
            }
        )
        after_tool = service.list_sessions()[0]
        self.assertEqual(after_tool["runtimeState"], "running")

        service.handle_hook_event(
            {
                "hook_event_name": "Interrupt",
                "session_id": "ide",
                "turn_id": "turn-new",
            }
        )
        interrupted = service.list_sessions()[0]
        self.assertEqual(interrupted["runtimeState"], "settled")

    def test_each_post_tool_event_clears_a_later_permission_in_same_turn(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)
        base = {
            "session_id": "ide",
            "turn_id": "turn-hook",
        }

        for tool_name in ("Bash", "ApplyPatch"):
            service.handle_hook_event(
                {
                    **base,
                    "hook_event_name": "PermissionRequest",
                    "tool_name": tool_name,
                }
            )
            waiting = service.list_sessions()[0]
            self.assertEqual(waiting["status"]["type"], "waiting_approval")

            service.handle_hook_event(
                {**base, "hook_event_name": "PostToolUse"}
            )
            continued = service.list_sessions()[0]
            self.assertEqual(continued["attention"]["type"], "none")
            self.assertEqual(continued["runtimeState"], "running")


    def test_inferred_attention_can_be_dismissed_by_message(self):
        protocol = FakeProtocol([self.thread("ide")])
        service = self.make_service(protocol)
        service.handle_hook_event(
            {
                "hook_event_name": "Stop",
                "session_id": "ide",
                "turn_id": "turn-hook",
                "last_assistant_message": "还有需要补充的边界吗？",
            }
        )
        waiting = service.list_sessions()[0]

        result = service.dismiss_attention(
            "ide", waiting["attention"]["messageId"]
        )
        dismissed = service.list_sessions()[0]

        self.assertEqual(result["action"], "attention_dismissed")
        self.assertEqual(dismissed["attention"]["type"], "none")

    def test_rejects_malformed_structured_answers(self):
        protocol = FakeProtocol()
        service = self.make_service(protocol)
        thread_id = service.create_session()["id"]
        service.handle_server_request(
            {
                "request_id": 8,
                "kind": "user_input",
                "params": {
                    "threadId": thread_id,
                    "questions": [{"id": "choice", "question": "请选择"}],
                },
            }
        )

        with self.assertRaises(AgentViewsError) as context:
            service.send(thread_id, answers=["1"])

        self.assertEqual(context.exception.code, "answers_invalid")
        self.assertEqual(protocol.responses, [])

    def test_permission_approval_grants_only_the_requested_profile(self):
        protocol = FakeProtocol()
        service = self.make_service(protocol)
        thread_id = service.create_session()["id"]
        service.handle_server_request(
            {
                "request_id": 9,
                "kind": "permissions",
                "params": {"threadId": thread_id, "permissions": {"network": {"enabled": True}}},
            }
        )

        waiting = next(item for item in service.list_sessions() if item["id"] == thread_id)
        result = service.respond_approval(thread_id, "accept")

        self.assertEqual(waiting["pending"]["permissions"], {"network": {"enabled": True}})
        self.assertEqual(result["action"], "approval_resolved")
        self.assertEqual(
            protocol.responses,
            [
                {
                    "requestId": 9,
                    "result": {
                        "permissions": {"network": {"enabled": True}},
                        "scope": "turn",
                    },
                    "error": None,
                }
            ],
        )

    def test_ignores_non_object_thread_in_protocol_event(self):
        protocol = FakeProtocol()
        service = self.make_service(protocol)

        service.handle_protocol_event(
            {"kind": "turn.completed", "payload": {"thread": "invalid"}}
        )

        self.assertIsNone(service.health()["lastError"])

    def test_fatal_protocol_error_marks_bridge_offline(self):
        protocol = FakeProtocol()
        service = self.make_service(protocol)

        service.handle_protocol_event(
            {
                "kind": "protocol.error",
                "payload": {
                    "code": "protocol_output_too_large",
                    "message": "response exceeded hard limit",
                },
            }
        )

        self.assertFalse(service.health()["ok"])
        self.assertEqual(
            service.health()["lastError"], "response exceeded hard limit"
        )

    def test_nonfatal_protocol_warning_keeps_bridge_online(self):
        protocol = FakeProtocol()
        service = self.make_service(protocol)

        service.handle_protocol_event(
            {
                "kind": "protocol.error",
                "payload": {
                    "code": "protocol_invalid_json",
                    "message": "ignored malformed notification",
                },
            }
        )

        self.assertTrue(service.health()["ok"])


class ManagedThreadStoreTest(unittest.TestCase):
    def test_round_trip_and_file_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            store = ManagedThreadStore(path)
            store.save({"b", "a"})

            self.assertEqual(store.load(), {"a", "b"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
