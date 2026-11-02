"""Check that site-apply rejects changes needing shared service work."""
import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
PLAY = yaml.safe_load((ROOT / 'playbooks/site-apply.yml').read_text())[0]


class SiteApplyTests(unittest.TestCase):
    def test_selected_site_needs_explicit_php_branch(self):
        variables = {'wp_site_only': 'first.example',
                     'wp_sites': {'first.example': {}}}
        result = self.run_check('Require an explicit PHP branch for the selected site', variables)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Set a quoted major.minor php_version', result.stdout)
        variables['wp_sites']['first.example']['php_version'] = '8.6'
        result = self.run_check('Require an explicit PHP branch for the selected site', variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_task_list_excludes_shared_service_convergence(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['ansible-playbook', '-i',
                str(ROOT / 'inventories/example-server/settings.yml'),
                str(ROOT / 'playbooks/site-apply.yml'), '--list-tasks'],
                env={**os.environ, 'ANSIBLE_LOCAL_TEMP': directory},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('site : Configure sites', result.stdout)
            for name in ('nginx : Install nginx packages', 'php : Install PHP packages',
                         'mariadb :', 'valkey : Install Valkey', 'backup_restic :'):
                self.assertNotIn(name, result.stdout)

    def run_check(self, name, variables):
        task = next(task for task in PLAY['pre_tasks'] if task.get('name') == name)
        with tempfile.TemporaryDirectory() as directory:
            playbook = Path(directory) / 'check.yml'
            playbook.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False,
                'vars': variables, 'tasks': [task]}], sort_keys=False))
            return subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(playbook)],
                env={**os.environ, 'ANSIBLE_LOCAL_TEMP': directory + '/ansible'},
                capture_output=True, text=True)

    def test_certificate_alias_change_requires_full_run(self):
        variables = {'wp_site_only': 'first.example',
                     'wp_sites': {'first.example': {'aliases': ['www.first.example']}},
                     'site_apply_cert_names': {'stdout': 'DNS:first.example, DNS:www.first.example'},
                     'certbot_cloudflare_require_valid': True}
        result = self.run_check('Require a certificate for every requested name', variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        variables['site_apply_cert_names']['stdout'] = 'DNS:first.example'
        result = self.run_check('Require a certificate for every requested name', variables)
        self.assertNotEqual(result.returncode, 0)

    def test_redis_toggle_requires_review(self):
        variables = {'wp_site_only': 'first.example',
                     'wp_sites': {'first.example': {'redis': True}},
                     'site_apply_env': {'content': base64.b64encode(b'REDIS=true\n').decode()}}
        result = self.run_check('Refuse a Valkey switch through site-apply', variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        variables['wp_sites']['first.example']['redis'] = False
        result = self.run_check('Refuse a Valkey switch through site-apply', variables)
        self.assertNotEqual(result.returncode, 0)

    def test_incomplete_php_branch_requires_full_playbook(self):
        variables = {'wp_site_only': 'first.example',
                     'wp_sites': {'first.example': {'php_version': '8.6'}},
                     'inventory_hostname': 'test-server',
                     'php_required_extensions': ['fpm', 'cli'],
                     'site_apply_php_packages': {'results': [
                         {'stdout': 'installed'}, {'stdout': 'not-installed'}]}}
        result = self.run_check('Require the complete PHP package set', variables)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('needs PHP 8.6 packages', result.stdout)
        variables['site_apply_php_packages']['results'][1]['stdout'] = 'installed'
        result = self.run_check('Require the complete PHP package set', variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_php_binary_has_a_clear_error(self):
        variables = {'wp_site_only': 'first.example',
                     'wp_sites': {'first.example': {'php_version': '99.99'}},
                     'inventory_hostname': 'test-server',
                     'site_apply_php_files': {'results': [
                         {'item': 'one', 'stat': {'exists': True, 'isreg': True}},
                         {'item': 'two', 'stat': {'exists': True, 'isreg': True}},
                         {'item': 'three', 'stat': {'exists': True, 'isreg': True}},
                         {'item': 'missing', 'stat': {'exists': False}}]}}
        result = self.run_check('Require an installed PHP branch', variables)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('needs PHP 99.99 installed and managed', result.stdout)
        variables['site_apply_php_files']['results'][3]['stat'].update({'exists': True, 'isreg': True})
        result = self.run_check('Require an installed PHP branch', variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dns_token_change_requires_full_run(self):
        variables = {'wp_site_only': 'first.example',
                     'wp_sites': {'first.example': {}},
                     'site_apply_saved_token_path': {'stdout': '/etc/letsencrypt/cloudflare.ini'},
                     'certbot_cloudflare_require_valid': True,
                     'certbot_cloudflare_method': 'dns-cloudflare'}
        result = self.run_check('Require the selected DNS token in renewal settings', variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        variables['wp_sites']['first.example']['cloudflare_token_ref'] = 'customer_b'
        result = self.run_check('Require the selected DNS token in renewal settings', variables)
        self.assertNotEqual(result.returncode, 0)
