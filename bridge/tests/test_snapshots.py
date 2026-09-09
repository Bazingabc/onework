import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid

from agent_views.bridge.http_api import make_server
from agent_views.bridge.product import ProductRuntime
from agent_views.bridge.service import AgentViewsError
from agent_views.bridge.snapshots import ReadSnapshots


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.generation = 'first'
        self.cache = ReadSnapshots(lambda: self.generation, clock=lambda: self.now)
        self.addCleanup(self.cache.close)

    def settle(self, cache=None):
        cache = cache or self.cache
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with cache.lock:
                if not cache.active:
                    return
            time.sleep(0.005)
        self.fail('background loader did not finish')

    def test_single_flight_and_snapshot_age_includes_source_wait(self):
        gate, entered = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        calls = []
        def read():
            calls.append(1); entered.set(); gate.wait(2)
            return [{'id': 'one'}]
        try:
            for _ in range(10):
                value, meta = self.cache.read('list', read)
                self.assertIsNone(value)
                self.assertEqual(meta['state'], 'loading')
            self.assertTrue(entered.wait(1))
            self.assertEqual(calls, [1])
            self.now += 16
        finally:
            gate.set()
        self.settle()
        value, meta = self.cache.read('list', read)
        self.assertEqual(value, [{'id': 'one'}])
        self.assertEqual(meta['state'], 'stale')
        self.assertEqual(meta['ageMs'], 16_000)

    def test_refresh_failure_retains_value_without_resetting_success_age(self):
        self.cache.read('list', lambda: ['old']); self.settle()
        self.now += 3
        def failed(): raise TimeoutError('must not expose upstream details')
        self.cache.read('list', failed); self.settle()
        value, meta = self.cache.read('list', failed)
        self.assertEqual(value, ['old'])
        self.assertEqual(meta['state'], 'stale')
        self.assertEqual(meta['ageMs'], 3000)

    def test_first_failure_is_not_reported_as_loading_forever(self):
        def fail(): raise TimeoutError('test')
        self.cache.read('list', fail); self.settle()
        value, meta = self.cache.read('list', fail)
        self.assertIsNone(value)
        self.assertEqual(meta['state'], 'stale')

    def test_new_generation_discards_old_inflight_result(self):
        gate, entered = threading.Event(), threading.Event()
        def old(): entered.set(); gate.wait(2); return ['old approval']
        try:
            self.cache.read('list', old); self.assertTrue(entered.wait(1))
            self.generation = 'second'
            self.assertIsNone(self.cache.read('list', lambda: ['new'])[0])
        finally: gate.set()
        self.settle()
        self.assertIsNone(self.cache.read('list', lambda: ['new'])[0])
        self.settle()
        self.assertEqual(self.cache.read('list', lambda: ['new'])[0], ['new'])

    def test_invalidation_discards_late_pre_action_result(self):
        gate, entered = threading.Event(), threading.Event()
        def old(): entered.set(); gate.wait(2); return ['old']
        try:
            self.cache.read('list', old); self.assertTrue(entered.wait(1))
            self.cache.invalidate('list')
        finally: gate.set()
        self.settle()
        self.assertIsNone(self.cache.read('list', lambda: ['new'])[0])
        self.settle()
        self.assertEqual(self.cache.read('list', lambda: ['new'])[0], ['new'])

    def test_readers_cannot_mutate_shared_cache(self):
        self.cache.read('list', lambda: [{'value': 1}]); self.settle()
        value, _ = self.cache.read('list', lambda: [])
        value[0]['value'] = 2
        self.assertEqual(self.cache.read('list', lambda: [])[0], [{'value': 1}])

    def test_capacity_and_worker_count_are_bounded(self):
        gate = threading.Event()
        cache = ReadSnapshots(lambda: 'same', capacity=3, workers=2)
        self.addCleanup(cache.close)
        try:
            for i in range(30): cache.read(str(i), lambda: gate.wait(2))
            with cache.lock:
                self.assertLessEqual(len(cache.active), 2)
                self.assertLessEqual(len(cache.entries), 3)
        finally: gate.set()
        self.settle(cache)
        cache.close()
        value, meta = cache.read('closed', lambda: self.fail('read after close'))
        self.assertFalse(meta['refreshing'])


class ProductSnapshotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.detail = {'id': 'one', 'updatedAt': 1, 'messages': []}
        self.calls = []
        def detail(identifier, **kwargs):
            self.assertTrue(kwargs['strict']); self.calls.append('read')
            return dict(self.detail)
        self.service = SimpleNamespace(generation='one', get_session=detail,
                list_sessions=lambda **kwargs: [], health=lambda: {'ok': True})
        self.runtime = ProductRuntime(self.service, None, self.path)
        self.addCleanup(self.runtime.close)

    def settle(self, cache):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with cache.lock:
                if not cache.active: return
            time.sleep(0.005)
        self.fail('loader did not settle')

    def body(self, revision):
        return dict(kind='message', target='one', operationId=str(uuid.uuid4()),
                    expectedRevision=revision, payload={'text': 'test-only'})

    def test_read_snapshot_is_not_used_for_write_validation(self):
        self.runtime.session('one'); self.settle(self.runtime.detail_snapshots)
        old = self.runtime.session('one')['revision']
        self.detail['updatedAt'] = 2
        with self.assertRaises(AgentViewsError) as failure:
            self.runtime.action({'role': 'control', 'deviceId': 'test'}, self.body(old))
        self.assertEqual(failure.exception.code, 'object_changed')
        self.assertEqual(self.calls, ['read', 'read'])
        self.assertEqual(self.runtime.operations.recent(), [])

    def test_source_failure_before_write_never_dispatches(self):
        self.runtime.session('one'); self.settle(self.runtime.detail_snapshots)
        old = self.runtime.session('one')['revision']
        def fail(*args, **kwargs): raise AgentViewsError('source_unavailable', 'test', 503)
        self.service.get_session = fail
        with self.assertRaises(AgentViewsError):
            self.runtime.action({'role': 'control', 'deviceId': 'test'}, self.body(old))
        self.assertEqual(self.runtime.operations.recent(), [])

    def test_stale_detail_has_no_action_revision(self):
        self.runtime.session('one'); self.settle(self.runtime.detail_snapshots)
        self.runtime.detail_snapshots.max_age = 0
        value = self.runtime.session('one')
        self.assertTrue(value['stale'])
        self.assertEqual(value['revision'], '')
        self.assertEqual(value['session']['id'], 'one')

    def test_http_list_and_detail_return_while_source_is_blocked(self):
        gate = threading.Event()
        self.service.list_sessions = lambda **kwargs: gate.wait(3) or []
        self.service.get_session = lambda *args, **kwargs: gate.wait(3) or self.detail
        server = make_server(self.service, '127.0.0.1', 0)
        server.product = self.runtime
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for path in ('/api/sessions', '/api/sessions/one', '/api/sessions'):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=0.5)
                try:
                    connection.request('GET', path)
                    response = connection.getresponse(); value = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                    self.assertTrue(value['stale'])
                    self.assertEqual(value['snapshot']['state'], 'loading')
                finally: connection.close()
        finally:
            gate.set(); server.shutdown(); server.server_close(); thread.join(2)
            self.settle(self.runtime.list_snapshots); self.settle(self.runtime.detail_snapshots)
