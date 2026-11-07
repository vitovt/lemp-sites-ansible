import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'server-tools/bin/wp-site'
sys.path.insert(0, str(ROOT / 'library'))
sys.path.insert(0, str(ROOT / 'server-tools/lib'))
loader = importlib.machinery.SourceFileLoader('wp_site_helper', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class HelperTests(unittest.TestCase):
    def test_runtime_checksum_command_uses_its_own_writable_cache(self):
        args = helper.wp_args({'base': Path('/srv/sites/one.example'), 'PHP_VERSION': '8.4'},
                              'plugin', 'verify-checksums', '--all')
        self.assertIn('HOME=/srv/sites/one.example/tmp', args)
        self.assertIn('WP_CLI_CACHE_DIR=/srv/sites/one.example/tmp/wp-cli-cache', args)
        self.assertNotIn('HOME=/srv/sites/one.example/owner-tmp', args)

    def test_sudo_policy_is_limited_to_the_callers_site(self):
        s = {'SITE_USER': 'site_one'}
        accounts = {'site_one': SimpleNamespace(pw_dir='/srv/sites/one.example', pw_uid=1001),
                    'site_two': SimpleNamespace(pw_dir='/srv/sites/two.example', pw_uid=1002),
                    'admin': SimpleNamespace(pw_dir='/home/admin', pw_uid=1000)}
        with patch.object(helper.os, 'geteuid', return_value=0), \
             patch.object(helper.pwd, 'getpwnam', side_effect=accounts.get):
            with patch.dict(helper.os.environ, {'SUDO_USER': 'site_one', 'SUDO_UID': '1001'}):
                helper.authorized(s, edit=True)
                with self.assertRaises(PermissionError):
                    helper.authorized(s, edit=False)
            with patch.dict(helper.os.environ, {'SUDO_USER': 'site_two', 'SUDO_UID': '1002'}):
                with self.assertRaises(PermissionError):
                    helper.authorized(s, edit=True)
            with patch.dict(helper.os.environ, {'SUDO_USER': 'admin', 'SUDO_UID': '1000'}):
                helper.authorized(s, edit=True)

    def test_help_and_noninteractive_menu(self):
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'server-tools/lib')}
        result = subprocess.run([sys.executable, str(SCRIPT), '--help'], text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertIn('jump-in example.com', result.stdout)
        result = subprocess.run([sys.executable, str(SCRIPT)], text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn('interactive terminal', result.stderr)

    def test_full_hold_command_only_changes_selected_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = {'base': Path(tmp), 'SITE_USER': 'example', 'domain': 'example.com'}
            saved = []
            args = SimpleNamespace(action='hold', site='example.com', component='patched',
                                   kind='plugin', reason='Local fix', mode=None, file=None,
                                   yes=False, yaml=False)
            with patch.object(helper, 'site', return_value=s), patch.object(helper, 'authorized'), \
                 patch.object(helper, 'load', return_value=helper.load.__globals__['empty']()), \
                 patch.object(helper, 'save', side_effect=lambda base, data: saved.append(data)), \
                 patch.object(helper, 'show'):
                helper.policy_command(args)
            self.assertEqual(saved[0]['plugins']['patched']['updates'],
                             {'hold': True, 'reason': 'Local fix'})
            self.assertEqual(saved[0]['themes'], {})

    def test_allow_file_records_exact_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            file = base / 'htdocs/wp-content/plugins/plug/storage/index.php'
            file.parent.mkdir(parents=True)
            file.write_text('reviewed')
            s = {'base': base, 'SITE_USER': 'example', 'domain': 'example.com'}
            args = SimpleNamespace(action='allow-file', site='example.com', component='plug',
                                   kind='plugin', reason=None, mode=None, file='storage/index.php',
                                   yes=True, yaml=False)
            saved = []
            row = {'plugin_name': 'plug', 'file': 'storage/index.php', 'message': 'File was added'}
            with patch.object(helper, 'site', return_value=s), patch.object(helper, 'authorized'), \
                 patch.object(helper, 'load', return_value=helper.load.__globals__['empty']()), \
                 patch.object(helper, 'save', side_effect=lambda root, data: saved.append(data)), \
                 patch.object(helper, 'mismatches', return_value=([row], None)), \
                 patch.object(helper, 'show'):
                helper.policy_command(args)
            exception = saved[0]['plugins']['plug']['checksums']['exceptions']['storage/index.php']
            self.assertEqual(exception['state'], 'added')
            self.assertEqual(exception['sha256'], helper.plugin_file(base, 'plug', 'storage/index.php')[0])

    def test_remove_last_exception_clears_default_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = {'base': Path(tmp), 'SITE_USER': 'example', 'domain': 'example.com'}
            data = {'version': 1, 'plugins': {'plug': {'checksums': {'mode': 'wordpress_org',
                    'exceptions': {'extra.php': {'state': 'deleted'}}}}}, 'themes': {}}
            args = SimpleNamespace(action='remove-file', site='example.com', component='plug',
                                   kind='plugin', reason=None, mode=None, file='extra.php',
                                   yes=False, yaml=False)
            saved = []
            with patch.object(helper, 'site', return_value=s), patch.object(helper, 'authorized'), \
                 patch.object(helper, 'load', return_value=data), \
                 patch.object(helper, 'save', side_effect=lambda base, value: saved.append(value)), \
                 patch.object(helper, 'show'):
                helper.policy_command(args)
            self.assertEqual(saved[0]['plugins'], {})

    def test_partial_hold_prompts_only_for_missing_component_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = {'base': Path(tmp), 'SITE_USER': 'example', 'domain': 'example.com'}
            args = SimpleNamespace(action='hold', site='example.com', component=None,
                                   kind='plugin', reason=None, mode=None, file=None,
                                   yes=False, yaml=False)
            saved = []
            with patch.object(helper, 'site', return_value=s), patch.object(helper, 'authorized'), \
                 patch.object(helper, 'load', return_value={'version': 1, 'plugins': {}, 'themes': {}}), \
                 patch.object(helper, 'save', side_effect=lambda base, value: saved.append(value)), \
                 patch.object(helper, 'show'), patch.object(helper, 'ask', side_effect=['patched', 'Local fix']) as ask:
                helper.policy_command(args)
            self.assertEqual(ask.call_count, 2)
            self.assertEqual(saved[0]['plugins']['patched']['updates']['reason'], 'Local fix')

    def test_policy_menu_asks_for_action_then_its_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = {'base': Path(tmp), 'SITE_USER': 'example', 'domain': 'example.com'}
            args = SimpleNamespace(action=None, site='example.com', component=None,
                                   kind='plugin', reason=None, mode=None, file=None,
                                   yes=False, yaml=False)
            saved = []
            with patch.object(helper, 'site', return_value=s), patch.object(helper, 'authorized'), \
                 patch.object(helper, 'load', return_value={'version': 1, 'plugins': {}, 'themes': {}}), \
                 patch.object(helper, 'save', side_effect=lambda base, value: saved.append(value)), \
                 patch.object(helper, 'show'), \
                 patch.object(helper, 'ask', side_effect=['hold', 'patched', 'Local fix']) as ask:
                helper.policy_command(args)
            self.assertEqual(ask.call_count, 3)
            self.assertEqual(saved[0]['plugins']['patched']['updates']['reason'], 'Local fix')


if __name__ == '__main__':
    unittest.main()
