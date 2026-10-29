from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class MaintenanceTests(unittest.TestCase):
    def test_skip_staging_and_report_any_failed_update(self):
        tasks = yaml.safe_load((ROOT / 'roles/maintenance/tasks/main.yml').read_text())
        runner = tasks[0]['ansible.builtin.copy']['content']
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for domain in ['a.example', 'b.example', 'staging.example', 'empty.example']:
                conf = base / domain / 'conf'
                conf.mkdir(parents=True)
                (conf / 'site.env').touch()
                if domain != 'empty.example':
                    (conf / 'wordpress-installed').touch()
                if domain == 'staging.example':
                    (conf / 'maintenance').touch()
            log = base / 'called'
            backup = base / 'wp-backup'
            backup.write_text('#!/bin/sh\nprintf "%s\\n" "$2" >> ' + str(log) + '\n[ "$2" != a.example ]\n')
            backup.chmod(0o755)
            runner = runner.replace("pathlib.Path('/srv/sites')", f'pathlib.Path({str(base)!r})')
            runner = runner.replace("'/usr/local/sbin/wp-backup'", repr(str(backup)))
            result = subprocess.run(['python3', '-c', runner], capture_output=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(sorted(log.read_text().splitlines()), ['a.example', 'b.example'])
