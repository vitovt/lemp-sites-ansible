"""Translation installation is a postcondition, not an exit-code assumption."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('translation_checks', str(Path(__file__).resolve().parents[2] / 'server-tools/bin/wp-update'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class TranslationChecks(unittest.TestCase):
    def test_unknown_locale_suggests_only_confirmed_wordpress_mapping(self):
        for available in [['uk'], ['fr_FR']]:
            lines = []
            with patch.object(module, 'core_language', return_value='uk_UA'), \
                 patch.object(module, 'wp', return_value=SimpleNamespace(stdout=json.dumps([{'language': code} for code in available]))) as wp:
                with self.assertRaisesRegex(RuntimeError, 'WPLANG=uk_UA'):
                    module.update_core_translations({'domain': 'allourworld.info'}, 'uk_UA', lines)
            self.assertEqual(wp.call_count, 1)  # Never attempt installation.
            self.assertEqual(any('wp option update WPLANG uk' in line for line in lines), 'uk' in available)
            self.assertTrue(any('allourworld.info' in line for line in lines) if 'uk' in available else True)

    def test_successful_exit_does_not_prove_installation(self):
        for installed_results in [[1], [0, 1], [0, 0]]:
            installed = iter(installed_results)
            def wp(site, *args, **kwargs):
                if args[:3] == ('language', 'core', 'list'):
                    return SimpleNamespace(stdout='[{"language":"uk"}]', returncode=0)
                if args[:3] == ('language', 'core', 'is-installed'):
                    return SimpleNamespace(stdout='', returncode=next(installed))
                return SimpleNamespace(stdout='Success: Installed 0 of 1 languages (1 skipped).', returncode=0)
            with patch.object(module, 'core_language', return_value='uk'), patch.object(module, 'wp', side_effect=wp) as call:
                if 1 in installed_results:
                    with self.assertRaisesRegex(RuntimeError, 'not installed|missing'):
                        module.update_core_translations({}, 'uk', [])
                else:
                    module.update_core_translations({}, 'uk', [])
                self.assertFalse(any('--activate' in c.args for c in call.call_args_list))

    def test_wplang_change_fails_even_after_installation(self):
        with patch.object(module, 'core_language', return_value='uk'), \
             patch.object(module, 'wp', return_value=SimpleNamespace(stdout='', returncode=0)):
            with self.assertRaises(module.SiteLanguageChanged):
                module.update_core_translations({}, '', [])

    def test_invalid_or_empty_catalog_does_not_claim_unknown_locale(self):
        for catalog in ['[]', '{}', 'null', '[{}]']:
            with patch.object(module, 'core_language', return_value='uk'), \
                 patch.object(module, 'wp', return_value=SimpleNamespace(stdout=catalog)):
                with self.assertRaisesRegex(RuntimeError, 'Cannot validate'):
                    module.update_core_translations({}, 'uk', [])

    def test_core_success_and_translation_failure_are_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'conf').mkdir()
            args = SimpleNamespace(scope='core', policy='minor', skip_backup_check=True,
                automatic=False, no_maintenance=False, allow_dirty_target=False)
            lines = []
            with patch.object(module, 'git', return_value=SimpleNamespace(returncode=0)), \
                 patch.object(module, 'inventory', return_value=[('core', 'core', '6.5.4', '6.5.14', ['wp-includes', 'wp-content/languages/uk.mo'])]), \
                 patch.object(module, 'policy', return_value={}), patch.object(module, 'target_dirty', return_value=False), \
                 patch.object(module, 'file_state', return_value={}), patch.object(module, 'translation_paths', return_value=['wp-content/languages/uk.mo']), \
                 patch.object(module, 'prepare_core_package', return_value=('uk_UA', 'en_US')), \
                 patch.object(module, 'update_core_translations', side_effect=RuntimeError('Unknown locale uk_UA')), \
                 patch.object(module, 'wp', return_value=SimpleNamespace(stdout='6.5.14', returncode=0)), \
                 patch.object(module, 'command', return_value=SimpleNamespace(stdout='200')), \
                 patch.object(module, 'commit_paths', return_value='CORE_COMMIT') as commit, \
                 patch.object(module, 'report', side_effect=lambda s, report: lines.extend(report)), \
                 patch.object(module.subprocess, 'run'):
                self.assertTrue(module.apply({'base': base, 'domain': 'allourworld.info'}, args))
            self.assertEqual(commit.call_args.args[1], ['wp-includes'])
            self.assertTrue(any('PASS core:core' in line and 'CORE_COMMIT' in line for line in lines))
            self.assertTrue(any('FAIL translations:core' in line for line in lines))
            self.assertIn('wp-update translations allourworld.info', '\n'.join(lines))
            self.assertEqual(lines[-1], 'FAIL')
            self.assertFalse((base / 'conf/maintenance').exists())

    def test_failed_repair_never_commits_or_removes_existing_gate(self):
        for existing in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / 'conf').mkdir()
                marker = base / 'conf/maintenance'
                if existing:
                    marker.write_text('operator gate')
                with patch.object(module, 'git'), patch.object(module, 'core_language', return_value='uk'), \
                     patch.object(module, 'translation_paths', return_value=[]), \
                     patch.object(module, 'file_state', return_value={}), patch.object(module, 'wp'), \
                     patch.object(module, 'update_core_translations', side_effect=RuntimeError('translation missing')) as repair, \
                     patch.object(module, 'commit_paths') as commit, patch.object(module, 'report'):
                    self.assertTrue(module.translations_only({'base': base, 'domain': 'allourworld.info'}, SimpleNamespace(skip_backup_check=True)))
                commit.assert_not_called()
                self.assertEqual(marker.exists(), existing)
                if existing:
                    repair.assert_not_called()
                    self.assertEqual(marker.read_text(), 'operator gate')

    def test_standalone_repair_does_not_update_core_and_commits_only_translations(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'conf').mkdir()
            args = SimpleNamespace(skip_backup_check=True)
            with patch.object(module, 'git'), patch.object(module, 'core_language', return_value='uk'), \
                 patch.object(module, 'translation_paths', return_value=['wp-content/languages/uk.mo']), \
                 patch.object(module, 'target_dirty', return_value=False), patch.object(module, 'file_state', return_value={}), \
                 patch.object(module, 'update_core_translations') as translations, \
                 patch.object(module, 'wp') as wp, \
                 patch.object(module, 'command', return_value=SimpleNamespace(stdout='200')), \
                 patch.object(module, 'commit_paths', return_value='TRANSLATION_COMMIT') as commit, \
                 patch.object(module, 'report'):
                self.assertFalse(module.translations_only({'base': base, 'domain': 'allourworld.info'}, args))
            self.assertEqual([call.args[1:] for call in wp.call_args_list], [('core', 'verify-checksums'), ('core', 'verify-checksums')])
            translations.assert_called_once()
            self.assertEqual(commit.call_args.args[1], ['wp-content/languages/uk.mo'])
            self.assertFalse((base / 'conf/maintenance').exists())
