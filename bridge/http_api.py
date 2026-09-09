"""Loopback-only HTTP facade for the Android Agent Views client."""

from __future__ import annotations

import json
import hmac
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlsplit

from .service import AgentViewsError, AgentViewsService
from .work import WorkService


CLIENT_HEADER = "X-Agent-Views-Client"
CLIENT_HEADER_VALUE = "android"
HOOK_HEADER = "X-Agent-Views-Hook"
MAX_BODY_BYTES = 64 * 1024


class AgentViewsHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: Any,
        service: AgentViewsService,
        hook_token: Optional[str] = None,
    ):
        self.service = service
        self.work = WorkService(getattr(service, 'protocol', None))
        self.hook_token = str(hook_token or "")
        super().__init__(server_address, AgentViewsRequestHandler)


class AgentViewsRequestHandler(BaseHTTPRequestHandler):
    server_version = "AgentViewsBridge/0.1"

    @property
    def service(self) -> AgentViewsService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("agent-views http: %s\n" % (fmt % args))

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, error: Exception) -> None:
        if isinstance(error, AgentViewsError):
            status = error.status
            code = error.code
            message = str(error)
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            code = "internal_error"
            message = "Agent Views Bridge 内部错误"
        self._send_json(
            status,
            {"error": {"code": code, "message": message}},
        )

    def _require_android_write(self) -> None:
        if self.headers.get("Origin"):
            raise AgentViewsError(
                "browser_write_forbidden", "不接受来自浏览器页面的写请求", 403
            )
        if self.headers.get(CLIENT_HEADER) != CLIENT_HEADER_VALUE:
            raise AgentViewsError(
                "client_header_required", "缺少 Agent Views 客户端标识", 403
            )
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise AgentViewsError(
                "json_content_type_required", "写请求必须使用 application/json", 415
            )

    def _require_hook_write(self) -> None:
        if self.headers.get("Origin"):
            raise AgentViewsError(
                "browser_write_forbidden", "不接受来自浏览器页面的写请求", 403
            )
        expected = self.server.hook_token  # type: ignore[attr-defined]
        provided = self.headers.get(HOOK_HEADER, "")
        if not expected or not hmac.compare_digest(expected, provided):
            raise AgentViewsError("hook_token_invalid", "Hook 令牌无效", 403)
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise AgentViewsError(
                "json_content_type_required", "写请求必须使用 application/json", 415
            )

    def _read_json(self) -> Dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise AgentViewsError("body_length_invalid", "请求长度无效", 400) from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise AgentViewsError("body_too_large", "请求内容过大", 413)
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentViewsError("json_invalid", "请求 JSON 无效", 400) from exc
        if not isinstance(value, dict):
            raise AgentViewsError("json_object_required", "请求 JSON 必须是对象", 400)
        return value

    def _path_segments(self) -> list[str]:
        path = urlsplit(self.path).path
        return [unquote(value) for value in path.split("/") if value]

    def do_GET(self) -> None:  # noqa: N802
        try:
            segments = self._path_segments()
            product = getattr(self.server, 'product', None)
            if product and segments == ['api','product','status']:
                value = product.status()
                if getattr(self, 'actor', None): value.pop('operations', None)
                self._send_json(200, value)
                return
            if product and len(segments)==4 and segments[:3]==['api','v2','operations']:
                actor = getattr(self,'actor', None) or {'deviceId':'usb','role':'control'}
                self._send_json(200, product.operations.inspect(segments[3], actor['deviceId']))
                return
            if segments == ["api", "work", "tasks"]:
                self._send_json(200, product.tasks() if product else self.server.work.tasks())
                return
            if segments == ["api", "work", "usage"]:
                self._send_json(200, self.server.work.usage())
                return
            if segments == ["api", "health"]:
                health = self.service.health()
                self._send_json(200 if health.get("ok") else 503, health)
                return
            if segments == ["api", "sessions"]:
                self._send_json(200, product.sessions() if product else {"sessions": self.service.list_sessions()})
                return
            if len(segments) == 3 and segments[:2] == ["api", "sessions"]:
                self._send_json(200, product.session(segments[2]) if product else {"session": self.service.get_session(segments[2])})
                return
            self._send_json(
                404, {"error": {"code": "not_found", "message": "接口不存在"}}
            )
        except Exception as exc:
            self._send_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            segments = self._path_segments()
            if segments == ["api", "hooks", "events"]:
                self._require_hook_write()
                payload = self._read_json()
                result = self.service.handle_hook_event(payload)
                self._send_json(202, result)
                return
            self._require_android_write()
            payload = self._read_json()
            product = getattr(self.server, 'product', None)
            if product and segments == ['api','v2','actions']:
                actor = getattr(self,'actor', None) or {'deviceId':'usb','role':'control'}
                self._send_json(200, product.action(actor, payload))
                return
            if segments == ["api", "work", "tasks", "refresh"]:
                self._send_json(200, product.tasks(force=True) if product else self.server.work.tasks(force=True))
                return
            if len(segments) == 5 and segments[:3] == ["api", "work", "tasks"] and segments[4] == "verify":
                result = self.server.work.verify(segments[3])
                if product and result.get('confirmed'):
                    product.operations.resolve_verified('task:' + segments[3], result)
                self._send_json(200, result)
                return
            if product:
                raise AgentViewsError('client_upgrade_required', '请更新平板：写操作须使用新版防重复提交接口', 426)
            if len(segments) == 5 and segments[:3] == ["api", "work", "tasks"] and segments[4] == "complete":
                self._send_json(200, self.server.work.complete(segments[3]))
                return
            if segments == ["api", "sessions"]:
                session = self.service.create_session(payload.get("initialText"))
                self._send_json(201, {"session": session})
                return
            if (
                len(segments) == 4
                and segments[:2] == ["api", "sessions"]
                and segments[3] == "messages"
            ):
                result = self.service.send(
                    segments[2],
                    payload.get("text"),
                    answers=payload.get("answers"),
                    expected_message_id=payload.get("expectedMessageId"),
                    expected_updated_at=payload.get("expectedUpdatedAt"),
                )
                self._send_json(202, result)
                return
            if (
                len(segments) == 4
                and segments[:2] == ["api", "sessions"]
                and segments[3] == "approvals"
            ):
                result = self.service.respond_approval(
                    segments[2], str(payload.get("decision") or "")
                )
                self._send_json(202, result)
                return
            if (
                len(segments) == 5
                and segments[:2] == ["api", "sessions"]
                and segments[3:] == ["attention", "dismiss"]
            ):
                result = self.service.dismiss_attention(
                    segments[2], payload.get("messageId")
                )
                self._send_json(200, result)
                return
            self._send_json(
                404, {"error": {"code": "not_found", "message": "接口不存在"}}
            )
        except Exception as exc:
            self._send_error(exc)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(
            405,
            {
                "error": {
                    "code": "cors_disabled",
                    "message": "Agent Views Bridge 不启用浏览器跨域访问",
                }
            },
        )


def make_server(
    service: AgentViewsService,
    host: str = "127.0.0.1",
    port: int = 8765,
    hook_token: Optional[str] = None,
) -> AgentViewsHttpServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise AgentViewsError(
            "loopback_required", "Agent Views Bridge 只允许监听回环地址", 400
        )
    if not 0 <= int(port) <= 65_535:
        raise AgentViewsError("port_invalid", "端口必须在 0 到 65535 之间", 400)
    return AgentViewsHttpServer((host, int(port)), service, hook_token)
