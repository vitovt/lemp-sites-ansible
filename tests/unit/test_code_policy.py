import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('code_policy', ROOT / 'server-tools/lib/code_policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class CodePolicyTests(unittest.TestCase):
    def test_site_policy_is_separate_and_root_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / 'site-one/conf'
            second = base / 'site-two/conf'
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            data = policy.empty()
            data['plugins']['patched'] = {'updates': {'hold': True, 'reason': 'Local fix'},
                'checksums': {'mode': 'git', 'exceptions': {}}}
            self.assertEqual(policy.validate(data), data)
            self.assertEqual(policy.load(second.parent), policy.empty())
            if first.stat().st_uid == 0:
                policy.save(first.parent, data)
                self.assertEqual(policy.load(first.parent), data)
                self.assertEqual(policy.load(second.parent), policy.empty())
            else:
                with self.assertRaises(ValueError):
                    policy.save(first.parent, data)

    def test_validation_rejects_traversal_and_unknown_rules(self):
        for path in ('../wp-config.php', '/tmp/file', 'a//b', 'a/./b', 'a\\b'):
            self.assertFalse(policy.safe_path(path))
        data = policy.empty()
        data['plugins']['patched'] = {'checksums': {'mode': 'wordpress_org',
            'exceptions': {'../bad': {'state': 'added', 'sha256': 'a' * 64}}}}
        with self.assertRaises(ValueError):
            policy.validate(data)
        data['plugins']['patched']['checksums']['exceptions'] = {}
        data['plugins']['patched']['checksums']['mode'] = 'git'
        self.assertEqual(policy.validate(data), data)

    def test_exact_added_modified_and_deleted_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'htdocs/wp-content/plugins/plug'
            root.mkdir(parents=True)
            added = root / 'storage/index.php'
            added.parent.mkdir()
            added.write_text('reviewed')
            modified = root / 'main.php'
            modified.write_text('patched')
            data = policy.empty()
            data['plugins']['plug'] = {'checksums': {'mode': 'wordpress_org', 'exceptions': {
                'storage/index.php': {'state': 'added', 'sha256': policy.file_hash(added)},
                'main.php': {'state': 'modified', 'sha256': policy.file_hash(modified)},
                'old.php': {'state': 'deleted'},
            }}}
            for path, message in [('storage/index.php', 'File was added'),
                                  ('main.php', "File doesn't verify against checksum"),
                                  ('old.php', 'File does not exist')]:
                self.assertTrue(policy.allowed_mismatch(base, data, {'plugin_name': 'plug', 'file': path, 'message': message}))
            added.write_text('changed')
            self.assertFalse(policy.allowed_mismatch(base, data, {'plugin_name': 'plug', 'file': 'storage/index.php', 'message': 'File was added'}))
            (root / 'old.php').write_text('returned')
            self.assertFalse(policy.allowed_mismatch(base, data, {'plugin_name': 'plug', 'file': 'old.php', 'message': 'File does not exist'}))
            self.assertFalse(policy.allowed_mismatch(base, data, {'plugin_name': 'plug', 'file': 'other.php', 'message': 'File was added'}))

    def test_plugin_reader_rejects_symlink_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'htdocs/wp-content/plugins/plug'
            root.mkdir(parents=True)
            outside = base / 'outside'
            outside.mkdir()
            (outside / 'secret.php').write_text('private')
            (root / 'storage').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                policy.plugin_file(base, 'plug', 'storage/secret.php')

    def test_deleted_file_can_have_an_absent_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'htdocs/wp-content/plugins/plug').mkdir(parents=True)
            self.assertTrue(policy.plugin_file_absent(base, 'plug', 'removed/old.php'))
            self.assertFalse(policy.plugin_file_absent(base, 'missing-plugin', 'old.php'))

    def test_checker_accepts_only_reviewed_rows_and_reports_unavailable_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / 'htdocs/wp-content/plugins/plug/storage/index.php'
            path.parent.mkdir(parents=True)
            path.write_text('reviewed')
            data = policy.empty()
            data['plugins']['plug'] = {'checksums': {'mode': 'wordpress_org', 'exceptions': {
                'storage/index.php': {'state': 'added', 'sha256': policy.file_hash(path)}}}}
            row = {'plugin_name': 'plug', 'file': 'storage/index.php', 'message': 'File was added'}
            warnings = ('Warning: Could not retrieve the checksums for version 1 of plugin private, skipping.\n'
                        'Error: Only verified 1 of 3 plugins (1 failed, 1 skipped).\n')
            result = SimpleNamespace(stdout=json.dumps([row]), stderr=warnings, returncode=1)
            with patch.object(policy, 'load', return_value=data), patch.object(policy.subprocess, 'run', return_value=result):
                self.assertIn('1 accepted, 1 unavailable', policy.check_plugins(base, 'owner', '8.3', '/mock/wp'))
                extra = {'plugin_name': 'plug', 'file': 'new.php', 'message': 'File was added'}
                result.stdout = json.dumps([row, extra])
                self.assertIn('FAIL plugin_checksums: 1 unexpected', policy.check_plugins(base, 'owner', '8.3', '/mock/wp'))
                result.stdout = json.dumps([])
                self.assertIn('expected local exception is absent or changed', policy.check_plugins(base, 'owner', '8.3', '/mock/wp'))
                result.stdout = json.dumps([row])
                path.write_text('tampered')
                self.assertIn('FAIL plugin_checksums:', policy.check_plugins(base, 'owner', '8.3', '/mock/wp'))

    def test_single_failed_plugin_can_be_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / 'htdocs/wp-content/plugins/plug/extra.php'
            path.parent.mkdir(parents=True)
            path.write_text('reviewed')
            data = policy.empty()
            data['plugins']['plug'] = {'checksums': {'mode': 'wordpress_org', 'exceptions': {
                'extra.php': {'state': 'added', 'sha256': policy.file_hash(path)}}}}
            row = {'plugin_name': 'plug', 'file': 'extra.php', 'message': 'File was added'}
            result = SimpleNamespace(stdout=json.dumps([row]),
                                     stderr='Error: No plugins verified (1 failed).\n', returncode=1)
            with patch.object(policy, 'load', return_value=data), patch.object(policy.subprocess, 'run', return_value=result):
                self.assertIn('PASS plugin_checksums:', policy.check_plugins(base, 'owner', '8.3', '/mock/wp'))

    def test_git_mode_requires_tracked_clean_plugin(self):
        data = policy.empty()
        data['plugins']['custom'] = {'checksums': {'mode': 'git', 'exceptions': {}}}
        success = SimpleNamespace(stdout='Success: Verified 1 of 1 plugins.\n', stderr='', returncode=0)
        inventory = SimpleNamespace(stdout='[{"status":"active"}]', stderr='', returncode=0)
        tracked = SimpleNamespace(stdout='wp-content/plugins/custom/main.php\n', stderr='', returncode=0)
        clean = SimpleNamespace(stdout='', stderr='', returncode=0)
        dirty = SimpleNamespace(stdout=' M wp-content/plugins/custom/main.php\n', stderr='', returncode=0)
        with patch.object(policy, 'load', return_value=data), \
             patch.object(policy.subprocess, 'run', side_effect=[success, tracked, clean, inventory]):
            self.assertIn('PASS plugin_checksums:', policy.check_plugins('/tmp/site', 'owner', '8.3', '/mock/wp'))
        with patch.object(policy, 'load', return_value=data), \
             patch.object(policy.subprocess, 'run', side_effect=[success, tracked, dirty]):
            self.assertIn('Git baseline missing or dirty', policy.check_plugins('/tmp/site', 'owner', '8.3', '/mock/wp'))

    def test_unexpected_checker_stderr_never_passes(self):
        result = SimpleNamespace(stdout='[]', stderr='Error: network authentication failed\n', returncode=0)
        with patch.object(policy, 'load', return_value=policy.empty()), \
             patch.object(policy.subprocess, 'run', return_value=result):
            self.assertIn('FAIL plugin_checksums', policy.check_plugins('/tmp/site', 'owner', '8.3', '/mock/wp'))


if __name__ == '__main__':
    unittest.main()
