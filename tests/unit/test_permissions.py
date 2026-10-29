import contextlib
import io
import os
from pathlib import Path
import pwd
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class PermissionTests(unittest.TestCase):
    def invoke(self, base, *args):
        account = pwd.getpwuid(os.getuid())
        module = types.ModuleType('wp_hosting')
        module.site = lambda domain: {'base': base, 'SITE_USER': account.pw_name}
        with patch.dict(sys.modules, {'wp_hosting':module}), \
             patch.object(sys, 'argv', ['site-fix-perms', 'example.com', *args]), \
             contextlib.redirect_stdout(io.StringIO()):
            try:
                runpy.run_path(str(ROOT / 'roles/site/files/site-fix-perms'), run_name='__main__')
            except SystemExit as exc:
                return exc.code

    def test_modes_are_idempotent_and_do_not_follow_links(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'htdocs/wp-content/uploads').mkdir(parents=True)
            (base / 'htdocs/index.php').write_text('<?php')
            self.assertEqual(self.invoke(base), 0)
            self.assertEqual(self.invoke(base, '--check'), 0)
            self.assertEqual((base / 'htdocs/index.php').stat().st_mode & 0o7777, 0o640)
            self.assertEqual((base / 'htdocs/wp-content/uploads').stat().st_mode & 0o7777, 0o2770)
            outside = base / 'outside'
            outside.write_text('private')
            outside.chmod(0o600)
            (base / 'htdocs/wp-content/uploads/leak').symlink_to(outside)
            with self.assertRaises(OSError):
                self.invoke(base)
            self.assertEqual(outside.stat().st_mode & 0o7777, 0o600)

    def test_hard_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'htdocs').mkdir()
            outside = base / 'outside'
            outside.touch()
            os.link(outside, base / 'htdocs/link')
            self.assertIn('hard link', str(self.invoke(base)))
