"""An ACL change must keep cache when the active admin key still works."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]


class ValkeyACLTests(unittest.TestCase):
    def test_acl_change_selects_reload_or_restart_from_admin_state(self):
        tasks = yaml.safe_load((ROOT / 'roles/valkey/tasks/main.yml').read_text())
        acl = next(task for task in tasks if task['name'] == 'Install Valkey ACL users')
        handlers = yaml.safe_load((ROOT / 'roles/valkey/handlers/main.yml').read_text())
        self.assertEqual(acl['notify'], 'Apply valkey ACL')
        self.assertEqual([handler['name'] for handler in handlers], [
            'Restart valkey', 'Reload valkey ACL with existing admin password',
            'Restart valkey for changed admin password'])
        reload, restart = handlers[1:]
        self.assertEqual(reload['when'], 'valkey_acl_reload_safe | bool')
        self.assertEqual(restart['when'], 'not valkey_acl_reload_safe | bool')
        self.assertEqual(reload['ansible.builtin.command']['argv'][-2:], ['ACL', 'LOAD'])
        self.assertIn('REDISCLI_AUTH', reload['environment'])
        self.assertTrue(reload['no_log'])
        self.assertEqual(restart['ansible.builtin.systemd_service']['state'], 'restarted')

    def test_stale_live_acl_is_loaded_even_when_file_is_unchanged(self):
        path = ROOT / 'roles/valkey/files/valkey-acl-sync'
        loader = importlib.machinery.SourceFileLoader('valkey_acl_sync', str(path))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        desired = ('user default off resetkeys resetchannels -@all\n'
                   'user admin on #' + 'a' * 64 + ' ~* &* +@all\n'
                   'user site on #' + 'b' * 64 + ' ~site:* resetchannels +@all -@admin\n')
        active = desired.replace('resetkeys ', '').replace(' on #', ' on sanitize-payload #')
        stale = '\n'.join(active.splitlines()[:2]) + '\n'
        with tempfile.TemporaryDirectory() as directory:
            acl = Path(directory) / 'users.acl'
            admin = Path(directory) / 'admin.pass'
            acl.write_text(desired)
            admin.write_text('test-admin\n')
            current = [active]
            actions = []

            def valkey_cli(argv, **kwargs):
                self.assertEqual(kwargs['env']['REDISCLI_AUTH'], 'test-admin')
                self.assertNotIn('test-admin', argv)
                action = argv[-1]
                actions.append(action)
                if action == 'LOAD':
                    current[0] = active
                    output = 'OK\n'
                else:
                    output = current[0]
                return subprocess.CompletedProcess(argv, 0, output, '')

            with mock.patch.object(module.subprocess, 'run', side_effect=valkey_cli):
                self.assertFalse(module.sync(acl, admin, '/test/socket'))
                self.assertEqual(actions, ['LIST'])
                current[0] = stale
                self.assertTrue(module.sync(acl, admin, '/test/socket'))
                self.assertEqual(actions[-3:], ['LIST', 'LOAD', 'LIST'])
