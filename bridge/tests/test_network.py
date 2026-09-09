import hashlib
import http.client
from pathlib import Path
import ssl
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_views.bridge.devices import DeviceStore
from agent_views.bridge.lan import make_lan_server
from agent_views.bridge.network import LanRecovery
from agent_views.bridge.pairing import certificate
from agent_views.bridge.product import ProductRuntime


class NetworkRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'wifi'
        self.service = SimpleNamespace(health=lambda: {'ok': True, 'codexVersion': 'test'}, generation='unchanged')
        self.work = SimpleNamespace(error=None, updated=1)
        self.product = ProductRuntime(self.service, self.work, self.root)
        self.addCleanup(self.product.operations.close)
        self.local = SimpleNamespace(service=self.service, work=self.work, product=self.product)
        self.created = []
        def factory(local, host, port, directory):
            # Only the bind address changes in this isolated test. Real TLS,
            # certificate, DeviceStore and request handler remain in use.
            with patch('agent_views.bridge.lan.private_origin'):
                server = make_lan_server(local, '127.0.0.1', 0, directory)
            self.created.append(server)
            return server
        self.recovery = LanRecovery(self.local, 8766, self.directory, factory=factory)
        self.addCleanup(self.recovery.close)
        self.pin = certificate(self.directory)[2]
        self.store = DeviceStore(self.directory)
        qr = self.store.issue('https://192.168.1.2:8766', self.pin)
        self.token = 't' * 43
        request = self.store.begin(qr['code'], 'isolated tablet', self.token)
        self.store.approve(request['requestId'], 'view')

    def request(self, token=None):
        server = self.recovery.listener
        connection = http.client.HTTPSConnection('127.0.0.1', server.server_port,
                    context=ssl._create_unverified_context(), timeout=3)
        try:
            connection.connect()
            actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            self.assertEqual(actual, self.pin)
            connection.request('GET', '/api/product/status', headers={'Authorization': 'Bearer ' + (token or self.token)})
            response = connection.getresponse(); response.read()
            return response.status
        finally:
            connection.close()

    def test_same_address_does_not_replace_listener(self):
        self.recovery.reconcile('192.168.1.2')
        first = self.recovery.listener
        self.recovery.reconcile('192.168.1.2')
        self.assertIs(first, self.recovery.listener)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.request(), 200)

    def test_disconnect_and_new_address_preserve_identity_and_reject_bad_token(self):
        self.recovery.reconcile('192.168.1.2')
        epoch = self.product.epoch
        self.assertEqual(self.request(), 200)
        self.recovery.reconcile(None)
        self.assertIsNone(self.product.lan_url)
        self.assertEqual(self.product.network['state'], 'offline')
        self.recovery.reconcile('192.168.2.9')
        self.assertEqual(self.product.lan_url, 'https://192.168.2.9:8766')
        self.assertEqual(self.product.epoch, epoch)
        self.assertEqual(self.service.generation, 'unchanged')
        self.assertEqual(self.request(), 200)
        self.assertEqual(self.request('wrong-token'), 401)
        self.assertEqual(self.product.operations.recent(), [])

    def test_port_failure_does_not_advertise_unbound_address_and_can_retry(self):
        factory = self.recovery.factory
        self.recovery.factory = lambda *args: (_ for _ in ()).throw(OSError('port busy'))
        self.recovery.reconcile('192.168.1.2')
        self.assertEqual(self.product.network['state'], 'recovering')
        self.assertFalse(getattr(self.product, 'lan_url', None))
        self.recovery.factory = factory
        self.recovery.reconcile('192.168.1.2')
        self.assertEqual(self.product.network['state'], 'ready')
        self.assertEqual(self.request(), 200)

    def test_public_address_is_never_bound(self):
        self.recovery.reconcile('8.8.8.8')
        self.assertEqual(self.created, [])
        self.assertEqual(self.product.network['state'], 'offline')

    def test_close_stops_listener(self):
        self.recovery.reconcile('192.168.1.2')
        worker = self.recovery.worker
        self.recovery.close()
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.product.lan_url)

    def test_timer_detects_loss_and_recovery_then_stops(self):
        current = ['192.168.1.2']
        self.recovery.address = lambda: current[0]
        self.recovery.interval = 0.02
        self.recovery.start()
        def until(predicate):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.01)
            self.fail('network state did not converge')
        current[0] = None
        until(lambda: self.product.network['state'] == 'offline')
        current[0] = '192.168.2.9'
        until(lambda: self.product.network['state'] == 'ready' and self.recovery.host == current[0])
        self.assertEqual(self.request(), 200)
        self.recovery.close()
        self.assertFalse(self.recovery.thread.is_alive())

    def test_completed_and_unknown_operations_are_preserved_without_replay(self):
        calls = []
        store = self.product.operations
        completed, unknown = str(uuid.uuid4()), str(uuid.uuid4())
        store.execute(completed, 'test', 'task:a', {}, lambda: None, lambda: calls.append('sent') or {'ok': True})
        def timeout():
            raise TimeoutError('test only')
        from agent_views.bridge.service import AgentViewsError
        with self.assertRaises(AgentViewsError):
            store.execute(unknown, 'test', 'task:b', {}, lambda: None, timeout)
        before = store.recent()
        self.recovery.reconcile('192.168.1.2')
        self.recovery.reconcile(None)
        self.recovery.reconcile('192.168.2.9')
        self.assertEqual(store.recent(), before)
        self.assertEqual(calls, ['sent'])
        self.assertEqual(store.inspect(unknown, 'test')['state'], 'unknown')

    def test_accepted_request_can_finish_during_rebind(self):
        self.recovery.reconcile('192.168.1.2')
        entered, finish = threading.Event(), threading.Event()
        status = self.product.status
        def blocked_status():
            entered.set()
            if not finish.wait(3):
                raise TimeoutError('test timeout')
            return status()
        self.product.status = blocked_status
        results = []
        def request():
            try:
                results.append(self.request())
            except Exception as error:
                results.append(error)
        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.recovery.reconcile('192.168.2.9')
        finally:
            finish.set()
            thread.join(timeout=3)
            self.product.status = status
        self.assertEqual(results, [200])
        self.assertEqual(self.request(), 200)


if __name__ == '__main__':
    unittest.main()
