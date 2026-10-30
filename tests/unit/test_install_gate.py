import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('gate', Path(__file__).resolve().parents[2] / 'library/hosting_install_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class InstallationGateTests(unittest.TestCase):
    def test_claim_is_idempotent_and_release_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'maintenance'
            self.assertTrue(gate.manage(path))
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertFalse(gate.manage(path))
            path.chmod(0o600)
            self.assertTrue(gate.manage(path))
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertTrue(gate.manage(path, release=True))
            self.assertFalse(gate.manage(path, release=True))

    def test_another_operation_is_never_accepted_or_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'maintenance'
            path.write_text('staging clone\n')
            for release in [False, True]:
                with self.assertRaises(ValueError):
                    gate.manage(path, release=release)
                self.assertEqual(path.read_text(), 'staging clone\n')

    def test_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            original = base / 'original'
            original.write_bytes(gate.CONTENT)
            path = base / 'maintenance'
            path.symlink_to(original)
            with self.assertRaises(OSError):
                gate.manage(path, release=True)
            path.unlink()
            os.link(original, path)
            with self.assertRaises(ValueError):
                gate.manage(path, release=True)
            self.assertTrue(original.exists())

    def test_check_mode_does_not_change_the_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'maintenance'
            self.assertTrue(gate.manage(path, check=True))
            self.assertFalse(path.exists())
            gate.manage(path)
            self.assertTrue(gate.manage(path, release=True, check=True))
            self.assertTrue(path.exists())
