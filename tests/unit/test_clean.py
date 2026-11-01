"""Check that controller cleanup keeps user data unless explicitly confirmed."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / 'clean.sh'


class CleanScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        shutil.copy2(SOURCE, self.root / 'clean.sh')
        self.outside = Path(self.tmp.name) / 'outside.txt'
        self.outside.write_text('keep')

        for name in ('.venv', 'collections', 'galaxy_roles', '.ansible', '.pytest_cache'):
            path = self.root / name
            path.mkdir()
            (path / 'generated').write_text('cache')
        cache = self.root / 'scripts' / '__pycache__'
        cache.mkdir(parents=True)
        (cache / 'module.pyc').write_text('bytecode')
        (self.root / 'scripts' / 'loose.pyc').write_text('bytecode')
        (self.root / 'scripts' / 'external').symlink_to(self.outside)
        (self.root / 'test-results').mkdir()
        (self.root / 'test-results' / 'report.txt').write_text('keep')

        self.example = self.root / 'inventories' / 'example-server'
        self.example.mkdir(parents=True)
        (self.example / 'sites.yml').write_text('example template')
        (self.example / 'secrets').mkdir()
        (self.example / 'secrets' / 'token.example').write_text('template')
        (self.example / 'secrets' / 'token').write_text('private token')
        self.private = self.root / 'inventories' / 'server-one'
        (self.private / 'secrets').mkdir(parents=True)
        (self.private / 'settings.yml').write_text('private config')
        (self.private / 'secrets' / 'password').write_text('private secret')

    def run_clean(self, *args, input_text=None):
        return subprocess.run(
            ['bash', str(self.root / 'clean.sh'), *args],
            input=input_text, text=True, capture_output=True, check=False,
        )

    def test_default_removes_generated_files_and_keeps_user_data(self):
        result = self.run_clean()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ('.venv', 'collections', 'galaxy_roles', '.ansible', '.pytest_cache'):
            self.assertFalse((self.root / name).exists())
        self.assertFalse((self.root / 'scripts' / '__pycache__').exists())
        self.assertFalse((self.root / 'scripts' / 'loose.pyc').exists())
        self.assertTrue((self.private / 'secrets' / 'password').exists())
        self.assertTrue((self.example / 'sites.yml').exists())
        self.assertTrue((self.example / 'secrets' / 'token').exists())
        self.assertTrue((self.root / 'test-results' / 'report.txt').exists())
        self.assertEqual(self.outside.read_text(), 'keep')
        self.assertEqual(self.run_clean().returncode, 0)

    def test_declined_inventory_clear_changes_nothing(self):
        result = self.run_clean('--clear-inventories', input_text='n\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('[y/N]', result.stderr)
        self.assertTrue((self.private / 'settings.yml').exists())
        self.assertTrue((self.root / '.venv').exists())

    def test_confirmed_inventory_clear_keeps_example(self):
        result = self.run_clean('--clear-inventories', input_text='y\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.private.exists())
        self.assertTrue((self.example / 'sites.yml').exists())
        self.assertTrue((self.example / 'secrets' / 'token.example').exists())
        self.assertFalse((self.example / 'secrets' / 'token').exists())
        self.assertFalse((self.root / '.venv').exists())

    def test_force_requires_clear_and_removes_without_prompt(self):
        rejected = self.run_clean('--force-danger-clear-inventories')
        self.assertEqual(rejected.returncode, 2)
        self.assertTrue(self.private.exists())
        self.assertTrue((self.root / '.venv').exists())
        forced = self.run_clean(
            '--clear-inventories', '--force-danger-clear-inventories'
        )
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertFalse(self.private.exists())
        self.assertTrue((self.example / 'sites.yml').exists())


if __name__ == '__main__':
    unittest.main()
