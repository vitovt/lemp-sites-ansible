"""Controller-only checks for server inventory isolation and safe examples."""
import importlib.machinery
import importlib.util
import configparser
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / 'inventories/example-server'


class InventoryLayoutTests(unittest.TestCase):
    def load_lemp(self):
        loader = importlib.machinery.SourceFileLoader('lemp', str(ROOT / 'scripts/lemp'))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        return module

    def copy_server(self, base, name, domain):
        target = base / 'inventories' / name
        shutil.copytree(EXAMPLE, target)
        settings = target / 'settings.yml'
        settings.write_text(settings.read_text().replace('example-server:', name + ':'))
        sites = target / 'sites.yml'
        sites.write_text('wp_sites:\n  ' + domain + ':\n    user: ' + name.replace('-', '_') + '\n')
        return target

    def test_example_yaml_and_supported_site_fields(self):
        settings = yaml.safe_load((EXAMPLE / 'settings.yml').read_text())
        hosts = settings['all']['children']['wp']['hosts']
        self.assertEqual(list(hosts), ['example-server'])
        self.assertEqual(hosts['example-server']['secrets_dir'], '{{ inventory_dir }}/secrets')
        sites = yaml.safe_load((EXAMPLE / 'sites.yml').read_text())['wp_sites']
        supported = set(yaml.safe_load((ROOT / 'roles/preflight/defaults/main.yml').read_text())['preflight_site_keys'])
        self.assertEqual(set(sites), {'site1.example.com'})
        self.assertTrue(set(sites['site1.example.com']) <= supported)
        source = (EXAMPLE / 'sites.yml').read_text()
        for key in supported:
            with self.subTest(key=key):
                self.assertRegex(source, rf'(?m)^\s*#?\s*{re.escape(key)}:')
        self.assertEqual((EXAMPLE / 'secrets/cloudflare.ini.example').read_text().splitlines()[-1],
                         'dns_cloudflare_api_token = CHANGE_ME')

    def test_no_default_inventory_and_only_example_is_tracked(self):
        config = configparser.ConfigParser()
        config.read(ROOT / 'ansible.cfg')
        self.assertNotIn('inventory', config['defaults'])
        tracked = subprocess.check_output(['git', 'ls-files', '-z', 'inventories'], cwd=ROOT)
        paths = [path.decode() for path in tracked.split(b'\0') if path]
        self.assertTrue(paths)
        self.assertTrue(all(path.startswith('inventories/example-server/') for path in paths), paths)
        self.assertTrue(all('/secrets/' not in path or path.endswith('.example') for path in paths), paths)

    def test_example_site_passes_real_preflight_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            sites = yaml.safe_load((EXAMPLE / 'sites.yml').read_text())['wp_sites']
            defaults = yaml.safe_load((ROOT / 'roles/preflight/defaults/main.yml').read_text())
            tasks = yaml.safe_load((ROOT / 'roles/preflight/tasks/main.yml').read_text())[:2]
            playbook = Path(directory) / 'preflight.yml'
            playbook.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False,
                'vars': {**defaults, 'php_default_version': '8.3', 'wp_sites': sites,
                         'php_supported_versions': ['8.3', '8.4', '8.5'], 'site_user_max_len': 28},
                'tasks': tasks}], sort_keys=False))
            env = {**os.environ, 'ANSIBLE_LOCAL_TEMP': str(Path(directory) / 'tmp')}
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(playbook)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_two_server_copies_load_only_their_own_sites(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for server, domain in [('125-wp-lnmp', 'first.example'), ('241-ans01-presta', 'second.example')]:
                target = self.copy_server(base, server, domain)
                playbook = base / 'load.yml'
                playbook.write_text('''- hosts: wp
  gather_facts: false
  connection: local
  tasks:
    - ansible.builtin.include_vars:
        file: "{{ inventory_dir }}/sites.yml"
    - ansible.builtin.assert:
        that:
          - inventory_hostname == expected_server
          - wp_sites.keys() | list == [expected_domain]
          - secrets_dir == inventory_dir ~ '/secrets'
''')
                env = {**os.environ, 'ANSIBLE_LOCAL_TEMP': str(base / 'ansible-tmp')}
                command = ['ansible-playbook', '-i', str(target / 'settings.yml'), str(playbook),
                           '-e', f'expected_server={server}', '-e', f'expected_domain={domain}']
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_doctor_checks_copied_secret_and_external_key_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = self.copy_server(base, '125-wp-lnmp', 'first.example')
            key = base / 'outside-key'
            key.write_text('test-only')
            key.chmod(0o600)
            settings = target / 'settings.yml'
            settings.write_text(settings.read_text().replace('ansible_user: root',
                f'ansible_user: root\n          ansible_ssh_private_key_file: {key}'))
            token = target / 'secrets/cloudflare.ini'
            token.write_text('dns_cloudflare_api_token = test-only\n')
            token.chmod(0o600)
            lemp = self.load_lemp()
            lemp.ROOT = base
            lemp.INVENTORIES = base / 'inventories'
            self.assertEqual(lemp.inspect('125-wp-lnmp'), settings)
            token.chmod(0o644)
            with self.assertRaisesRegex(ValueError, '0600'):
                lemp.inspect('125-wp-lnmp')
            token.chmod(0o600)
            key.chmod(0o644)
            with self.assertRaisesRegex(ValueError, '0600'):
                lemp.inspect('125-wp-lnmp')

    def test_doctor_command_selects_only_the_named_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = self.copy_server(base, '125-wp-lnmp', 'first.example')
            lemp = self.load_lemp()
            lemp.ROOT = base
            lemp.INVENTORIES = base / 'inventories'
            with mock.patch('sys.argv', ['lemp', '125-wp-lnmp', 'doctor']), \
                 mock.patch.object(lemp.subprocess, 'call', return_value=0) as run:
                self.assertEqual(lemp.main(), 0)
            self.assertEqual(run.call_args.args[0], [
                'ansible-playbook', '-i', str(target / 'settings.yml'),
                'playbooks/doctor.yml', '--limit', '125-wp-lnmp'])

    def test_site_cli_edits_a_copy_of_the_example(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = self.copy_server(base, '125-wp-lnmp', 'first.example')
            binary = base / 'bin'
            binary.mkdir()
            (binary / 'ansible-playbook').write_text('#!/bin/sh\nexit 0\n')
            (binary / 'ansible-playbook').chmod(0o755)
            env = {**os.environ, 'LEMP_INVENTORIES_ROOT': str(base / 'inventories'),
                   'PATH': str(binary) + ':' + os.environ['PATH']}
            command = [str(ROOT / 'scripts/site'), 'add', '125-wp-lnmp', 'new.example',
                       'newsite', '8.3', '--install-wp', '--yes']
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            sites = yaml.safe_load((target / 'sites.yml').read_text())['wp_sites']
            self.assertEqual(set(sites), {'first.example', 'new.example'})
            self.assertTrue(sites['new.example']['wp_install'])

    def test_site_cli_keeps_the_example_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / 'inventories/my-server'
            shutil.copytree(EXAMPLE, target)
            settings = target / 'settings.yml'
            settings.write_text(settings.read_text().replace('example-server:', 'my-server:'))
            binary = base / 'bin'
            binary.mkdir()
            (binary / 'ansible-playbook').write_text('#!/bin/sh\nexit 0\n')
            (binary / 'ansible-playbook').chmod(0o755)
            env = {**os.environ, 'LEMP_INVENTORIES_ROOT': str(base / 'inventories'),
                   'PATH': str(binary) + ':' + os.environ['PATH']}
            result = subprocess.run([str(ROOT / 'scripts/site'), 'add', 'my-server',
                                     'new.example', 'newsite', '8.3', '--yes'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            content = (target / 'sites.yml').read_text()
            self.assertIn('# Linux code owner.', content)
            self.assertIn('# Optional second site.', content)
            self.assertIn('new.example:', content)

    def test_site_cli_refuses_an_unrenamed_host_key(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = self.copy_server(base, '125-wp-lnmp', 'first.example')
            settings = target / 'settings.yml'
            settings.write_text(settings.read_text().replace('125-wp-lnmp:', 'example-server:'))
            before = (target / 'sites.yml').read_text()
            result = subprocess.run([str(ROOT / 'scripts/site'), 'add', '125-wp-lnmp',
                                     'new.example', 'newsite', '8.3', '--yes'],
                                    env={**os.environ, 'LEMP_INVENTORIES_ROOT': str(base / 'inventories')},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((target / 'sites.yml').read_text(), before)
