import tempfile
import io
import json
import unittest
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from agent_views.bridge.devices import DeviceStore
from agent_views.bridge.service import AgentViewsError


class ReplacePairingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.now = 1000
        self.store = DeviceStore(self.path, clock=lambda: self.now)
        self.sequence = 0

    def request(self):
        self.sequence += 1
        token = f'demo-client-{self.sequence:032d}'
        qr = self.store.issue('https://192.168.1.2:8766', 'a' * 64)
        request = self.store.begin(qr['code'], 'Same tablet model', token, {'model': 'DEMO'})
        return request['requestId'], token

    def approved(self, role='view'):
        identifier, token = self.request()
        self.store.approve(identifier, role)
        return identifier, token

    def test_inherits_both_roles_and_uses_new_identity(self):
        for role in ('view', 'control'):
            with self.subTest(role=role):
                old, old_token = self.approved(role)
                other, _ = self.approved()
                before = self.store._read(self.store.devices_path)[other]
                self.now += 10
                new, token = self.request()
                count = len(self.store.devices())
                result = self.store.replace(new, old, role)
                self.assertEqual(result['role'], role)
                self.assertEqual(len(self.store.devices()), count)
                self.assertIsNone(self.store.identity(old_token))
                self.assertEqual(self.store.identity(token), {'deviceId': new, 'role': role})
                saved = self.store._read(self.store.devices_path)
                self.assertEqual(saved[other], before)
                self.assertEqual(saved[new]['pairedAt'], self.now)
                self.assertEqual(saved[new]['metadata'], {'model': 'DEMO'})
                self.assertEqual(self.store.pair_status(new, token)['state'], 'approved')
                self.assertNotIn(new, [v['requestId'] for v in self.store.pending()])

    def test_same_model_remains_separate_until_explicit_replacement(self):
        first, _ = self.approved()
        second, _ = self.approved()
        self.assertEqual({v['deviceId'] for v in self.store.devices()}, {first, second})

    def test_rejects_permission_online_and_identity_changes(self):
        old, old_token = self.approved()
        new, _ = self.request()
        for target, role in [(new, 'view'), ('missing', 'view'), (old, 'control')]:
            with self.assertRaises(ValueError): self.store.replace(new, target, role)
        self.store.set_role(old, 'control')
        with self.assertRaisesRegex(ValueError, '权限已变化'): self.store.replace(new, old, 'view')
        self.store.identity(old_token)
        with self.assertRaisesRegex(ValueError, '在线'): self.store.replace(new, old, 'control')
        self.store.replace(new, old, 'control', confirm_online=True)
        self.assertIsNone(self.store.identity(old_token))

    def test_expired_and_denied_requests_do_not_revoke_old(self):
        old, token = self.approved()
        new, _ = self.request()
        self.store.approve(new, 'denied')
        with self.assertRaises(ValueError): self.store.replace(new, old, 'view')
        new, _ = self.request()
        self.now += 301
        with self.assertRaises(ValueError): self.store.replace(new, old, 'view')
        self.assertIsNotNone(self.store.identity(token))

    def test_rejects_reused_credential(self):
        old, token = self.approved()
        qr = self.store.issue('https://192.168.1.2:8766', 'a' * 64)
        new = self.store.begin(qr['code'], 'Same token', token)['requestId']
        with self.assertRaisesRegex(ValueError, '新的配对凭证'): self.store.replace(new, old, 'view')

    def test_retry_is_idempotent_and_revocation_never_resurrects(self):
        old, old_token = self.approved()
        new, token = self.request()
        first = self.store.replace(new, old, 'view')
        self.now += 301
        fresh = DeviceStore(self.path, clock=lambda: self.now)
        self.assertEqual(fresh.replace(new, old, 'view'), first)
        self.assertEqual(fresh.pair_status(new, token)['state'], 'approved')
        fresh.revoke(new)
        with self.assertRaises(ValueError): fresh.replace(new, old, 'view')
        with self.assertRaises(ValueError): fresh.approve(new, 'control')
        self.assertIsNone(fresh.identity(token))
        self.assertIsNone(fresh.identity(old_token))

    def test_retry_does_not_revert_a_later_permission_change(self):
        old, _ = self.approved('control')
        new, _ = self.request()
        self.store.replace(new, old, 'control')
        self.store.set_role(new, 'view')
        self.assertEqual(self.store.replace(new, old, 'control')['role'], 'view')
        with self.assertRaises(ValueError): self.store.replace(new, old, 'view')

    def test_two_requests_compete_for_one_target(self):
        old, _ = self.approved()
        requests = [self.request()[0], self.request()[0]]
        def replace(identifier):
            store = DeviceStore(self.path, clock=lambda: self.now)
            try: return store.replace(identifier, old, 'view')['state']
            except ValueError: return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(replace, requests)), ['approved', 'rejected'])
        self.assertEqual(len(self.store.devices()), 1)

    def test_duplicate_concurrent_calls_return_one_commit(self):
        old, _ = self.approved()
        new, _ = self.request()
        def replace(_): return DeviceStore(self.path, clock=lambda: self.now).replace(new, old, 'view')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(replace, range(8)))
        self.assertTrue(all(v == results[0] for v in results))
        self.assertEqual(len(self.store.devices()), 1)

    def test_fail_before_authorization_commit_preserves_old(self):
        old, old_token = self.approved()
        new, token = self.request()
        save = self.store._save
        def fail(path, data):
            if path == self.store.devices_path: raise OSError('before rename')
            save(path, data)
        with patch.object(self.store, '_save', side_effect=fail):
            with self.assertRaises(OSError): self.store.replace(new, old, 'view')
        self.assertIsNotNone(self.store.identity(old_token))
        self.assertIsNone(self.store.identity(token))
        self.assertEqual(self.store.pair_status(new, token)['state'], 'pending')
        with self.assertRaises(ValueError): self.store.approve(new, 'control')
        self.store.replace(new, old, 'view', confirm_online=True)

    def test_failure_before_intent_preserves_all_files(self):
        old, _ = self.approved()
        new, _ = self.request()
        before = {p: p.read_bytes() for p in (self.store.devices_path, self.store.pending_path)}
        with patch.object(self.store, '_save', side_effect=OSError('intent failure')):
            with self.assertRaises(OSError): self.store.replace(new, old, 'view')
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_interrupted_replace_can_reconfirm_changed_role_or_be_denied(self):
        for action in ('retry', 'deny'):
            with self.subTest(action=action):
                old, old_token = self.approved()
                new, token = self.request()
                save = self.store._save
                def fail(path, data):
                    if path == self.store.devices_path: raise OSError('before commit')
                    save(path, data)
                with patch.object(self.store, '_save', side_effect=fail):
                    with self.assertRaises(OSError): self.store.replace(new, old, 'view')
                self.store.set_role(old, 'control')
                if action == 'retry':
                    self.assertEqual(self.store.replace(new, old, 'control')['role'], 'control')
                    self.assertIsNone(self.store.identity(old_token))
                else:
                    self.store.approve(new, 'denied')
                    self.assertEqual(self.store.pair_status(new, token)['state'], 'denied')
                    self.assertIsNotNone(self.store.identity(old_token))
                    self.assertIsNone(self.store.identity(token))

    def test_local_command_dispatch_and_safe_result(self):
        from agent_views.bridge import desktop
        old, _ = self.approved('control')
        new, token = self.request()
        output = io.StringIO()
        argv = ['onework', 'replace', '--id', new, '--old-id', old, '--expected-role', 'control']
        with patch.object(desktop, 'DATA_DIR', self.path), patch.object(desktop, 'DeviceStore', return_value=self.store), \
             patch('sys.argv', argv), redirect_stdout(output):
            self.assertEqual(desktop.main(), 0)
        value = json.loads(output.getvalue())
        self.assertTrue(value['ok'])
        self.assertEqual(value['data']['device']['deviceId'], new)
        self.assertNotIn('hash', value['data']['device'])
        self.assertNotIn(token, output.getvalue())

    def test_commit_survives_pending_save_failure_and_revocation(self):
        old, old_token = self.approved()
        new, token = self.request()
        save = self.store._save
        def fail(path, data):
            if path == self.store.pending_path and data[new]['state'] == 'approved':
                raise OSError('pending unavailable')
            save(path, data)
        with patch.object(self.store, '_save', side_effect=fail):
            self.assertEqual(self.store.replace(new, old, 'view')['state'], 'approved')
        fresh = DeviceStore(self.path, clock=lambda: self.now)
        self.assertEqual(fresh.pair_status(new, token)['state'], 'approved')
        self.assertEqual(fresh.pending(), [])
        self.assertEqual(fresh.replace(new, old, 'view')['state'], 'approved')
        fresh.revoke(new)
        self.assertEqual(fresh.pair_status(new, token)['state'], 'denied')
        self.assertEqual(fresh.pending(), [])
        with self.assertRaises(ValueError): fresh.replace(new, old, 'view')
        with self.assertRaises(ValueError): fresh.approve(new, 'control')
        self.assertIsNone(fresh.identity(old_token))
        self.assertIsNone(fresh.identity(token))

    def test_error_after_atomic_rename_is_success_not_rollback(self):
        old, _ = self.approved()
        new, token = self.request()
        save = self.store._save
        def fail(path, data):
            save(path, data)
            if path == self.store.devices_path: raise OSError('lost acknowledgement')
        with patch.object(self.store, '_save', side_effect=fail):
            self.assertEqual(self.store.replace(new, old, 'view')['state'], 'approved')
        self.assertEqual(self.store.pair_status(new, token)['state'], 'approved')

    def test_full_device_store_accepts_replacement_not_normal_approval(self):
        old = [self.approved()[0] for _ in range(20)][0]
        # Approved entries may still be retained for polling; they must not
        # consume the quota for genuinely pending replacement requests.
        new, _ = self.request()
        with self.assertRaisesRegex(ValueError, '上限'): self.store.approve(new, 'view')
        self.store.replace(new, old, 'view')
        self.assertEqual(len(self.store.devices()), 20)

    def test_pending_limit_still_applies(self):
        for _ in range(20): self.request()
        with self.assertRaises(AgentViewsError) as error: self.request()
        self.assertEqual(error.exception.code, 'devices_full')

    def test_replacement_preserves_online_limit(self):
        old, _ = self.approved()
        for _ in range(3):
            _, token = self.approved()
            self.store.identity(token)
        new, token = self.request()
        self.store.replace(new, old, 'view')
        with self.assertRaises(AgentViewsError) as error: self.store.identity(token)
        self.assertEqual(error.exception.code, 'device_limit')

    def test_never_connected_is_not_online_at_small_clock(self):
        self.now = 1
        self.approved()
        self.assertFalse(self.store.devices()[0]['online'])


if __name__ == '__main__': unittest.main()
