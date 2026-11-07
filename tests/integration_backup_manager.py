#!/usr/bin/env python3
"""Run only inside the disposable, network-disabled backup-manager test container."""
import contextlib
import gzip
import io
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, '/source/server-tools/lib')
import backup_manager as bm
import backup_recovery as recovery
import wp_hosting as hosting


def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


class RealBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path('/.dockerenv').exists() or Path('/source/inventories').exists():
            raise RuntimeError('Use the allowlisted disposable container runner')
        cls.root = Path(tempfile.mkdtemp(prefix='wbm-real-'))
        cls.repo = cls.root / 'repository'
        cls.passfile = cls.root / 'password'
        cls.passfile.write_text(os.urandom(32).hex())
        cls.passfile.chmod(0o600)
        Path('/etc/restic').mkdir(mode=0o700)
        Path('/etc/restic/env').write_text(f'RESTIC_REPOSITORY={cls.repo}\nRESTIC_PASSWORD_FILE={cls.passfile}\nBACKUP_REQUIRE_MOUNT=false\nBACKUP_PATHS="/srv/sites"\nBACKUP_EXCLUDES="/nonexistent"\nKEEP_DAILY=7\nKEEP_WEEKLY=4\nKEEP_MONTHLY=6\n')
        Path('/etc/restic/env').chmod(0o600)
        cls.env = bm.environment('/etc/restic/env')
        cls.repository = bm.Repository(cls.env)
        cls.repository.call('init')
        cls.tables = sorted('wp_' + name for name in ('options', 'posts', 'postmeta', 'users', 'usermeta', 'terms',
            'termmeta', 'term_taxonomy', 'term_relationships', 'comments', 'commentmeta', 'links'))
        for domain in ('deleted.example', 'current.example'):
            base = Path('/srv/sites') / domain
            (base / 'htdocs/sub').mkdir(parents=True)
            (base / 'conf').mkdir()
            (base / 'backup').mkdir()
            (base / 'htdocs/a.php').write_text('<?php // Never execute this file')
            (base / 'htdocs/sub/name [x];file.txt').write_text('literal data')
            (base / 'conf/code-policy.yml').write_text('version: 1\n')
            (base / 'htdocs/wp-config.php').write_text('<?php /* generated fixture only */')
            with gzip.open(base / 'backup/db.sql.gz', 'wb') as dump:
                dump.write(('\n'.join('CREATE TABLE ' + table + ' (id int);' for table in cls.tables) + '\n-- Dump completed on fixture\n').encode())
            files = base / 'backup/files.jsonl'
            files.write_text('"a.php"\n"sub/name [x];file.txt"\n"wp-config.php"\n')
            (base / 'backup/manifest.json').write_text(json.dumps({'sha256': bm.sha256_file(base / 'backup/db.sql.gz'),
                'files_sha256': bm.sha256_file(files), 'tables': cls.tables, 'installed': True, 'prefix': 'wp_'}))
        cls.repository.call('backup', '--tag', 'complete', '--tag', 'nightly', '/srv/sites')
        cls.complete = cls.repository.snapshots()[0]['id']
        cls.repository.call('backup', '--tag', 'manual', '/srv/sites/deleted.example')
        cls.single = next(s['id'] for s in cls.repository.snapshots() if 'manual' in s.get('tags', []))
        shutil.rmtree('/srv/sites/deleted.example')

    def invoke(self, *arguments):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bm.main([*arguments, '--mode', 'run'])
        return out.getvalue()

    def test_01_deleted_and_multisite_listing(self):
        out = self.invoke('list', '--site', 'deleted.example')
        self.assertIn('yes', out)
        self.assertIn('incomplete/unconfirmed', out)
        self.assertEqual(self.repository.sites(self.repository.select('deleted.example', self.complete)),
                         ['current.example', 'deleted.example'])
        self.assertEqual(self.repository.sites(self.repository.select('deleted.example', self.single)), ['deleted.example'])

    def test_02_all_extraction_scopes_without_database(self):
        for scope, path in [('complete', None), ('database', None), ('files', None),
                            ('file', 'htdocs/sub/name [x];file.txt'), ('directory', 'htdocs/sub')]:
            with self.subTest(scope=scope):
                args = ['extract', '--site', 'deleted.example', '--snapshot', self.complete, '--scope', scope]
                if path:
                    args += ['--path', path]
                with patch.object(recovery, 'Database', side_effect=AssertionError('Database accessed')):
                    out = self.invoke(*args)
                self.assertIn('PASS extraction', out)
                tasks = bm.Tasks(self.env).inventory()
                task = max(tasks, key=lambda t: t['created'])
                base = Path(task['path']) / 'data'
                if scope in ('file', 'directory'):
                    self.assertEqual((base / 'htdocs/sub/name [x];file.txt').read_text(), 'literal data')
                if scope == 'database':
                    self.assertTrue((base / 'backup/db.sql.gz').exists())
                    self.assertFalse((base / 'htdocs').exists())
                if scope == 'files':
                    self.assertFalse((base / 'backup/db.sql.gz').exists())
                self.assertFalse(Path('/srv/sites/deleted.example').exists())

    def test_03_portable_archive_and_encrypted_copy(self):
        for encrypted in (False, True):
            args = ['export', '--site', 'deleted.example', '--snapshot', self.complete, '--class', 'kept', '--yes']
            if encrypted:
                args += ['--encrypt', '--export-password-file', str(self.passfile)]
            out = self.invoke(*args)
            self.assertIn('PASS portable copy', out)
            task = max(bm.Tasks(self.env).inventory(), key=lambda t: t['created'])
            root = Path(task['path'])
            checksum = next(root.glob('*.sha256'))
            command(['sha256sum', '-c', checksum.name], cwd=root)
            self.assertFalse(list(root.glob('*.partial')))
            archive_path = checksum.with_name(checksum.name.removesuffix('.sha256'))
            payload = subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase-file', str(self.passfile),
                '--decrypt', str(archive_path)], check=True, capture_output=True).stdout if encrypted else archive_path.read_bytes()
            with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
                self.assertIn('deleted.example/conf/code-policy.yml', archive.getnames())
                self.assertIn('deleted.example/backup/db.sql.gz', archive.getnames())
                self.assertIn('RECOVERY.txt', archive.getnames())
                self.assertNotIn(self.passfile.name, archive.getnames())
            with self.assertRaisesRegex(ValueError, 'Kept'):
                bm.Tasks(self.env).remove(task)

    def test_04_snapshot_copy(self):
        password = self.root / 'destination.pass'
        password.write_text(os.urandom(32).hex())
        password.chmod(0o600)
        out = self.invoke('copy', '--snapshot', self.complete, '--destination', str(self.root / 'destination'),
                          '--destination-password-file', str(password), '--init-destination', '--yes')
        self.assertIn('Destination snapshot:', out)
        self.assertEqual(len(self.repository.snapshots()), 2)

    def test_05_verify_without_and_with_scratch_database(self):
        out = self.invoke('verify', '--site', 'deleted.example', '--snapshot', self.complete)
        self.assertIn('NOT CHECKED scratch', out)
        # This MariaDB instance is inside the disposable container, with no host mounts or network.
        Path('/run/mysqld').mkdir(exist_ok=True)
        shutil.chown('/run/mysqld', user='mysql', group='mysql')
        process = subprocess.Popen(['mariadbd', '--user=mysql', '--skip-networking', '--socket=/run/mysqld/mysqld.sock'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                ready = subprocess.run(['mariadb-admin', 'ping', '--silent'], capture_output=True)
                if ready.returncode == 0:
                    break
                time.sleep(.1)
            out = self.invoke('verify', '--site', 'deleted.example', '--snapshot', self.complete, '--scratch-db')
            self.assertIn('PASS scratch import', out)
            db = hosting.Database()
            self.assertEqual(db.query("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME LIKE %s", ("wpstage_%",)), ())
            # Exercise the real standard backup functions with a generated local database.
            # No WordPress code or external endpoint is required by this workflow.
            db.query('CREATE DATABASE fixturedb')
            for name in self.tables:
                db.query('CREATE TABLE fixturedb.`' + name + '` (id int)')
            base = Path('/srv/sites/current.example')
            (base / 'conf/site.env').write_text('DOMAIN=current.example\nSITE_USER=fixture_owner\nDB_NAME=fixturedb\nPHP_VERSION=8.4\n')
            (base / 'conf/wordpress-installed').write_text('installed\n')
            (base / 'htdocs/wp-config.php').write_text("<?php\ndefine('DB_NAME', 'fixturedb');\ndefine('DB_USER', 'fixturedb');\ndefine('DB_PASSWORD', 'generated-fixture-only');\ndefine('DB_HOST', 'localhost:/run/mysqld/mysqld.sock');\n$table_prefix = 'wp_';\n")
            for scope in (['--site', 'current.example'], ['--all']):
                out = self.invoke('create', *scope)
                self.assertIn('Complete snapshot ID:', out)
        finally:
            process.terminate()
            process.wait(timeout=30)

    def test_06_cleanup_and_lock_conflict(self):
        tasks = bm.Tasks(self.env)
        task = tasks.create('verification')
        tasks.write(task, 'failed')
        out = self.invoke('cleanup', '--task', task['id'], '--yes')
        self.assertIn('Removed:', out)
        with hosting.operation_lock(timeout=1):
            proc = subprocess.run(['/usr/local/sbin/wp-backup-manager', 'cleanup', '--all', '--yes', '--mode', 'run'],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn('holds the lock', proc.stderr)

    def test_07_installed_cli_and_tui(self):
        result = command(['/usr/local/sbin/wp-backup-manager', 'list', '--site', 'deleted.example', '--mode', 'run'])
        self.assertIn('COMPLETE SNAPSHOTS', result.stdout)
        master, slave = pty.openpty()
        proc = subprocess.Popen(['/usr/local/sbin/wp-backup-manager'], stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        os.write(master, b'14\n')
        output = b''
        deadline = time.time() + 15
        try:
            while time.time() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try:
                        output += os.read(master, 65536)
                    except OSError:
                        break
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
            os.close(master)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b'AT STARTUP', output)
        self.assertIn(b'AT EXIT', output)

    def test_08_interrupted_export_and_live_unchanged(self):
        before = bm.sha256_file('/srv/sites/current.example/htdocs/a.php')
        def fail(task, *args):
            (Path(task['path']) / 'interrupted.tar.gz.partial').write_bytes(b'partial')
            raise InterruptedError('test interruption')
        with patch.object(bm, 'portable', side_effect=fail):
            with self.assertRaises(InterruptedError):
                self.invoke('export', '--site', 'deleted.example', '--snapshot', self.complete, '--class', 'temporary', '--yes')
        task = max(bm.Tasks(self.env).inventory(), key=lambda t: t['created'])
        self.assertEqual(task['state'], 'interrupted')
        self.assertTrue(list(Path(task['path']).glob('*.partial')))
        self.assertEqual(bm.sha256_file('/srv/sites/current.example/htdocs/a.php'), before)

    def test_09_remaining_actions_and_snapshot_deletion(self):
        self.assertIn('Current site', self.invoke('compare', '--site', 'current.example', '--snapshot', self.complete))
        self.assertIn('backup_restic_keep_daily', self.invoke('retention'))
        self.assertIn('Last successful snapshot:', self.invoke('status'))
        self.assertIn('RECOVERY IS MANUAL', self.invoke('instructions', '--site', 'current.example'))
        self.assertIn('Total allocated bytes:', self.invoke('workspaces', '--full'))
        self.assertIn('Saved command display: run', self.invoke('preferences', '--default-mode', 'run'))
        self.assertEqual(bm.preference(), 'run')
        self.assertIn('PASS repository', self.invoke('verify', '--all'))
        before = {s['id'] for s in self.repository.snapshots()}
        self.assertIn('Deleted snapshot records', self.invoke('delete-snapshots', '--snapshot', self.single, '--yes'))
        self.assertEqual({s['id'] for s in self.repository.snapshots()}, before - {self.single})


def installation():
    env = {**os.environ, 'ANSIBLE_ROLES_PATH': '/source/roles', 'ANSIBLE_LOCAL_TEMP': '/tmp/ansible-local'}
    for repeat in range(2):
        result = command(['ansible-playbook', '-i', 'localhost,', '/source/tests/backup-manager-install.yml'], env=env)
        print(result.stdout, flush=True)
        if repeat and 'changed=0 ' not in result.stdout:
            raise RuntimeError('Second installation changed resources')


if __name__ == '__main__':
    installation()
    unittest.main(verbosity=2)
