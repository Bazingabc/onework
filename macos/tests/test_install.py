import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('onework_install', Path(__file__).resolve().parents[1] / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.target = self.root / 'Applications/OneWork.app'
        self.source = self.root / 'candidate/OneWork.app'
        self.bundle(self.source, 5)
        self.options = {'verify': lambda path: None, 'running': lambda path: []}

    def bundle(self, path, build):
        for item in ('MacOS/OneWork', 'Resources/python/bin/python3',
                     'Resources/runtime/agent_views/bridge/desktop.py'):
            dest = path / 'Contents' / item
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(str(build))
        with (path / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': installer.BUNDLE_ID, 'CFBundleExecutable': 'OneWork',
                          'CFBundleShortVersionString': '0.3.0', 'CFBundleVersion': str(build)}, stream)

    def test_plan_does_not_create_target(self):
        plan = installer.prepare(self.source, self.target, **self.options)
        self.assertEqual(plan['incoming']['build'], '5')
        self.assertFalse(self.target.parent.exists())

    def test_install_and_upgrade_keep_old_bundle_and_data(self):
        data = self.root / '.agent-views/credentials'
        data.parent.mkdir(); data.write_text('untouched')
        installer.install(self.source, self.target, **self.options)
        self.bundle(self.source, 6)
        result = installer.install(self.source, self.target, **self.options)
        self.assertEqual(installer.bundle_info(self.target)['build'], '6')
        self.assertEqual(installer.bundle_info(Path(result['backup']) / 'OneWork.app')['build'], '5')
        self.assertEqual(data.read_text(), 'untouched')

    def test_explicit_rollback_preserves_replaced_version(self):
        installer.install(self.source, self.target, **self.options)
        self.bundle(self.source, 6)
        updated = installer.install(self.source, self.target, **self.options)
        old = Path(updated['backup']) / 'OneWork.app'
        with self.assertRaises(ValueError):
            installer.install(old, self.target, **self.options)
        rollback = installer.install(old, self.target, rollback=True, **self.options)
        self.assertEqual(installer.bundle_info(self.target)['build'], '5')
        self.assertEqual(installer.bundle_info(Path(rollback['backup']) / 'OneWork.app')['build'], '6')

    def test_running_bundle_is_not_replaced(self):
        self.bundle(self.target, 4)
        with self.assertRaises(ValueError):
            installer.install(self.source, self.target, verify=lambda path: None, running=lambda path: [42])
        self.assertEqual(installer.bundle_info(self.target)['build'], '4')

    def test_bad_signature_leaves_previous_install_untouched(self):
        self.bundle(self.target, 4)
        with self.assertRaises(RuntimeError):
            installer.install(self.source, self.target, running=lambda path: [],
                              verify=lambda path: (_ for _ in ()).throw(RuntimeError('bad signature')))
        self.assertEqual(installer.bundle_info(self.target)['build'], '4')

    def test_failed_post_swap_verification_restores_previous(self):
        self.bundle(self.target, 4)
        def verify(path):
            if path == self.target:
                raise RuntimeError('post-swap verification failure')
        with self.assertRaises(RuntimeError):
            installer.install(self.source, self.target, running=lambda path: [], verify=verify)
        self.assertEqual(installer.bundle_info(self.target)['build'], '4')

    def test_failed_publish_restores_previous(self):
        self.bundle(self.target, 4)
        rename = Path.rename
        def fail(path, destination):
            if '.onework-stage-' in str(path) and destination == self.target:
                raise OSError('simulated disk error')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail), self.assertRaises(OSError):
            installer.install(self.source, self.target, **self.options)
        self.assertEqual(installer.bundle_info(self.target)['build'], '4')

    def test_symlink_target_or_backup_is_rejected(self):
        self.target.parent.mkdir()
        self.target.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install(self.source, self.target, **self.options)
        self.target.unlink()
        (self.target.parent / '.onework-backups').symlink_to(self.source.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install(self.source, self.target, **self.options)
        self.assertTrue(self.source.exists())

    def test_recovery_failure_retains_stage_backup_and_actionable_error(self):
        self.bundle(self.target, 4)
        rename = Path.rename
        def fail_restore(path, destination):
            if '.onework-backups' in str(path) and destination == self.target:
                raise OSError('restore unavailable')
            return rename(path, destination)
        def verify(path):
            if path == self.target:
                raise RuntimeError('new signature invalid')
        with patch.object(Path, 'rename', fail_restore), self.assertRaises(RuntimeError) as error:
            installer.install(self.source, self.target, running=lambda path: [], verify=verify)
        self.assertIn('new signature invalid', str(error.exception))
        self.assertIn('restore unavailable', str(error.exception))
        self.assertIn('--rollback', str(error.exception))
        self.assertEqual(len(list(self.target.parent.glob('.onework-stage-*/failed.app'))), 1)
        backups = list(self.target.parent.glob('.onework-backups/*/OneWork.app'))
        self.assertEqual(installer.bundle_info(backups[0])['build'], '4')


if __name__ == '__main__':
    unittest.main()
