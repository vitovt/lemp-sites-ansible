"""Offline release-note fixtures. No production services or network access."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server-tools/lib'))
import wp_changelog as notes
loader = importlib.machinery.SourceFileLoader('changelog_updater', str(ROOT / 'server-tools/bin/wp-update'))
updater = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(updater)
HTML = '''<h4>Unreleased</h4><p>Do not include.</p>
<h4>1.11</h4><p>Future release.</p>
<h4>Version 1.10 - 2026-01-10</h4><ul><li>Fix &amp; improve.</li><li>Second change.</li></ul>
<h4>v1.9</h4><p>Older fix. <a href="https://example.com/issue">Issue</a></p>
<h4>1.8</h4><p>Baseline excluded.</p>'''
ENTRY = ('plugin', 'fixture-plugin', '1.8', '1.10')


class ParserTests(unittest.TestCase):
    def test_numeric_range_excludes_old_and_future(self):
        selected, boundaries = notes.parse_notes(HTML, '1.8', '1.10')
        self.assertEqual([x[0] for x in selected], ['1.10', '1.9'])
        self.assertEqual(boundaries, {'old', 'new'})
        self.assertIn('Fix & improve.', selected[0][1][0])
        self.assertIn('https://example.com/issue', ' '.join(selected[1][1]))

    def test_scripts_controls_and_unversioned_sections_are_excluded(self):
        html = '<h4>2</h4><p>Good\x1b[31m\u202eevil</p><script>BAD</script><style>BAD</style><h4>Other notes</h4><p>UNRELATED</p>'
        selected, _ = notes.parse_notes(html, '1', '2')
        text = str(selected)
        for forbidden in ('BAD', 'UNRELATED', '\x1b', '\u202e'):
            self.assertNotIn(forbidden, text)

    def test_non_numeric_versions_fail_without_guessing(self):
        for old, new in [('1-beta', '2'), ('2', '1'), ('1.0', '1')]:
            with self.subTest(old=old, new=new), self.assertRaises(ValueError):
                notes.parse_notes(HTML, old, new)

    def test_full_brief_and_none(self):
        html = ''.join(f'<h4>1.{i}</h4><p>Release note {i}</p>' for i in range(13))
        with patch.object(notes, 'fetch_plugin', return_value=html) as fetch:
            full, saved = notes.render([ENTRY], 'full')
            self.assertIn('Version 1.10', full)
            self.assertNotIn('Version 1.11', full)
            self.assertEqual(full, saved)
            brief, saved = notes.render([('plugin', 'fixture-plugin', '1.0', '1.12')], 'brief')
            self.assertIn('7 more published releases', brief)
            self.assertNotIn('Version 1.1\n', brief)
            self.assertIn('Version 1.1\n', saved)
            fetch.reset_mock()
            self.assertEqual(notes.render([ENTRY], 'none'), ('', ''))
            fetch.assert_not_called()

    def test_partial_or_empty_source_never_claims_completeness(self):
        with patch.object(notes, 'fetch_plugin', return_value='<h4>1.9</h4><p>Only retained release.</p>'):
            brief, full = notes.component_notes(*ENTRY)
            self.assertIn('PARTIAL', brief)
            self.assertIn('completeness is not guaranteed', full)
        with patch.object(notes, 'fetch_plugin', return_value='<h4>Unreleased</h4>'):
            brief, full = notes.component_notes(*ENTRY)
            self.assertIn('unavailable', brief)
            self.assertIsNone(full)

    def test_missing_private_theme_core_and_network_failures_are_informational(self):
        with patch.object(notes, 'fetch_plugin', side_effect=TimeoutError('SECRET')):
            for entry in (ENTRY, ('plugin', 'private-plugin', '1', '2'),
                          ('theme', 'fixture-theme', '1', '2'), ('core', 'core', '1', '2')):
                brief, _ = notes.component_notes(*entry)
                self.assertIn('unavailable', brief)
                self.assertNotIn('SECRET', brief)
                self.assertNotIn('FAIL', brief)


class TransportTests(unittest.TestCase):
    def response(self, data):
        response = io.BytesIO(data)
        opener = SimpleNamespace(open=lambda *a, **kw: response)
        return patch.object(notes.urllib.request, 'build_opener', return_value=opener)

    def test_validated_slug_and_json(self):
        data = json.dumps({'slug': 'fixture-plugin', 'sections': {'changelog': HTML}}).encode()
        with self.response(data):
            self.assertEqual(notes.fetch_plugin('fixture-plugin'), HTML)
        for payload in ({'slug': 'other'}, {'slug': 'fixture-plugin', 'sections': {}}, {'error': 'not found'}):
            with self.response(json.dumps(payload).encode()), self.assertRaises(ValueError):
                notes.fetch_plugin('fixture-plugin')
        with self.assertRaises(ValueError):
            notes.fetch_plugin('../other')

    def test_size_and_deadline_are_bounded(self):
        with patch.object(notes, 'MAX_BYTES', 20), self.response(b'x' * 21), self.assertRaises(ValueError):
            notes.fetch_plugin('fixture-plugin')
        with self.response(b'{}'), patch.object(notes.time, 'monotonic', side_effect=[0, 20]), self.assertRaises(TimeoutError):
            notes.fetch_plugin('fixture-plugin')

    def test_redirects_are_not_followed(self):
        self.assertIsNone(notes.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/'))

    def test_saved_file_is_private_and_outside_code(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'logs').mkdir()
            path = notes.save_notes({'base': base}, 'Public release notes\n')
            self.assertEqual(path.parent, base / 'logs')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), 'Public release notes\n')
            self.assertEqual(list((base / 'logs').iterdir()), [path])


class UpdaterTests(unittest.TestCase):
    def test_cli_modes_and_default(self):
        for option in ([], ['--changelog', 'full'], ['--changelog', 'brief'], ['--changelog', 'none']):
            with contextlib.redirect_stdout(io.StringIO()), \
                    patch.object(updater.os, 'geteuid', return_value=0), \
                    patch.object(updater, 'library', return_value=(lambda name: {'domain': name, 'base': Path('/not-used')}, contextlib.nullcontext)), \
                    patch.object(updater, 'backup_today', return_value=None), \
                    patch.object(updater, 'changed', return_value=set()), \
                    patch.object(updater, 'policy', return_value={}), \
                    patch.object(updater, 'inventory', return_value=[ENTRY]), \
                    patch.object(updater, 'show_changelogs') as display:
                self.assertEqual(updater.main(['plan', 'example.com', *option]), 0)
                display.assert_called_once_with([ENTRY], option[-1] if option else 'none')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            updater.main(['plan', 'example.com', '--changelog', 'invalid'])
        self.assertEqual(error.exception.code, 2)

    def test_apply_fetches_after_maintenance_release_and_reports_only_success(self):
        for success in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / 'conf').mkdir()
                (base / 'logs').mkdir()
                site = {'base': base, 'domain': 'example.com'}
                args = SimpleNamespace(scope='plugin:fixture-plugin', policy='latest', skip_backup_check=True,
                                       automatic=False, no_maintenance=False, allow_dirty_target=False, changelog='full')
                args_seen = []
                def command(argv, **kwargs):
                    return SimpleNamespace(stdout='200' if success else '503', returncode=0)
                def display(entries, mode, target):
                    args_seen.append(entries)
                    self.assertEqual(mode, 'full')
                    if success:
                        self.assertFalse((base / 'conf/maintenance').exists())
                    else:
                        self.assertTrue((base / 'conf/maintenance').exists())
                with patch.object(updater, 'git', return_value=SimpleNamespace(returncode=0)), \
                        patch.object(updater, 'inventory', return_value=[(*ENTRY, ['wp-content/plugins/fixture-plugin'])]), \
                        patch.object(updater, 'policy', return_value={}), \
                        patch.object(updater, 'target_dirty', return_value=False), \
                        patch.object(updater, 'file_state', return_value={}, create=True), \
                        patch.object(updater, 'changed', return_value=set()), \
                        patch.object(updater, 'translation_paths', return_value=[], create=True), \
                        patch.object(updater, 'wp', return_value=SimpleNamespace(stdout='1.10', returncode=0)), \
                        patch.object(updater, 'command', side_effect=command), \
                        patch.object(updater, 'commit_paths', return_value='COMMIT'), \
                        patch.object(updater, 'report'), patch.object(updater.subprocess, 'run'), \
                        patch.object(updater, 'show_changelogs', side_effect=display):
                    self.assertEqual(updater.apply(site, args), not success)
                self.assertEqual(args_seen, [[ENTRY] if success else []])

    def test_fetch_or_save_failures_do_not_raise(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors, contextlib.redirect_stdout(io.StringIO()):
            with patch.object(notes, 'render', side_effect=RuntimeError('SECRET')):
                updater.show_changelogs([ENTRY], 'full')
            with patch.object(notes, 'render', return_value=('notes', 'notes')), \
                    patch.object(notes, 'save_notes', side_effect=OSError('SECRET')):
                updater.show_changelogs([ENTRY], 'brief', {'base': Path('/not-used')})
        self.assertNotIn('SECRET', errors.getvalue())
        self.assertIn('result is unchanged', errors.getvalue())

    def test_none_skips_render_and_writes(self):
        with patch.object(notes, 'render') as render, patch.object(notes, 'save_notes') as save:
            updater.show_changelogs([ENTRY], 'none', {})
        render.assert_not_called()
        save.assert_not_called()


class DefaultChangelogTests(unittest.TestCase):
    def test_default_performs_no_lookup_or_write(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(updater.os, 'geteuid', return_value=0), \
                patch.object(updater, 'library', return_value=(lambda name: {'domain': name, 'base': Path('/not-used')}, contextlib.nullcontext)), \
                patch.object(updater, 'backup_today', return_value=('ID', 'today')), \
                patch.object(updater, 'changed', return_value=set()), \
                patch.object(updater, 'policy', return_value={}), \
                patch.object(updater, 'inventory', return_value=[ENTRY]), \
                patch.object(notes, 'render') as render, patch.object(notes, 'save_notes') as save:
            self.assertEqual(updater.main(['plan', 'example.com']), 0)
        render.assert_not_called()
        save.assert_not_called()
