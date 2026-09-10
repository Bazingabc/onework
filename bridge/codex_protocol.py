"""Version-gated JSONL client for the experimental Codex App Server."""

import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


CLIENT_METHODS = frozenset(
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
    }
)

SERVER_REQUEST_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
    }
)

SAFE_NOTIFICATION_METHODS = frozenset(
    {
        "error",
        "thread/started",
        "thread/status/changed",
        "thread/tokenUsage/updated",
        "turn/started",
        "turn/plan/updated",
        "turn/diff/updated",
        "turn/completed",
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/commandExecution/outputDelta",
        "item/commandExecution/terminalInteraction",
        "item/fileChange/outputDelta",
        "item/fileChange/patchUpdated",
        "item/mcpToolCall/progress",
    }
)

_VERSION_PATTERN = re.compile(r"(?:codex-cli\s+)?(\d+)\.(\d+)\.(\d+)")
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "env",
    "environment",
    "environmentvariables",
    "environment_variables",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
}
_SENSITIVE_SUFFIXES = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
_NORMALIZED_SENSITIVE_KEYS = {
    re.sub(r"[^a-z0-9]", "", item) for item in _SENSITIVE_KEYS
}
_OMITTED_PROTOCOL_VALUE = "[OMITTED]"
_DROP_PROTOCOL_VALUE = object()


