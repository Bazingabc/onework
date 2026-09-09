import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from types import SimpleNamespace

from agent_views.bridge.product import ProductRuntime


class SnapshotResponsivenessTests(unittest.TestCase):
    def test_slow_list_does_not_hold_http_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            entered, release = threading.Event(), threading.Event()
            def slow_list(**kwargs):
                entered.set()
                release.wait(3)
                return [{'id': 'test'}]
            service = SimpleNamespace(list_sessions=slow_list, generation='one')
            runtime = ProductRuntime(service, None, Path(directory))
            runtime.sessions_at = -10  # Expire the legacy cache even on a newly started clock.
            try:
                with ThreadPoolExecutor(max_workers=1) as callers:
                    future = callers.submit(runtime.sessions)
                    try:
                        self.assertTrue(entered.wait(1))
                        response = future.result(timeout=0.2)
                        self.assertTrue(response['stale'])
                        self.assertEqual(response['snapshot']['state'], 'loading')
                    except TimeoutError:
                        self.fail('HTTP caller waits for the slow Codex list read')
                    finally:
                        release.set()
            finally:
                if hasattr(type(runtime), 'close'):
                    runtime.close()
                else:
                    runtime.operations.close()
