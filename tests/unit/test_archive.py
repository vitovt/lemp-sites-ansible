"""Exercise portable encrypted exports without reading real inventories."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


SOURCE = Path(__file__).resolve().parents[2]
PASSWORD = 'test-password-only-123'


@unittest.skipUnless(shutil.which('7z'), 'The 7zip package is required')
class ArchiveScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'lemp-sites-ansible'
        self.root.mkdir()
        for name in ('archive.sh', 'clean.sh'):
            shutil.copy2(SOURCE / name, self.root / name)

        self.server = self.root / 'inventories' / 'demo-server'
        self.secret_dir = self.server / 'secrets'
        self.secret_dir.mkdir(parents=True)
        self.secret_dir.chmod(0o700)
        (self.server / 'sites.yml').write_text('wp_sites: {}\n')
        secret = self.secret_dir / 'restic.pass'
        secret.write_text('test secret')
        secret.chmod(0o600)
        self.example = self.root / 'inventories' / 'example-server'
        self.example.mkdir()
        (self.example / 'settings.yml').write_text(
            'all:\n  children:\n    wp:\n      hosts:\n        example-server: {}\n'
        )
        self.key = self.root / 'controller-key'
        self.key.write_text('dummy private key')
        self.key.chmod(0o600)
        self.write_settings('controller-key')
        (self.root / 'secrets').mkdir()
        (self.root / 'secrets' / 'legacy.pass').write_text('legacy secret')
        (self.root / '.pytest_cache').mkdir()
        (self.root / '.pytest_cache' / 'cache').write_text('generated')

    def write_settings(self, key=None):
        setting = f'          ansible_ssh_private_key_file: {key}\n' if key else ''
        (self.server / 'settings.yml').write_text(
            'all:\n  children:\n    wp:\n      hosts:\n        demo-server:\n'
            f'{setting}          secrets_dir: "{{{{ inventory_dir }}}}/secrets"\n'
        )

    def archive(self, *arguments, input_text=None, env=None):
        return subprocess.run(
            ['bash', str(self.root / 'archive.sh'), *arguments],
            input=input_text, text=True, capture_output=True, check=False,
            env=env,
        )

    def archive_path(self):
        paths = list(self.root.glob('lemp-sites-ansible-*.zip'))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def test_archive_restores_credentials_and_key_at_portable_paths(self):
        result = self.archive('--password', PASSWORD)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        archive = self.archive_path()
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        listing = subprocess.run(
            ['7z', 'l', '-slt', str(archive)],
            text=True, capture_output=True, check=False,
        )
        self.assertIn('AES-256', listing.stdout)
        restored = Path(self.tmp.name) / 'restored'
        restored.mkdir()
        extracted = subprocess.run(
            ['7z', 'x', f'-o{restored}', str(archive)], input=PASSWORD + '\n',
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(extracted.returncode, 0, extracted.stderr + extracted.stdout)
        self.assertFalse((restored / 'inventories/example-server').exists())
        settings = yaml.safe_load((restored / 'inventories/demo-server/settings.yml').read_text())
        key_path = settings['all']['children']['wp']['hosts']['demo-server'][
            'ansible_ssh_private_key_file'
        ]
        self.assertEqual(key_path, 'inventories/demo-server/secrets/ssh/controller.key')
        self.assertEqual((restored / key_path).read_text(), 'dummy private key')
        self.assertEqual((restored / key_path).stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            (restored / 'inventories/demo-server/secrets/restic.pass').read_text(),
            'test secret',
        )
        self.assertEqual((restored / 'secrets/legacy.pass').read_text(), 'legacy secret')
        self.assertFalse((restored / '.pytest_cache').exists())
        self.assertEqual((self.server / 'settings.yml').read_text().count('controller-key'), 1)
        self.assertTrue(self.key.exists())

    def test_missing_key_setting_is_intentionally_skipped(self):
        self.write_settings()
        result = self.archive('--password', PASSWORD)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        listing = subprocess.run(
            ['7z', 'l', str(self.archive_path())],
            text=True, capture_output=True, check=False,
        )
        self.assertNotIn('controller.key', listing.stdout)

    def test_interactive_password_prompt(self):
        result = self.archive(input_text=f'{PASSWORD}\n{PASSWORD}\n')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('Confirm password:', result.stderr)
        self.assertTrue(self.archive_path().exists())

    def test_deletion_runs_only_after_verified_archive_and_confirmation(self):
        result = self.archive('--password', PASSWORD, '--delete-after-archive', input_text='y\n')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(self.archive_path().exists())
        self.assertFalse(self.server.exists())
        self.assertTrue(self.example.exists())
        self.assertFalse(self.key.exists())
        self.assertFalse((self.root / 'secrets').exists())
        self.assertFalse((self.root / '.pytest_cache').exists())

    def test_declined_deletion_preserves_sources(self):
        result = self.archive('--password', PASSWORD, '--delete-after-archive', input_text='n\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.archive_path().exists())
        self.assertTrue(self.server.exists())
        self.assertTrue(self.key.exists())
        self.assertTrue((self.root / 'secrets').exists())

    def test_failed_integrity_check_never_deletes_sources(self):
        tools = Path(self.tmp.name) / 'tools'
        tools.mkdir()
        fake = tools / '7z'
        fake.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = t ]; then exit 2; fi\n'
            f'exec {shutil.which("7z")} "$@"\n'
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env['PATH'] = f'{tools}:{env["PATH"]}'
        result = self.archive(
            '--password', PASSWORD, '--delete-after-archive', input_text='y\n',
            env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list(self.root.glob('*.zip')))
        self.assertFalse(list(self.root.glob('.*.partial')))
        self.assertTrue(self.server.exists())
        self.assertTrue(self.key.exists())

    def test_linked_inventory_content_is_rejected(self):
        (self.secret_dir / 'linked.pass').symlink_to(self.key)
        result = self.archive('--password', PASSWORD)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refuse a symlink', result.stderr)
        self.assertFalse(list(self.root.glob('*.zip')))


if __name__ == '__main__':
    unittest.main()
