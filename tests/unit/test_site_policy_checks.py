"""The owner checksum path reuses policy checks without runuser or sudo."""
import contextlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server-tools/lib'))
import code_policy as policy


class OwnerChecksumTests(unittest.TestCase):
    def test_current_owner_uses_own_php_cache_and_component(self):
        result = SimpleNamespace(stdout='[]', stderr='', returncode=0)
        with patch.object(policy.os, 'getuid', return_value=1234), patch.object(policy.os, 'geteuid', return_value=1234), \
             patch('pwd.getpwuid', return_value=SimpleNamespace(pw_name='owner')), \
             patch.object(policy, 'load', return_value=policy.empty()), patch.object(policy.subprocess, 'run', return_value=result) as execute:
            summary = policy.check_plugins('/srv/sites/one.example', 'owner', '9.7', '/usr/local/bin/wp',
                                           as_current_user=True, component='plug')
        command = execute.call_args.args[0]
        self.assertEqual(command[:2], ['env', '-i'])
        self.assertNotIn('runuser', command)
        self.assertIn('php9.7', command)
        self.assertIn('WP_CLI_CACHE_DIR=/srv/sites/one.example/owner-tmp/wp-cli-cache', command)
        self.assertIn('plug', command)
        self.assertNotIn('--all', command)
        self.assertTrue(summary.startswith('PASS'))

    def test_direct_identity_rejects_root_or_another_uid(self):
        with patch.object(policy.os, 'getuid', return_value=0):
            with self.assertRaises(PermissionError):
                policy.identity_prefix('owner', current=True)
        with patch.object(policy.os, 'getuid', return_value=1234), patch.object(policy.os, 'geteuid', return_value=1234), \
             patch('pwd.getpwuid', return_value=SimpleNamespace(pw_name='other')):
            with self.assertRaises(PermissionError):
                policy.identity_prefix('owner', current=True)

    def test_detailed_checks_list_every_mismatch(self):
        rows = [{'plugin_name': 'plug', 'file': f'file{i}.php', 'message': 'File was added'} for i in range(8)]
        result = SimpleNamespace(stdout=json.dumps(rows), stderr='', returncode=1)
        with patch.object(policy, 'identity_prefix', return_value=['env', '-i']), \
             patch.object(policy, 'load', return_value=policy.empty()), patch.object(policy.subprocess, 'run', return_value=result), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            summary = policy.check_plugins('/srv/sites/one.example', 'owner', '8.4', '/wp', as_current_user=True, details=True)
        self.assertTrue(summary.startswith('FAIL'))
        for i in range(8):
            self.assertIn(f'file{i}.php', output.getvalue())

    def test_git_baseline_direct_mode_never_runs_as_root(self):
        clean = SimpleNamespace(stdout='', stderr='', returncode=0)
        tracked = SimpleNamespace(stdout='wp-content/plugins/plug/main.php', stderr='', returncode=0)
        with patch.object(policy, 'identity_prefix', return_value=['env', '-i']) as identity, \
             patch.object(policy.subprocess, 'run', side_effect=[tracked, clean]) as execute:
            self.assertEqual(policy.git_baseline_issues('/srv/sites/one.example', 'owner', {'plug'}, as_current_user=True), [])
        identity.assert_called_once_with('owner', current=True)
        self.assertTrue(all('runuser' not in c.args[0] for c in execute.call_args_list))


if __name__ == '__main__':
    unittest.main()
