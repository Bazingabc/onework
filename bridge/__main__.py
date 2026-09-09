"""Command-line entry point for the loopback Agent Views bridge."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
from pathlib import Path

from .http_api import make_server
from .service import AgentViewsError, AgentViewsService


from .codex_protocol import CodexAppServerClient
from .hook_auth import HookTokenStore  # noqa: E402


# thread/read with includeTurns is returned as one JSONL record. Long-lived
# sessions routinely exceed the protocol client's conservative 1 MB default,
# while the service still projects at most 50 bounded user/agent messages.
AGENT_VIEWS_MAX_LINE_BYTES = 16 * 1024 * 1024
AGENT_VIEWS_MIN_CLI_VERSION = "0.144.0"
AGENT_VIEWS_MAX_CLI_VERSION = "0.154.0"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Expose supervised Codex sessions to the local Agent Views Android app."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--lan-host", help="启用 TLS 局域网入口，绑定指定私网 IPv4")
    parser.add_argument("--follow-network", action="store_true", help="Mac 网络变化后自动恢复 TLS 监听")
    parser.add_argument("--lan-port", type=int, default=8766)
    parser.add_argument("--pairing-directory", type=Path, default=Path.home() / '.agent-views' / 'wifi')
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path.home() / ".agent-views" / "state.json",
    )
    parser.add_argument(
        "--hook-token-file",
        type=Path,
        default=Path.home() / ".agent-views" / "hook-token",
    )
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--codex", default=shutil.which("codex") or "codex")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = CodexAppServerClient(
        args.codex,
        minimum_version=AGENT_VIEWS_MIN_CLI_VERSION,
        maximum_version=AGENT_VIEWS_MAX_CLI_VERSION,
        request_timeout=30,
        max_line_bytes=AGENT_VIEWS_MAX_LINE_BYTES,
    )
    server = lan = service = network = None
    try:
        from .recovery import RecoveringService
        from .product import ProductRuntime
        def factory():
            protocol = CodexAppServerClient(args.codex, minimum_version=AGENT_VIEWS_MIN_CLI_VERSION,
                maximum_version=AGENT_VIEWS_MAX_CLI_VERSION, request_timeout=30,
                max_line_bytes=AGENT_VIEWS_MAX_LINE_BYTES)
            return AgentViewsService(protocol, workspace=args.workspace, state_path=args.state_file,
                window_minutes=args.window_minutes)
        service = RecoveringService(factory)
        health = service.start()
        hook_token = HookTokenStore(args.hook_token_file).load_or_create()
        server = make_server(service, args.host, args.port, hook_token)
        server.product = ProductRuntime(service, server.work, args.pairing_directory.parent)
        lan = None
        if args.follow_network:
            from .network import LanRecovery
            network = LanRecovery(server, args.lan_port, args.pairing_directory)
            network.start()
        elif args.lan_host:
            from .lan import make_lan_server
            lan = make_lan_server(server, args.lan_host, args.lan_port, args.pairing_directory)
            server.product.lan_url = 'https://%s:%s' % (args.lan_host, args.lan_port)
            threading.Thread(target=lan.serve_forever, daemon=True).start()
    except Exception as exc:
        sys.stderr.write(
            json.dumps(
                {"error": {"code": getattr(exc, 'code', 'bridge_start_failed'), "message": str(exc)}},
                ensure_ascii=False,
            )
            + "\n"
        )
        if network:
            network.close()
        if lan:
            lan.server_close()
        if server:
            server.server_close()
        if service:
            service.close()
        else:
            client.close()
        return 1

    actual_host, actual_port = server.server_address[:2]
    sys.stdout.write(
        json.dumps(
            {
                "ready": True,
                "url": "http://%s:%s" % (actual_host, actual_port),
                "codexVersion": health.get("codexVersion"),
                "model": health.get("model"),
                "workspace": health.get("workspace"),
                "lanUrl": getattr(server.product, 'lan_url', None),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    sys.stdout.flush()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        if network:
            network.close()
        if lan:
            lan.shutdown()
            lan.server_close()
        service.close()
        server.product.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
