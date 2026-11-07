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
    def invoke(self, base, *args, runtime_file=None):
        account = pwd.getpwuid(os.getuid())
        runtime_uid = account.pw_uid + 100000
        real_getpwnam = pwd.getpwnam
        real_fstat = os.fstat
        def getpwnam(name):
            return types.SimpleNamespace(pw_uid=runtime_uid) if name == account.pw_name + '-php' else real_getpwnam(name)
        def fstat(fd):
            info = real_fstat(fd)
            if runtime_file is not None and os.readlink(f'/proc/self/fd/{fd}') == str(runtime_file):
                fields = list(info)
                fields[4] = runtime_uid
                return os.stat_result(fields)
            return info
        module = types.ModuleType('wp_hosting')
        module.site = lambda domain: {'base': base, 'SITE_USER': account.pw_name}
        with patch.dict(sys.modules, {'wp_hosting':module}), \
             patch.object(sys, 'argv', ['site-fix-perms', 'example.com', *args]), \
             patch.object(pwd, 'getpwnam', side_effect=getpwnam), \
             patch.object(os, 'fstat', side_effect=fstat), \
             contextlib.redirect_stdout(io.StringIO()):
            try:
                runpy.run_path(str(ROOT / 'server-tools/bin/site-fix-perms'), run_name='__main__')
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
            self.assertEqual(self.invoke(base), 1)
            self.assertEqual(outside.stat().st_mode & 0o7777, 0o600)

    def test_hard_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'htdocs').mkdir()
            outside = base / 'outside'
            outside.touch()
            os.link(outside, base / 'htdocs/link')
            self.assertIn('hard link', str(self.invoke(base)))

    def test_runtime_created_upload_is_accepted_but_code_and_world_write_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            upload = base / 'htdocs/wp-content/uploads/ewww/lazy/placeholder.png'
            upload.parent.mkdir(parents=True)
            upload.write_bytes(b'PNG')
            self.assertEqual(self.invoke(base), 0)
            upload.chmod(0o644)
            self.assertEqual(self.invoke(base, '--check', runtime_file=upload), 0)
            upload.chmod(0o666)
            self.assertEqual(self.invoke(base, '--check', runtime_file=upload), 1)
            code = base / 'htdocs/wp-content/plugins/example/main.php'
            code.parent.mkdir(parents=True)
            code.write_text('<?php')
            self.assertEqual(self.invoke(base, '--check', runtime_file=code), 1)
