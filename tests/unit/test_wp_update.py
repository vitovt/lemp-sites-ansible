import contextlib
import io
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

    def test_backup_refusal_is_visible_and_does_not_run_updates(self):
        args = SimpleNamespace(scope='core', policy='minor', skip_backup_check=False)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'logs').mkdir()
            site = {'base': base, 'domain': 'example.com'}
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(module, 'backup_today', return_value=None), \
                 patch.object(module, 'wp') as wp, \
                 patch.object(module.subprocess, 'run'), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertTrue(module.apply(site, args))
            wp.assert_not_called()
            self.assertIn('FAIL No completed site backup from today', errors.getvalue())
            self.assertIn('wp-backup example.com', errors.getvalue())
            self.assertIn('--skip-backup-check', errors.getvalue())
            self.assertIn('recovery data may be unavailable', errors.getvalue())
            logs = list((base / 'logs').glob('wp-update-*.log'))
            self.assertEqual(len(logs), 1)
            self.assertIn('LOG ' + str(logs[0]), errors.getvalue())
            self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
            self.assertIn('FAIL No completed site backup from today', logs[0].read_text())
            self.assertIn('HINT Create a backup: wp-backup example.com', logs[0].read_text())
            self.assertFalse((base / 'conf/maintenance').exists())

    def test_reporting_failures_do_not_hide_the_original_result(self):
        with tempfile.TemporaryDirectory() as directory:
            errors = io.StringIO()
            with patch.object(module.subprocess, 'run', side_effect=OSError('unavailable')), \
                 contextlib.redirect_stderr(errors):
                module.report({'base': Path(directory), 'domain': 'example.com'}, ['FAIL original cause', 'FAIL'])
            self.assertIn('FAIL original cause', errors.getvalue())
            self.assertIn('log could not be written', errors.getvalue())
            self.assertIn('syslog delivery is unavailable', errors.getvalue())

    def test_each_failure_category_has_actionable_advice(self):
        site = {'domain': 'example.com', 'base': Path('/srv/sites/example.com')}
        cases = {
            'No completed site backup from today': 'wp-backup example.com',
            'Site is already in maintenance': 'conf/maintenance',
            'Git baseline is missing': 'initial commit',
            'Git index or worktree is not clean': 'git diff',
            'target has local changes': '--allow-dirty-target',
            'held: Local patch': 'wp-site policy unhold',
            'source checksum is unavailable or does not match': 'wp-site check example.com plugins',
            'Plugin checksum verification failed': 'trusted release',
            'core:core expected 6.1, got 6.0': 'wp core version',
            'Uncached health check failed': 'PHP-FPM',
            'Update changed paths outside the selected component': 'Do not bulk-commit',
            'Invalid site facts': 'scripts/lemp SERVER doctor',
            'Unsafe operation lock ownership or link': 'Do not bypass the lock',
            'Operation lock timeout': 'Do not delete the lock',
            'runuser failed (exit 1)': 'permissions, disk space',
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                lines = module.diagnostic(site, 'FAIL ' + reason)
                self.assertIn(expected, '\n'.join(lines))
                self.assertTrue(all(x.startswith('HINT ') for x in lines[1:]))

    def test_hint_commands_quote_site_values(self):
        site = {'domain': 'site; touch /tmp/unsafe', 'base': Path('/srv/sites/site with spaces')}
        advice = '\n'.join(module.error_hints(site, 'No completed site backup from today'))
        self.assertIn("wp-backup 'site; touch /tmp/unsafe'", advice)
        advice = '\n'.join(module.error_hints(site, 'Site is already in maintenance'))
        self.assertIn("'/srv/sites/site with spaces'/conf/maintenance", advice)

    def test_invalid_cli_has_help_hint(self):
        result = self.run_cli('apply', 'example.com', '--scope', 'invalid')
        self.assertEqual(result.returncode, 2)
        self.assertIn('HINT\n  Run wp-update --help', result.stderr)

    def test_plan_failure_has_hints_without_a_traceback(self):
        errors = io.StringIO()
        with patch.object(module.os, 'geteuid', return_value=0), \
             patch.object(module, 'library', return_value=(lambda domain: (_ for _ in ()).throw(ValueError('Unknown site')), None)), \
             contextlib.redirect_stderr(errors):
            self.assertEqual(module.main(['plan', 'example.com']), 1)
        self.assertIn('FAIL Unknown site', errors.getvalue())
        self.assertIn('HINT ', errors.getvalue())
        self.assertNotIn('Traceback', errors.getvalue())

    def test_commands_override_inherited_umask_without_changing_parent(self):
        previous_mask = os.umask(0o022)
        try:
            result = module.command([sys.executable, '-c', 'import os; print(oct(os.umask(0)))'])
            self.assertEqual(result.stdout.strip(), '0o27')
            self.assertEqual(os.umask(0o022), 0o022)
        finally:
            os.umask(previous_mask)

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
            previous_mask = os.umask(0o022)
            old_objects = set((root / '.git/objects').rglob('*'))
            try:
                with patch.object(module, 'command', side_effect=without_runuser):
                    commit = module.commit_paths(s, ['wp-content/plugins/selected'], 'update selected')
            finally:
                os.umask(previous_mask)
            new_objects = set((root / '.git/objects').rglob('*')) - old_objects
            self.assertTrue(new_objects)
            for path in [root / '.git/index', *new_objects]:
                self.assertEqual(path.stat().st_mode & 0o027, 0, str(path))
                self.assertEqual(path.stat().st_uid, os.getuid(), str(path))
            self.assertEqual((root / '.git/index').stat().st_mode & 0o777, 0o640)
            self.assertEqual(git('rev-parse', 'HEAD'), commit)
            self.assertEqual(git('show', '--format=', '--name-only', 'HEAD'),
                             'wp-content/plugins/selected/main.php')
            self.assertEqual(git('diff', '--cached', '--name-only'), 'other.php')
            self.assertEqual(git('status', '--porcelain'), 'M  other.php')


if __name__ == '__main__':
    unittest.main()
