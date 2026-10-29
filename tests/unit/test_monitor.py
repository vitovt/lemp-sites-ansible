import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MonitorTests(unittest.TestCase):
    def run_monitor(self, *, core_error=False, missing=False, bad_json=False):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            site = base / 'sites/example.com'
            (site / 'conf').mkdir(parents=True)
            (site / 'htdocs/wp-includes').mkdir(parents=True)
            (site / 'conf/site.env').write_text('DOMAIN=example.com\nSITE_USER=example\nPHP_VERSION=8.3\nWORDPRESS_EXPECTED=true\n')
            if not missing:
                (site / 'htdocs/wp-includes/version.php').write_text('<?php')
            (base / 'config').write_text(f'STATE_DIR={base}/state\nWPCLI=/mock/wp\nCHECKS="core_checksums plugin_checksums"\n')
            (base / 'bin').mkdir()
            mock = base / 'bin/runuser'
            mock.write_text('#!/bin/sh\n' + ('echo "Error: mock failure" >&2; exit 1\n' if core_error else
                ('echo broken; exit 0\n' if bad_json else 'case "$*" in *plugin*) echo "[]";; *) echo "Success: verified";; esac\n')))
            mock.chmod(0o755)
            script = base / 'monitor'
            script.write_text((ROOT / 'roles/wp_monitor/files/wp-monitor').read_text()
                .replace('SITES_ROOT=/srv/sites', f'SITES_ROOT={base}/sites')
                .replace('. /etc/wp-monitor.conf', f'. {base}/config'))
            return subprocess.run(['bash', str(script), '--check', 'example.com'],
                env={**os.environ, 'PATH': str(base / 'bin') + ':' + os.environ['PATH']}, text=True, capture_output=True)

    def test_checker_failure_is_not_success(self):
        result = self.run_monitor(core_error=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('PASS', result.stdout)

    def test_missing_installed_core_fails(self):
        result = self.run_monitor(missing=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAIL example.com wordpress', result.stdout)

    def test_invalid_json_fails(self):
        self.assertEqual(self.run_monitor(bad_json=True).returncode, 1)

    def test_successful_checks(self):
        self.assertEqual(self.run_monitor().returncode, 0)

    def test_telegram_chunks_multiline_and_unicode(self):
        loader = importlib.machinery.SourceFileLoader('alerts', str(ROOT / 'roles/telegram_alert/files/tg-alert'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        alerts = importlib.util.module_from_spec(spec)
        loader.exec_module(alerts)
        text = ('line\n' * 2000) + ('😀' * 2500)
        chunks = list(alerts.chunks(text))
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(len(c.encode('utf-16-le')) // 2 <= 3500 for c in chunks))
