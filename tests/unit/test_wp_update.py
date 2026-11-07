import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import sys
import os
import pwd
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'server-tools/bin/wp-update'
loader = importlib.machinery.SourceFileLoader('wp_update', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class InterfaceTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_no_arguments_print_short_help(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn('Usage:', result.stdout)
        self.assertNotIn('Examples:', result.stdout)

    def test_full_help_has_manual_and_scheduled_examples(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('Examples:', result.stdout)
        self.assertIn('--skip-backup-check', result.stdout)
        self.assertIn('--automatic --all-sites', result.stdout)

    def test_invalid_scope_is_rejected_before_privilege_check(self):
        result = self.run_cli('apply', 'example.com', '--scope', 'plugin:../other')
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid scope', result.stderr)

    def test_automatic_requires_all_sites(self):
        result = self.run_cli('run', '--automatic')
        self.assertEqual(result.returncode, 2)

    def test_site_local_holds_do_not_block_other_components(self):
        local = {'version': 1, 'plugins': {
            'patched': {'updates': {'hold': True, 'reason': 'Local fix'}},
            'normal': {}}, 'themes': {
            'custom': {'updates': {'hold': True, 'reason': 'Fixed theme'}}}}
        with patch.dict(sys.modules, {'code_policy': SimpleNamespace(load=lambda base: local)}):
            self.assertEqual(module.policy('example.com'),
                             {'plugins': {'patched': 'Local fix'}, 'themes': {'custom': 'Fixed theme'}})

    def test_manual_skip_does_not_read_backup_credentials(self):
        args = SimpleNamespace(scope='all', policy='latest', skip_backup_check=True,
                               automatic=False, no_maintenance=False, allow_dirty_target=False)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'conf').mkdir()
            site = {'base': base, 'domain': 'example.com'}
            with patch.object(module, 'backup_today', side_effect=AssertionError('backup was read')), \
                 patch.object(module, 'git', return_value=SimpleNamespace(returncode=0)), \
                 patch.object(module, 'inventory', return_value=[]), \
                 patch.object(module, 'policy', return_value={}), \
                 patch.object(module, 'report'):
                self.assertFalse(module.apply(site, args))

    def test_component_commit_keeps_other_staged_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'htdocs'
            root.mkdir()
            (base / 'owner-tmp').mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            git('init', '-q')
            (root / 'wp-content/plugins/selected').mkdir(parents=True)
            (root / 'wp-content/plugins/selected/main.php').write_text('old\n')
            (root / 'other.php').write_text('old\n')
            git('add', '-A')
            git('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'baseline')
            (root / 'wp-content/plugins/selected/main.php').write_text('new\n')
            (root / 'other.php').write_text('manual\n')
            git('add', 'other.php')
            original = module.command
            def without_runuser(args, **kwargs):
                if args[0] == 'runuser':
                    args = args[4:]
                return original(args, **kwargs)
            s = {'base': base, 'SITE_USER': pwd.getpwuid(os.getuid()).pw_name}
            with patch.object(module, 'command', side_effect=without_runuser):
                commit = module.commit_paths(s, ['wp-content/plugins/selected'], 'update selected')
            self.assertEqual(git('rev-parse', 'HEAD'), commit)
            self.assertEqual(git('show', '--format=', '--name-only', 'HEAD'),
                             'wp-content/plugins/selected/main.php')
            self.assertEqual(git('diff', '--cached', '--name-only'), 'other.php')
            self.assertEqual(git('status', '--porcelain'), 'M  other.php')


if __name__ == '__main__':
    unittest.main()
