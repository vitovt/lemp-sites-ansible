"""An ACL change must apply the new admin password with the ACL file."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


class ValkeyACLTests(unittest.TestCase):
    def test_acl_change_restarts_with_the_new_credentials(self):
        tasks = yaml.safe_load((ROOT / 'roles/valkey/tasks/main.yml').read_text())
        acl = next(task for task in tasks if task['name'] == 'Install Valkey ACL users')
        handlers = yaml.safe_load((ROOT / 'roles/valkey/handlers/main.yml').read_text())
        self.assertEqual(acl['notify'], 'Restart valkey')
        self.assertEqual([handler['name'] for handler in handlers], ['Restart valkey'])
        self.assertEqual(handlers[0]['ansible.builtin.systemd_service']['state'], 'restarted')
