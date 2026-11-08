import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server-tools/lib'))
import lemp_help


class LempHelpTests(unittest.TestCase):
    def render(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(lemp_help.main(args), 0)
        return output.getvalue()

    def test_catalog_covers_all_installed_commands(self):
        names = {row[0] for rows in lemp_help.GROUPS.values() for row in rows}
        commands = {p.name for p in (ROOT / 'server-tools/bin').iterdir() if p.is_file()}
        self.assertTrue(commands <= names, commands - names)
        output = self.render([])
        for name in names:
            self.assertIn(name + ' — ', output)
        self.assertIn('Controller', output)
        self.assertIn('as root', output)
        self.assertIn('Site owner', output)
        self.assertNotIn('\033', output)

    def test_detail_alias_and_all(self):
        output = self.render(['wp'])
        self.assertIn('Raw WP-CLI updates bypass', output)
        self.assertNotIn('wp-monitor — ', output)
        self.assertIn('does not replace a live site', self.render(['--all']))

    def test_invalid_arguments_fail(self):
        for args in (['unknown'], ['wp', '--all']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    lemp_help.main(args)
                self.assertEqual(caught.exception.code, 2)

    def test_color_is_optional(self):
        with patch.object(lemp_help, 'supports_color', return_value=True):
            self.assertIn('\033[1;36m', self.render([]))
