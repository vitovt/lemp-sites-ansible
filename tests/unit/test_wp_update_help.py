"""Help must work without root, site facts, or external commands."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'server-tools/bin/wp-update'
loader = importlib.machinery.SourceFileLoader('update_help_test', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
updater = importlib.util.module_from_spec(spec)
loader.exec_module(updater)


class HelpTests(unittest.TestCase):
    def render(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                patch.object(updater, 'library', side_effect=AssertionError('must not load sites')), \
                patch.object(updater.os, 'geteuid', return_value=1000):
            self.assertEqual(updater.main(args), 0)
        return output.getvalue()

    def test_each_help_position_works_without_privileges(self):
        for args in (['--help'], ['-h'], ['plan', '--help'],
                     ['apply', '--help'], ['run', '--help'],
                     ['apply', 'example.com', '--help']):
            with self.subTest(args=args):
                output = self.render(args)
                self.assertIn('Usage:', output)
                self.assertIn('Examples:', output)
                self.assertNotIn('\033', output)

    def test_scope_policy_and_defaults_are_explicit(self):
        output = self.render(['apply', '--help'])
        for value in ('plugin:SLUG', 'theme:SLUG', 'core', 'all', '5.9.19 -> 5.9.20',
                      '3.9 -> 3.10', 'default policy is latest', '--allow-dirty-target'):
            self.assertIn(value, output)
        self.assertIn('Defaults: --scope all --policy latest', self.render(['plan', '--help']))
        run = self.render(['run', '--help'])
        self.assertIn('Require clean Git', run)
        self.assertIn('overridden by all/minor', run)
        self.assertNotIn('--allow-dirty-target', run)

    def test_invalid_scope_has_separate_blocks_and_code_two(self):
        with contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as caught:
                updater.main(['apply', 'example.com', '--scope', 'invalid'])
        self.assertEqual(caught.exception.code, 2)
        text = output.getvalue()
        for block in ('ERROR\n', '\n\nVALID VALUES\n', '\n\nHINT\n', '\n\nEXAMPLE\n'):
            self.assertIn(block, text)
        self.assertIn('plugin:SLUG', text)
        self.assertNotIn('\033', text)

    def test_color_obeys_terminal_and_opt_out(self):
        for terminal, env, expected in (
                (True, {'TERM': 'xterm'}, True),
                (False, {'TERM': 'xterm'}, False),
                (True, {'TERM': 'dumb'}, False),
                (True, {'TERM': 'xterm', 'NO_COLOR': ''}, False)):
            with self.subTest(terminal=terminal, env=env), patch.dict(os.environ, env, clear=True):
                stream = io.StringIO()
                with patch.object(stream, 'isatty', return_value=terminal):
                    self.assertEqual('\033' in updater.terminal_text('ERROR', '31', stream), expected)
                    with contextlib.redirect_stdout(stream):
                        updater.print_help_text('Scope:\n  core')
                    self.assertEqual('\033' in stream.getvalue(), expected)

    def test_status_colors_do_not_enter_logs(self):
        for line, color in [('FAIL problem', '31'), ('HINT inspect', '33'), ('PASS done', '32')]:
            with patch.dict(os.environ, {'TERM': 'xterm'}, clear=True):
                output = io.StringIO()
                with patch.object(output, 'isatty', return_value=True):
                    updater.print_status(line, output)
                self.assertIn('\033[' + color + 'm', output.getvalue())
                plain = io.StringIO()
                updater.print_status(line, plain)
                self.assertEqual(plain.getvalue(), line + '\n')


class BulkScopeTests(unittest.TestCase):
    def test_bulk_scope_reads_only_selected_component_type(self):
        from types import SimpleNamespace
        import json
        entries = [
            {'name': 'held', 'version': '1.0', 'update_version': '1.1', 'update': 'available'},
            {'name': 'ready', 'version': '1.0', 'update_version': '1.1', 'update': 'available'},
            {'name': 'major', 'version': '1.0', 'update_version': '2.0', 'update': 'available'},
        ]
        for scope, kind in [('plugins', 'plugin'), ('themes', 'theme')]:
            for policy, expected in [('minor', ['ready']), ('latest', ['ready', 'major'])]:
                with self.subTest(scope=scope, policy=policy), \
                        patch.object(updater, 'wp', return_value=SimpleNamespace(stdout=json.dumps(entries))) as wp, \
                        patch.object(updater, 'translation_paths', return_value=[], create=True):
                    log = []
                    selected = updater.inventory({}, scope, policy, {scope: {'held': 'Local fix'}}, log)
                    wp.assert_called_once_with({}, kind, 'list', '--format=json')
                    self.assertEqual([row[1] for row in selected], expected)
                    self.assertTrue(all(row[0] == kind for row in selected))
                    self.assertIn('held: Local fix', log[0])

    def test_plan_and_apply_accept_bulk_scope(self):
        for action in ('plan', 'apply'):
            for scope in ('plugins', 'themes'):
                with self.subTest(action=action, scope=scope), \
                        patch.object(updater.os, 'geteuid', return_value=0), \
                        patch.object(updater, 'library', return_value=(lambda domain: {'domain': domain}, contextlib.nullcontext)), \
                        patch.object(updater, 'apply', return_value=False) as apply, \
                        patch.object(updater, 'backup_today', return_value=('ID', 'today')), \
                        patch.object(updater, 'changed', return_value=set()), \
                        patch.object(updater, 'policy', return_value={}), \
                        patch.object(updater, 'inventory', return_value=[]) as inventory, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(updater.main([action, 'example.com', '--scope', scope, '--policy', 'minor']), 0)
                    if action == 'apply':
                        args = apply.call_args.args[1]
                        self.assertEqual((args.scope, args.policy), (scope, 'minor'))
                    else:
                        self.assertEqual(inventory.call_args.args[1:3], (scope, 'minor'))

    def test_bulk_apply_requires_explicit_policy(self):
        for scope in ('plugins', 'themes'):
            with self.subTest(scope=scope), contextlib.redirect_stderr(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as caught:
                    updater.main(['apply', 'example.com', '--scope', scope])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn('require --policy', output.getvalue())
