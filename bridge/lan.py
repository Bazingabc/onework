"""TLS listener: every business request requires a paired-device credential."""
import ssl
from http.server import ThreadingHTTPServer
from .http_api import AgentViewsRequestHandler
from .pairing import PairStore, certificate, private_origin
from .service import AgentViewsError
from .devices import DeviceStore


class LanHandler(AgentViewsRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        # Do not log URL, headers or pairing material.
        pass

    def _authorize(self):
        authorization = self.headers.get('Authorization', '')
        self.actor = self.server.pairs.identity(authorization[7:]) if authorization.startswith('Bearer ') and not self.headers.get('Origin') and isinstance(self.server.pairs, DeviceStore) else None
        allowed = self.actor if isinstance(self.server.pairs, DeviceStore) else authorization.startswith('Bearer ') and self.server.pairs.authorized(authorization[7:])
        if self.headers.get('Origin') or not allowed:
            raise AgentViewsError('device_unauthorized', '设备未配对或已被撤销，请重新扫码', 401)

    def do_GET(self):
        try:
            self._authorize()
        except Exception as exc:
            self._send_error(exc)
            return
        super().do_GET()

    def do_POST(self):
        try:
            segments = self._path_segments()
            if segments == ['api', 'pair', 'begin']:
                self._require_android_write()
                body = self._read_json()
                self._send_json(202, self.server.pairs.begin(body.get('code'), body.get('name'), body.get('clientToken'), body.get('device')))
                return
            if segments == ['api', 'pair', 'status']:
                self._require_android_write()
                body = self._read_json()
                self._send_json(200, self.server.pairs.pair_status(str(body.get('requestId') or ''), body.get('clientToken')))
                return
            if segments == ['api', 'pair']:
                if getattr(self.server, 'product', None):
                    raise AgentViewsError('upgrade_required', '请更新平板，使用需要 Mac 确认的新配对流程', 426)
                self._require_android_write()
                body = self._read_json()
                result = self.server.pairs.claim(body.get('code'), body.get('name'))
                self._send_json(200, result)
                return
            self._authorize()
            if segments == ['api','device','profile']:
                self._require_android_write()
                body = self._read_json()
                self._send_json(200, self.server.pairs.update_metadata(self.actor['deviceId'], body.get('device')))
                return
            if segments == ['api', 'hooks', 'events']:
                raise AgentViewsError('local_only', 'Hook 仅接受 Mac 本机连接', 403)
            if getattr(self.server, 'product', None) and segments != ['api','v2','actions'] and not (len(segments)>=4 and segments[:3]==['api','work','tasks'] and segments[-1] in ('refresh','verify')):
                raise AgentViewsError('upgrade_required', '旧版写入接口已停用，请更新平板', 426)
        except Exception as exc:
            self._send_error(exc)
            return
        super().do_POST()


class LanServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def process_request_thread(self, connection, address):
        try:
            secure = self.tls.wrap_socket(connection, server_side=True)
        except Exception:
            connection.close()
            return
        super().process_request_thread(secure, address)


def make_lan_server(local, host, port, directory):
    private_origin('https://%s:%s' % (host, port))
    cert, key, _ = certificate(directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    server = LanServer((host, port), LanHandler)
    server.tls = context
    server.service, server.work = local.service, local.work
    server.product = getattr(local, 'product', None)
    server.pairs = DeviceStore(directory)
    return server
