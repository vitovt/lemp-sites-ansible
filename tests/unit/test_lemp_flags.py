"""The controller forwards explicit check/diff flags without changing server scope."""
import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def runner():
    loader = importlib.machinery.SourceFileLoader('lemp_flags', str(ROOT / 'scripts/lemp'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class LempFlagTests(unittest.TestCase):
    def test_flags_are_optional_and_forwarded_independently(self):
        module = runner()
        inventory = ROOT / 'inventories/my-server/settings.yml'
        for flags in ([], ['--check'], ['--diff'], ['--check', '--diff'], ['--step'],
                      ['--list-tasks'], ['--list-hosts'], ['--check', '--diff', '--step', '--list-tasks', '--list-hosts']):
            with self.subTest(flags=flags), patch.object(sys, 'argv', ['lemp', 'my-server', 'site', *flags]), \
                 patch.object(module, 'inspect', return_value=inventory), patch.object(module.subprocess, 'call', return_value=7) as call:
                self.assertEqual(module.main(), 7)
            self.assertEqual(call.call_args.args[0], [
                'ansible-playbook', '-i', str(inventory), 'playbooks/site.yml', '--limit', 'my-server', *flags])
            self.assertEqual(call.call_args.kwargs['cwd'], ROOT)

    def test_site_selection_is_preserved_with_both_flags(self):
        module = runner()
        with patch.object(sys, 'argv', ['lemp', 'my-server', 'site-apply', 'example.com', '--diff', '--check']), \
             patch.object(module, 'inspect', return_value=Path('/fixture/settings.yml')), \
             patch.object(module.subprocess, 'call', return_value=0) as call:
            module.main()
        self.assertEqual(call.call_args.args[0][-4:], ['-e', 'wp_site_only=example.com', '--check', '--diff'])
        self.assertIn('playbooks/site-apply.yml', call.call_args.args[0])

    def test_verbosity_levels_are_forwarded(self):
        module = runner()
        for level in range(1, 5):
            flag = '-' + 'v' * level
            with self.subTest(flag=flag), patch.object(sys, 'argv', ['lemp', 'my-server', 'site', flag]), \
                 patch.object(module, 'inspect', return_value=Path('/fixture/settings.yml')), \
                 patch.object(module.subprocess, 'call', return_value=0) as call:
                module.main()
            self.assertEqual(call.call_args.args[0][-1], flag)

    def test_options_can_precede_the_site_domain(self):
        module = runner()
        with patch.object(sys, 'argv', ['lemp', '--check', 'my-server', 'site-apply', '-v', 'example.com', '-v', '--diff']), \
             patch.object(module, 'inspect', return_value=Path('/fixture/settings.yml')), \
             patch.object(module.subprocess, 'call', return_value=0) as call:
            module.main()
        self.assertEqual(call.call_args.args[0][-5:], ['-e', 'wp_site_only=example.com', '--check', '--diff', '-vv'])

    def test_help_documents_flags_and_examples(self):
        module = runner()
        with patch.object(sys, 'argv', ['lemp', '--help']), contextlib.redirect_stdout(io.StringIO()) as output, \
             patch.object(module.subprocess, 'call') as call:
            with self.assertRaises(SystemExit) as exit_status:
                module.main()
        self.assertEqual(exit_status.exception.code, 0)
        self.assertIn('site --check --diff', output.getvalue())
        self.assertIn('check_mode: false', output.getvalue())
        for option in ('-vvvv', '--step', '--list-tasks', '--list-hosts'):
            self.assertIn(option, output.getvalue())
        call.assert_not_called()

    def test_other_ansible_options_remain_rejected(self):
        module = runner()
        with patch.object(sys, 'argv', ['lemp', 'my-server', 'site', '--limit', 'other-server']), \
             contextlib.redirect_stderr(io.StringIO()), patch.object(module.subprocess, 'call') as call:
            with self.assertRaises(SystemExit):
                module.main()
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
