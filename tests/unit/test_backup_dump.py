"""A cancelled dump must reap its child and close its output pipe."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server-tools/lib'))
import wp_hosting as hosting


class DumpTests(unittest.TestCase):
    def test_failed_copy_reaps_dump_process_and_removes_partial_file(self):
        original = subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            child = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            children.append(child)
            return child
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'db.sql.gz'
            with patch.object(hosting.subprocess, 'Popen', side_effect=spawn), \
                 patch.object(hosting.shutil, 'copyfileobj', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    hosting.dump('fixture', destination, Mock())
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix('.gz.tmp').exists())
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)


if __name__ == '__main__':
    unittest.main()
