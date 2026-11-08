"""Real Git regressions for component language packs and pre-existing edits."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader('translation_updater', str(ROOT / 'server-tools/bin/wp-update'))
module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(module)


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'htdocs'
        self.root.mkdir()
        (self.base / 'owner-tmp').mkdir()
        (self.base / 'conf').mkdir()
        self.site = {'base': self.base, 'domain': 'fixture.example', 'SITE_USER': pwd.getpwuid(os.getuid()).pw_name}
        self.git('init', '-q')
        self.write('wp-includes/version.php', 'old core')
        self.write('outside.txt', 'baseline')
        self.write('wp-content/languages/ru_RU.po', 'old translation')
        self.write('wp-content/languages/plugins/akismet-ru_RU.po', 'old plugin translation')
        self.git('add', '-A')
        self.git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'baseline')
        actual = module.command
        def local_command(args, **kwargs):
            if args[0] == 'runuser':
                return actual(args[4:], **kwargs)
            if args[0] == 'curl':
                return SimpleNamespace(stdout='200', returncode=0)
            if str(args[0]).endswith('/site-fix-perms'):
                return SimpleNamespace(stdout='', returncode=0)
            return actual(args, **kwargs)
        patcher = patch.object(module, 'command', side_effect=local_command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True).stdout

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def apply(self, mutate, *, allow_dirty=False):
        args = SimpleNamespace(scope='core', policy='minor', skip_backup_check=True,
                               automatic=False, no_maintenance=False, allow_dirty_target=allow_dirty)
        paths = ['wp-includes', *module.translation_paths(self.site, 'core', 'core')]
        def wp(site, *args, **kwargs):
            if args[:2] == ('core', 'update'):
                mutate()
            return SimpleNamespace(stdout='2', returncode=0)
        with patch.object(module, 'inventory', return_value=[('core', 'core', '1', '2', paths)]), \
             patch.object(module, 'policy', return_value={}), patch.object(module, 'wp', side_effect=wp), \
             patch.object(module, 'report') as report, patch.object(module.subprocess, 'run', wraps=subprocess.run):
            # Avoid notifications, while keeping real Git subprocesses.
            real = module.subprocess.run
            def run(args, **kwargs):
                if args[0] == '/usr/local/sbin/tg-alert':
                    return SimpleNamespace(returncode=0)
                return real(args, **kwargs)
            with patch.object(module.subprocess, 'run', side_effect=run):
                failed = module.apply(self.site, args)
        return failed, '\n'.join(report.call_args.args[1])

    def test_core_commits_its_translations_and_keeps_staged_plugin_edits(self):
        self.write('wp-content/languages/plugins/akismet-ru_RU.po', 'local patch')
        self.git('add', 'wp-content/languages/plugins/akismet-ru_RU.po')
        index_before = self.git('ls-files', '--stage', 'wp-content/languages/plugins')
        def update():
            self.write('wp-includes/version.php', 'new core')
            self.write('wp-content/languages/ru_RU.po', 'new translation')
            self.write('wp-content/languages/admin-ru_RU.l10n.php', '<?php return [];')
        failed, report = self.apply(update)
        self.assertFalse(failed, report)
        self.assertEqual(set(self.git('show', '--format=', '--name-only', 'HEAD').splitlines()), {
            'wp-includes/version.php', 'wp-content/languages/ru_RU.po',
            'wp-content/languages/admin-ru_RU.l10n.php'})
        self.assertEqual(self.git('ls-files', '--stage', 'wp-content/languages/plugins'), index_before)
        self.assertEqual((self.root / 'wp-content/languages/plugins/akismet-ru_RU.po').read_text(), 'local patch')
        self.assertFalse((self.base / 'conf/maintenance').exists())

    def test_dirty_target_translation_requires_permission_and_prevents_commit(self):
        self.write('wp-content/languages/ru_RU.po', 'local core translation')
        head = self.git('rev-parse', 'HEAD')
        def update():
            self.write('wp-content/languages/ru_RU.po', 'updated core translation')
        failed, report = self.apply(update)
        self.assertTrue(failed)
        self.assertIn('target has local changes', report)
        self.assertEqual((self.root / 'wp-content/languages/ru_RU.po').read_text(), 'local core translation')
        failed, report = self.apply(update, allow_dirty=True)
        self.assertFalse(failed, report)
        self.assertIn('commit=none', report)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_second_edit_to_already_dirty_external_file_is_reported(self):
        self.write('outside.txt', 'local change')
        head = self.git('rev-parse', 'HEAD')
        def update():
            self.write('wp-includes/version.php', 'new core')
            self.write('outside.txt', 'unexpected second change')
        failed, report = self.apply(update)
        self.assertTrue(failed)
        self.assertIn("outside the selected component: 'outside.txt'", report)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertTrue((self.base / 'conf/maintenance').exists())

    def test_language_paths_are_limited_to_component_and_pack_formats(self):
        paths = ['wp-content/languages/admin-network-de_DE_formal.mo',
                 'wp-content/languages/continents-cities-fr_FR.po',
                 'wp-content/languages/ru_RU-' + 'a' * 32 + '.json',
                 'wp-content/languages/plugins/akismet-fr_FR.l10n.php',
                 'wp-content/languages/plugins/akismet-pro-fr_FR.mo',
                 'wp-content/languages/themes/allium-fr_FR.mo',
                 'wp-content/languages/arbitrary.php']
        for path in paths:
            self.write(path, 'fixture')
        core = module.translation_paths(self.site, 'core', 'core')
        self.assertTrue(set(paths[:3]) <= set(core))
        self.assertFalse(set(paths[3:]) & set(core))
        self.assertFalse(module.in_scope('wp-content/languages/ru_RU.po/extra.php', core))
        self.assertEqual(module.translation_paths(self.site, 'plugin', 'akismet'),
                         ['wp-content/languages/plugins/akismet-fr_FR.l10n.php', 'wp-content/languages/plugins/akismet-ru_RU.po'])
        self.assertEqual(module.translation_paths(self.site, 'theme', 'allium'), [paths[5]])
        def wp(site, *args, **kwargs):
            if args[:2] == ('core', 'version'):
                data = '1'
            elif args[:2] == ('core', 'check-update'):
                data = json.dumps([{'version': '2', 'update_type': 'minor'}])
            else:
                name = 'akismet' if args[0] == 'plugin' else 'allium'
                data = json.dumps([{'name': name, 'version': '1', 'update': 'available', 'update_version': '2'}])
            return SimpleNamespace(stdout=data)
        with patch.object(module, 'wp', side_effect=wp):
            entries = module.inventory(self.site, 'all', 'latest', {})
        self.assertIn(paths[0], entries[0][4])
        self.assertIn(paths[3], entries[1][4])
        self.assertNotIn(paths[4], entries[1][4])
        self.assertIn(paths[5], entries[2][4])


    def test_fingerprint_detects_same_status_edits_deletion_and_symlinks(self):
        self.write('outside.txt', 'first edit')
        before = module.file_state(self.site)
        self.write('outside.txt', 'second edit')
        (self.root / 'wp-includes/version.php').unlink()
        (self.root / 'link').symlink_to('/nonexistent-fixture')
        self.assertEqual(module.changed_paths(before, module.file_state(self.site)), ['link', 'outside.txt', 'wp-includes/version.php'])

    def test_fingerprint_refuses_a_replaced_parent_directory(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'version.php').write_text('private outside fixture')
        (self.root / 'wp-includes/version.php').unlink()
        (self.root / 'wp-includes').rmdir()
        (self.root / 'wp-includes').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            module.file_state(self.site)
