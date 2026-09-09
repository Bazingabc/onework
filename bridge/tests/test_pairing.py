import concurrent.futures
import http.client
import json
import ssl
import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace

from agent_views.bridge.pairing import PairStore, certificate, private_origin
from agent_views.bridge.lan import LanServer, LanHandler
from agent_views.bridge.service import AgentViewsError
from agent_views.bridge.http_api import CLIENT_HEADER, CLIENT_HEADER_VALUE


class PairingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.store = PairStore(self.temp.name, clock=lambda: self.now)

    def issue(self):
        return self.store.issue('https://192.168.1.2:8766', 'a' * 64)

    def test_one_use_and_revocation(self):
        invite = self.issue()
        result = self.store.claim(invite['code'], 'tablet')
        self.assertTrue(self.store.authorized(result['token']))
        self.assertNotIn(result['token'], self.store.devices_path.read_text())
        with self.assertRaises(AgentViewsError):
            self.store.claim(invite['code'], 'again')
        PairStore(self.temp.name).revoke(result['deviceId'])
        self.assertFalse(self.store.authorized(result['token']))

    def test_expiry_invalid_and_replaced_invite(self):
        old = self.issue()
        current = self.issue()
        for code in [None, 'wrong', old['code']]:
            with self.assertRaises(AgentViewsError):
                self.store.claim(code, 'tablet')
        self.now = current['expiresAt']
        with self.assertRaises(AgentViewsError):
            self.store.claim(current['code'], 'tablet')

    def test_independent_stores_cannot_claim_twice(self):
        code = self.issue()['code']
        def claim(_):
            try:
                PairStore(self.temp.name, clock=lambda: self.now).claim(code, 'tablet')
                return True
            except AgentViewsError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim, range(16))), 1)

    def test_reject_non_private_and_ambiguous_origins(self):
        for origin in ['http://192.168.1.2:8766', 'https://8.8.8.8:8766',
                       'https://localhost:8766', 'https://127.0.0.1:8766',
                       'https://192.168.1.2:8766/a', 'https://u@192.168.1.2:8766',
                       'https://192.168.1.2', 'https://[::1]:8766']:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                private_origin(origin)

    def test_tls_auth_pair_and_local_only_hooks(self):
        cert, key, fingerprint = certificate(self.temp.name)
        self.assertEqual(certificate(self.temp.name)[2], fingerprint)
        server = LanServer(('127.0.0.1', 0), LanHandler)
        server.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.tls.load_cert_chain(cert, key)
        server.pairs = self.store
        server.service = SimpleNamespace(health=lambda: {'ok': True})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        # Slow unauthenticated handshakes must not block the accept loop.
        for _ in range(3):
            slow = socket.create_connection(server.server_address)
            self.addCleanup(slow.close)
        context = ssl.create_default_context(cafile=str(cert))
        # Test fixture connects via loopback; trust only its exact generated cert.
        context.check_hostname = False
        def request(path, body=None, token=None, origin=None):
            conn = http.client.HTTPSConnection('127.0.0.1', server.server_port, context=context, timeout=3)
            headers = {CLIENT_HEADER: CLIENT_HEADER_VALUE, 'Content-Type': 'application/json'}
            if token: headers['Authorization'] = token
            if origin: headers['Origin'] = origin
            conn.request('POST' if body is not None else 'GET', path,
                         json.dumps(body) if body is not None else None, headers)
            response = conn.getresponse()
            result = response.status, json.loads(response.read())
            conn.close()
            return result
        self.assertEqual(request('/api/health')[0], 401)
        invite = self.issue()
        status, result = request('/api/pair', {'code': invite['code'], 'name': 'tablet'})
        self.assertEqual(status, 200)
        bearer = 'Bearer ' + result['token']
        self.assertEqual(request('/api/health', token=bearer), (200, {'ok': True}))
        self.assertEqual(request('/api/health', token=result['token'])[0], 401)
        self.assertEqual(request('/api/health', token=bearer, origin='https://example.com')[0], 401)
        self.assertEqual(request('/api/hooks/events', {}, bearer)[0], 403)
        self.assertEqual(request('/api/pair', {'code': invite['code']})[0], 403)
        self.store.revoke(result['deviceId'])
        self.assertEqual(request('/api/health', token=bearer)[0], 401)

    def test_concurrent_certificate_creation_is_consistent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: certificate(self.temp.name), range(4)))
        self.assertEqual(len({result[2] for result in results}), 1)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(results[0][0], results[0][1])


if __name__ == '__main__':
    unittest.main()
