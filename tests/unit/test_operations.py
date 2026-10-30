import contextlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'library'))
spec = importlib.util.spec_from_file_location('operations', ROOT / 'roles/site/files/wp_hosting.py')
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class OperationTests(unittest.TestCase):
    def test_client_password_cannot_add_an_option(self):
        value = 'secret\nuser=root\r\npassword="\\quoted"\t#;'
        encoded = ops.option_value(value)
        self.assertEqual(encoded, '"secret\\nuser=root\\r\\npassword=\\"\\\\quoted\\"\\t#;"')
        self.assertNotIn('\n', encoded)
        self.assertNotIn('\r', encoded)
        with self.assertRaises(ValueError):
            ops.option_value('secret\x00hidden')

    def test_facts_are_data_not_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'executed'
            file = Path(directory) / 'env'
            file.write_text(f'VALUE="$(touch {marker})"\nEMPTY=""\n')
            self.assertEqual(ops.read_env(file)['VALUE'], f'$(touch {marker})')
            self.assertFalse(marker.exists())
            self.assertEqual(ops.read_env(file)['EMPTY'], '')

    def test_required_wordpress_schema(self):
        with self.assertRaises(ValueError):
            ops.require_wordpress_tables(['wp_posts'], 'wp_')

    def test_temporary_database_grant_is_literal(self):
        database = object.__new__(ops.Database)
        calls = []
        database.query = lambda sql, args=(): calls.append((sql, args))
        database.recreate = lambda name: None
        with database.scratch() as (name, password):
            grant = next(sql for sql, _ in calls if sql.startswith('GRANT'))
            self.assertIn(name.replace('_', r'\_'), grant)
            self.assertNotIn('ON *.*', grant)

    def test_failed_promotion_restores_original_database(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'conf').mkdir()
            (base / 'backup').mkdir()
            s = {'base': base, 'DB_NAME': 'target'}
            database = object.__new__(ops.Database)
            database.scratch = lambda: contextlib.nullcontext(('scratch', 'secret'))
            database.tables = lambda name: ['wp_posts']
            recreated = []
            database.recreate = recreated.append
            imports = []

            def load(path, name, user, password):
                imports.append((Path(path).name, name))
                if name == 'target' and Path(path).name == 'staged.sql.gz':
                    raise RuntimeError('injected SQL promotion failure')

            def dump(name, path, db):
                Path(path).write_bytes(b'recovery')

            @contextlib.contextmanager
            def maintenance(site):
                (base / 'conf/maintenance').touch()
                yield

            with patch.object(ops, 'credentials', return_value='secret'), patch.object(ops, 'load', side_effect=load), \
                 patch.object(ops, 'dump', side_effect=dump), patch.object(ops, 'maintenance', maintenance), patch.object(ops, 'RUNTIME', base), patch.object(ops, 'flush_cache'):
                with self.assertRaisesRegex(RuntimeError, 'promotion failure'):
                    ops.restore_database(s, base / 'input.sql.gz', database)
            self.assertEqual(recreated, ['target', 'target'])
            self.assertTrue(imports[-1][0].startswith('db-before-restore-'))
            self.assertFalse((base / 'conf/maintenance').exists())

    def test_schema_mismatch_does_not_touch_live_database(self):
        database = object.__new__(ops.Database)
        database.scratch = lambda: contextlib.nullcontext(('scratch', 'secret'))
        database.tables = lambda name: []
        with patch.object(ops, 'credentials', return_value='secret'), patch.object(ops, 'load'), \
             patch.object(database, 'recreate', create=True) as recreate:
            with self.assertRaises(ValueError):
                ops.restore_database({'DB_NAME': 'target'}, 'input.sql.gz', database, {'tables': ['wp_posts']})
            recreate.assert_not_called()

    def test_operation_lock_times_out(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'lock').mkdir()
            with (base / 'lock/wp-hosting.lock').open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_SH)
                with patch.object(ops, 'RUNTIME', base), self.assertRaises(TimeoutError):
                    with ops.operation_lock(timeout=.1):
                        self.fail('An exclusive lock was granted over a shared lock')

    def test_partial_restic_backup_cannot_prune_or_be_complete(self):
        import importlib.machinery
        import types
        loader = importlib.machinery.SourceFileLoader('backup_cli', str(ROOT / 'roles/backup_restic/files/wp-backup'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'wp_hosting':ops}):
            loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            env = {'BACKUP_PATHS':directory, 'BACKUP_EXCLUDES':''}
            with patch.object(module, 'backup_site'), patch.object(module, 'run',
                return_value=types.SimpleNamespace(returncode=3)) as command:
                with self.assertRaises(RuntimeError):
                    module.backup([{'domain':'example.com'}], env, None, full=True)
                self.assertEqual(command.call_count, 1)
                self.assertNotIn('complete', command.call_args.args[0])

    def test_cache_purge_does_not_follow_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            cache = base / 'cache'
            cache.mkdir()
            (cache / 'old-page').write_text('stale')
            outside = base / 'private'
            outside.write_text('untouched')
            (cache / 'link').symlink_to(outside)
            with patch.object(ops, 'CACHE_ROOT', cache):
                ops.flush_cache({'REDIS':'false'})
            self.assertEqual(list(cache.iterdir()), [])
            self.assertEqual(outside.read_text(), 'untouched')

    def test_missing_backup_path_stops_before_snapshot_or_prune(self):
        import importlib.machinery
        loader = importlib.machinery.SourceFileLoader('missing_path_cli', str(ROOT / 'roles/backup_restic/files/wp-backup'))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        with patch.dict(sys.modules, {'wp_hosting':ops}):
            loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            env = {'BACKUP_PATHS':str(Path(directory) / 'missing'), 'BACKUP_EXCLUDES':''}
            with patch.object(module, 'backup_site'), patch.object(module, 'run') as command:
                with self.assertRaisesRegex(ValueError, 'backup path is missing'):
                    module.backup([{'domain':'example.com'}], env, None, full=True)
                command.assert_not_called()

    def test_local_recovery_retention_preserves_live_dump_and_links(self):
        import importlib.machinery
        import os
        import time
        loader = importlib.machinery.SourceFileLoader('retention_cli', str(ROOT / 'roles/backup_restic/files/wp-backup'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'wp_hosting':ops}):
            loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            backup = base / 'backup'
            backup.mkdir()
            live = backup / 'db.sql.gz'
            live.write_text('keep')
            old = backup / 'update-123'
            old.mkdir()
            (old / 'original').write_text('old recovery')
            os.utime(old, (time.time() - 30 * 86400,) * 2)
            link = backup / 'clone-456'
            link.symlink_to(base, target_is_directory=True)
            module.prune_recovery([{'base':base}])
            self.assertFalse(old.exists())
            self.assertEqual(live.read_text(), 'keep')
            self.assertTrue(link.is_symlink())

    def test_staging_gate_is_readable_and_remains_closed(self):
        import types
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'conf').mkdir()
            with patch.object(ops.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=99999)), \
                 patch.object(ops, 'run', return_value=types.SimpleNamespace(returncode=1)):
                with ops.maintenance({'base':base, 'SITE_USER':'example'}, release=False):
                    self.assertEqual((base / 'conf/maintenance').stat().st_mode & 0o777, 0o644)
                self.assertTrue((base / 'conf/maintenance').exists())
