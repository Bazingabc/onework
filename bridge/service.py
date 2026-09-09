"""Codex session projection and command routing for Agent Views."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .attention import classify_agent_message


class AgentViewsError(RuntimeError):
    """A stable error safe to expose through the local HTTP API."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = int(status)


class ManagedThreadStore:
    """Persists bridge attachment and per-message attention decisions."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()

    def _read_payload(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"managedThreadIds": [], "dismissedAttention": []}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise AgentViewsError(
                "managed_state_invalid",
                "Agent Views 状态不可读取",
                500,
            ) from exc
        if not isinstance(payload, dict):
            raise AgentViewsError(
                "managed_state_invalid",
                "Agent Views 状态格式无效",
                500,
            )
        values = payload.get("managedThreadIds", [])
        dismissed = payload.get("dismissedAttention", [])
        if (
            not isinstance(values, list)
            or not all(isinstance(item, str) for item in values)
            or not isinstance(dismissed, list)
            or not all(isinstance(item, str) for item in dismissed)
        ):
            raise AgentViewsError(
                "managed_state_invalid",
                "Agent Views 状态格式无效",
                500,
            )
        return {"managedThreadIds": values, "dismissedAttention": dismissed}

    def load(self) -> set[str]:
        with self._lock:
            return {
                item
                for item in self._read_payload()["managedThreadIds"]
                if item
            }

    def load_dismissed(self) -> set[str]:
        with self._lock:
            return {
                item
                for item in self._read_payload()["dismissedAttention"]
                if item
            }

    def save(
        self,
        thread_ids: Iterable[str],
        dismissed_attention: Optional[Iterable[str]] = None,
    ) -> None:
        values = sorted({str(item) for item in thread_ids if item})
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if dismissed_attention is None:
                dismissed = self._read_payload()["dismissedAttention"]
            else:
                dismissed = sorted(
                    {str(item) for item in dismissed_attention if item}
                )[-500:]
            descriptor, temporary = tempfile.mkstemp(
                prefix=".agent-views-", suffix=".json", dir=str(parent)
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "managedThreadIds": values,
                            "dismissedAttention": dismissed,
                        },
                        handle,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


class AgentViewsService:
    """Owns one protocol client and exposes a conservative session model."""

    SOURCE_KINDS = ["cli", "vscode", "exec", "appServer", "unknown"]
    MAX_LIST_PAGES = 5
    MAX_MESSAGE_TEXT = 8_000

    def __init__(
        self,
        protocol: Any,
        *,
        workspace: Path,
        state_path: Path,
        window_minutes: int = 60,
        clock: Any = time.time,
    ):
        resolved_workspace = Path(workspace).expanduser().resolve()
        if not resolved_workspace.is_dir():
            raise AgentViewsError(
                "workspace_invalid", "默认工作区不存在或不是目录", 400
            )
        if not 1 <= int(window_minutes) <= 24 * 60:
            raise AgentViewsError(
                "window_invalid", "会话时间窗口必须在 1 到 1440 分钟之间", 400
            )
        self.protocol = protocol
        self.workspace = resolved_workspace
        self.window_minutes = int(window_minutes)
        self.clock = clock
        self.store = ManagedThreadStore(state_path)
        self._lock = threading.RLock()
        self._managed = self.store.load()
        self._attached: set[str] = set()
        self._dismissed_attention = self.store.load_dismissed()
        self._statuses: Dict[str, Dict[str, Any]] = {}
        self._active_turns: Dict[str, str] = {}
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._pending_evictions: Dict[str, Dict[str, Any]] = {}
        self._created_threads: Dict[str, Dict[str, Any]] = {}
        self._thread_details: Dict[str, Dict[str, Any]] = {}
        self._detail_versions: Dict[str, int] = {}
        self._hook_states: Dict[str, Dict[str, Any]] = {}
        self._last_hook_event_at: Optional[int] = None
        self._online = False
        self._initialized: Dict[str, Any] = {}
        self._model: Optional[str] = None
        self._last_error: Optional[str] = None
        self.protocol.event_handler = self.handle_protocol_event
        self.protocol.server_request_handler = self.handle_server_request

    def start(self) -> Dict[str, Any]:
        try:
            initialized = self.protocol.initialize()
            models = self.protocol.request("model/list", {"limit": 100})
            values = models.get("data", []) if isinstance(models, dict) else []
            selected = next(
                (
                    str(item.get("id") or item.get("model"))
                    for item in values
                    if isinstance(item, dict) and item.get("isDefault")
                ),
                None,
            )
            if not selected:
                selected = next(
                    (
                        str(item.get("id") or item.get("model"))
                        for item in values
                        if isinstance(item, dict)
                        and (item.get("id") or item.get("model"))
                    ),
                    None,
                )
            if not selected:
                raise AgentViewsError(
                    "model_unavailable", "Codex app-server 未提供可用模型", 503
                )
            with self._lock:
                self._initialized = dict(initialized or {})
                self._model = selected
                self._online = True
                self._last_error = None
            raw_threads = self._list_raw_threads()
            self._resume_managed_threads(raw_threads)
            return self.health()
        except AgentViewsError:
            raise
        except Exception as exc:
            with self._lock:
                self._online = False
                self._last_error = str(exc)
            raise AgentViewsError(
                "codex_unavailable", "无法启动 Codex app-server: %s" % exc, 503
            ) from exc

    def close(self) -> None:
        self.protocol.close()
        with self._lock:
            self._online = False

    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "ok": self._online,
                "codexVersion": getattr(self.protocol, "cli_version", None),
                "serverUserAgent": self._initialized.get("userAgent"),
                "model": self._model,
                "workspace": str(self.workspace),
                "windowMinutes": self.window_minutes,
                "lastError": self._last_error,
                "hooks": {
                    "lastEventAt": self._last_hook_event_at,
                    "observedSessions": len(self._hook_states),
                },
                "pending": {
                    "active": len(self._pending),
                    "lastEvictionAt": max(
                        (
                            int(item.get("evictedAt") or 0)
                            for item in self._pending_evictions.values()
                        ),
                        default=None,
                    ),
                },
            }

    def _require_online(self) -> None:
        with self._lock:
            online = self._online
        if not online:
            raise AgentViewsError(
                "bridge_offline", "Agent Views Bridge 尚未连接 Codex", 503
            )

    @staticmethod
    def _timestamp(value: Any) -> int:
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return number // 1000 if number > 10_000_000_000 else number

    @staticmethod
    def _source_kind(value: Any) -> str:
        if isinstance(value, str):
            return value or "unknown"
        if isinstance(value, dict):
            return str(value.get("type") or value.get("kind") or "unknown")
        return "unknown"

    @staticmethod
    def _status_object(value: Any) -> Dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {"type": "notLoaded"}

    @staticmethod
    def _latest_turn(thread: Mapping[str, Any]) -> Dict[str, Any]:
        turns = [item for item in thread.get("turns") or [] if isinstance(item, dict)]
        if not turns:
            return {}
        turn = turns[-1]
        agent = None
        for item in reversed(turn.get("items") or []):
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                agent = item
                break
        return {
            "id": str(turn.get("id") or ""),
            "status": str(turn.get("status") or ""),
            "agent": dict(agent) if isinstance(agent, dict) else None,
        }

    @staticmethod
    def _message_fingerprint(turn_id: str, item: Mapping[str, Any]) -> str:
        payload = "%s\0%s" % (turn_id, str(item.get("text") or ""))
        return "inferred-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _dismissal_key(thread_id: str, message_id: str) -> str:
        return "%s\0%s" % (thread_id, message_id)

    @staticmethod
    def _pending_turn_id(pending: Mapping[str, Any]) -> str:
        params = pending.get("params")
        protocol_turn_id = params.get("turnId") if isinstance(params, Mapping) else None
        return str(protocol_turn_id or pending.get("_agent_views_turn_id") or "")

    @staticmethod
    def _latest_user_marker(thread: Mapping[str, Any]) -> str:
        turns = [item for item in thread.get("turns") or [] if isinstance(item, dict)]
        for turn in reversed(turns):
            turn_id = str(turn.get("id") or "")
            for item in reversed(turn.get("items") or []):
                if not isinstance(item, dict) or item.get("type") != "userMessage":
                    continue
                item_id = str(item.get("id") or "")
                if item_id:
                    return "%s\0%s" % (turn_id, item_id)
                content = item.get("content") if isinstance(item.get("content"), list) else []
                text = "\n".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
                return "%s\0%s" % (turn_id, digest)
        return ""

    def _normalize_pending(
        self, thread_id: str, request: Mapping[str, Any]
    ) -> Dict[str, Any]:
        normalized = dict(request)
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        with self._lock:
            active_turn = self._active_turns.get(thread_id)
            cached = self._thread_details.get(thread_id)
        cached_latest = self._latest_turn(cached or {})
        normalized["_agent_views_received_at"] = int(self.clock())
        normalized["_agent_views_turn_id"] = str(
            params.get("turnId") or active_turn or ""
        )
        normalized["_agent_views_baseline_turn_id"] = str(
            cached_latest.get("id") or ""
        )
        normalized["_agent_views_baseline_user"] = self._latest_user_marker(
            cached or {}
        )
        return normalized

    def _evict_pending(
        self, thread_id: str, reason: str, *, detach: bool = False
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            pending = self._pending.pop(thread_id, None)
            if not pending:
                return None
            pending_turn_id = self._pending_turn_id(pending)
            if pending_turn_id and self._active_turns.get(thread_id) == pending_turn_id:
                self._active_turns.pop(thread_id, None)
            if detach:
                self._attached.discard(thread_id)
            self._pending_evictions[thread_id] = {
                "requestId": str(pending.get("request_id") or ""),
                "turnId": pending_turn_id,
                "reason": reason,
                "evictedAt": int(self.clock()),
            }
            return pending

    def _evict_pending_for_progress(
        self,
        thread_id: str,
        reason: str,
        *,
        turn_id: str = "",
        keep_same_turn: bool = False,
        detach: bool = False,
    ) -> bool:
        with self._lock:
            pending = self._pending.get(thread_id)
            if not pending:
                return False
            pending_turn_id = self._pending_turn_id(pending)
            if (
                keep_same_turn
                and turn_id
                and pending_turn_id
                and turn_id == pending_turn_id
            ):
                return False
        return self._evict_pending(thread_id, reason, detach=detach) is not None

    def _reconcile_pending_with_thread(
        self, thread_id: str, thread: Mapping[str, Any]
    ) -> None:
        with self._lock:
            pending = self._pending.get(thread_id)
            if not pending:
                return
            pending = dict(pending)
        latest = self._latest_turn(thread)
        latest_turn_id = str(latest.get("id") or "")
        latest_status = str(latest.get("status") or "")
        pending_turn_id = self._pending_turn_id(pending)
        baseline_turn_id = str(pending.get("_agent_views_baseline_turn_id") or "")
        baseline_user = str(pending.get("_agent_views_baseline_user") or "")
        latest_user = self._latest_user_marker(thread)

        if not pending_turn_id and not baseline_turn_id and latest_turn_id:
            with self._lock:
                current = self._pending.get(thread_id)
                if current is not None and str(current.get("request_id")) == str(
                    pending.get("request_id")
                ):
                    current["_agent_views_baseline_turn_id"] = latest_turn_id
                    current["_agent_views_baseline_user"] = latest_user
            return

        if pending_turn_id and latest_turn_id and pending_turn_id != latest_turn_id:
            self._evict_pending(thread_id, "latest_turn_changed", detach=True)
            return
        if pending_turn_id and latest_turn_id == pending_turn_id and latest_status in {
            "completed",
            "interrupted",
            "failed",
            "cancelled",
            "canceled",
        }:
            self._evict_pending(thread_id, "pending_turn_settled", detach=True)
            return
        if (
            not pending_turn_id
            and baseline_turn_id
            and latest_turn_id
            and baseline_turn_id != latest_turn_id
        ):
            self._evict_pending(thread_id, "latest_turn_changed", detach=True)
            return
        if baseline_user and latest_user and baseline_user != latest_user:
            self._evict_pending(thread_id, "new_user_message", detach=True)

    def _pending_diagnostic(self, thread_id: str) -> Dict[str, Any]:
        with self._lock:
            pending = self._pending.get(thread_id)
            eviction = self._pending_evictions.get(thread_id)
            if pending:
                return {
                    "active": True,
                    "requestId": str(pending.get("request_id") or ""),
                    "turnId": self._pending_turn_id(pending),
                    "receivedAt": int(pending.get("_agent_views_received_at") or 0),
                }
            if eviction:
                return {
                    "active": False,
                    "lastRequestId": str(eviction.get("requestId") or ""),
                    "lastTurnId": str(eviction.get("turnId") or ""),
                    "lastEvictionReason": str(eviction.get("reason") or ""),
                    "lastEvictionAt": int(eviction.get("evictedAt") or 0),
                }
        return {"active": False}

    def _refresh_thread_detail(
        self, thread: Mapping[str, Any], *, force: bool = False, strict: bool = False
    ) -> Dict[str, Any]:
        thread_id = str(thread.get("id") or "")
        snapshot = dict(thread)
        if not thread_id:
            return snapshot
        version = self._timestamp(thread.get("updatedAt") or thread.get("createdAt"))
        if (
            isinstance(thread.get("turns"), list)
            and thread.get("turns")
            and not force
        ):
            with self._lock:
                self._thread_details[thread_id] = snapshot
                self._detail_versions[thread_id] = version
            return snapshot
        with self._lock:
            cached = self._thread_details.get(thread_id)
            cached_version = self._detail_versions.get(thread_id)
            locally_created = thread_id in self._created_threads and not cached
        if cached is not None and not force and cached_version == version:
            return {**cached, **snapshot, "turns": cached.get("turns", [])}
        if locally_created and not strict:
            return snapshot
        try:
            response = self.protocol.request(
                "thread/read", {"threadId": thread_id, "includeTurns": True}
            )
            detail = response.get("thread") if isinstance(response, dict) else None
            if not isinstance(detail, dict):
                if strict:
                    raise AgentViewsError('thread_read_failed', 'Codex 详情响应无效', 503)
                return snapshot
            merged = {**snapshot, **detail}
            with self._lock:
                self._thread_details[thread_id] = merged
                self._detail_versions[thread_id] = self._timestamp(
                    merged.get("updatedAt") or merged.get("createdAt")
                )
            return merged
        except Exception:
            if strict:
                raise
            return {**cached, **snapshot} if cached is not None else snapshot

    def _projection(
        self, thread_id: str, thread: Mapping[str, Any]
    ) -> Dict[str, Any]:
        self._reconcile_pending_with_thread(thread_id, thread)
        raw_status = self._status_object(thread.get("status"))
        updated_at = self._timestamp(thread.get("updatedAt") or thread.get("createdAt"))
        latest = self._latest_turn(thread)
        latest_agent = latest.get("agent") if isinstance(latest.get("agent"), dict) else None
        latest_turn_id = str(latest.get("id") or "")
        latest_turn_status = str(latest.get("status") or "")
        with self._lock:
            managed = thread_id in self._managed
            attached = thread_id in self._attached
            pending = self._pending.get(thread_id)
            observed_status = dict(self._statuses.get(thread_id) or raw_status)
            hook_state = dict(self._hook_states.get(thread_id) or {})
            dismissed = set(self._dismissed_attention)

        attention: Dict[str, Any] = {"type": "none", "evidence": []}
        if pending:
            params = pending.get("params") if isinstance(pending.get("params"), dict) else {}
            questions = params.get("questions") if isinstance(params.get("questions"), list) else []
            first = questions[0] if questions and isinstance(questions[0], dict) else {}
            options = first.get("options") if isinstance(first.get("options"), list) else []
            attention = {
                "type": "required_protocol",
                "responseMode": "choice" if options else "free_text",
                "question": str(first.get("question") or params.get("reason") or "需要你的处理")[:1_000],
                "options": [
                    str(option.get("label") or "")[:200]
                    for option in options
                    if isinstance(option, dict) and option.get("label")
                ],
                "messageId": "request:%s" % pending.get("request_id"),
                "evidence": ["server_request"],
            }
        elif latest_agent is not None:
            phase = latest_agent.get("phase")
            message_id = self._message_fingerprint(latest_turn_id, latest_agent)
            attention = classify_agent_message(
                str(latest_agent.get("text") or ""),
                message_id=message_id,
                phase=str(phase) if phase is not None else None,
                turn_status=latest_turn_status,
            )

        hook_is_current = bool(hook_state) and (
            not latest_turn_id
            or not hook_state.get("turnId")
            or hook_state.get("turnId") == latest_turn_id
            or int(hook_state.get("receivedAt") or 0) >= updated_at
        )
        if not pending and hook_is_current:
            hook_attention = hook_state.get("attention")
            if isinstance(hook_attention, dict):
                attention = dict(hook_attention)

        message_id = str(attention.get("messageId") or "")
        if message_id and self._dismissal_key(thread_id, message_id) in dismissed:
            attention = {
                "type": "none",
                "messageId": message_id,
                "evidence": ["dismissed_by_user"],
            }

        status_kind = str(observed_status.get("type") or "notLoaded")
        runtime = "unknown"
        if latest_turn_status == "failed" or status_kind == "systemError":
            runtime = "error"
        elif status_kind == "active" or latest_turn_status in {"inProgress", "in_progress"}:
            runtime = "running"
        elif latest_agent is not None and latest_agent.get("phase") == "final_answer":
            runtime = "settled"
        elif latest_agent is not None and latest_agent.get("phase") == "commentary":
            runtime = "running"
        elif managed and status_kind in {"idle", "notLoaded"}:
            runtime = "settled"
        elif latest_turn_status == "completed" and latest_agent is not None:
            runtime = "settled"

        if hook_is_current and hook_state.get("runtime"):
            runtime = str(hook_state["runtime"])

        if runtime == "error":
            control = {"type": "unavailable", "reason": "thread_error"}
        elif attached:
            control = {"type": "direct", "reason": "bridge_attached"}
        elif runtime == "settled":
            control = {"type": "attachable", "reason": "latest_turn_completed"}
        else:
            control = {"type": "busy_elsewhere", "reason": "other_client_may_be_active"}

        attention_type = str(attention.get("type") or "none")
        if attention_type == "required_protocol":
            approval = pending and pending.get("kind") != "user_input"
            if hook_state.get("event") == "PermissionRequest":
                approval = True
            legacy_type = "waiting_approval" if approval else "waiting_input"
            label = "等待审批" if approval else "等待输入"
        elif attention_type == "required_inferred":
            legacy_type, label = "waiting_input", "等你回复"
        elif attention_type == "possible_inferred":
            legacy_type, label = "possible_input", "可能需要你"
        elif runtime == "running":
            legacy_type, label = "running", "运行中"
        elif runtime == "error":
            legacy_type, label = "error", "异常"
        elif runtime == "settled":
            legacy_type, label = "idle", "最近完成"
        else:
            legacy_type, label = "unknown", "状态待确认"
        legacy_status: Dict[str, Any] = {
            "type": legacy_type,
            "label": label,
            "controllable": control["type"] in {"direct", "attachable"},
        }
        if observed_status.get("message"):
            legacy_status["message"] = str(observed_status["message"])[:500]
        return {
            "runtimeState": runtime,
            "attention": attention,
            "control": control,
            "status": legacy_status,
        }

    def _list_raw_threads(self) -> List[Dict[str, Any]]:
        self._require_online()
        cursor: Optional[str] = None
        values: List[Dict[str, Any]] = []
        for _ in range(self.MAX_LIST_PAGES):
            params: Dict[str, Any] = {
                "limit": 100,
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "sourceKinds": list(self.SOURCE_KINDS),
                "archived": False,
            }
            if cursor:
                params["cursor"] = cursor
            try:
                response = self.protocol.request("thread/list", params)
            except Exception as exc:
                raise AgentViewsError(
                    "thread_list_failed", "无法读取 Codex 会话列表: %s" % exc, 503
                ) from exc
            page = response.get("data", []) if isinstance(response, dict) else []
            values.extend(item for item in page if isinstance(item, dict))
            cursor = response.get("nextCursor") if isinstance(response, dict) else None
            if not cursor:
                break
        return values

    def _resume_managed_threads(self, threads: Iterable[Dict[str, Any]]) -> None:
        cutoff = int(self.clock()) - self.window_minutes * 60
        for thread in threads:
            thread_id = str(thread.get("id") or "")
            recency = self._timestamp(thread.get("updatedAt") or thread.get("createdAt"))
            if not thread_id or thread_id not in self._managed or recency < cutoff:
                continue
            status = self._status_object(thread.get("status"))
            if status.get("type") != "notLoaded":
                with self._lock:
                    self._statuses[thread_id] = status
                continue
            try:
                resumed = self.protocol.request("thread/resume", {"threadId": thread_id})
                resumed_thread = (
                    resumed.get("thread", {}) if isinstance(resumed, dict) else {}
                )
                resumed_status = self._status_object(resumed_thread.get("status"))
                if resumed_status.get("type") == "notLoaded":
                    resumed_status = {"type": "idle"}
                with self._lock:
                    self._attached.add(thread_id)
                    self._statuses[thread_id] = resumed_status
            except Exception as exc:
                with self._lock:
                    if "already has an active writer" in str(exc):
                        self._attached.discard(thread_id)
                        # An active-writer conflict is positive evidence that the
                        # thread is running in another Codex client. Keep writes
                        # disabled, but do not downgrade it to an unknown state.
                        self._statuses[thread_id] = {
                            "type": "active",
                            "activeFlags": [],
                        }
                    else:
                        self._statuses[thread_id] = {
                            "type": "systemError",
                            "message": "无法恢复受管会话: %s" % exc,
                        }

    def _pending_view(self, thread_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            pending = self._pending.get(thread_id)
            if not pending:
                return None
            params = dict(pending.get("params") or {})
            result: Dict[str, Any] = {
                "kind": pending.get("kind"),
                "requestId": str(pending.get("request_id")),
            }
        if pending.get("kind") == "user_input":
            questions = []
            for value in params.get("questions", []):
                if not isinstance(value, dict):
                    continue
                options = []
                for option in value.get("options") or []:
                    if isinstance(option, dict):
                        options.append(
                            {
                                "label": str(option.get("label") or "")[:200],
                                "description": str(option.get("description") or "")[:500],
                            }
                        )
                questions.append(
                    {
                        "id": str(value.get("id") or ""),
                        "header": str(value.get("header") or "")[:200],
                        "question": str(value.get("question") or "")[:1_000],
                        "options": options,
                    }
                )
            result["questions"] = questions
            result["autoResolutionMs"] = params.get("autoResolutionMs")
        else:
            result["reason"] = str(params.get("reason") or "需要人工审批")[:1_000]
            command = params.get("command")
            if isinstance(command, list):
                result["command"] = [str(item)[:500] for item in command[:20]]
            elif isinstance(command, str):
                result["command"] = command[:2_000]
            if pending.get("kind") == "permissions":
                permissions = params.get("permissions")
                if isinstance(permissions, dict):
                    # The user must see the exact requested profile before
                    # granting it. It has already passed protocol sanitizing.
                    result["permissions"] = permissions
        return result

    def _summary(self, thread: Mapping[str, Any]) -> Dict[str, Any]:
        thread_id = str(thread.get("id") or "")
        preview = str(thread.get("preview") or "").strip()
        name = str(thread.get("name") or "").strip()
        title = name or (preview.splitlines()[0][:100] if preview else "未命名会话")
        created_at = self._timestamp(thread.get("createdAt"))
        updated_at = self._timestamp(thread.get("updatedAt")) or created_at
        projection = self._projection(thread_id, thread)
        return {
            "id": thread_id,
            "title": title,
            "preview": preview[:500],
            "cwd": str(thread.get("cwd") or ""),
            "source": self._source_kind(thread.get("source")),
            "createdAt": created_at,
            "updatedAt": updated_at,
            # Kept for one Android compatibility cycle. Every Codex thread is
            # product-managed; bridge attachment lives in control.type.
            "ownership": "managed",
            "runtimeState": projection["runtimeState"],
            "attention": projection["attention"],
            "control": projection["control"],
            "status": projection["status"],
            "pending": self._pending_view(thread_id),
            "pendingDiagnostic": self._pending_diagnostic(thread_id),
        }

    def list_sessions(self, *, strict: bool = False) -> List[Dict[str, Any]]:
        cutoff = int(self.clock()) - self.window_minutes * 60
        values = self._list_raw_threads()
        indexed = {
            str(item.get("id")): dict(item)
            for item in values
            if item.get("id")
        }
        with self._lock:
            for thread_id, thread in self._created_threads.items():
                indexed.setdefault(thread_id, dict(thread))
        result = []
        for thread in indexed.values():
            recency = self._timestamp(thread.get("updatedAt") or thread.get("createdAt"))
            if recency >= cutoff:
                thread_id = str(thread.get("id") or "")
                with self._lock:
                    pending_requires_reconciliation = thread_id in self._pending
                result.append(
                    self._summary(
                        self._refresh_thread_detail(
                            thread, force=pending_requires_reconciliation, strict=strict
                        )
                    )
                )
        result.sort(key=lambda item: (item["updatedAt"], item["createdAt"]), reverse=True)
        return result

    def _safe_messages(self, thread: Mapping[str, Any]) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        for turn in thread.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            turn_id = str(turn.get("id") or "")
            for item in turn.get("items") or []:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                text = ""
                role = ""
                if item_type == "userMessage":
                    role = "user"
                    parts = []
                    for content in item.get("content") or []:
                        if isinstance(content, dict) and content.get("type") == "text":
                            parts.append(str(content.get("text") or ""))
                    text = "\n".join(parts)
                elif item_type == "agentMessage":
                    role = "agent"
                    text = str(item.get("text") or "")
                if role and text.strip():
                    projected = {
                        "id": str(item.get("id") or ""),
                        "turnId": turn_id,
                        "turnStatus": str(turn.get("status") or ""),
                        "role": role,
                        "text": text.strip()[: self.MAX_MESSAGE_TEXT],
                    }
                    if role == "agent" and item.get("phase") is not None:
                        projected["phase"] = str(item.get("phase"))
                    messages.append(projected)
        return messages[-50:]

    def get_session(self, thread_id: str, *, strict: bool = False) -> Dict[str, Any]:
        self._require_online()
        identifier = str(thread_id or "").strip()
        if not identifier:
            raise AgentViewsError("thread_id_required", "缺少会话 ID", 400)
        try:
            raw_threads = self._list_raw_threads()
            raw = next(
                (item for item in raw_threads if str(item.get("id") or "") == identifier),
                None,
            )
            if raw is not None:
                thread = self._refresh_thread_detail(raw, force=True, strict=strict)
            else:
                response = self.protocol.request(
                    "thread/read", {"threadId": identifier, "includeTurns": True}
                )
                thread = response.get("thread") if isinstance(response, dict) else None
        except Exception as exc:
            if strict:
                raise AgentViewsError('thread_read_failed', '无法核实最新会话，请稍后重试', 503) from exc
            with self._lock:
                thread = self._created_threads.get(identifier)
            if not thread:
                raise AgentViewsError(
                    "thread_read_failed", "无法读取 Codex 会话: %s" % exc, 404
                ) from exc
        if not isinstance(thread, dict):
            raise AgentViewsError("thread_not_found", "Codex 会话不存在", 404)
        return {**self._summary(thread), "messages": self._safe_messages(thread)}

    def create_session(self, initial_text: Optional[str] = None) -> Dict[str, Any]:
        self._require_online()
        with self._lock:
            model = self._model
        params = {
            "cwd": str(self.workspace),
            "model": model,
            "sandbox": "workspace-write",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "ephemeral": False,
            "serviceName": "agent-views-bridge",
        }
        try:
            response = self.protocol.request("thread/start", params)
        except Exception as exc:
            raise AgentViewsError(
                "thread_start_failed", "无法新建 Codex 会话: %s" % exc, 503
            ) from exc
        thread = response.get("thread") if isinstance(response, dict) else None
        thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
        if not thread_id:
            raise AgentViewsError(
                "protocol_invalid_response", "thread/start 未返回会话 ID", 503
            )
        now = int(self.clock())
        snapshot = dict(thread)
        snapshot.setdefault("id", thread_id)
        snapshot.setdefault("createdAt", now)
        snapshot.setdefault("updatedAt", now)
        snapshot.setdefault("cwd", str(self.workspace))
        snapshot.setdefault("source", {"type": "appServer"})
        snapshot.setdefault("status", {"type": "idle"})
        with self._lock:
            self._managed.add(thread_id)
            self._attached.add(thread_id)
            self._created_threads[thread_id] = snapshot
            self._statuses[thread_id] = {"type": "idle"}
            managed = set(self._managed)
        self.store.save(managed)
        if initial_text and initial_text.strip():
            self.send(thread_id, initial_text)
        return self._summary(snapshot)

    def _require_direct_connection(self, thread_id: str) -> None:
        with self._lock:
            attached = thread_id in self._attached
        if not attached:
            raise AgentViewsError(
                "session_not_direct",
                "该审批仍由原 Codex 客户端持有，请在原端处理",
                409,
            )

    def _read_thread_for_write(self, thread_id: str) -> Dict[str, Any]:
        try:
            response = self.protocol.request(
                "thread/read", {"threadId": thread_id, "includeTurns": True}
            )
            thread = response.get("thread") if isinstance(response, dict) else None
        except Exception as exc:
            raise AgentViewsError(
                "thread_read_failed", "无法读取 Codex 会话: %s" % exc, 404
            ) from exc
        if not isinstance(thread, dict):
            raise AgentViewsError("thread_not_found", "Codex 会话不存在", 404)
        with self._lock:
            self._thread_details[thread_id] = dict(thread)
            self._detail_versions[thread_id] = self._timestamp(
                thread.get("updatedAt") or thread.get("createdAt")
            )
        return dict(thread)

    def _ensure_attached_for_send(
        self,
        thread_id: str,
        *,
        expected_message_id: Optional[str] = None,
        expected_updated_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            attached = thread_id in self._attached
            cached = self._thread_details.get(thread_id)
            created = self._created_threads.get(thread_id)
        requires_validation = bool(expected_message_id) or expected_updated_at is not None
        if attached and not requires_validation:
            return dict(cached or created or {"id": thread_id, "cwd": str(self.workspace)})

        thread = self._read_thread_for_write(thread_id)
        summary = self._summary(thread)
        attention = summary.get("attention") if isinstance(summary.get("attention"), dict) else {}
        actual_message_id = str(attention.get("messageId") or "")
        actual_updated_at = int(summary.get("updatedAt") or 0)
        if expected_message_id and expected_message_id != actual_message_id:
            raise AgentViewsError(
                "session_changed",
                "会话已有新进展，旧答案未发送",
                409,
            )
        if expected_updated_at is not None and int(expected_updated_at) != actual_updated_at:
            raise AgentViewsError(
                "session_changed",
                "会话已有新进展，旧答案未发送",
                409,
            )
        if attached:
            return thread
        control = summary.get("control") if isinstance(summary.get("control"), dict) else {}
        if control.get("type") != "attachable":
            raise AgentViewsError(
                "session_not_attachable",
                "该会话仍由原客户端持有，暂不能从平板写入",
                409,
            )
        try:
            response = self.protocol.request("thread/resume", {"threadId": thread_id})
            resumed = response.get("thread") if isinstance(response, dict) else None
        except Exception as exc:
            raise AgentViewsError(
                "thread_resume_failed", "无法接入原 Codex 会话: %s" % exc, 503
            ) from exc
        if not isinstance(resumed, dict):
            raise AgentViewsError(
                "protocol_invalid_response", "thread/resume 未返回会话", 503
            )
        merged = {**thread, **resumed}
        with self._lock:
            self._managed.add(thread_id)
            self._attached.add(thread_id)
            self._thread_details[thread_id] = merged
            self._detail_versions[thread_id] = self._timestamp(
                merged.get("updatedAt") or merged.get("createdAt")
            )
            resumed_status = self._status_object(merged.get("status"))
            self._statuses[thread_id] = (
                {"type": "idle"}
                if resumed_status.get("type") == "notLoaded"
                else resumed_status
            )
            managed_ids = set(self._managed)
            dismissed = set(self._dismissed_attention)
        self.store.save(managed_ids, dismissed)
        return merged

    def _collaboration_mode(self, mode: str) -> Dict[str, Any]:
        """Build the app-server mode object using the selected model."""
        with self._lock:
            model = self._model
        return {
            "mode": mode,
            "settings": {
                "model": model,
                "reasoning_effort": None,
                "developer_instructions": None,
            },
        }

    def _answer_user_input(
        self,
        thread_id: str,
        pending: Mapping[str, Any],
        *,
        text: Optional[str],
        answers: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        questions = (pending.get("params") or {}).get("questions") or []
        normalized: Dict[str, Dict[str, List[str]]] = {}
        if answers:
            for key, value in answers.items():
                answer_id = str(key).strip()
                raw_values = value if isinstance(value, list) else [value]
                if any(
                    item is None or isinstance(item, (dict, list))
                    for item in raw_values
                ):
                    raise AgentViewsError(
                        "answers_invalid", "问题回答必须是文本或文本数组", 400
                    )
                values = [str(item).strip() for item in raw_values]
                values = [item for item in values if item]
                if answer_id and values:
                    normalized[answer_id] = {"answers": values}
        elif text is not None:
            if len(questions) != 1 or not isinstance(questions[0], dict):
                raise AgentViewsError(
                    "structured_answers_required",
                    "当前包含多个问题，请逐项选择或填写",
                    409,
                )
            question_id = str(questions[0].get("id") or "")
            normalized[question_id] = {"answers": [text]}
        expected = {
            str(item.get("id") or "")
            for item in questions
            if isinstance(item, dict) and item.get("id")
        }
        if not expected or not expected.issubset(normalized):
            raise AgentViewsError(
                "answers_incomplete", "请回答全部必填问题", 400
            )
        try:
            self.protocol.respond_server_request(
                pending.get("request_id"), {"answers": normalized}
            )
        except Exception as exc:
            raise AgentViewsError(
                "answer_failed", "无法提交 Codex 回答: %s" % exc, 503
            ) from exc
        with self._lock:
            if self._pending.get(thread_id) is pending:
                self._evict_pending(thread_id, "answered_by_agent_views")
                self._statuses[thread_id] = {"type": "active", "activeFlags": []}
        return {"action": "answered", "threadId": thread_id}

    def send(
        self,
        thread_id: str,
        text: Optional[str] = None,
        *,
        answers: Optional[Mapping[str, Any]] = None,
        expected_message_id: Optional[str] = None,
        expected_updated_at: Optional[int] = None,
        expected_pending_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._require_online()
        identifier = str(thread_id or "").strip()
        if not identifier:
            raise AgentViewsError("thread_id_required", "缺少会话 ID", 400)
        if answers is not None and not isinstance(answers, Mapping):
            raise AgentViewsError(
                "answers_invalid", "问题回答必须是 JSON 对象", 400
            )
        value = str(text or "").strip()
        if not value and not answers:
            raise AgentViewsError("message_required", "请输入要发送的内容", 400)
        if len(value) > 20_000:
            raise AgentViewsError("message_too_long", "单次输入不能超过 20000 字符", 400)
        parsed_expected_updated_at: Optional[int] = None
        if expected_updated_at is not None:
            try:
                parsed_expected_updated_at = int(expected_updated_at)
            except (TypeError, ValueError) as exc:
                raise AgentViewsError(
                    "expected_version_invalid", "会话版本格式无效", 400
                ) from exc
        thread = self._ensure_attached_for_send(
            identifier,
            expected_message_id=str(expected_message_id or "") or None,
            expected_updated_at=parsed_expected_updated_at,
        )
        with self._lock:
            pending = self._pending.get(identifier)
            active_turn = self._active_turns.get(identifier)
            if expected_pending_id is not None and str((pending or {}).get('request_id') or '') != expected_pending_id:
                raise AgentViewsError('pending_changed', '待处理问题已变化，请刷新后再操作', 409)
            created = self._created_threads.get(identifier)
            first_managed_turn = created is not None and not created.get("preview")
        if pending:
            if pending.get("kind") != "user_input":
                raise AgentViewsError(
                    "approval_response_required", "当前等待审批，不能作为文本回答", 409
                )
            return self._answer_user_input(
                identifier, pending, text=value if value else None, answers=answers
            )
        params = {"threadId": identifier, "input": [{"type": "text", "text": value}]}
        try:
            if active_turn:
                response = self.protocol.request(
                    "turn/steer", {**params, "expectedTurnId": active_turn}
                )
                return {
                    "action": "steered",
                    "threadId": identifier,
                    "turnId": str(
                        (response or {}).get("turnId")
                        or (response or {}).get("turn", {}).get("id")
                        or active_turn
                    ),
                }
            turn_params = {
                **params,
                "cwd": str(thread.get("cwd") or self.workspace),
                "model": self._model,
                "approvalPolicy": "on-request",
            }
            # Begin tablet-created work in Plan mode so Codex can emit
            # requestUserInput. The fixed “继续” action is the hand-off to
            # Default mode, where implementation can proceed.
            if value == "继续":
                turn_params["collaborationMode"] = self._collaboration_mode("default")
            elif first_managed_turn:
                turn_params["collaborationMode"] = self._collaboration_mode("plan")
            response = self.protocol.request("turn/start", turn_params)
        except Exception as exc:
            raise AgentViewsError(
                "message_send_failed", "无法发送到 Codex: %s" % exc, 503
            ) from exc
        turn = response.get("turn") if isinstance(response, dict) else None
        turn_id = str(turn.get("id") or "") if isinstance(turn, dict) else ""
        if not turn_id:
            raise AgentViewsError(
                "protocol_invalid_response", "turn/start 未返回 turn ID", 503
            )
        with self._lock:
            self._active_turns[identifier] = turn_id
            self._statuses[identifier] = {"type": "active", "activeFlags": []}
            self._hook_states[identifier] = {
                "event": "TabletPromptSubmit",
                "turnId": turn_id,
                "runtime": "running",
                "attention": {"type": "none", "evidence": ["tablet_reply"]},
                "receivedAt": int(self.clock()),
            }
            if identifier in self._created_threads:
                self._created_threads[identifier]["updatedAt"] = int(self.clock())
                if not self._created_threads[identifier].get("preview"):
                    self._created_threads[identifier]["preview"] = value[:500]
        return {"action": "started", "threadId": identifier, "turnId": turn_id}

    def respond_approval(self, thread_id: str, decision: str, *, expected_pending_id: Optional[str] = None) -> Dict[str, Any]:
        self._require_online()
        identifier = str(thread_id or "").strip()
        self._require_direct_connection(identifier)
        allowed = {"accept", "acceptForSession", "decline", "cancel"}
        if decision not in allowed:
            raise AgentViewsError("approval_decision_invalid", "审批决定无效", 400)
        with self._lock:
            pending = self._pending.get(identifier)
            if expected_pending_id is not None and str((pending or {}).get('request_id') or '') != expected_pending_id:
                raise AgentViewsError('pending_changed', '待审批请求已变化，请刷新后再操作', 409)
        if not pending or pending.get("kind") == "user_input":
            raise AgentViewsError("approval_not_pending", "当前没有待处理审批", 409)
        kind = pending.get("kind")
        response: Dict[str, Any]
        if kind == "permissions":
            params = pending.get("params")
            requested = params.get("permissions") if isinstance(params, dict) else None
            if not isinstance(requested, dict):
                raise AgentViewsError(
                    "permission_request_invalid", "Codex 权限请求格式无效", 409
                )
            response = {
                "permissions": requested if decision in {"accept", "acceptForSession"} else {},
                "scope": "session" if decision == "acceptForSession" else "turn",
            }
        elif kind in {"command", "file_change"}:
            response = {"decision": decision}
        else:
            raise AgentViewsError(
                "approval_kind_unsupported", "当前审批类型暂不支持", 409
            )
        try:
            self.protocol.respond_server_request(
                pending.get("request_id"), response
            )
        except Exception as exc:
            raise AgentViewsError(
                "approval_failed", "无法提交审批决定: %s" % exc, 503
            ) from exc
        with self._lock:
            if self._pending.get(identifier) is pending:
                self._evict_pending(identifier, "approved_by_agent_views")
                self._statuses[identifier] = {"type": "active", "activeFlags": []}
        return {"action": "approval_resolved", "threadId": identifier}

    @staticmethod
    def _thread_id(payload: Mapping[str, Any]) -> str:
        thread = payload.get("thread")
        nested_id = thread.get("id") if isinstance(thread, Mapping) else None
        return str(payload.get("threadId") or nested_id or "")

    def handle_hook_event(self, event: Mapping[str, Any]) -> Dict[str, Any]:
        allowed = {
            "SessionStart",
            "UserPromptSubmit",
            "Stop",
            "PermissionRequest",
            "PostToolUse",
            "Interrupt",
            "SessionEnd",
        }
        kind = str(event.get("hook_event_name") or "")
        thread_id = str(event.get("session_id") or "").strip()
        turn_id = str(event.get("turn_id") or "").strip()
        if kind not in allowed:
            raise AgentViewsError("hook_event_unsupported", "不支持的 Hook 事件", 400)
        if not thread_id or len(thread_id) > 200:
            raise AgentViewsError("hook_session_invalid", "Hook 会话 ID 无效", 400)
        if len(turn_id) > 200:
            raise AgentViewsError("hook_turn_invalid", "Hook turn ID 无效", 400)
        now = int(self.clock())

        runtime = "unknown"
        attention: Dict[str, Any] = {"type": "none", "evidence": ["hook_event"]}
        if kind == "Stop":
            runtime = "settled"
            text = str(event.get("last_assistant_message") or "")[: self.MAX_MESSAGE_TEXT]
            message_id = self._message_fingerprint(turn_id, {"text": text})
            attention = classify_agent_message(
                text,
                message_id=message_id,
                phase="final_answer",
                turn_status="completed",
            )
        elif kind == "UserPromptSubmit":
            runtime = "running"
            attention = {"type": "none", "evidence": ["user_prompt_submitted"]}
        elif kind == "PermissionRequest":
            runtime = "running"
            tool_name = str(event.get("tool_name") or "Codex")[:200]
            message_id = "permission-" + hashlib.sha256(
                (turn_id + "\0" + tool_name).encode("utf-8")
            ).hexdigest()[:20]
            attention = {
                "type": "required_protocol",
                "responseMode": "confirmation",
                "question": "%s 正在原客户端等待权限确认" % tool_name,
                "options": [],
                "messageId": message_id,
                "evidence": ["permission_hook"],
            }
        elif kind in {"PostToolUse", "Interrupt", "SessionEnd"}:
            runtime = "running" if kind == "PostToolUse" else "settled"
            attention = {"type": "none", "evidence": [kind]}

        hook_state = {
            "event": kind,
            "turnId": turn_id,
            "runtime": runtime,
            "attention": attention,
            "receivedAt": now,
        }
        if kind == "UserPromptSubmit":
            self._evict_pending_for_progress(
                thread_id,
                "user_prompt_submitted",
                turn_id=turn_id,
                keep_same_turn=True,
                detach=True,
            )
        elif kind == "PostToolUse":
            self._evict_pending_for_progress(
                thread_id, "tool_continued", turn_id=turn_id, detach=True
            )
        elif kind in {"Stop", "Interrupt", "SessionEnd"}:
            self._evict_pending_for_progress(
                thread_id, "turn_settled", turn_id=turn_id, detach=True
            )
        with self._lock:
            self._hook_states[thread_id] = hook_state
            self._last_hook_event_at = now
        return {"action": "recorded", "threadId": thread_id, "event": kind}

    def dismiss_attention(
        self, thread_id: str, message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        identifier = str(thread_id or "").strip()
        if not identifier:
            raise AgentViewsError("thread_id_required", "缺少会话 ID", 400)
        detail = self.get_session(identifier)
        attention = detail.get("attention") if isinstance(detail.get("attention"), dict) else {}
        current_message_id = str(attention.get("messageId") or "")
        expected = str(message_id or "").strip()
        if expected and expected != current_message_id:
            raise AgentViewsError("session_changed", "卡点已经变化，请刷新后重试", 409)
        if attention.get("type") not in {"required_inferred", "possible_inferred"}:
            raise AgentViewsError("attention_not_dismissible", "当前没有可忽略的文本卡点", 409)
        if not current_message_id:
            raise AgentViewsError("attention_id_missing", "卡点缺少消息标识", 409)
        key = self._dismissal_key(identifier, current_message_id)
        with self._lock:
            self._dismissed_attention.add(key)
            hook_state = self._hook_states.get(identifier)
            if isinstance(hook_state, dict):
                hook_attention = hook_state.get("attention")
                if (
                    isinstance(hook_attention, dict)
                    and hook_attention.get("messageId") == current_message_id
                ):
                    hook_state["attention"] = {
                        "type": "none",
                        "messageId": current_message_id,
                        "evidence": ["dismissed_by_user"],
                    }
            managed = set(self._managed)
            dismissed = set(self._dismissed_attention)
        self.store.save(managed, dismissed)
        return {"action": "attention_dismissed", "threadId": identifier}

    def handle_protocol_event(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        thread_id = self._thread_id(payload)
        if kind == "protocol.error":
            with self._lock:
                self._last_error = str(payload.get("message") or "Codex 协议错误")
                if payload.get("code") in {
                    "protocol_output_too_large",
                    "protocol_process_exited",
                }:
                    self._online = False
            return
        if not thread_id:
            return
        if kind == "thread.status.changed":
            with self._lock:
                self._statuses[thread_id] = self._status_object(payload.get("status"))
        elif kind == "turn.started":
            turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or payload.get("turnId") or "")
            self._evict_pending_for_progress(
                thread_id,
                "new_turn_started",
                turn_id=turn_id,
                keep_same_turn=True,
            )
            with self._lock:
                if turn_id:
                    self._active_turns[thread_id] = turn_id
                self._statuses[thread_id] = {"type": "active", "activeFlags": []}
                self._hook_states[thread_id] = {
                    "event": "turn.started",
                    "turnId": turn_id,
                    "runtime": "running",
                    "attention": {"type": "none", "evidence": ["turn_started"]},
                    "receivedAt": int(self.clock()),
                }
        elif kind == "turn.completed":
            turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
            status = str(turn.get("status") or "completed")
            self._evict_pending(thread_id, "turn_completed")
            with self._lock:
                self._active_turns.pop(thread_id, None)
                self._statuses[thread_id] = (
                    {"type": "idle"}
                    if status in {"completed", "interrupted"}
                    else {"type": "systemError", "message": "Turn %s" % status}
                )

    def handle_server_request(self, request: Dict[str, Any]) -> None:
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        thread_id = str(params.get("threadId") or "")
        if not thread_id:
            self.protocol.respond_server_request(
                request.get("request_id"),
                error={"code": -32602, "message": "Missing threadId"},
            )
            return
        normalized = self._normalize_pending(thread_id, request)
        with self._lock:
            self._managed.add(thread_id)
            self._attached.add(thread_id)
            previous = self._pending.get(thread_id)
            if (
                previous
                and str(previous.get("request_id"))
                == str(request.get("request_id"))
            ):
                for key in (
                    "_agent_views_received_at",
                    "_agent_views_turn_id",
                    "_agent_views_baseline_turn_id",
                    "_agent_views_baseline_user",
                ):
                    normalized[key] = previous.get(key)
            self._pending[thread_id] = normalized
            self._pending_evictions.pop(thread_id, None)
            self._statuses[thread_id] = {
                "type": "active",
                "activeFlags": [
                    "waitingOnUserInput"
                    if request.get("kind") == "user_input"
                    else "waitingOnApproval"
                ],
            }
            managed = set(self._managed)
            dismissed = set(self._dismissed_attention)
        self.store.save(managed, dismissed)
