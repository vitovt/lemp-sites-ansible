"""An ACL change must keep cache when the active admin key still works."""
from pathlib import Path
import unittest

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
