"""Run real handler sequencing against private pool trees and service stubs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class PHPHandlerTests(unittest.TestCase):
    def test_pool_discovery_after_success_and_early_failure(self):
        for pool, invalid, early_failure in [(False, False, True), (True, False, False),
                                              (True, False, True), (True, True, False)]:
            with self.subTest(pool=pool, invalid=invalid, early_failure=early_failure):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    pools = root / '8.4/fpm/pool.d'
                    pools.mkdir(parents=True)
                    if pool:
                        (pools / 'site.conf').write_text('[site]\n')
                    trace = root / 'trace'
                    stub = root / 'service'
                    stub.write_text('#!/bin/sh\necho "$1" >> "' + str(trace) + '"\n'
                                    + ('[ "$1" != validate ]\n' if invalid else 'exit 0\n'))
                    stub.chmod(0o755)
                    handlers = yaml.safe_load((ROOT / 'roles/php/handlers/main.yml').read_text())
                    handlers[0]['ansible.builtin.find']['paths'] = str(root) + '/{{ item }}/fpm/pool.d'
                    handlers[1]['ansible.builtin.command']['argv'] = [str(stub), 'validate']
                    handlers[2].pop('ansible.builtin.systemd_service')
                    handlers[2]['ansible.builtin.command'] = {'argv': [str(stub), 'reload']}
                    tasks = [{'ansible.builtin.debug': {'msg': 'changed'}, 'changed_when': True,
                              'notify': 'Reload php-fpm'}]
                    if early_failure:
                        tasks.append({'ansible.builtin.fail': {'msg': 'certificate failure'}})
                    play = [{'hosts': 'localhost', 'gather_facts': False, 'force_handlers': True,
                             'vars': {'php_site_versions': ['8.4']}, 'tasks': tasks, 'handlers': handlers}]
                    path = root / 'test.yml'
                    path.write_text(yaml.safe_dump(play, sort_keys=False))
                    result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(path)],
                        env={**os.environ, 'ANSIBLE_LOCAL_TEMP': str(root / 'ansible'),
                             'ANSIBLE_REMOTE_TEMP': str(root / 'remote')},
                        capture_output=True, text=True)
                    self.assertNotIn('UNREACHABLE!', result.stdout)
                    self.assertEqual(result.returncode == 0, not (early_failure or invalid), result.stdout + result.stderr)
                    self.assertEqual(trace.read_text().splitlines() if trace.exists() else [],
                                     [] if not pool else ['validate'] if invalid else ['validate', 'reload'])


if __name__ == '__main__':
    unittest.main()
