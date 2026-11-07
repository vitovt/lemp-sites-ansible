"""Guidance must not change commands, safety gates, or exit codes."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server-tools/lib'))
import cli_diagnostics as diag


class DiagnosticTests(unittest.TestCase):
    def test_every_manual_tool_has_specific_advice(self):
        tools = ('lemp', 'site', 'wp-backup', 'wp-backup-manager', 'wp-monitor',
                 'jump-in', 'site-shell', 'site-wp', 'site-php', 'site-check',
                 'site-policy', 'jump-in-help', 'wp-site', 'site-fix-perms',
                 'wp-git-commit', 'tg-alert', 'valkey-acl-sync')
        for tool in tools:
            with self.subTest(tool=tool), contextlib.redirect_stderr(io.StringIO()) as output:
                diag.hint(tool, 'unsafe repository lock')
                self.assertIn('HINT ', output.getvalue())
                self.assertIn('Do not delete a lock', output.getvalue())
                self.assertNotIn('Check command help, permissions', output.getvalue())

    def test_child_exit_status_is_preserved_and_stdout_stays_clean(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(diag.run_cli('site-check', lambda: 7), 7)
        self.assertIn('exit code 7', errors.getvalue())
        self.assertIn('HINT ', errors.getvalue())
        self.assertEqual(output.getvalue(), '')

    def test_success_and_cancellation_have_no_failure_hint(self):
        for status in (0, None, 130):
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(diag.run_cli('site-check', lambda: status), status)
            self.assertEqual(output.getvalue(), '')
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(diag.run_cli('site-check', lambda: (_ for _ in ()).throw(KeyboardInterrupt())), 130)
        self.assertNotIn('HINT', output.getvalue())

    def test_argparse_exit_codes_are_preserved(self):
        with contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as caught:
                diag.run_cli('lemp', lambda: (_ for _ in ()).throw(SystemExit(2)))
        self.assertEqual(caught.exception.code, 2)
        self.assertIn('scripts/lemp --help', output.getvalue())

    def test_failed_subprocess_does_not_disclose_argv_or_output(self):
        exc = subprocess.CalledProcessError(1, ['client', '--password', 'SENTINEL'], output='PRIVATE')
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(diag.run_cli('tg-alert', lambda: (_ for _ in ()).throw(exc)), 1)
        self.assertNotIn('SENTINEL', output.getvalue())
        self.assertNotIn('PRIVATE', output.getvalue())
        self.assertIn('HINT', output.getvalue())

    def test_usage_errors_keep_the_command_failure_code(self):
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(diag.run_cli('wp-site', lambda: (_ for _ in ()).throw(ValueError('Unknown site')), failure_code=2), 2)
        self.assertIn('Unknown site', output.getvalue())
