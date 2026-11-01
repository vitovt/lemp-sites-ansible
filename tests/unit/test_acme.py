import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from jinja2 import Environment
import yaml

ROOT = Path(__file__).resolve().parents[2]


class ACMETests(unittest.TestCase):
    def run_ansible_tasks(self, tasks, variables):
        with tempfile.TemporaryDirectory() as directory:
            playbook = Path(directory) / 'test.yml'
            playbook.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False,
                                                'vars': variables, 'tasks': tasks}], sort_keys=False))
            return subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(playbook)],
                                  cwd=ROOT, env={**os.environ, 'ANSIBLE_LOCAL_TEMP': directory + '/ansible',
                                                 'ANSIBLE_REMOTE_TEMP': directory + '/remote', 'ANSIBLE_NOCOWS': '1'},
                                  capture_output=True, text=True)

    def test_site_token_reference_is_validated_before_use(self):
        tasks = yaml.safe_load((ROOT / 'roles/preflight/tasks/main.yml').read_text())[:2]
        keys = yaml.safe_load((ROOT / 'roles/preflight/defaults/main.yml').read_text())['preflight_site_keys']
        settings = dict(preflight_site_keys=keys, php_default_version='8.3',
                        php_supported_versions=['8.3'], site_user_max_len=28)
        valid = self.run_ansible_tasks(tasks, {**settings, 'wp_sites': {
            'example.com': {'cloudflare_token_ref': 'customer_b'}}})
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        invalid = self.run_ansible_tasks(tasks, {**settings, 'wp_sites': {
            'example.com': {'cloudflare_token_ref': '../outside'}}})
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('cloudflare_token_ref must be a simple name', invalid.stdout)

    def test_missing_site_token_stops_preflight(self):
        tasks = yaml.safe_load((ROOT / 'roles/preflight/tasks/secrets.yml').read_text())[:4]
        with tempfile.TemporaryDirectory() as directory:
            secrets = Path(directory)
            (secrets / 'cloudflare').mkdir()
            (secrets / 'cloudflare.ini').write_text('dns_cloudflare_api_token = test-default\n')
            (secrets / 'cloudflare.ini').chmod(0o600)
            named = secrets / 'cloudflare/customer_b.ini'
            named.write_text('dns_cloudflare_api_token = test-other\n')
            named.chmod(0o600)
            variables = dict(secrets_dir=directory, certbot_cloudflare_enabled=True,
                             certbot_cloudflare_method='dns-cloudflare', wp_sites={
                                 'first.example': {}, 'second.example': {'cloudflare_token_ref': 'customer_b'}})
            valid = self.run_ansible_tasks(tasks, variables)
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            named.chmod(0o644)
            insecure = self.run_ansible_tasks(tasks, variables)
            self.assertNotEqual(insecure.returncode, 0)
            self.assertIn('Missing or insecure Cloudflare token for second.example', insecure.stdout)
            named.unlink()
            missing = self.run_ansible_tasks(tasks, variables)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('Missing or insecure Cloudflare token for second.example', missing.stdout)

    def test_default_and_site_credentials_are_selected_per_certificate(self):
        tasks = yaml.safe_load((ROOT / 'roles/certbot_cloudflare/tasks/certs.yml').read_text())
        template = next(task['ansible.builtin.set_fact']['certbot_cloudflare_certs'] for task in tasks
                        if task['name'] == 'Select certificates and their credentials')
        values = dict(
            wp_sites={'first.example': {'aliases': ['www.first.example']},
                      'second.example': {'cloudflare_token_ref': 'other_account'},
                      'third.example': {'cloudflare_token_ref': 'other_account'}},
            certbot_cloudflare_credentials='/etc/letsencrypt/cloudflare.ini',
            certbot_cloudflare_credentials_dir='/etc/letsencrypt/cloudflare-credentials')
        certs = ast.literal_eval(Environment().from_string(template).render(**values, wp_site_only=''))
        self.assertEqual(certs[0]['credentials'], '/etc/letsencrypt/cloudflare.ini')
        self.assertEqual(certs[0]['names'], ['first.example', 'www.first.example'])
        self.assertEqual(certs[1]['credentials'], '/etc/letsencrypt/cloudflare-credentials/other_account.ini')
        self.assertEqual(certs[2]['credentials'], certs[1]['credentials'])
        selected = ast.literal_eval(Environment().from_string(template).render(**values, wp_site_only='second.example'))
        self.assertEqual([item['domain'] for item in selected], ['second.example'])

    def test_authenticator_arguments(self):
        tasks = yaml.safe_load((ROOT / 'roles/certbot_cloudflare/tasks/certs.yml').read_text())
        template = next(task['ansible.builtin.command']['argv'] for task in tasks if task['name'] == 'Request the certificate')
        env = Environment()
        for mode in ['dns-cloudflare', 'webroot']:
            argv = ast.literal_eval(env.from_string(template).render(
                item={'item':{'domain':'example.com','names':['example.com','www.example.com'],
                              'credentials':'/private/test-token.ini'}},
                certbot_cloudflare_method=mode,
                certbot_cloudflare_propagation_seconds=30, certbot_cloudflare_webroot='/var/lib/letsencrypt',
                certbot_cloudflare_email='', certbot_cloudflare_extra_args=[]))
            self.assertEqual('--dns-cloudflare' in argv, mode == 'dns-cloudflare')
            self.assertEqual('--webroot' in argv, mode == 'webroot')
            self.assertEqual('/private/test-token.ini' in argv, mode == 'dns-cloudflare')
            self.assertIn('example.com,www.example.com', argv)

    def test_renewal_path_readers_accept_certbot_preamble(self):
        tasks = yaml.safe_load((ROOT / 'roles/certbot_cloudflare/tasks/certs.yml').read_text())
        site_apply = yaml.safe_load((ROOT / 'playbooks/site-apply.yml').read_text())[0]
        readers = [next(task['ansible.builtin.command']['argv'][2] for task in tasks
                        if task['name'] == name) for name in
                   ('Read the saved DNS credential path', 'Confirm the saved DNS credential path')]
        readers.append(next(task['ansible.builtin.command']['argv'][2] for task in site_apply['pre_tasks']
                            if task['name'] == 'Read the saved DNS token path'))
        with tempfile.TemporaryDirectory() as directory:
            renewal = Path(directory) / 'example.com.conf'
            renewal.write_text('version = 4.0.0\narchive_dir = /etc/letsencrypt/archive/example.com\n'
                               '[renewalparams]\nauthenticator = dns-cloudflare\n'
                               'dns_cloudflare_credentials = /etc/letsencrypt/cloudflare.ini\n')
            for code in readers:
                with self.subTest(code=code):
                    result = subprocess.run([sys.executable, '-c', code, str(renewal)],
                                            capture_output=True, text=True, check=True)
                    self.assertEqual(result.stdout.strip(), '/etc/letsencrypt/cloudflare.ini')

    def test_only_changed_renewal_credentials_are_reconfigured(self):
        tasks = yaml.safe_load((ROOT / 'roles/certbot_cloudflare/tasks/certs.yml').read_text())
        task = next(task for task in tasks
                    if task['name'] == 'Move existing certificates to their selected DNS credentials')
        with tempfile.TemporaryDirectory() as directory:
            command = Path(directory) / 'certbot'
            log = Path(directory) / 'calls'
            command.write_text('#!/usr/bin/env python3\nimport pathlib,sys\n'
                               f'pathlib.Path({str(log)!r}).write_text(" ".join(sys.argv[1:]))\n')
            command.chmod(0o700)
            task['ansible.builtin.command']['argv'][0] = str(command)
            cert = {'domain': 'example.com', 'credentials': '/etc/letsencrypt/cloudflare-credentials/other.ini'}
            current = {'certbot_cloudflare_saved_credentials': {'results': [
                {'item': cert, 'stdout': cert['credentials']}]}}
            same = self.run_ansible_tasks([task], current)
            self.assertEqual(same.returncode, 0, same.stdout + same.stderr)
            self.assertFalse(log.exists())
            previous = {'certbot_cloudflare_saved_credentials': {'results': [
                {'item': cert, 'stdout': '/etc/letsencrypt/cloudflare.ini'}]}}
            changed = self.run_ansible_tasks([task], previous)
            self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
            self.assertIn('--cert-name example.com --dns-cloudflare', log.read_text())
            self.assertIn('--dns-cloudflare-credentials ' + cert['credentials'], log.read_text())
