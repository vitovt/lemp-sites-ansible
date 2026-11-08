import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class CLITests(unittest.TestCase):
    def test_named_cloudflare_token_is_written_before_site_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / 'test/sites.yml'
            path.parent.mkdir(parents=True)
            (path.parent / 'settings.yml').write_text(
                'all:\n  children:\n    wp:\n      hosts:\n        test:\n'
                '          secrets_dir: "{{ inventory_dir }}/secrets"\n')
            path.write_text('wp_sites: {}\n')
            binary = base / 'bin'
            binary.mkdir()
            command = binary / 'ansible-playbook'
            command.write_text('#!/bin/sh\nexit 0\n')
            command.chmod(0o755)
            env = {**os.environ, 'LEMP_INVENTORIES_ROOT': directory,
                   'PATH': str(binary) + ':' + os.environ['PATH']}
            invalid = subprocess.run([str(ROOT / 'scripts/site'), 'add', 'test', 'example.com',
                                      'example', '8.3', '--cloudflare-token-ref', '../escape', '--dry-run'],
                                     env=env, capture_output=True)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(yaml.safe_load(path.read_text())['wp_sites'], {})
            result = subprocess.run([str(ROOT / 'scripts/site'), 'add', 'test', 'example.com',
                                     'example', '10.0', '--cloudflare-token-ref', 'customer_b',
                                     '--install-wp', '--yes'], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            site = yaml.safe_load(path.read_text())['wp_sites']['example.com']
            self.assertEqual(site['php_version'], '10.0')
            self.assertEqual(site['cloudflare_token_ref'], 'customer_b')
            self.assertTrue(site['wp_install'])

    def test_add_saves_and_previews_before_confirmation(self):
        for config, flags, expected in [
            ('', [], 'secrets/cloudflare.ini'),
            ('', ['--cloudflare-token-ref', 'customer_b'], 'secrets/cloudflare/customer_b.ini'),
            ('          certbot_cloudflare_method: webroot\n', [], 'HTTP-01'),
            ('          certbot_cloudflare_enabled: false\n', [], 'ACME disabled'),
        ]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                server = base / 'test'
                server.mkdir()
                (server / 'settings.yml').write_text(
                    'all:\n  children:\n    wp:\n      hosts:\n        test:\n'
                    '          secrets_dir: "{{ inventory_dir }}/secrets"\n' + config)
                (server / 'sites.yml').write_text('wp_sites: {}\n')
                binary = base / 'bin'
                binary.mkdir()
                marker = base / 'deployed'
                command = binary / 'ansible-playbook'
                command.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
                command.chmod(0o755)
                result = subprocess.run([
                    str(ROOT / 'scripts/site'), 'add', 'test', 'example.com',
                    'example', '8.4', '--install-wp', *flags],
                    input='n\n', text=True, capture_output=True,
                    env={**os.environ, 'LEMP_INVENTORIES_ROOT': directory,
                         'PATH': str(binary) + ':' + os.environ['PATH']})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('example.com', yaml.safe_load((server / 'sites.yml').read_text())['wp_sites'])
                self.assertFalse(marker.exists())
                self.assertLess(result.stdout.index('php_version:'), result.stdout.index('Deploy '))
                self.assertLess(result.stdout.index(expected), result.stdout.index('Deploy '))

    def test_clone_can_select_a_named_token_for_new_target(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / 'test/sites.yml'
            path.parent.mkdir(parents=True)
            (path.parent / 'settings.yml').write_text(
                'all:\n  children:\n    wp:\n      hosts:\n        test:\n'
                '          secrets_dir: "{{ inventory_dir }}/secrets"\n')
            path.write_text('wp_sites:\n  source.example:\n    php_version: "8.4"\n')
            binary = base / 'bin'
            binary.mkdir()
            command = binary / 'ansible-playbook'
            command.write_text('#!/bin/sh\nexit 0\n')
            command.chmod(0o755)
            env = {**os.environ, 'LEMP_INVENTORIES_ROOT': directory,
                   'PATH': str(binary) + ':' + os.environ['PATH']}
            result = subprocess.run([str(ROOT / 'scripts/site'), 'clone', 'test', 'source.example',
                                     'target.example', '--cloudflare-token-ref', 'customer_b', '--yes'],
                                    env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(yaml.safe_load(path.read_text())['wp_sites']['target.example']['cloudflare_token_ref'],
                             'customer_b')

    def test_yaml_input_validation_and_exact_domains(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test/sites.yml'
            path.parent.mkdir(parents=True)
            (path.parent / 'settings.yml').write_text(
                'all:\n  children:\n    wp:\n      hosts:\n        test:\n'
                '          secrets_dir: "{{ inventory_dir }}/secrets"\n')
            original = 'wp_sites:\n  a-b.example:\n    user: old\n    php_version: "8.4"\n'
            path.write_text(original)
            env = {**os.environ, 'LEMP_INVENTORIES_ROOT': directory}
            cases = [('a.b.example', 'new', '8.3', 0), ('new.example', 'new', '10.0', 0),
                     ('new.example', 'root', '8.3', 1),
                     ('new.example', 'new', '8.3"\nmalicious: yes', 1), ('../escape', 'new', '8.3', 2)]
            for domain, user, php, expected in cases:
                with self.subTest(domain=domain, user=user):
                    result = subprocess.run([str(ROOT / 'scripts/site'), 'add', 'test', domain, user, php, '--dry-run'], env=env, capture_output=True)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual(path.read_text(), original)
            missing = subprocess.run([str(ROOT / 'scripts/site'), 'add', 'test',
                                      'new.example', 'new', '--dry-run'], env=env, capture_output=True)
            self.assertEqual(missing.returncode, 2)
            self.assertIn(b'add requires <user> <php-version>', missing.stderr)

    def test_confirmed_clone_passes_the_exact_target(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / 'test/sites.yml'
            path.parent.mkdir(parents=True)
            (path.parent / 'settings.yml').write_text(
                'all:\n  children:\n    wp:\n      hosts:\n        test:\n'
                '          secrets_dir: "{{ inventory_dir }}/secrets"\n')
            path.write_text('wp_sites:\n  a.example:\n    php_version: "8.4"\n  b.example:\n    php_version: "8.3"\n')
            binary = base / 'bin'
            binary.mkdir()
            log = base / 'arguments'
            command = binary / 'ansible-playbook'
            command.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_ARGUMENT_LOG"\n')
            command.chmod(0o755)
            env = {**os.environ, 'LEMP_INVENTORIES_ROOT':directory, 'TEST_ARGUMENT_LOG':str(log),
                   'PATH':str(binary) + ':' + os.environ['PATH']}
            result = subprocess.run([str(ROOT / 'scripts/site'), 'clone', 'test', 'a.example', 'b.example'],
                                    input='b.example\n', text=True, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('confirm=b.example', log.read_text().splitlines())
            self.assertIn('over b.example', result.stdout)

    def test_remove_without_backup_requires_explicit_flag_and_keeps_inventory_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            server = base / 'test'
            server.mkdir()
            (server / 'settings.yml').write_text(
                'all:\n  children:\n    wp:\n      hosts:\n        test:\n'
                '          secrets_dir: "{{ inventory_dir }}/secrets"\n')
            sites = server / 'sites.yml'
            sites.write_text('wp_sites:\n  first.example: {}\n  second.example: {}\n')
            binary = base / 'bin'
            binary.mkdir()
            log = base / 'arguments'
            command = binary / 'ansible-playbook'
            command.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_ARGUMENT_LOG"\nexit "${TEST_PLAY_EXIT:-0}"\n')
            command.chmod(0o755)
            env = {**os.environ, 'LEMP_INVENTORIES_ROOT': directory,
                   'TEST_ARGUMENT_LOG': str(log), 'PATH': str(binary) + ':' + os.environ['PATH']}
            invalid = subprocess.run([str(ROOT / 'scripts/site'), 'info', 'test', '--no-backup'],
                                     env=env, capture_output=True)
            self.assertEqual(invalid.returncode, 2)
            failed = subprocess.run([str(ROOT / 'scripts/site'), 'remove', 'test',
                                     'first.example', '--no-backup', '--yes'],
                                    env={**env, 'TEST_PLAY_EXIT': '1'}, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('first.example', yaml.safe_load(sites.read_text())['wp_sites'])
            removed = subprocess.run([str(ROOT / 'scripts/site'), 'remove', 'test',
                                      'first.example', '--no-backup'], input='first.example\n',
                                     text=True, env=env, capture_output=True)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertIn('without a backup', removed.stdout)
            self.assertIn('remove_without_backup=true', log.read_text().splitlines())
            self.assertEqual(list(yaml.safe_load(sites.read_text())['wp_sites']), ['second.example'])
