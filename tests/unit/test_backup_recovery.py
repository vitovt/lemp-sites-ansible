"""Regression tests for shared extraction verification."""
import gzip
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server-tools/lib'))
import backup_recovery as recovery


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'backup').mkdir()
        (self.root / 'htdocs').mkdir()
        (self.root / 'htdocs/a.php').write_text('code')
        self.dump = self.root / 'backup/db.sql.gz'
        with gzip.open(self.dump, 'wb') as stream:
            stream.write(b'-- SQL\n-- Dump completed on fixture\n')
        files = self.root / 'backup/files.jsonl'
        files.write_text('"a.php"\n')
        self.manifest = {'sha256': recovery.sha256_file(self.dump), 'files_sha256': recovery.sha256_file(files),
                         'installed': False, 'tables': ['fixture']}
        self.save()

    def save(self):
        (self.root / 'backup/manifest.json').write_text(json.dumps(self.manifest))

    def test_extraction_verification_never_connects_mariadb(self):
        with patch.object(recovery, 'Database', side_effect=AssertionError('live database accessed')):
            result = recovery.verify_manifest(self.root)
        self.assertTrue(any('SQL dump hash' in s for s in result))
        self.assertTrue(any('NOT CHECKED scratch' in s for s in result))

    def test_missing_or_corrupt_metadata(self):
        (self.root / 'backup/manifest.json').unlink()
        self.assertIn('missing', recovery.verify_manifest(self.root)[0])
        self.save()
        (self.root / 'backup/files.jsonl').write_text('corrupt')
        with self.assertRaisesRegex(ValueError, 'manifest hash'):
            recovery.verify_manifest(self.root)

    def test_corrupt_missing_and_unfinished_sql(self):
        self.dump.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            recovery.verify_manifest(self.root)
        self.dump.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            recovery.verify_manifest(self.root)
        with gzip.open(self.dump, 'wb') as stream:
            stream.write(b'no completion')
        self.manifest['sha256'] = recovery.sha256_file(self.dump)
        self.save()
        with self.assertRaisesRegex(ValueError, 'completion marker'):
            recovery.verify_manifest(self.root)

    def test_manifest_traversal_rejected(self):
        files = self.root / 'backup/files.jsonl'
        files.write_text('"../secret"\n')
        self.manifest['files_sha256'] = recovery.sha256_file(files)
        self.save()
        with self.assertRaises(ValueError):
            recovery.verify_manifest(self.root)

    def test_scratch_table_names_do_not_depend_on_database_collation(self):
        from unittest.mock import Mock
        self.manifest['tables'] = ['wp_term_relationships', 'wp_termmeta']
        self.save()
        db = Mock()
        db.scratch.return_value = contextlib.nullcontext(('scratch', 'fixture'))
        db.tables.return_value = list(reversed(self.manifest['tables']))
        with patch.object(recovery, 'load'):
            result = recovery.verify_manifest(self.root, scratch=True, db=db)
        self.assertIn('PASS scratch import and manifest table list', result)


if __name__ == '__main__':
    unittest.main()
