"""Checked replacement, recovery archives, and actionable batch reports."""
import hashlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

loader = importlib.machinery.SourceFileLoader('reinstall_updater', str(Path(__file__).resolve().parents[2] / 'server-tools/bin/wp-update'))
spec = importlib.util.spec_from_loader(loader.name, loader)
updater = importlib.util.module_from_spec(spec)
loader.exec_module(updater)


class ReplacementTests(unittest.TestCase):
    def test_command_failure_keeps_output_private_and_redacts_diagnostics(self):
        result = SimpleNamespace(returncode=1, stdout='Downloading package',
            stderr='Error: permission denied\nhttps://user:password@example.com/?token=private\nDB_PASSWORD=hidden\n')
        with patch.object(updater.subprocess, 'run', return_value=result):
            with self.assertRaises(updater.CommandFailure) as caught:
                updater.command(['runuser', '-u', 'site'])
        self.assertNotIn('permission denied', str(caught.exception))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'logs').mkdir()
            filename = Path(updater.save_failure_diagnostic({'base': base}, 'core', 'core', 'update', caught.exception))
            self.assertEqual(filename.stat().st_mode & 0o777, 0o600)
            text = filename.read_text()
            self.assertIn('Stage: update', text)
            self.assertIn('permission denied', text)
            self.assertIn('Downloading package', text)
            self.assertNotIn('hidden', text)
            self.assertNotIn('user:password', text)
            self.assertNotIn('token=private', text)

    def test_manifest_detects_extra_modified_missing_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'htdocs/wp-content/plugins/demo'
            root.mkdir(parents=True)
            file = root / 'demo.php'
            file.write_text('trusted')
            expected = {'demo.php': hashlib.sha256(b'trusted').hexdigest()}
            updater.verify_manifest({'base': base}, 'plugin', 'demo', expected)
            file.write_text('modified')
            with self.assertRaises(RuntimeError):
                updater.verify_manifest({'base': base}, 'plugin', 'demo', expected)
            file.unlink()
            with self.assertRaises(RuntimeError):
                updater.verify_manifest({'base': base}, 'plugin', 'demo', expected)
            file.write_text('trusted')
            extra = root / 'extra.php'
            extra.write_text('unknown')
            with self.assertRaises(RuntimeError):
                updater.verify_manifest({'base': base}, 'plugin', 'demo', expected)
            extra.unlink()
            extra.symlink_to(file)
            with self.assertRaises(RuntimeError):
                updater.verify_manifest({'base': base}, 'plugin', 'demo', expected)

    def test_package_manifest_rejects_traversal_and_wrong_root(self):
        for path in ['demo/demo.php', 'demo/../bad.php', 'other/file.php']:
            package = io.BytesIO()
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr(path, 'trusted')
            response = io.BytesIO(package.getvalue())
            response.geturl = lambda: 'https://downloads.wordpress.org/plugin/demo.1.0.zip'
            with patch.object(updater, 'urlopen', return_value=response):
                if path == 'demo/demo.php':
                    self.assertIn('demo.php', updater.release_manifest('plugin', 'demo', '1.0'))
                else:
                    with self.assertRaises(RuntimeError):
                        updater.release_manifest('plugin', 'demo', '1.0')

    def test_reinstall_and_dirty_update_commit_only_verified_result(self):
        for action, dirty, invalid in [('reinstall', False, False), ('apply', True, False), ('apply', True, True)]:
            with self.subTest(action=action, invalid=invalid), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / 'conf').mkdir()
                site = {'base': base, 'domain': 'specific.example'}
                args = SimpleNamespace(action=action, scope='plugin:demo', policy='latest',
                    skip_backup_check=True, automatic=False, no_maintenance=False, allow_dirty_target=True)
                reports = []
                def wp(*values, **kwargs):
                    return SimpleNamespace(stdout='active' if '--field=status' in values else '1.0', returncode=0)
                with patch.object(updater, 'wp', side_effect=wp), \
                     patch.object(updater, 'git', return_value=SimpleNamespace(returncode=0)), \
                     patch.object(updater, 'policy', return_value={}), \
                     patch.object(updater, 'inventory', return_value=[('plugin', 'demo', '0.9', '1.0', ['wp-content/plugins/demo'])]), \
                     patch.object(updater, 'target_dirty', return_value=dirty), \
                     patch.object(updater, 'file_state', return_value={}), \
                     patch.object(updater, 'translation_paths', return_value=[]), \
                     patch.object(updater, 'release_manifest', return_value={'demo.php': 'hash'}), \
                     patch.object(updater, 'preserve_target', return_value='/private/original.tar'), \
                     patch.object(updater, 'verify_manifest', side_effect=RuntimeError('Mismatch') if invalid else None), \
                     patch.object(updater, 'command', return_value=SimpleNamespace(stdout='200')), \
                     patch.object(updater, 'commit_paths', return_value='commit') as commit, \
                     patch.object(updater, 'report', side_effect=lambda s, lines: reports.extend(lines)), \
                     patch.object(updater.subprocess, 'run'):
                    self.assertEqual(updater.apply(site, args), invalid)
                self.assertEqual(commit.called, not invalid)
                self.assertFalse((base / 'conf/maintenance').exists())
                if invalid:
                    self.assertTrue(any('NEEDS ATTENTION' in line for line in reports))
                    self.assertTrue(any('specific.example --scope plugin:demo' in line for line in reports))

    def test_dirty_batch_summary_contains_exact_review_and_retry_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'conf').mkdir()
            args = SimpleNamespace(action='apply', scope='plugins', policy='latest',
                skip_backup_check=True, automatic=False, no_maintenance=False, allow_dirty_target=False)
            reports = []
            with patch.object(updater, 'git', return_value=SimpleNamespace(returncode=0)), \
                 patch.object(updater, 'policy', return_value={}), \
                 patch.object(updater, 'inventory', return_value=[('plugin', 'export-all-urls', '4.7.1', '6.0', ['wp-content/plugins/export-all-urls'])]), \
                 patch.object(updater, 'target_dirty', return_value=True), \
                 patch.object(updater, 'wp') as wp, \
                 patch.object(updater, 'report', side_effect=lambda s, lines: reports.extend(lines)), \
                 patch.object(updater.subprocess, 'run'):
                self.assertTrue(updater.apply({'base': base, 'domain': 'allourworld.info'}, args))
            wp.assert_not_called()
            summary = '\n'.join(reports[reports.index('NEEDS ATTENTION (not updated):'):])
            self.assertIn('export-all-urls: target has local changes', summary)
            self.assertIn('wp-site git allourworld.info diff HEAD -- wp-content/plugins/export-all-urls', summary)
            self.assertIn('wp-update apply allourworld.info --scope plugin:export-all-urls --policy latest --allow-dirty-target', summary)

    def test_reinstall_rejects_bulk_policy_and_automatic_arguments(self):
        for options in [[], ['--scope', 'plugins'], ['--scope', 'plugin:demo', '--policy', 'latest'],
                        ['--scope', 'theme:demo', '--automatic']]:
            with self.subTest(options=options), patch.object(sys, 'stderr', io.StringIO()):
                with self.assertRaises(SystemExit):
                    updater.main(['reinstall', 'specific.example', *options])

    def test_recovery_archive_is_private_and_preserves_untracked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'htdocs/wp-content/plugins/demo'
            root.mkdir(parents=True)
            (root / 'local.php').write_text('local changes')
            original_lstat = Path.lstat
            def owned(path, *args, **kwargs):
                info = original_lstat(path, *args, **kwargs)
                if path == base / 'backup':
                    return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
                return info
            with patch.object(Path, 'lstat', owned):
                saved = Path(updater.preserve_target({'base': base}, 'plugin', 'demo'))
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            with tarfile.open(saved) as archive:
                self.assertEqual(archive.extractfile('demo/local.php').read(), b'local changes')
