import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent_views.bridge.codex_protocol import CodexAppServerClient, CodexProtocolError, CLIENT_METHODS
from agent_views.bridge.service import AgentViewsService
from agent_views.bridge.tests.test_service import FakeProtocol, NOW


class BoundedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.client = CodexAppServerClient('unused')
        self.calls = []
        self.thread = {'id': 'demo', 'updatedAt': NOW, 'createdAt': NOW, 'status': {'type': 'idle'}}
        self.turns = [
            {'id': 'new', 'status': 'completed', 'itemsView': 'summary', 'items': [
                {'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer', 'text': '请选择 A 或 B'}]},
            {'id': 'old', 'status': 'completed', 'itemsView': 'summary', 'items': []}]
        self.client.request = self.request

    def request(self, method, params):
        self.calls.append((method, dict(params)))
        if method == 'thread/read':
            if params.get('includeTurns'): raise AssertionError('Full history exceeds 16 MiB')
            return {'thread': copy.deepcopy(self.thread)}
        if method == 'thread/turns/list':
            self.assertEqual(params, {'threadId': 'demo', 'limit': 4, 'sortDirection': 'desc', 'itemsView': 'summary'})
            return {'data': copy.deepcopy(self.turns), 'nextCursor': 'older-history-not-needed'}
        if method == 'thread/resume':
            self.assertTrue(params.get('excludeTurns'))
            return {'thread': {**self.thread, 'turns': []}}
        raise AssertionError(method)

    def test_recent_page_is_chronological_and_does_not_fetch_older_history(self):
        self.assertIn('thread/turns/list', CLIENT_METHODS)
        value = self.client.read_thread('demo')['thread']
        self.assertEqual([t['id'] for t in value['turns']], ['old', 'new'])
        self.assertEqual(value['turns'][-1]['items'][0]['text'], '请选择 A 或 B')
        self.assertEqual(len(self.calls), 3)

    def test_resume_does_not_rehydrate_or_overwrite_recent_turns(self):
        value = self.client.resume_thread('demo')
        self.assertNotIn('turns', value['thread'])
        self.assertEqual(self.calls[-1], ('thread/resume', {'threadId': 'demo', 'excludeTurns': True}))

    def test_unsupported_method_only_allows_legacy_fallback(self):
        calls = []
        def legacy(method, params):
            calls.append((method, params))
            if method == 'thread/turns/list':
                raise CodexProtocolError('protocol_remote_error', 'method not found', {'remote_code': -32601})
            return {'thread': self.thread}
        self.client.request = legacy
        self.client.read_thread('demo')
        self.assertFalse(self.client._paged_history)
        self.client.read_thread('demo')
        self.assertEqual(calls[-1][1]['includeTurns'], True)
        self.assertEqual(sum(m == 'thread/turns/list' for m, _ in calls), 1)

    def test_timeout_or_other_server_error_never_falls_back_to_full_history(self):
        for error in [CodexProtocolError('protocol_timeout', 'timeout'),
                      CodexProtocolError('protocol_remote_error', 'busy', {'remote_code': -32000})]:
            with self.subTest(error=error.code):
                def fail(method, params):
                    if method == 'thread/turns/list': raise error
                    return self.request(method, params)
                self.client.request = fail
                with self.assertRaises(CodexProtocolError): self.client.read_thread('demo')
                self.assertIsNone(self.client._paged_history)

    def test_metadata_race_retries_then_fails_closed(self):
        def moving(method, params):
            if method == 'thread/read': self.thread['updatedAt'] += 1
            return self.request(method, params)
        self.client.request = moving
        with self.assertRaises(CodexProtocolError) as error: self.client.read_thread('demo')
        self.assertEqual(error.exception.code, 'protocol_snapshot_changed')
        self.assertEqual(sum(m == 'thread/turns/list' for m, _ in self.calls), 2)

    def test_malformed_or_unloaded_summary_is_rejected(self):
        for turns in [None, [None], [{'id': 'bad', 'items': [], 'itemsView': 'notLoaded'}], self.turns * 3]:
            with self.subTest(turns=turns):
                self.turns = turns
                with self.assertRaises(CodexProtocolError): self.client.read_thread('demo')

    def test_empty_history_is_valid(self):
        self.turns = []
        self.assertEqual(self.client.read_thread('demo')['thread']['turns'], [])

    def test_service_list_detail_and_write_precheck_use_bounded_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            protocol = FakeProtocol([self.thread])
            protocol.read_thread = Mock(side_effect=self.client.read_thread)
            service = AgentViewsService(protocol, workspace=Path(directory),
                        state_path=Path(directory)/'state.json', clock=lambda: NOW)
            try:
                service.start()
                self.assertEqual(service.list_sessions(strict=True)[0]['id'], 'demo')
                self.assertEqual(service.get_session('demo', strict=True)['messages'][-1]['text'], '请选择 A 或 B')
                self.assertEqual(service._read_thread_for_write('demo')['turns'][-1]['id'], 'new')
                self.assertGreaterEqual(protocol.read_thread.call_count, 3)
                self.assertFalse(any(m == 'thread/read' for m, _ in protocol.calls))
            finally: service.close()


if __name__ == '__main__': unittest.main()
