import json
import subprocess
import unittest
from agent_views.bridge.work import WorkService
from agent_views.bridge.service import AgentViewsError

ID = '92d34393-e6d2-4cff-9e89-d77db62b9bad'

class WorkTest(unittest.TestCase):
    def service(self, replies):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            value = replies.pop(0)
            if isinstance(value, Exception): raise value
            return subprocess.CompletedProcess(args, 0, json.dumps({'ok': True, 'data': value}), '')
        service = WorkService(None, run=run, clock=lambda: 1000)
        service.items = [{'guid': ID, 'summary': 'test'}]
        return service, calls

    def test_completion_requires_independent_cloud_read(self):
        service, calls = self.service([{'task': {'status': 'todo'}}, {},
            {'task': {'status': 'done', 'completed_at': '1000'}}])
        self.assertTrue(service.complete(ID)['confirmed'])
        self.assertEqual(len(calls), 3)
        self.assertIn('+complete', calls[1])
        self.assertIn('get', calls[2])
        self.assertEqual(service.items, [])

    def test_unconfirmed_completion_keeps_task(self):
        service, _ = self.service([{'task': {'status': 'todo'}}, {}, {'task': {'status': 'todo'}}])
        with self.assertRaises(AgentViewsError): service.complete(ID)
        self.assertEqual(len(service.items), 1)

    def test_timed_out_write_is_reconciled_before_retry(self):
        service, calls = self.service([{'task': {'status': 'todo'}},
            subprocess.TimeoutExpired('lark-cli', 45), {'task': {'status': 'done', 'completed_at': '1000'}}])
        self.assertTrue(service.complete(ID)['confirmed'])
        self.assertEqual(sum('+complete' in call for call in calls), 1)

    def test_unknown_target_is_never_written(self):
        service, calls = self.service([])
        with self.assertRaises(AgentViewsError): service.complete('e0887bff-cec4-4704-91a7-d65a69593ad8')
        self.assertEqual(calls, [])

    def test_verify_does_not_write(self):
        service, calls = self.service([{'task': {'status': 'todo'}}])
        self.assertFalse(service.verify(ID)['confirmed'])
        self.assertEqual(len(calls), 1)
        self.assertIn('get', calls[0])
        self.assertNotIn('+complete', calls[0])

    def test_five_minute_cache_and_manual_refresh(self):
        service, calls = self.service([{'items': [{'guid': ID, 'summary': 'one'}]},
                                      {'items': [{'guid': ID, 'summary': 'two'}]}])
        self.assertEqual(service.tasks()['items'][0]['summary'], 'one')
        service.tasks()
        self.assertEqual(len(calls), 1)
        self.assertEqual(service.tasks(force=True)['items'][0]['summary'], 'two')

    def test_failed_refresh_preserves_last_snapshot(self):
        service, _ = self.service([subprocess.TimeoutExpired('lark-cli', 45)])
        result = service.tasks()
        self.assertTrue(result['stale'])
        self.assertEqual(result['items'][0]['guid'], ID)
