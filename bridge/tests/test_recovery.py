import unittest
from unittest.mock import Mock

from agent_views.bridge.recovery import RecoveringService


class RecoveryTests(unittest.TestCase):
    def test_disconnect_reason_survives_successful_reconnection(self):
        dead = Mock()
        dead.protocol.process.poll.return_value = 1
        dead.health.return_value = {'ok': False, 'lastError': 'output too large'}
        healthy = Mock()
        healthy.health.return_value = {'ok': True, 'lastError': None}
        service = RecoveringService(lambda: healthy)
        service.current = dead
        old_generation = service.generation
        def alive():
            service.stop.set()
            return None
        healthy.protocol.process.poll.side_effect = alive
        service._run()
        dead.close.assert_called_once()
        healthy.start.assert_called_once()
        self.assertTrue(service.health()['ok'])
        self.assertIsNone(service.health()['lastError'])
        self.assertEqual(service.health()['lastDisconnect'], 'output too large')
        self.assertNotEqual(service.generation, old_generation)
