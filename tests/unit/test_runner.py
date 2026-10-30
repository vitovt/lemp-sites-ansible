import json
import ast
import os
from pathlib import Path
import subprocess
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RunnerTests(unittest.TestCase):
    def test_only_the_declared_disk_alert_can_be_expected(self):
        source = ast.parse((ROOT / 'tests/run-integration.py').read_text())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'disk_alert_only')
        namespace = {'re':re}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<runner policy>', 'exec'), namespace)
        check = namespace['disk_alert_only']
        valid = "    msg: '1 FAIL: FAIL host disk_space: / 92% used'\nwptest : ok=8 changed=0 unreachable=0 failed=1\n"
        self.assertTrue(check(valid))
        self.assertFalse(check(valid.replace('1 FAIL:', '2 FAIL:')))
        self.assertFalse(check(valid.replace('host disk_space', 'host host_services')))
        self.assertFalse(check(valid.replace('unreachable=0', 'unreachable=1')))
        self.assertFalse(check(''))

    def test_root_and_sudo_connections_use_the_correct_key_path(self):
        for user, keys, sudo in [('root', '/root/.ssh/authorized_keys', False),
                                 ('testadmin', '/home/testadmin/.ssh/authorized_keys', True)]:
            with self.subTest(user=user), tempfile.TemporaryDirectory(prefix='lemp-integration-runner-') as directory:
                base = Path(directory)
                binary = base / 'bin'
                binary.mkdir()
                log = base / 'ssh-arguments'
                ssh = binary / 'ssh'
                ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> ' + str(log) + '\n')
                ssh.chmod(0o755)
                playbook = binary / 'ansible-playbook'
                playbook.write_text('#!/bin/sh\necho "wptest : ok=1 changed=0 unreachable=0 failed=0"\n')
                playbook.chmod(0o755)
                key = base / 'key'
                key.touch()
                env = {**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH']}
                result = subprocess.run(['python3', str(ROOT / 'tests/run-integration.py'),
                    '--host', '192.0.2.1', '--user', user, '--key', str(key), '--disposable',
                    '--phase', 'deploy', '--work', str(base)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                hosts = json.loads((base / 'project/inventory/hosts.yml').read_text())
                settings = hosts['all']['children']['wp']['hosts']['wptest']
                self.assertEqual(settings['common_ssh_admin_keys_file'], keys)
                self.assertNotIn('/etc/letsencrypt', settings['backup_restic_paths'])
                self.assertIn('/etc/ssl/private/ssl-cert-snakeoil.key', settings['backup_restic_paths'])
                self.assertEqual('sudo -n sh -c' in log.read_text(), sudo)
                self.assertIn('IdentityAgent=none', log.read_text())
                self.assertIn('ServerAliveInterval=15', log.read_text())
                self.assertEqual(settings['ansible_ssh_retries'], 2)
