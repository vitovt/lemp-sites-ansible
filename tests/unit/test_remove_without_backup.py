"""Emergency removal skips backup setup but keeps normal removal safe."""
import contextlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'library'))
spec = importlib.util.spec_from_file_location('operations', ROOT / 'roles/site/files/wp_hosting.py')
operations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operations)


def backup_cli():
    loader = importlib.machinery.SourceFileLoader('remove_cli', str(ROOT / 'roles/backup_restic/files/wp-backup'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    with patch.dict(sys.modules, {'wp_hosting': operations}):
        loader.exec_module(module)
    return module


class RemoveWithoutBackupTests(unittest.TestCase):
    def test_emergency_remove_never_reads_restic_or_runs_backup(self):
        module = backup_cli()
        selected = {'domain': 'first.example'}
        with patch.object(sys, 'argv', ['wp-backup', 'remove', 'first.example', '--yes', '--no-backup']), \
             patch.object(module.os, 'geteuid', return_value=0), patch.object(module.os, 'umask'), \
             patch.object(module, 'read_env', side_effect=AssertionError('restic env was read')), \
             patch.object(module.os.path, 'ismount', side_effect=AssertionError('mount was checked')), \
             patch.object(module, 'operation_lock', return_value=contextlib.nullcontext()), \
             patch.object(module, 'Database'), patch.object(module, 'site', return_value=selected), \
             patch.object(module, 'backup', side_effect=AssertionError('backup was called')), \
             patch.object(module, 'remove_site') as remove:
            module.main()
        remove.assert_called_once()
        self.assertIs(remove.call_args.args[0], selected)
        self.assertEqual(remove.call_args.args[2], '/mnt/backups/removed')
        self.assertTrue(remove.call_args.kwargs['without_backup'])

    def test_normal_remove_still_backs_up(self):
        module = backup_cli()
        selected = {'domain': 'first.example'}
        with patch.object(sys, 'argv', ['wp-backup', 'remove', 'first.example', '--yes']), \
             patch.object(module.os, 'geteuid', return_value=0), patch.object(module.os, 'umask'), \
             patch.object(module, 'read_env', return_value={'BACKUP_REQUIRE_MOUNT': 'false'}), \
             patch.object(module, 'operation_lock', return_value=contextlib.nullcontext()), \
             patch.object(module, 'Database'), patch.object(module, 'site', return_value=selected), \
             patch.object(module, 'backup') as backup, patch.object(module, 'remove_site') as remove:
            module.main()
        backup.assert_called_once()
        self.assertFalse(remove.call_args.kwargs['without_backup'])

    def test_no_backup_cannot_be_used_with_other_actions(self):
        module = backup_cli()
        with patch.object(sys, 'argv', ['wp-backup', 'verify', '--no-backup']):
            with self.assertRaises(SystemExit):
                module.main()
