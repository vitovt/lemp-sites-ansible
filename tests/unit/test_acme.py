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
        settings = dict(preflight_site_keys=keys, site_user_max_len=28)
        valid = self.run_ansible_tasks(tasks, {**settings, 'wp_sites': {
            'example.com': {'php_version': '8.4', 'cloudflare_token_ref': 'customer_b'}}})
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        invalid = self.run_ansible_tasks(tasks, {**settings, 'wp_sites': {
            'example.com': {'php_version': '8.4', 'cloudflare_token_ref': '../outside'}}})
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('cloudflare_token_ref must be a simple name', invalid.stdout)

    def test_selected_token_is_checked_inside_recoverable_acme_attempt(self):
        source = yaml.safe_load((ROOT / 'roles/certbot_cloudflare/tasks/certs.yml').read_text())
        tasks = [task for task in source if task['name'] in
                 ('Check the selected DNS token on the controller', 'Require a private selected DNS token')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cloudflare/customer_b.ini'
            path.parent.mkdir()
            path.write_text('dns_cloudflare_api_token = dummy\n')
            variables = dict(secrets_dir=directory, certbot_cloudflare_method='dns-cloudflare',
                             certbot_cloudflare_certs=[{'token_ref': 'customer_b'}])
            for mode, valid in [(0o600, True), (0o644, False)]:
                path.chmod(mode)
                result = self.run_ansible_tasks(tasks, variables)
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
            path.unlink()
            self.assertNotEqual(self.run_ansible_tasks(tasks, variables).returncode, 0)

    def test_acme_failure_continues_other_sites_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            refreshed = root / 'refreshed'
            tasks = yaml.safe_load((ROOT / 'roles/certbot_cloudflare/tasks/attempt.yml').read_text())
            tasks[1]['block'][1] = {'ansible.builtin.command': {
                'argv': [sys.executable, '-c', 'import sys;sys.exit(int(sys.argv[1]))',
                         "{{ 1 if certificate_site.key == 'bad.example' and fail_first else 0 }}"]}}
            tasks[-1].pop('ansible.builtin.include_role')
            tasks[-1]['ansible.builtin.lineinfile'] = {
                'path': str(refreshed), 'create': True, 'line': '{{ certificate_site.key }}'}
            attempt = root / 'attempt.yml'
            attempt.write_text(yaml.safe_dump(tasks, sort_keys=False))
            actions = [{'ansible.builtin.include_tasks': str(attempt),
                        'loop': [{'key': 'bad.example'}, {'key': 'good.example'}],
                        'loop_control': {'loop_var': 'certificate_site'}}]
            result = self.run_ansible_tasks(actions, {'fail_first': True, 'certbot_cloudflare_method': 'webroot'})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('WARNING:', result.stdout)
            self.assertEqual(refreshed.read_text().splitlines(), ['good.example'])
            retry = self.run_ansible_tasks(actions, {'fail_first': False, 'certbot_cloudflare_method': 'webroot'})
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            self.assertEqual(set(refreshed.read_text().splitlines()), {'bad.example', 'good.example'})
            refreshed.unlink()
            invalid = self.run_ansible_tasks(actions, {'fail_first': False,
                                                       'certbot_cloudflare_method': 'invalid'})
            self.assertEqual(invalid.returncode, 0, invalid.stdout + invalid.stderr)
            self.assertFalse(refreshed.exists())

    def test_sites_bootstrap_before_acme(self):
        play = yaml.safe_load((ROOT / 'playbooks/site.yml').read_text())[0]
        roles = [item['role'] for item in play['roles']]
        self.assertLess(roles.index('site'), roles.index('certbot_cloudflare'))

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
