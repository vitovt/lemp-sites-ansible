import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MonitorTests(unittest.TestCase):
    def run_monitor(self, *, core_error=False, missing=False, bad_json=False, success_line=False, permissions=None, nightly=False):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            site = base / 'sites/example.com'
            (site / 'conf').mkdir(parents=True)
            (site / 'htdocs/wp-includes').mkdir(parents=True)
            (site / 'conf/site.env').write_text('DOMAIN=example.com\nSITE_USER=example\nPHP_VERSION=8.3\nWORDPRESS_EXPECTED=true\n')
            if not missing:
                (site / 'htdocs/wp-includes/version.php').write_text('<?php')
            (base / 'config').write_text(f'STATE_DIR={base}/state\nWPCLI=/mock/wp\nCHECKS="core_checksums plugin_checksums"\n')
            if permissions is not None:
                (base / 'config').write_text(f'STATE_DIR={base}/state\nWPCLI=/mock/wp\nCHECKS="permissions"\n')
            (base / 'bin').mkdir()
            permission_helper = base / 'bin/site-fix-perms'
            permission_helper.write_text('#!/bin/sh\n'
                'test "$1" = example.com && test "$2" = --check || exit 99\n'
                'echo "private permission details"\necho "private error details" >&2\n'
                + ('exit 0\n' if permissions else 'exit 1\n'))
            permission_helper.chmod(0o755)
            alert = base / 'bin/tg-alert'
            alert.write_text('#!/bin/sh\ncat\n')
            alert.chmod(0o755)
            mock = base / 'bin/runuser'
            if success_line:
                mock.write_text('''#!/bin/sh
case "$*" in
  *"plugin list"*) echo '[{"status":"inactive"}]';;
  *"plugin verify-checksums"*) echo "Success: Verified 1 of 1 plugins.";;
  *) echo "Success: verified";;
esac
''')
            else:
                mock.write_text('#!/bin/sh\n' + ('echo "Error: mock failure" >&2; exit 1\n' if core_error else
                    ('echo broken; exit 0\n' if bad_json else 'case "$*" in *plugin*) echo "[]";; *) echo "Success: verified";; esac\n')))
            mock.chmod(0o755)
            script = base / 'monitor'
            policy_script = base / 'code_policy.py'
            policy_script.write_text((ROOT / 'server-tools/lib/code_policy.py').read_text())
            script.write_text((ROOT / 'server-tools/bin/wp-monitor').read_text()
                .replace('SITES_ROOT=/srv/sites', f'SITES_ROOT={base}/sites')
                .replace('. /etc/wp-monitor.conf', f'. {base}/config')
                .replace('/usr/local/lib/wp-hosting/lib/code_policy.py', str(policy_script))
                .replace('/usr/local/sbin/site-fix-perms', str(permission_helper)))
            return subprocess.run(['bash', str(script), *(['example.com'] if nightly else ['--check', 'example.com'])],
                env={**os.environ, 'PATH': str(base / 'bin') + ':' + os.environ['PATH']}, text=True, capture_output=True)

    def test_permissions_check_reports_only_summary(self):
        for safe in (True, False):
            with self.subTest(safe=safe):
                result = self.run_monitor(permissions=safe)
                self.assertEqual(result.returncode, 0 if safe else 1)
                self.assertEqual(result.stdout.strip(), ('PASS' if safe else 'FAIL') + ' example.com permissions')
                self.assertNotIn('private', result.stdout + result.stderr)
                if not safe:
                    self.assertIn('site-fix-perms example.com --check', result.stderr)

    def test_scheduled_permission_failure_is_in_combined_alert(self):
        result = self.run_monitor(permissions=False, nightly=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('wp-monitor found problems:', result.stdout)
        self.assertIn('  - example.com permissions', result.stdout)
        self.assertNotIn('private', result.stdout + result.stderr)

    def test_permissions_enabled_by_default(self):
        import yaml
        defaults = yaml.safe_load((ROOT / 'roles/wp_monitor/defaults/main.yml').read_text())
        self.assertIn('permissions', defaults['wp_monitor_checks'])

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

    def test_success_line_is_checked_against_plugin_inventory(self):
        self.assertEqual(self.run_monitor(success_line=True).returncode, 0)

    def test_successful_checks(self):
        self.assertEqual(self.run_monitor().returncode, 0)

    def test_telegram_chunks_multiline_and_unicode(self):
        loader = importlib.machinery.SourceFileLoader('alerts', str(ROOT / 'server-tools/bin/tg-alert'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        alerts = importlib.util.module_from_spec(spec)
        loader.exec_module(alerts)
        text = ('line\n' * 2000) + ('😀' * 2500)
        chunks = list(alerts.chunks(text))
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(len(c.encode('utf-16-le')) // 2 <= 3500 for c in chunks))
