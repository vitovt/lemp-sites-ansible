"""Core package locale selection must not change site language."""
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('locale_updater', str(Path(__file__).resolve().parents[2] / 'server-tools/bin/wp-update'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class CoreLocaleTests(unittest.TestCase):
    def test_diagnostics_preserve_public_release_urls_but_not_credentials(self):
        url = 'https://uk.wordpress.org/wordpress-6.5.14-uk_UA.zip'
        self.assertEqual(module.redact_diagnostic(url), url)
        for private in ['https://user:password@wordpress.org/file.zip',
                        'https://wordpress.org/file.zip?token=private',
                        'https://wordpress.org.evil.example/file.zip']:
            self.assertEqual(module.redact_diagnostic(private), '[REDACTED_URL]')

    def test_apply_uses_package_locale_and_never_activates_site_language(self):
        for language_after in ('uk', 'en_US'):
            with self.subTest(language_after=language_after), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / 'conf').mkdir()
                args = SimpleNamespace(scope='core', policy='minor', skip_backup_check=True,
                    automatic=False, no_maintenance=False, allow_dirty_target=False)
                def wp_result(*args, **kwargs):
                    if args[1:4] == ('language', 'core', 'list'):
                        return SimpleNamespace(stdout='[{"language":"uk"}]', returncode=0)
                    return SimpleNamespace(stdout='6.5.14', returncode=0)
                with patch.object(module, 'inventory', return_value=[('core', 'core', '6.5.4', '6.5.14', ['wp-includes'])]), \
                     patch.object(module, 'policy', return_value={}), \
                     patch.object(module, 'git', return_value=SimpleNamespace(returncode=0)), \
                     patch.object(module, 'target_dirty', return_value=False), \
                     patch.object(module, 'file_state', return_value={}), \
                     patch.object(module, 'prepare_core_package', return_value=('uk', 'en_US')), \
                     patch.object(module, 'core_language', return_value=language_after), \
                     patch.object(module, 'wp', side_effect=wp_result) as wp, \
                     patch.object(module, 'command', return_value=SimpleNamespace(stdout='200')), \
                     patch.object(module, 'translation_paths', return_value=[]), \
                     patch.object(module, 'commit_paths', return_value='commit') as commit, \
                     patch.object(module, 'report'), patch.object(module.subprocess, 'run'):
                    site = {'base': base, 'domain': 'allourworld.info'}
                    self.assertEqual(module.apply(site, args), language_after != 'uk')
                wp.assert_any_call(site, 'core', 'update', '--version=6.5.14', '--locale=en_US')
                wp.assert_any_call(site, 'core', 'verify-checksums', '--version=6.5.14', '--locale=en_US')
                wp.assert_any_call(site, 'language', 'core', 'install', 'uk')
                self.assertFalse(any('--activate' in call.args for call in wp.call_args_list))
                self.assertEqual(commit.called, language_after == 'uk')
                self.assertFalse((base / 'conf/maintenance').exists())

    def test_localized_package_is_preferred(self):
        lines = []
        with patch.object(module, 'core_language', return_value='uk_UA'), \
             patch.object(module, 'probe_core_package', return_value={'status': 200}) as probe:
            self.assertEqual(module.prepare_core_package({}, '6.5.14', lines), ('uk_UA', 'uk_UA'))
        probe.assert_called_once_with({}, 'https://uk.wordpress.org/wordpress-6.5.14-uk_UA.zip')
        self.assertIn('locale=uk_UA', lines[-1])

    def test_missing_translation_falls_back_to_same_version(self):
        for status in (404, 410):
            lines = []
            with patch.object(module, 'core_language', return_value='uk_UA'), \
                 patch.object(module, 'probe_core_package', side_effect=[{'status': status}, {'status': 200}]) as probe:
                self.assertEqual(module.prepare_core_package({}, '6.5.14', lines), ('uk_UA', 'en_US'))
            self.assertEqual(probe.call_args.args[1], 'https://wordpress.org/wordpress-6.5.14.zip')
            self.assertIn(f'HTTP={status}', lines[0])
            self.assertIn('WPLANG remains uk_UA', lines[1])

    def test_network_tls_and_server_errors_do_not_trigger_fallback(self):
        for status in (0, 403, 405, 429, 500, 503):
            with patch.object(module, 'core_language', return_value='uk_UA'), \
                 patch.object(module, 'probe_core_package', return_value={'status': status, 'error': 'unavailable'}) as probe:
                with self.assertRaisesRegex(RuntimeError, f'HTTP={status}'):
                    module.prepare_core_package({}, '6.5.14', [])
            self.assertEqual(probe.call_count, 1)

    def test_unavailable_fallback_stops_before_update(self):
        with patch.object(module, 'core_language', return_value='uk_UA'), \
             patch.object(module, 'probe_core_package', side_effect=[{'status': 404}, {'status': 503}]):
            with self.assertRaisesRegex(RuntimeError, 'Fallback core package unavailable: HTTP=503'):
                module.prepare_core_package({}, '6.5.14', [])

    def test_empty_site_language_means_en_us_package(self):
        with patch.object(module, 'core_language', return_value=''), \
             patch.object(module, 'probe_core_package', return_value={'status': 200}):
            self.assertEqual(module.prepare_core_package({}, '6.5.14', []), ('', 'en_US'))

    def test_language_and_version_inputs_are_validated(self):
        for value in ['"../../outside"', 'false', '42']:
            with patch.object(module, 'wp', return_value=SimpleNamespace(stdout=value)):
                with self.assertRaises(RuntimeError):
                    module.core_language({})
        with self.assertRaises(RuntimeError):
            module.core_package_url('../../file', 'en_US')
        self.assertEqual(module.core_package_url('6.7.0', 'en_US'), 'https://wordpress.org/wordpress-6.7.zip')