class CodexProtocolError(RuntimeError):
    def __init__(self, code: str, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


def parse_codex_version(output: str) -> Tuple[int, int, int]:
    match = _VERSION_PATTERN.search(output or "")
    if not match:
        raise CodexProtocolError(
            "protocol_version_unknown", "无法识别 Codex CLI 版本"
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def format_version(value: Tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in value)


def _sanitize_protocol_value(value: Any, max_string: int) -> Any:
    if isinstance(value, dict):
        item_type = re.sub(r"[^a-z0-9]", "", str(value.get("type") or "").lower())
        if "reasoning" in item_type or "chainofthought" in item_type:
            return _DROP_PROTOCOL_VALUE
        cleaned: Dict[str, Any] = {}
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if (
                normalized in _NORMALIZED_SENSITIVE_KEYS
                or normalized.endswith(_SENSITIVE_SUFFIXES)
            ):
                cleaned[str(key)] = "[REDACTED]"
            elif "reasoning" in normalized or "chainofthought" in normalized:
                continue
            elif normalized == "aggregatedoutput":
                cleaned[str(key)] = _OMITTED_PROTOCOL_VALUE
            else:
                sanitized = _sanitize_protocol_value(child, max_string)
                if sanitized is not _DROP_PROTOCOL_VALUE:
                    cleaned[str(key)] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_items = []
        for item in value[:200]:
            sanitized = _sanitize_protocol_value(item, max_string)
            if sanitized is not _DROP_PROTOCOL_VALUE:
                cleaned_items.append(sanitized)
        return cleaned_items
    if isinstance(value, str):
        if len(value) > max_string:
            return value[:max_string] + "…[truncated]"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_string]


def sanitize_protocol_value(value: Any, max_string: int = 8192) -> Any:
    """Bound, redact, and remove private reasoning before persistence or logs."""
    sanitized = _sanitize_protocol_value(value, max_string)
    return None if sanitized is _DROP_PROTOCOL_VALUE else sanitized


def normalize_notification(method: str, params: Any) -> Optional[Dict[str, Any]]:
    """Convert protocol notifications to a small, stable application event."""
    if method.startswith("item/reasoning/") or method not in SAFE_NOTIFICATION_METHODS:
        return None
    kind_by_method = {
        "item/agentMessage/delta": "message.delta",
        "item/plan/delta": "plan.delta",
        "turn/plan/updated": "plan.updated",
        "item/commandExecution/outputDelta": "tool.command_output",
        "item/commandExecution/terminalInteraction": "tool.command_interaction",
        "item/fileChange/outputDelta": "tool.file_output",
        "item/fileChange/patchUpdated": "tool.file_change",
        "item/mcpToolCall/progress": "tool.progress",
        "turn/started": "turn.started",
        "turn/completed": "turn.completed",
        "error": "error",
    }
    return {
        "kind": kind_by_method.get(method, method.replace("/", ".")),
        "payload": sanitize_protocol_value(params or {}),
    }


def normalize_server_request(message: Dict[str, Any]) -> Dict[str, Any]:
    method = str(message.get("method") or "")
    if method not in SERVER_REQUEST_METHODS:
        raise CodexProtocolError(
            "protocol_server_method_unsupported",
            "App Server 请求了未允许的方法: %s" % method,
        )
    params = sanitize_protocol_value(message.get("params") or {})
    kind_by_method = {
        "item/commandExecution/requestApproval": "command",
        "item/fileChange/requestApproval": "file_change",
        "item/permissions/requestApproval": "permissions",
        "item/tool/requestUserInput": "user_input",
    }
    return {
        "request_id": message["id"],
        "method": method,
        "kind": kind_by_method[method],
        "params": params,
    }


class CodexAppServerClient:
    """A narrow, correlated client that never exposes raw App Server payloads."""

    def __init__(
        self,
        executable: str,
        *,
        command: Optional[Iterable[str]] = None,
        minimum_version: str = "0.144.0",
        maximum_version: str = "0.145.0",
        request_timeout: float = 15.0,
        max_line_bytes: int = 1_000_000,
        event_handler: Optional[Callable[[Dict[str, Any]], None]] = None,
        server_request_handler: Optional[Callable[[Dict[str, Any]], None]] = None,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        version_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.executable = executable
        self.command = list(command) if command else [executable, "app-server", "--stdio"]
        self.minimum_version = parse_codex_version(minimum_version)
        self.maximum_version = parse_codex_version(maximum_version)
        self.request_timeout = float(request_timeout)
        self.max_line_bytes = int(max_line_bytes)
        self.event_handler = event_handler or (lambda event: None)
        self.server_request_handler = server_request_handler
        self.process_factory = process_factory
        self.version_runner = version_runner
        self.process: Optional[subprocess.Popen] = None
        self.cli_version: Optional[str] = None
        self.initialized = False
        self._next_request_id = 1
        self._pending: Dict[str, "queue.Queue[Dict[str, Any]]"] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None
        self._closed = threading.Event()
        self.stderr_tail = ""
        self._paged_history: Optional[bool] = None

    def read_thread(self, thread_id: str) -> Dict[str, Any]:
        """Read a bounded recent display window, never hydrate modern full histories.

        Older servers may explicitly reject pagination; only that precise
        unsupported-method response allows the legacy path (not timeouts/errors).
        """
        if self._paged_history is False:
            return self.request('thread/read', {'threadId': thread_id, 'includeTurns': True})
        for _ in range(2):
            before = self.request('thread/read', {'threadId': thread_id, 'includeTurns': False})
            thread = before.get('thread') if isinstance(before, dict) else None
            if not isinstance(thread, dict) or thread.get('id') != thread_id:
                raise CodexProtocolError('protocol_invalid_response', 'Codex 会话元信息无效')
            try:
                page = self.request('thread/turns/list', {'threadId': thread_id, 'limit': 4,
                                    'sortDirection': 'desc', 'itemsView': 'summary'})
            except CodexProtocolError as exc:
                if exc.code == 'protocol_remote_error' and (exc.data or {}).get('remote_code') == -32601:
                    self._paged_history = False
                    return self.request('thread/read', {'threadId': thread_id, 'includeTurns': True})
                raise
            self._paged_history = True
            turns = page.get('data') if isinstance(page, dict) else None
            if (not isinstance(turns, list) or len(turns) > 4
                    or any(not isinstance(t, dict) or not t.get('id')
                           or not isinstance(t.get('items'), list)
                           or t.get('itemsView') not in ('summary', 'full') for t in turns)):
                raise CodexProtocolError('protocol_invalid_response', 'Codex 最近轮次响应无效，未使用旧历史')
            after = self.request('thread/read', {'threadId': thread_id, 'includeTurns': False})
            latest = after.get('thread') if isinstance(after, dict) else None
            if not isinstance(latest, dict) or latest.get('id') != thread_id:
                raise CodexProtocolError('protocol_invalid_response', 'Codex 会话校验响应无效')
            if all(thread.get(key) == latest.get(key) for key in ('updatedAt', 'status')):
                return {'thread': {**latest, 'turns': list(reversed(turns))}}
        raise CodexProtocolError('protocol_snapshot_changed', '会话正在变化，请同步最新状态后重试')

    def resume_thread(self, thread_id: str) -> Dict[str, Any]:
        if self._paged_history is None:
            self.read_thread(thread_id)  # Read-only capability negotiation before attaching.
        params: Dict[str, Any] = {'threadId': thread_id}
        if self._paged_history:
            params['excludeTurns'] = True
        result = self.request('thread/resume', params)
        if self._paged_history and isinstance(result, dict) and isinstance(result.get('thread'), dict):
            # An intentionally empty history must not overwrite the caller's
            # freshly verified recent window when it merges resume metadata.
            result = {**result, 'thread': {k: v for k, v in result['thread'].items() if k != 'turns'}}
        return result

    def probe_version(self) -> str:
        completed = self.version_runner(
            [self.executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            raise CodexProtocolError(
                "protocol_version_probe_failed", output or "Codex CLI 版本检查失败"
            )
        version = parse_codex_version(output)
        if version < self.minimum_version or version >= self.maximum_version:
            raise CodexProtocolError(
                "protocol_version_unsupported",
                "Codex CLI %s 不在支持范围 [%s, %s)"
                % (
                    format_version(version),
                    format_version(self.minimum_version),
                    format_version(self.maximum_version),
                ),
            )
        self.cli_version = format_version(version)
        return self.cli_version

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        self.probe_version()
        try:
            self.process = self.process_factory(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            raise CodexProtocolError(
                "protocol_start_failed", "无法启动 Codex App Server"
            ) from exc
        self._closed.clear()
        self._reader = threading.Thread(
            target=self._read_stdout, name="codex-app-server-reader", daemon=True
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name="codex-app-server-stderr", daemon=True
        )
        self._reader.start()
        self._stderr_reader.start()

    def initialize(self) -> Dict[str, Any]:
        self.start()
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "one-ripple-agent-runtime",
                    "title": "One Ripple Agent Runtime",
                    "version": "0.3.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "requestAttestation": False,
                },
            },
            allow_before_initialize=True,
        )
        self.notify("initialized", {}, allow_before_initialize=True)
        self.initialized = True
        return sanitize_protocol_value(result)

    def _write_message(self, message: Dict[str, Any]) -> None:
        process = self.process
        if not process or process.poll() is not None or not process.stdin:
            raise CodexProtocolError(
                "protocol_offline", "Codex App Server 未运行"
            )
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        with self._write_lock:
            try:
                process.stdin.write(encoded)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise CodexProtocolError(
                    "protocol_offline", "Codex App Server 连接已断开"
                ) from exc

    def request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        allow_before_initialize: bool = False,
    ) -> Any:
        if method not in CLIENT_METHODS:
            raise CodexProtocolError(
                "protocol_method_unsupported", "客户端方法未列入允许清单: %s" % method
            )
        if not allow_before_initialize and not self.initialized:
            raise CodexProtocolError(
                "protocol_not_initialized", "App Server 尚未完成 initialize"
            )
        with self._pending_lock:
            request_id = str(self._next_request_id)
            self._next_request_id += 1
            response_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
        try:
            self._write_message({"id": request_id, "method": method, "params": params or {}})
            try:
                response = response_queue.get(
                    timeout=self.request_timeout if timeout is None else timeout
                )
            except queue.Empty as exc:
                raise CodexProtocolError(
                    "protocol_timeout", "App Server 请求超时: %s" % method
                ) from exc
            if "error" in response:
                error = response.get("error") or {}
                raise CodexProtocolError(
                    "protocol_remote_error",
                    str(error.get("message") or "App Server 返回错误"),
                    {
                        "remote_code": error.get("code"),
                        "method": method,
                        "data": sanitize_protocol_value(error.get("data")),
                    },
                )
            return response.get("result")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notify(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        allow_before_initialize: bool = False,
    ) -> None:
        if method != "initialized":
            raise CodexProtocolError(
                "protocol_method_unsupported", "客户端通知未列入允许清单"
            )
        if not allow_before_initialize and not self.initialized:
            raise CodexProtocolError(
                "protocol_not_initialized", "App Server 尚未完成 initialize"
            )
        message: Dict[str, Any] = {"method": method}
        if params:
            message["params"] = params
        self._write_message(message)

    def respond_server_request(
        self, request_id: Any, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None
    ) -> None:
        if error is not None:
            self._write_message({"id": request_id, "error": error})
        else:
            self._write_message({"id": request_id, "result": result or {}})

    def _read_stdout(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        try:
            while not self._closed.is_set():
                line = process.stdout.readline(self.max_line_bytes + 1)
                if not line:
                    break
                if len(line) > self.max_line_bytes:
                    self._emit_local_error(
                        "protocol_output_too_large", "App Server 单行输出超过安全上限"
                    )
                    self.close()
                    return
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._emit_local_error(
                        "protocol_invalid_json", "App Server 输出不是有效 JSONL"
                    )
                    continue
                self._dispatch_message(message)
        finally:
            self._fail_pending("Codex App Server 已退出")
            if not self._closed.is_set():
                self._emit_local_error(
                    "protocol_process_exited", "Codex App Server 已意外退出"
                )

    def _read_stderr(self) -> None:
        process = self.process
        if not process or not process.stderr:
            return
        chunks: List[str] = []
        while not self._closed.is_set():
            chunk = process.stderr.read(4096)
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="replace"))
            joined = "".join(chunks)
            if len(joined) > 16384:
                joined = joined[-16384:]
                chunks = [joined]
            self.stderr_tail = joined

    def _dispatch_message(self, message: Any) -> None:
        if not isinstance(message, dict):
            self._emit_local_error(
                "protocol_invalid_message", "App Server 消息必须是对象"
            )
            return
        request_id = message.get("id")
        if request_id is not None and ("result" in message or "error" in message):
            with self._pending_lock:
                pending = self._pending.get(str(request_id))
            if pending:
                pending.put(message)
            return
        method = message.get("method")
        if not isinstance(method, str):
            self._emit_local_error(
                "protocol_invalid_message", "App Server 消息缺少 method"
            )
            return
        if request_id is not None:
            try:
                normalized = normalize_server_request(message)
            except CodexProtocolError as exc:
                self.respond_server_request(
                    str(request_id),
                    error={"code": -32601, "message": str(exc)},
                )
                self._emit_local_error(exc.code, str(exc))
                return
            if not self.server_request_handler:
                self.respond_server_request(
                    str(request_id),
                    error={"code": -32001, "message": "No human decision handler is available"},
                )
                return
            threading.Thread(
                target=self.server_request_handler,
                args=(normalized,),
                name="codex-app-server-request",
                daemon=True,
            ).start()
            return
        normalized_event = normalize_notification(method, message.get("params"))
        if normalized_event:
            self.event_handler(normalized_event)

    def _emit_local_error(self, code: str, message: str) -> None:
        self.event_handler(
            {"kind": "protocol.error", "payload": {"code": code, "message": message}}
        )

    def _fail_pending(self, message: str) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(
                    {"error": {"code": -32000, "message": message}}
                )
            except queue.Full:
                pass

    def close(self) -> None:
        self._closed.set()
        process = self.process
        if process:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for stream in (process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
        current = threading.current_thread()
        for thread in (self._reader, self._stderr_reader):
            if thread and thread is not current and thread.is_alive():
                thread.join(timeout=1)
        self._fail_pending("Codex App Server 已关闭")
        self.initialized = False

    def __enter__(self) -> "CodexAppServerClient":
        self.initialize()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def wait_for_exit(self, timeout: float = 5.0) -> Optional[int]:
        process = self.process
        if not process:
            return None
        deadline = time.time() + timeout
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.01)
        return process.poll()
