"""Run real validation tasks and render the production templates in a local sandbox."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


class InventoryTests(unittest.TestCase):
    def test_reserved_names_and_unsupported_php(self):
        tasks = yaml.safe_load((ROOT / 'roles/preflight/tasks/main.yml').read_text())
        defaults = yaml.safe_load((ROOT / 'roles/preflight/defaults/main.yml').read_text())
        for settings in ({'user': 'root'}, {'user': 'admin'}, {'db_name': 'mysql'}, {'php_version': '7.4'}):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as tmp:
                play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': {
                    **defaults, 'php_default_version': '8.3', 'wp_sites': {'a.example': settings}},
                    'tasks': [tasks[0], {'ansible.builtin.assert': {'that': 'preflight_site_errors | length > 0'}}]}]
                path = Path(tmp) / 'test.yml'
                path.write_text(yaml.safe_dump(play))
                result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(path)],
                    env={**os.environ, 'ANSIBLE_LOCAL_TEMP': tmp, 'ANSIBLE_NOCOWS': '1'},
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preflight_shell_on_dash(self):
        tasks = yaml.safe_load((ROOT / 'roles/preflight/tasks/main.yml').read_text())
        task = next(t for t in tasks if t.get('name') == 'Read existing sites on the host')
        spec = task['ansible.builtin.shell']
        cmd = Environment().from_string(spec['cmd']).render(site_root='/nonexistent-test-root')
        result = subprocess.run([spec.get('executable', '/bin/sh'), '-c', cmd], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class PolicyTests(unittest.TestCase):
    def test_network_modes(self):
        env = Environment()
        template = env.from_string((ROOT / 'roles/nginx/templates/cloudflare.conf.j2').read_text())
        for mode in ['direct', 'proxy', 'cloudflare']:
            rendered = template.render(ansible_managed='test', nginx_access_mode=mode,
                nginx_trusted_proxies=['10.0.0.1/32'], nginx_cf_ranges=['192.0.2.0/24'],
                nginx_cloudflare_only=mode != 'direct', nginx_real_ip_header='X-Forwarded-For')
            self.assertIn('default 1;' if mode == 'direct' else 'default 0;', rendered)
            self.assertEqual('set_real_ip_from 10.0.0.1/32;' in rendered, mode == 'proxy')

    def test_firewall_preserves_ipv6_and_blocks_smtp(self):
        env = Environment()
        # Ansible supplies this filter; use its normal boolean value here.
        env.filters['bool'] = bool
        rendered = env.from_string((ROOT / 'roles/firewall/templates/hosting.nft.j2').read_text()).render(
            ansible_managed='test', firewall_ssh_ports=[22], firewall_smtp_enabled=False,
            firewall_smtp_ports=[25, 465, 587, 2525], nginx_access_mode='direct')
        self.assertIn('ipv6-icmp', rendered)
        self.assertIn('policy drop', rendered)
        self.assertIn('25, 465, 587, 2525', rendered)
        self.assertNotIn('flush ruleset', rendered)


if __name__ == '__main__':
    unittest.main()
