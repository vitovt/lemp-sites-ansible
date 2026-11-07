"""Security and operator regressions for extraction-only backup management."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server-tools/lib'))
import backup_manager as bm
import backup_recovery as recovery


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.restore = self.root / 'restore'
        self.archives = self.root / 'archives'
        self.patches = [patch.object(bm, 'RESTORE', self.restore), patch.object(bm, 'ARCHIVES', self.archives),
                        patch.object(bm, 'PREFERENCES', self.root / 'prefs/config.json')]
        for p in self.patches:
            p.start()
        self.addCleanup(self.tmp.cleanup)
        for p in self.patches:
            self.addCleanup(p.stop)
        self.env = {'RESTIC_REPOSITORY': str(self.root / 'repo'), 'BACKUP_MOUNT': str(self.root / 'mount'),
                    'RESTIC_PASSWORD_FILE': str(self.root / 'pass')}
        self.tasks = bm.Tasks(self.env)

    def snapshot(self, tags=None):
        return {'id': 'a' * 64, 'time': '2026-01-01T00:00:00Z', 'paths': ['/srv/sites'], 'tags': tags or ['complete']}

    def nodes(self, site='deleted.example'):
        return [{'struct_type': 'node', 'path': '/srv/sites/' + site, 'type': 'dir'},
                {'struct_type': 'node', 'path': '/srv/sites/' + site + '/htdocs', 'type': 'dir'},
                {'struct_type': 'node', 'path': '/srv/sites/' + site + '/htdocs/a.php', 'type': 'file', 'size': 2}]

    def test_multisite_and_deleted_site_discovery_ignores_tags(self):
        repo = bm.Repository(self.env)
        snap = self.snapshot(['complete', 'nightly'])
        with patch.object(repo, 'nodes', return_value=self.nodes() + self.nodes('other.example')), \
             patch.object(repo, 'snapshots', return_value=[snap]):
            self.assertEqual(repo.sites(snap), ['deleted.example', 'other.example'])
            self.assertIs(repo.select('deleted.example', 'aaaa'), snap)
            self.assertEqual(len(repo.selected_nodes(snap, 'deleted.example', 'files')), 2)

    def test_single_site_snapshot(self):
        repo = bm.Repository(self.env)
        snap = {**self.snapshot(), 'paths': ['/srv/sites/deleted.example'], 'tags': ['complete', 'site:deleted.example']}
        with patch.object(repo, 'nodes', return_value=self.nodes()):
            self.assertEqual(repo.sites(snap), ['deleted.example'])

    def test_incomplete_is_separate(self):
        repo = bm.Repository(self.env)
        with patch.object(repo, 'nodes', return_value=self.nodes()), patch.object(repo, 'snapshots',
             return_value=[self.snapshot(), {**self.snapshot(['manual']), 'id': 'b' * 64}]), contextlib.redirect_stdout(io.StringIO()) as out:
            bm.show_snapshots(repo)
        self.assertIn('INCOMPLETE OR UNCONFIRMED', out.getvalue())
        self.assertIn('incomplete/unconfirmed', out.getvalue())

    def test_path_traversal_and_symlinks_are_rejected(self):
        for value in ('../htdocs', '/srv/sites', 'a/../b', 'a//b', './a', 'a\\b'):
            with self.assertRaises(ValueError):
                recovery.relative_path(value)
        repo = bm.Repository(self.env)
        for node in ({'path': '/srv/sites/deleted.example/htdocs/../../escape', 'type': 'file'},
                     {'path': '/srv/sites/deleted.example/htdocs/link', 'type': 'symlink'}):
            with patch.object(repo, 'nodes', return_value=self.nodes() + [node]):
                with self.assertRaises(ValueError):
                    repo.selected_nodes(self.snapshot(), 'deleted.example', 'complete')
        (self.root / 'link').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            recovery.regular(self.root, 'link')

    def test_scope_and_literal_pattern(self):
        snap = self.snapshot()
        for scope in bm.SCOPES:
            cmd = bm.restore_command(snap, 'deleted.example', scope, 'htdocs/a [x]; b.php', self.restore / 'data')
            self.assertIn('--verify', cmd)
            self.assertNotIn('/srv/sites/deleted.example', cmd[cmd.index('--target') + 1])
            if scope in ('file', 'directory'):
                self.assertIn('/htdocs/a \\[x\\]; b.php', cmd)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            bm.preview([['restic', 'dump', 'a', '/file; $(id) "x"']])
        self.assertIn("'/file; $(id) \"x\"'", out.getvalue())

    def test_tasks_interruption_active_and_kept_protection(self):
        task = self.tasks.create('extraction', selected=['htdocs'])
        with self.assertRaisesRegex(ValueError, 'active'):
            self.tasks.remove(task)
        self.tasks.write(task, 'complete')
        self.tasks.remove(task)
        self.assertFalse(Path(task['path']).exists())
        kept = self.tasks.create('export', kept=True)
        self.tasks.write(kept, 'complete')
        with self.assertRaisesRegex(ValueError, 'Kept'):
            self.tasks.remove(kept)
        self.tasks.remove(kept, archive=True)
        interrupted = self.tasks.create('verification')
        with patch.object(bm, 'process_identity', return_value=None):
            self.assertEqual(self.tasks.inventory()[0]['state'], 'interrupted')
        self.assertTrue(Path(interrupted['path']).exists())

    def test_cleanup_rejects_arbitrary_or_substituted_paths(self):
        task = self.tasks.create('extraction')
        with self.assertRaises(ValueError):
            self.tasks.remove({**task, 'path': '/srv/sites/deleted.example'})
        path = Path(task['path'])
        path.rename(path.with_name('saved'))
        path.symlink_to(path.with_name('saved'), target_is_directory=True)
        with self.assertRaises(ValueError):
            self.tasks.remove(task)
        self.assertTrue((path / 'task.json').exists())

    def test_unregistered_and_allocated_symlink(self):
        self.restore.mkdir(mode=0o700)
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'large').write_bytes(b'a' * 100000)
        (self.restore / 'link').symlink_to(outside, target_is_directory=True)
        values = self.tasks.inventory()
        self.assertEqual(values[0]['id'], 'unregistered')
        self.assertLess(values[0]['allocated'], 100000)
        args = bm.parser().parse_args(['cleanup', '--all'])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bm.cleanup_selection(args, self.tasks), [])

    def test_failed_operation_stays_visible(self):
        with patch.object(bm, 'notify'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                with bm.task_run(self.tasks, 'verification'):
                    raise RuntimeError('synthetic error')
        values = self.tasks.inventory()
        self.assertEqual(values[0]['state'], 'failed')
        self.assertTrue((Path(values[0]['path']) / 'result.log').exists())

    def test_missing_values_without_terminal(self):
        with patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'Missing value'):
                bm.ask('Snapshot')
            with self.assertRaisesRegex(ValueError, 'Confirmation'):
                bm.confirm('Delete')

    def test_partial_cli_only_prompts_missing_value(self):
        repo = bm.Repository(self.env)
        snap = self.snapshot()
        args = bm.parser().parse_args(['browse', '--site', 'deleted.example'])
        with patch.object(repo, 'snapshots', return_value=[snap]), patch.object(repo, 'nodes', return_value=self.nodes()), \
             patch.object(bm, 'show_snapshots'), patch.object(bm, 'ask', return_value='aaaa') as ask:
            self.assertIs(bm.resolve_selection(args, repo), snap)
        ask.assert_called_once_with('Snapshot ID')

    def test_password_disclosure_is_separate_and_never_saved(self):
        command = [['tool', '--password=very-secret']]
        for answer in ('n', 'y'):
            with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=answer) as prompt, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                bm.preview(command)
            prompt.assert_called_once_with('Print this command with the password visible? [y/N] ')
            self.assertEqual('very-secret' in out.getvalue(), answer == 'y')
        with patch.object(sys.stdin, 'isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()) as out:
            bm.preview(command)
        self.assertNotIn('very-secret', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            bm.preview(command, show_secrets=True)
        self.assertIn('very-secret', out.getvalue())

    def test_tui_prints_startup_and_exit_tables(self):
        args = bm.parser().parse_args([])
        with patch.object(bm, 'ask', return_value='14'), contextlib.redirect_stdout(io.StringIO()) as out:
            bm.tui(args, self.env)
        self.assertIn('AT STARTUP', out.getvalue())
        self.assertIn('AT EXIT', out.getvalue())
        self.assertEqual(out.getvalue().count('Total allocated bytes:'), 2)

    def test_preview_does_not_create_workspaces_or_run_restore(self):
        args = bm.parser().parse_args(['extract', '--site', 'deleted.example', '--snapshot', 'aaaa',
                                      '--scope', 'file', '--path', 'htdocs/a.php', '--mode', 'show'])
        with patch.object(bm.Repository, 'snapshots', return_value=[self.snapshot()]), \
             patch.object(bm.Repository, 'nodes', return_value=self.nodes()), patch.object(bm, 'run') as run, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            bm.dispatch(args, self.env)
        run.assert_not_called()
        self.assertFalse(self.restore.exists())
        self.assertIn('--target', out.getvalue())

    def test_protected_workspace_roots(self):
        with patch.object(bm, 'RESTORE', Path('/srv/sites')):
            with self.assertRaisesRegex(ValueError, 'overlaps'):
                bm.Tasks(self.env)
        with patch.object(bm, 'RESTORE', self.root / 'repo'):
            with self.assertRaisesRegex(ValueError, 'overlaps'):
                bm.Tasks(self.env)

    def test_standard_backup_is_reused_and_failure_is_visible(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        self.env['BACKUP_REQUIRE_MOUNT'] = 'false'
        standard = SimpleNamespace(site=Mock(return_value={'domain': 'deleted.example'}),
                                   Database=Mock(), backup=Mock())
        args = bm.parser().parse_args(['create', '--site', 'deleted.example', '--mode', 'run'])
        with patch.object(bm, 'operation_lock', return_value=contextlib.nullcontext()), patch.object(bm, 'source_backup', return_value=standard), patch.object(bm.Repository, 'snapshots',
             side_effect=[[], [self.snapshot()]]), patch.object(bm, 'notify'), contextlib.redirect_stdout(io.StringIO()):
            bm.dispatch(args, self.env)
        standard.backup.assert_called_once()
        self.assertEqual(self.tasks.inventory()[0]['snapshot'], 'a' * 64)
        standard.backup.side_effect = RuntimeError('synthetic failure')
        with patch.object(bm, 'operation_lock', return_value=contextlib.nullcontext()), patch.object(bm, 'source_backup', return_value=standard), patch.object(bm.Repository, 'snapshots', return_value=[]), \
             patch.object(bm, 'notify'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                bm.dispatch(args, self.env)
        self.assertIn('failed', [t['state'] for t in self.tasks.inventory()])

    def test_repository_verification_failure_keeps_task(self):
        repo = bm.Repository(self.env)
        with patch.object(repo, 'selected_nodes', return_value=self.nodes()), \
             patch.object(repo, 'call', side_effect=RuntimeError('repository is broken')), \
             patch.object(bm, 'notify'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                bm.verify_one(repo, self.tasks, self.snapshot(), 'deleted.example', False)
        self.assertEqual(self.tasks.inventory()[0]['state'], 'failed')

    def test_multiple_cleanup_selection_keeps_archives_and_active_tasks(self):
        done = self.tasks.create('extraction')
        self.tasks.write(done, 'complete')
        failed = self.tasks.create('verification')
        self.tasks.write(failed, 'failed')
        active = self.tasks.create('verification')
        kept = self.tasks.create('export', kept=True)
        self.tasks.write(kept, 'complete')
        args = bm.parser().parse_args(['cleanup', '--task', done['id'], '--task', failed['id']])
        with contextlib.redirect_stdout(io.StringIO()):
            chosen = bm.cleanup_selection(args, self.tasks)
        self.assertEqual({t['id'] for t in chosen}, {done['id'], failed['id']})
        args = bm.parser().parse_args(['cleanup', '--all'])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual({t['id'] for t in bm.cleanup_selection(args, self.tasks)}, {done['id'], failed['id']})

    def test_separate_password_arguments_are_masked(self):
        with patch.object(sys.stdin, 'isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()) as out:
            bm.preview([['client', '--password', 'TOPSECRET', '--password-file', '/private/file']])
        self.assertNotIn('TOPSECRET', out.getvalue())
        self.assertIn('/private/file', out.getvalue())

    def test_failure_logs_do_not_include_exception_data(self):
        with patch.object(bm, 'notify'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                with bm.task_run(self.tasks, 'extraction'):
                    raise RuntimeError('TOPSECRET')
        task = self.tasks.inventory()[0]
        for path in Path(task['path']).iterdir():
            self.assertNotIn('TOPSECRET', path.read_text())

    def test_cancellation_is_not_failure(self):
        with patch.object(bm, 'notify') as notify, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                with bm.task_run(self.tasks, 'extraction'):
                    raise KeyboardInterrupt()
        self.assertEqual(self.tasks.inventory()[0]['state'], 'interrupted')
        self.assertFalse(notify.call_args.kwargs['failed'])

    def test_destination_does_not_inherit_source_backend_credentials(self):
        repo = bm.Repository({**self.env, 'AWS_SECRET_ACCESS_KEY': 'SOURCE-SECRET'})
        args = bm.parser().parse_args(['copy', '--snapshot', 'aaaa', '--destination', str(self.root / 'destination'),
             '--destination-password-file', str(self.root / 'destination.pass'), '--yes', '--mode', 'run'])
        password = Path(args.destination_password_file)
        password.write_text('GENERATED-TEST')
        password.chmod(0o600)
        with patch.object(bm.Repository, 'snapshots', return_value=[self.snapshot()]), \
             patch.object(bm, 'copy_snapshots') as copy, patch.object(bm, 'operation_lock', return_value=contextlib.nullcontext()), \
             patch.object(bm, 'notify'), contextlib.redirect_stdout(io.StringIO()):
            bm.dispatch(args, repo.env)
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', copy.call_args.args[1].destination_environment)

    def test_preferences_show_mode_does_not_write(self):
        args = bm.parser().parse_args(['preferences', '--default-mode', 'run', '--mode', 'show'])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            bm.dispatch(args, self.env)
        self.assertIn('--default-mode run', out.getvalue())
        self.assertFalse(bm.PREFERENCES.exists())

    def test_export_rejects_a_repository_password_inside_site_data(self):
        self.env['RESTIC_PASSWORD_FILE'] = '/srv/sites/deleted.example/htdocs/a.php'
        args = bm.parser().parse_args(['export', '--site', 'deleted.example', '--snapshot', 'aaaa',
                                      '--class', 'kept', '--unencrypted', '--mode', 'show'])
        with patch.object(bm.Repository, 'snapshots', return_value=[self.snapshot()]), \
             patch.object(bm.Repository, 'nodes', return_value=self.nodes()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'repository password'):
                bm.dispatch(args, self.env)

    def test_status_reports_missing_systemd_tools(self):
        repo = bm.Repository(self.env)
        with patch.object(repo, 'snapshots', return_value=[]), patch.object(bm, 'run', side_effect=FileNotFoundError), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            bm.status(repo)
        self.assertIn('NOT AVAILABLE: systemctl', out.getvalue())
        self.assertIn('NOT AVAILABLE: journalctl', out.getvalue())

    def test_show_run_cleanup_asks_for_confirmation_once(self):
        task = self.tasks.create('extraction')
        self.tasks.write(task, 'complete')
        args = bm.parser().parse_args(['cleanup', '--task', task['id'], '--mode', 'show-run'])
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value='y') as prompt, \
             patch.object(bm, 'operation_lock', return_value=contextlib.nullcontext()), contextlib.redirect_stdout(io.StringIO()):
            bm.dispatch(args, self.env)
        self.assertEqual(prompt.call_count, 1)
        self.assertFalse(Path(task['path']).exists())

    def test_incomplete_snapshot_preview_does_not_need_execution_approval(self):
        args = bm.parser().parse_args(['extract', '--site', 'deleted.example', '--snapshot', 'aaaa',
                                      '--scope', 'file', '--path', 'htdocs/a.php', '--mode', 'show'])
        with patch.object(bm.Repository, 'snapshots', return_value=[self.snapshot(['manual'])]), \
             patch.object(bm.Repository, 'nodes', return_value=self.nodes()), patch.object(bm, 'confirm') as confirm, \
             contextlib.redirect_stdout(io.StringIO()):
            bm.dispatch(args, self.env)
        confirm.assert_not_called()

    def test_destination_cannot_write_to_live_or_temporary_data(self):
        for path in ('/srv/sites/deleted.example', str(self.restore / 'repository'),
                     str(self.archives / 'repository'), 'local:/srv/sites/repository', self.env['RESTIC_REPOSITORY']):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'overlaps'):
                bm.validate_destination(path, self.env)
        bm.validate_destination(str(self.root / 'new-repository'), self.env)

    def test_cleanup_never_deletes_a_repository_inserted_into_a_workspace(self):
        task = self.tasks.create('transfer')
        self.tasks.write(task, 'complete')
        repository = Path(task['path']) / 'repo'
        repository.mkdir()
        for name in ('data', 'index', 'keys', 'snapshots'):
            (repository / name).mkdir()
        (repository / 'config').write_text('fixture')
        with self.assertRaisesRegex(ValueError, 'restic repository'):
            self.tasks.remove(task)
        self.assertTrue((repository / 'config').exists())

    def test_local_prefix_repository_is_protected_from_cleanup(self):
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            bm.Tasks({**self.env, 'RESTIC_REPOSITORY': 'local:' + str(self.restore / 'repo')})


if __name__ == '__main__':
    unittest.main()
