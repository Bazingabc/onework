from pathlib import Path
import tempfile
import unittest

from agent_views.bridge.service import AgentViewsError, AgentViewsService
from agent_views.bridge.tests.test_service import FakeProtocol, NOW


class StrictReadsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.thread = {'id': 'one', 'updatedAt': NOW, 'createdAt': NOW,
                       'status': {'type': 'idle'}, 'turns': []}
        self.protocol = FakeProtocol([self.thread])
        self.service = AgentViewsService(self.protocol, workspace=Path(temp.name),
                state_path=Path(temp.name)/'state.json', clock=lambda: NOW)
        self.service.start(); self.addCleanup(self.service.close)
        self.service.get_session('one')
        self.request = self.protocol.request

    def fail_details(self, method, params=None):
        if method == 'thread/read': raise TimeoutError('test')
        return self.request(method, params)

    def test_strict_detail_never_falls_back_to_warm_history(self):
        self.protocol.request = self.fail_details
        self.assertEqual(self.service.get_session('one')['id'], 'one')
        with self.assertRaises(AgentViewsError): self.service.get_session('one', strict=True)

    def test_strict_list_propagates_changed_thread_read_failure(self):
        self.thread['updatedAt'] += 1
        self.protocol.request = self.fail_details
        with self.assertRaises(TimeoutError): self.service.list_sessions(strict=True)

    def test_strict_detail_rejects_malformed_upstream_response(self):
        self.protocol.request = lambda method, params=None: {} if method == 'thread/read' else self.request(method, params)
        with self.assertRaises(AgentViewsError): self.service.get_session('one', strict=True)
