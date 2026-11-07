"""Backup success markers must reference a readable completed snapshot."""
import contextlib
from datetime import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server-tools/lib'))
import wp_hosting


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_') + '_marker_test', str(ROOT / 'server-tools/bin' / name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class BackupWriterTests(unittest.TestCase):
    def test_manual_and_nightly_record_post_tag_id(self):
        for full in (False, True):
            with self.subTest(full=full), tempfile.TemporaryDirectory() as tmp:
                module = load('wp-backup')
                base = Path(tmp)
                markers = base / 'markers'
                old, new = '1' * 64, '2' * 64
                commands = []
                def command(args, **kwargs):
                    commands.append(args)
                    data = [{'id': new, 'tags': ['complete']}] if any(c[:2] == ['restic', 'tag'] for c in commands) else [{'id': old}]
                    return SimpleNamespace(returncode=0, stdout=json.dumps(data).encode())
                env = {'BACKUP_PATHS': tmp, 'BACKUP_EXCLUDES': '', 'KEEP_DAILY': '7', 'KEEP_WEEKLY': '4', 'KEEP_MONTHLY': '6'}
                with patch.object(module, 'BACKUP_MARKERS', markers), patch.object(module, 'backup_site'), \
                     patch.object(module, 'run', side_effect=command), patch.object(module, 'prune_recovery'), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    module.backup([{'domain': 'fixture.example', 'base': base}], env, None, full=full)
                files = list(markers.glob('*.json'))
                self.assertEqual(len(files), 1)
                marker = json.loads(files[0].read_text())
                self.assertEqual(marker['snapshot'], new)
                self.assertRegex(marker['run_tag'], r'^run-[0-9]+$')
                self.assertIn(new, files[0].name)
                self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
                self.assertFalse(list(markers.glob('.backup-*')))
                self.assertIn(new, output.getvalue())
                self.assertEqual(commands[-1][:4], ['restic', 'snapshots', '--json', '--tag'])

    def test_failed_completion_lookup_never_writes_a_marker(self):
        for completed in ([], [{'id': '2' * 64, 'tags': ['manual']}], [{'id': '2' * 64}, {'id': '3' * 64}]):
            with self.subTest(completed=completed), tempfile.TemporaryDirectory() as tmp:
                module = load('wp-backup')
                base = Path(tmp)
                tagged = False
                def command(args, **kwargs):
                    nonlocal tagged
                    if args[:2] == ['restic', 'tag']:
                        tagged = True
                    data = completed if tagged else [{'id': '1' * 64}]
                    return SimpleNamespace(returncode=0, stdout=json.dumps(data).encode())
                with patch.object(module, 'BACKUP_MARKERS', base / 'markers'), patch.object(module, 'backup_site'), \
                     patch.object(module, 'run', side_effect=command), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, 'completed snapshot'):
                        module.backup([{'domain': 'fixture.example', 'base': base}], {'BACKUP_EXCLUDES': ''}, None)
                self.assertFalse(list(base.glob('markers/*.json')))


class BackupReaderTests(unittest.TestCase):
    def setUp(self):
        self.module = load('wp-update')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.s = {'base': Path('/srv/sites/fixture.example'), 'domain': 'fixture.example'}
        self.old, self.new = '1' * 64, '2' * 64
        self.marker = {'snapshot': self.old, 'sites': ['fixture.example'], 'date': datetime.now().astimezone().date().isoformat(),
                       'time': datetime.now().astimezone().isoformat()}
        self.path = self.base / 'marker.json'
        self.write()
        actual_stat = Path.stat
        def root_owned(path, **kwargs):
            info = actual_stat(path, **kwargs)
            fields = list(info)
            if path == self.path:
                fields[4] = 0
            return os.stat_result(fields)
        for p in (patch.object(self.module, 'BACKUP_MARKERS', self.base), patch.object(Path, 'stat', root_owned),
                  patch.object(wp_hosting, 'read_env', return_value={'RESTIC_REPOSITORY': '/fixture/repository'})):
            p.start()
            self.addCleanup(p.stop)

    def write(self):
        self.path.write_text(json.dumps(self.marker))
        self.path.chmod(0o600)

    def command(self, catalog, listing=True, direct=None):
        def call(args, **kwargs):
            if args[:2] == ['restic', 'snapshots']:
                data = catalog if len(args) == 3 else direct or []
                return SimpleNamespace(returncode=0, stdout=json.dumps(data))
            self.assertEqual(args, ['restic', 'ls', '--json', self.new])
            names = ['backup/db.sql.gz', 'backup/manifest.json'] if listing else ['backup/db.sql.gz']
            return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps({'struct_type': 'node',
                'path': str(self.s['base'] / name)}) for name in names))
        return call

    def test_old_marker_resolves_original_id_and_verifies_site_files(self):
        snap = {'id': self.new, 'original': self.old, 'tags': ['complete']}
        before = self.path.read_bytes()
        with patch.object(self.module, 'command', side_effect=self.command([snap])):
            self.assertEqual(self.module.backup_today(self.s), (self.new, self.marker['time']))
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_marker_resolves_direct_id(self):
        self.marker['snapshot'] = self.new
        self.write()
        with patch.object(self.module, 'command', side_effect=self.command([], direct=[{'id': self.new, 'tags': ['complete']}])):
            self.assertEqual(self.module.backup_today(self.s)[0], self.new)

    def test_new_run_tag_survives_another_retag(self):
        self.marker['run_tag'] = 'run-123'
        self.write()
        snap = {'id': self.new, 'original': '3' * 64, 'tags': ['complete', 'run-123']}
        with patch.object(self.module, 'command', side_effect=self.command([snap])):
            self.assertEqual(self.module.backup_today(self.s)[0], self.new)

    def test_incomplete_ambiguous_and_unrelated_snapshots_are_not_accepted(self):
        snap = {'id': self.new, 'original': self.old, 'tags': ['complete']}
        for catalog in ([{**snap, 'tags': ['manual']}], [snap, {**snap, 'id': '4' * 64}], [{**snap, 'original': '3' * 64}]):
            with self.subTest(catalog=catalog), patch.object(self.module, 'command', side_effect=self.command(catalog)):
                self.assertIsNone(self.module.backup_today(self.s))

    def test_missing_manifest_does_not_pass(self):
        with patch.object(self.module, 'command', side_effect=self.command([{'id': self.new, 'original': self.old, 'tags': ['complete']}], listing=False)):
            self.assertIsNone(self.module.backup_today(self.s))

    def test_wrong_site_or_old_date_never_queries_restic(self):
        for field, value in [('sites', ['other.example']), ('date', '2000-01-01')]:
            with self.subTest(field=field), patch.object(self.module, 'command') as command:
                old = self.marker[field]
                self.marker[field] = value
                self.write()
                self.assertIsNone(self.module.backup_today(self.s))
                command.assert_not_called()
                self.marker[field] = old

    def test_corrupt_or_incomplete_marker_is_skipped(self):
        for value in ('{', '[]', '{}', '{"sites": ["fixture.example"]}'):
            self.path.write_text(value)
            with patch.object(self.module, 'command') as command:
                self.assertIsNone(self.module.backup_today(self.s))
                command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
