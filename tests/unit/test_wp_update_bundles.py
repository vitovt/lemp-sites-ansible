"""Core-update policy tests. PHP fixtures use only disposable local directories."""
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader('bundled_updater', str(ROOT / 'server-tools/bin/wp-update'))
module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(module)


class CommandTests(unittest.TestCase):
    def test_core_update_sets_process_local_policy(self):
        site = {'base': Path('/srv/sites/fixture.example'), 'SITE_USER': 'fixture', 'PHP_VERSION': '8.3'}
        for options in ((), ('--minor',)):
            with self.subTest(options=options), patch.object(module, 'command', return_value=SimpleNamespace(returncode=0)) as command:
                module.wp(site, 'core', 'update', *options)
                args = command.call_args.args[0]
                self.assertEqual(args[:4], ['runuser', '-u', 'fixture', '--'])
                self.assertIn('php8.3', args)
                self.assertIn('--exec=' + module.CORE_UPDATE_EXEC, args)
                if options:
                    self.assertIn('--minor', args)
                self.assertNotIn('--skip-content', args)

    def test_theme_plugin_and_other_core_commands_remain_independent(self):
        site = {'base': Path('/srv/sites/fixture.example'), 'SITE_USER': 'fixture', 'PHP_VERSION': '8.3'}
        for args in (('theme', 'update', 'allium'), ('plugin', 'update', 'akismet'),
                     ('core', 'version'), ('core', 'update-db'), ('core', 'verify-checksums')):
            with self.subTest(args=args), patch.object(module, 'command') as command:
                module.wp(site, *args)
                self.assertFalse(any(str(x).startswith('--exec=') for x in command.call_args.args[0]))


@unittest.skipUnless(shutil.which('php'), 'PHP CLI is required for the executable policy fixture')
class PHPPolicyTests(unittest.TestCase):
    def fixture(self, predefined=''):
        # Model the documented WordPress bundle gate; this is not a live core upgrade.
        code = '''class WP_CLI {
            public static function error($message) { fwrite(STDERR, $message); exit(1); }
        }
        ''' + predefined + module.CORE_UPDATE_EXEC + '''
        file_put_contents('version.txt', 'new core');
        if (!defined('CORE_UPGRADE_SKIP_NEW_BUNDLED') || !CORE_UPGRADE_SKIP_NEW_BUNDLED) {
            mkdir('wp-content/themes/new-default', 0750, true);
            file_put_contents('wp-content/themes/new-default/functions.php', '<?php // bundled');
            mkdir('wp-content/languages/themes', 0750, true);
            file_put_contents('wp-content/languages/themes/new-default-ru_RU.po', 'new translation');
        }
        '''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            theme = root / 'wp-content/themes/existing/style.css'
            theme.parent.mkdir(parents=True)
            theme.write_text('local theme')
            config = root / 'wp-config.php'
            config.write_text('<?php // local config')
            result = subprocess.run(['php', '-r', code], cwd=root, capture_output=True, text=True)
            self.assertEqual(theme.read_text(), 'local theme')
            self.assertEqual(config.read_text(), '<?php // local config')
            self.assertFalse((root / 'wp-content/themes/new-default').exists())
            self.assertFalse((root / 'wp-content/languages/themes/new-default-ru_RU.po').exists())
            return result, (root / 'version.txt').exists()

    def test_unset_policy_blocks_bundles_but_updates_core(self):
        result, updated = self.fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(updated)

    def test_existing_true_policy_is_supported_without_redefinition(self):
        result, updated = self.fixture("define('CORE_UPGRADE_SKIP_NEW_BUNDLED', true);")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertTrue(updated)

    def test_conflicting_policy_fails_before_update(self):
        result, updated = self.fixture("define('CORE_UPGRADE_SKIP_NEW_BUNDLED', false);")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CORE_UPGRADE_SKIP_NEW_BUNDLED=true', result.stderr)
        self.assertFalse(updated)
