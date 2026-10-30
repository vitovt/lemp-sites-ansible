import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class SSHTests(unittest.TestCase):
    def test_socket_activation_and_failed_validation(self):
        handlers = yaml.safe_load((ROOT / 'roles/common/handlers/main.yml').read_text())
        handlers = [item for item in handlers if item.get('listen') == 'Reload sshd']
        for socket_rc, validation_rc, expected in [(0, 0, 'restarted'), (3, 0, 'reloaded'), (0, 1, '')]:
            with self.subTest(socket_rc=socket_rc, validation_rc=validation_rc), tempfile.TemporaryDirectory() as directory:
                tasks = copy.deepcopy(handlers)
                for task in tasks:
                    task.pop('listen')
                tasks[0]['ansible.builtin.command']['argv'] = ['/bin/sh', '-c', 'exit ' + str(validation_rc)]
                tasks[0]['ignore_errors'] = True  # Simulate force_handlers after a failed validation.
                tasks[1]['ansible.builtin.command']['argv'] = ['/bin/sh', '-c', 'exit ' + str(socket_rc)]
                action = tasks[2].pop('ansible.builtin.systemd_service')
                tasks[2]['ansible.builtin.set_fact'] = {'applied_state': action['state']}
                tasks.append({'ansible.builtin.assert': {'that': 'applied_state | default("") == expected'}})
                path = Path(directory) / 'play.yml'
                path.write_text(yaml.safe_dump([{'hosts':'localhost', 'gather_facts':False,
                    'vars':{'expected':expected}, 'tasks':tasks}]))
                result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(path)],
                    capture_output=True, text=True, env={**os.environ, 'ANSIBLE_LOCAL_TEMP':directory,
                        'ANSIBLE_REMOTE_TEMP':directory, 'ANSIBLE_NOCOWS':'1'})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
