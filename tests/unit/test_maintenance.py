from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class MaintenanceTests(unittest.TestCase):
    def test_timer_uses_explicit_automatic_mode(self):
        tasks = yaml.safe_load((ROOT / 'roles/maintenance/tasks/main.yml').read_text())
        self.assertEqual(tasks[0]['ansible.builtin.copy']['src'], 'wp-update')
        service_task = next(task for task in tasks if task['name'] == 'Install the update service and timer')
        service = next(item['content'] for item in service_task['loop'] if item['name'] == 'wp-update.service')
        self.assertIn('ExecStart=/usr/local/sbin/wp-update run --automatic --all-sites', service)
        self.assertNotIn('wp-backup update', service)

    def test_new_command_does_not_update_with_no_arguments(self):
        script = ROOT / 'roles/maintenance/files/wp-update'
        result = subprocess.run(['python3', str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('Usage:', result.stdout)
        self.assertNotIn('Examples:', result.stdout)


if __name__ == '__main__':
    unittest.main()
