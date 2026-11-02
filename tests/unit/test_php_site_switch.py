"""Exercise the real route transaction with isolated paths and service stubs."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'library'))
import php_site_switch as switch


class PHPSiteSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.site = self.root / 'site'
        (self.site / 'conf').mkdir(parents=True)
        for name, value in {
            'PHP_ROOT': self.root / 'php',
            'FPM_BIN_ROOT': self.root / 'bin',
            'NGINX_AVAILABLE': self.root / 'nginx/available',
            'NGINX_ENABLED': self.root / 'nginx/enabled',
            'SOCKET_ROOT': self.root / 'run',
            'LOCK_PATH': self.root / 'switch.lock',
        }.items():
            patcher = mock.patch.object(switch, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (switch.PHP_ROOT / '8.4/fpm/pool.d').mkdir(parents=True)
        switch.FPM_BIN_ROOT.mkdir()
        (switch.FPM_BIN_ROOT / 'php-fpm8.4').touch()
        switch.NGINX_AVAILABLE.mkdir(parents=True)
        switch.NGINX_ENABLED.mkdir(parents=True)
        self.old_pool = switch.PHP_ROOT / '8.3/fpm/pool.d/first-wp.conf'
        self.old_pool.parent.mkdir(parents=True)
        self.old_pool.write_text('old pool')
        self.vhost = switch.NGINX_AVAILABLE / 'first.example.conf'
        self.vhost.write_text('old route')
        (switch.NGINX_ENABLED / self.vhost.name).symlink_to(self.vhost)
        self.env = self.site / 'conf/site.env'
        self.env.write_text('PHP_VERSION=8.3\n')
        self.args = ('first.example', 'first', '8.4', str(self.site),
                     'new pool', 'new route', 'PHP_VERSION=8.4\n')
        run_patcher = mock.patch.object(switch, 'run', return_value='')
        self.commands = run_patcher.start()
        self.addCleanup(run_patcher.stop)
        self.real_php_probe = switch.php_probe
        php_patcher = mock.patch.object(switch, 'php_probe')
        self.php_probe = php_patcher.start()
        self.addCleanup(php_patcher.stop)
        service_patcher = mock.patch.object(switch, 'service_ready', return_value=True)
        self.service_ready = service_patcher.start()
        self.addCleanup(service_patcher.stop)
        self.real_nginx_probe = switch.nginx_probe
        nginx_patcher = mock.patch.object(switch, 'nginx_probe')
        self.nginx_probe = nginx_patcher.start()
        self.addCleanup(nginx_patcher.stop)

    def test_switch_removes_old_pool_and_repeat_changes_nothing(self):
        self.assertTrue(switch.converge(*self.args))
        self.assertFalse(self.old_pool.exists())
        self.assertEqual(self.vhost.read_text(), 'new route')
        self.assertEqual(self.env.read_text(), 'PHP_VERSION=8.4\n')
        self.assertTrue((switch.PHP_ROOT / '8.4/fpm/pool.d/first-wp.conf').exists())
        self.assertFalse((self.site / 'conf/php-switch.json').exists())
        self.assertFalse(switch.converge(*self.args))
        self.assertIn(mock.call(['systemctl', 'disable', '--now', 'php8.3-fpm']),
                      self.commands.call_args_list)

    def test_stopped_fpm_is_restarted_on_repeat(self):
        self.assertTrue(switch.converge(*self.args))
        self.service_ready.return_value = False
        self.assertTrue(switch.converge(*self.args))
        self.assertIn(mock.call(['systemctl', 'enable', '--now', 'php8.4-fpm']),
                      self.commands.call_args_list)

    def test_waits_for_socket_after_fpm_reload(self):
        self.php_probe.side_effect = [RuntimeError('socket not ready'), None]
        with mock.patch.object(switch.time, 'sleep') as pause:
            self.assertTrue(switch.converge(*self.args))
        self.assertEqual(self.php_probe.call_count, 2)
        pause.assert_called_once()

    def test_failed_new_route_restores_old_route_and_pool(self):
        self.nginx_probe.side_effect = [RuntimeError('bad response'), None]
        with self.assertRaisesRegex(RuntimeError, 'bad response'):
            switch.converge(*self.args)
        self.assertEqual(self.old_pool.read_text(), 'old pool')
        self.assertEqual(self.vhost.read_text(), 'old route')
        self.assertEqual(self.env.read_text(), 'PHP_VERSION=8.3\n')
        self.assertFalse((switch.PHP_ROOT / '8.4/fpm/pool.d/first-wp.conf').exists())
        self.assertFalse((self.site / 'conf/php-switch.json').exists())

    def test_fpm_validation_failure_never_changes_nginx_route(self):
        self.commands.side_effect = [RuntimeError('invalid FPM config'), '', '', '']
        with self.assertRaisesRegex(RuntimeError, 'invalid FPM config'):
            switch.converge(*self.args)
        self.assertEqual(self.vhost.read_text(), 'old route')
        self.assertEqual(self.old_pool.read_text(), 'old pool')
        self.assertEqual(self.env.read_text(), 'PHP_VERSION=8.3\n')
        self.assertFalse((switch.PHP_ROOT / '8.4/fpm/pool.d/first-wp.conf').exists())

    def test_retry_finishes_failed_old_service_cleanup(self):
        attempts = 0

        def fail_once(argv, **_):
            nonlocal attempts
            if argv == ['systemctl', 'disable', '--now', 'php8.3-fpm']:
                attempts += 1
                if attempts == 1:
                    raise RuntimeError('service stop failed')
            return ''

        self.commands.side_effect = fail_once
        with self.assertRaisesRegex(RuntimeError, 'service stop failed'):
            switch.converge(*self.args)
        self.assertEqual(self.vhost.read_text(), 'new route')
        self.assertEqual(self.env.read_text(), 'PHP_VERSION=8.4\n')
        self.assertFalse(self.old_pool.exists())
        self.assertTrue((self.site / 'conf/php-switch.json').exists())
        self.assertTrue(switch.converge(*self.args))
        self.assertEqual(attempts, 2)
        self.assertFalse((self.site / 'conf/php-switch.json').exists())
        self.assertFalse(switch.converge(*self.args))

    def test_invalid_branch_cannot_change_files(self):
        bad = list(self.args)
        bad[2] = '8.4/../../evil'
        with self.assertRaises(ValueError):
            switch.converge(*bad)
        self.assertEqual(self.vhost.read_text(), 'old route')
        self.commands.assert_not_called()

    def test_unmanaged_nginx_link_is_not_removed(self):
        link = switch.NGINX_ENABLED / self.vhost.name
        link.unlink()
        link.write_text('foreign route')
        with self.assertRaisesRegex(RuntimeError, 'unmanaged Nginx site link'):
            switch.converge(*self.args)
        self.assertEqual(link.read_text(), 'foreign route')
        self.assertEqual(self.old_pool.read_text(), 'old pool')
        self.commands.assert_not_called()

    def test_shared_lock_cannot_be_a_symbolic_link(self):
        target = self.root / 'foreign-lock-target'
        target.write_text('keep')
        switch.LOCK_PATH.symlink_to(target)
        with self.assertRaises(OSError):
            switch.converge(*self.args)
        self.assertEqual(target.read_text(), 'keep')
        self.commands.assert_not_called()

    def test_php_probe_removes_file_when_runtime_account_is_missing(self):
        probe = self.site / 'tmp'
        probe.mkdir()
        with mock.patch.object(switch.pwd, 'getpwnam', side_effect=KeyError('missing')):
            with self.assertRaises(KeyError):
                self.real_php_probe(self.root / 'absent.sock', 'first', '8.4', self.site)
        self.assertEqual(list(probe.iterdir()), [])

    def test_nginx_probe_bypasses_cache_without_login_rate_limit(self):
        core = self.site / 'htdocs/wp-includes/version.php'
        core.parent.mkdir(parents=True)
        core.touch()
        self.commands.return_value = '200'
        self.real_nginx_probe('first.example', self.site)
        command = self.commands.call_args.args[0]
        self.assertIn('https://first.example/?php-switch=1', command)
        self.assertNotIn('/wp-login.php', ' '.join(command))

    def test_incomplete_wordpress_install_skips_application_probe(self):
        core = self.site / 'htdocs/wp-includes/version.php'
        core.parent.mkdir(parents=True)
        core.touch()
        (self.site / 'conf/install-in-progress').touch()
        self.real_nginx_probe('first.example', self.site)
        self.commands.assert_not_called()


if __name__ == '__main__':
    unittest.main()
