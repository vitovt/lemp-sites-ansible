"""Identity, environment and completion regressions for site-owner shells."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server-tools/lib'))
import site_session as session
import site_commands as commands
import site_help


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.account = SimpleNamespace(pw_name='owner', pw_uid=1234, pw_gid=2345, pw_dir='/srv/sites/first.example')
        self.s = {'domain': 'first.example', 'base': Path(self.account.pw_dir), 'SITE_USER': 'owner',
                  'PHP_VERSION': '9.7', 'account': self.account, 'groups': [2345]}

    def test_environment_is_allowlisted_and_fact_derived(self):
        poison = {'HOME': '/root', 'PATH': '/tmp', 'LD_PRELOAD': 'bad', 'BASH_ENV': 'bad', 'ENV': 'bad',
                  'SITE_DOMAIN': 'other.example', 'SITE_PHP': '1.0', 'SUDO_USER': 'root',
                  'GIT_CONFIG_VALUE_0': 'bad', 'PYTHONPATH': '/tmp', 'SSH_AUTH_SOCK': '/root/socket', 'SECRET_PASSWORD': 'bad'}
        with patch.dict(os.environ, poison, clear=True):
            env = session.environment(self.s)
        self.assertEqual(env['SITE_DOMAIN'], 'first.example')
        self.assertEqual(env['SITE_PHP'], '9.7')
        self.assertEqual(env['HOME'], '/srv/sites/first.example/owner-tmp')
        self.assertEqual(env['SHELL'], '/bin/bash')
        self.assertEqual(env['PATH'].split(':')[0], '/usr/local/lib/wp-hosting/bin/site-session')
        for key in ('LD_PRELOAD', 'BASH_ENV', 'ENV', 'SUDO_USER', 'PYTHONPATH', 'SSH_AUTH_SOCK', 'SECRET_PASSWORD'):
            self.assertNotIn(key, env)

    def test_root_launcher_uses_explicit_ids_groups_and_caps(self):
        with patch.object(session.os, 'getresuid', return_value=(0, 0, 0)), patch.object(session, 'facts', return_value=self.s), \
             patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True), \
             patch.object(sys.stderr, 'isatty', return_value=True), \
             patch.object(session, 'docroot', return_value=Path('/srv/sites/first.example/htdocs')), \
             patch.object(session.grp, 'getgrgid', return_value=SimpleNamespace(gr_name='owner')), \
             patch.object(session, 'trusted', side_effect=lambda path, **kwargs: Path(path)), patch.object(session.os, 'chdir'), patch.object(session, 'close_inherited_fds'), \
             patch.object(session.os, 'execve') as execute:
            session.enter('first.example')
        cmd = execute.call_args.args[1]
        self.assertEqual(cmd[0], '/usr/sbin/runuser')
        self.assertIn('--pty', cmd)
        self.assertIn('--user=owner', cmd)
        self.assertIn('--group=owner', cmd)
        self.assertIn('--supp-group=owner', cmd)
        self.assertIn('--inh-caps=-all', cmd)
        self.assertIn('--ambient-caps=-all', cmd)
        self.assertNotIn('--init-groups', cmd)

    def test_launcher_rejects_nonroot_and_shell_rejects_root(self):
        with patch.object(session.os, 'getresuid', return_value=(1234,) * 3):
            with self.assertRaises(PermissionError):
                session.enter('first.example')
        with patch.object(session.os, 'getuid', return_value=0):
            with self.assertRaises(PermissionError):
                session.current_site()

    def test_site_resolves_from_uid_not_environment(self):
        with patch.dict(os.environ, {'SITE_DOMAIN': 'other.example', 'HOME': '/srv/sites/other.example'}), \
             patch.object(session.os, 'getuid', return_value=1234), patch.object(session.os, 'getresuid', return_value=(1234,) * 3), \
             patch.object(session.os, 'getresgid', return_value=(2345,) * 3), patch.object(session.os, 'getgroups', return_value=[2345]), \
             patch.object(session.pwd, 'getpwuid', return_value=self.account), patch.object(session, 'facts', return_value=self.s) as facts, \
             patch.object(session, 'has_capabilities', return_value=False):
            self.assertIs(session.current_site(), self.s)
        facts.assert_called_once_with('first.example')

    def test_runtime_uid_and_unexpected_groups_fail(self):
        runtime = SimpleNamespace(**{**self.account.__dict__, 'pw_name': 'owner-php', 'pw_uid': 1235})
        for groups, account in [([2345, 27], self.account), ([2345], runtime)]:
            with patch.object(session.os, 'getuid', return_value=account.pw_uid), \
                 patch.object(session.os, 'getresuid', return_value=(account.pw_uid,) * 3), \
                 patch.object(session.os, 'getresgid', return_value=(2345,) * 3), patch.object(session.os, 'getgroups', return_value=groups), \
                 patch.object(session.pwd, 'getpwuid', return_value=account), patch.object(session, 'facts', return_value=self.s):
                with self.assertRaises(PermissionError):
                    session.current_site()

    def test_saved_root_ids_and_capabilities_fail(self):
        with patch.object(session.os, 'getuid', return_value=1234), patch.object(session.os, 'getresuid', return_value=(1234, 1234, 0)):
            with self.assertRaises(PermissionError):
                session.current_site()
        with patch.object(session.os, 'getuid', return_value=1234), patch.object(session.os, 'getresuid', return_value=(1234,) * 3), \
             patch.object(session.os, 'getresgid', return_value=(2345,) * 3), patch.object(session.os, 'getgroups', return_value=[2345]), \
             patch.object(session.pwd, 'getpwuid', return_value=self.account), patch.object(session, 'facts', return_value=self.s), \
             patch.object(session, 'has_capabilities', return_value=True):
            with self.assertRaises(PermissionError):
                session.current_site()

    def test_arguments_never_fall_back_to_bash(self):
        for args in (['-c', 'id'], ['first.example'], ['--login'], ['-l']):
            with patch.object(session, 'shell') as shell, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(session.cli('site-shell', args), 2)
            shell.assert_not_called()

    def test_no_tty_is_rejected(self):
        with patch.object(session, 'current_site', return_value=self.s), patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'terminal'):
                session.shell()
        with patch.object(session.os, 'getresuid', return_value=(0, 0, 0)), patch.object(sys.stdin, 'isatty', return_value=False), \
             patch.object(session, 'facts') as facts:
            with self.assertRaisesRegex(ValueError, 'terminal'):
                session.enter('first.example')
        facts.assert_not_called()

    def test_domain_traversal_and_untrusted_ancestors_fail(self):
        for value in ('../first.example', '/srv/sites/first.example', 'first.example/..', 'ROOT'):
            with self.assertRaises(ValueError):
                session.facts(value)
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'facts'
            file.write_text('test')
            with self.assertRaisesRegex(ValueError, 'Untrusted'):
                session.trusted(file)

    def test_fact_reader_does_not_execute_shell_or_php(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'first.example'
            (base / 'conf').mkdir(parents=True)
            marker = Path(tmp) / 'never'
            (base / 'conf/site.env').write_text('DOMAIN=first.example\nSITE_USER=owner\nPHP_VERSION=9.7\n'
                                               f'UNUSED="$(touch {marker})"\n')
            account = SimpleNamespace(**{**self.account.__dict__, 'pw_dir': str(base)})
            with patch.object(session, 'SITES', Path(tmp)), patch.object(session, 'trusted', side_effect=lambda path, **kwargs: Path(path)), \
                 patch.object(session.os, 'fstat', return_value=SimpleNamespace(st_uid=0, st_mode=0o100644)), \
                 patch.object(session.pwd, 'getpwnam', return_value=account), \
                 patch.object(session.grp, 'getgrgid', return_value=SimpleNamespace(gr_name='owner')):
                self.assertEqual(session.facts('first.example')['PHP_VERSION'], '9.7')
            self.assertFalse(marker.exists())

    def test_docroot_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'htdocs').symlink_to('/tmp', target_is_directory=True)
            with self.assertRaises(ValueError):
                session.docroot({**self.s, 'base': base})

    def test_wp_wrapper_has_absolute_php_and_no_path_override(self):
        with patch.object(commands, 'php_path', return_value='/usr/bin/php9.7'), \
             patch.object(commands, 'docroot', return_value=Path('/srv/sites/first.example/htdocs')):
            cmd = commands.wp_command(self.s, ['plugin', 'list'])
            self.assertEqual(cmd[0], '/usr/bin/php9.7')
            self.assertIn('--path=/srv/sites/first.example/htdocs', cmd)
            for flag in ('--path=/tmp', '--ssh=target', '--http=http://other.example', '--path'):
                with self.assertRaises(ValueError):
                    commands.wp_command(self.s, [flag])

    def test_policy_only_inserts_uid_site_into_narrow_interface(self):
        with patch.object(commands, 'current_site', return_value=self.s), patch.object(commands.os, 'umask'), patch.object(commands.os, 'execve') as execute:
            self.assertEqual(commands.main('site-policy', ['hold', 'plug', '--reason', 'Local fix']), 0)
        cmd = execute.call_args.args[1]
        self.assertEqual(cmd, ['/usr/bin/sudo', '-n', '/usr/local/sbin/wp-site', 'policy', 'hold', 'first.example', 'plug', '--reason', 'Local fix'])
        self.assertNotIn('SUDO_USER', execute.call_args.args[2])

    def test_inherited_file_descriptors_are_closed_in_a_child(self):
        with tempfile.TemporaryFile() as stream:
            stream.write(b'private fixture')
            stream.flush()
            code = "import site_session,os,sys; site_session.close_inherited_fds();\ntry: os.fstat(int(sys.argv[1])); print('leaked')\nexcept OSError: print('closed')"
            result = subprocess.run([sys.executable, '-c', code, str(stream.fileno())],
                env={**os.environ, 'PYTHONPATH': str(ROOT / 'server-tools/lib')}, pass_fds=(stream.fileno(),),
                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'closed')

    def test_cache_is_writable_and_home_is_private_before_bash(self):
        old_mask = os.umask(0o027)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                home = base / 'owner-tmp'
                home.mkdir(mode=0o700)
                cache = home / 'wp-cli-cache'
                cache.mkdir(mode=0o500)
                account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
                s = {**self.s, 'base': base, 'account': account}
                with patch.object(session, 'current_site', return_value=s), patch.object(session, 'docroot', return_value=base), \
                     patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True), \
                     patch.object(sys.stderr, 'isatty', return_value=True), patch.object(session, 'trusted', side_effect=lambda path, **kwargs: Path(path)), \
                     patch.object(session, 'php_path', return_value='/usr/bin/php9.7'), patch.object(session, 'close_inherited_fds'), \
                     patch.object(session.os, 'chdir'), patch.object(session.os, 'execve') as execute:
                    session.shell()
                    self.assertEqual(cache.stat().st_mode & 0o777, 0o700)
                    execute.assert_called_once()
                    home.chmod(0o500)
                    try:
                        with self.assertRaisesRegex(ValueError, '0700'):
                            session.shell()
                    finally:
                        home.chmod(0o700)
                    history = home / '.bash_history'
                    history.unlink()
                    os.mkfifo(history)
                    with self.assertRaises(OSError):
                        session.shell()
        finally:
            os.umask(old_mask)


class HelpAndCompletionTests(unittest.TestCase):
    def test_help_summary_detail_and_all(self):
        for args, expected in [([], 'wp plugin list'), (['site-policy'], 'allow-file'), (['--all'], 'HISTORY')]:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(site_help.main(args), 0)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn('\033', output.getvalue())
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            site_help.main(['unknown'])

    def test_color_follows_terminal_capability(self):
        with patch.object(site_help, 'supports_color', return_value=True), contextlib.redirect_stdout(io.StringIO()) as out:
            site_help.main(['php'])
        self.assertIn('\033[', out.getvalue())

    def test_completion_does_not_execute_programs(self):
        script = f'''source {ROOT}/server-tools/lib/site-completion.bash
COMP_WORDS=(policy ch); COMP_CWORD=1; _site_complete; printf '%s\\n' "${{COMPREPLY[@]}}"
COMP_WORDS=(wp core ver); COMP_CWORD=2; _site_complete; printf '%s\\n' "${{COMPREPLY[@]}}"
COMP_WORDS=(site-check pl); COMP_CWORD=1; _site_complete; printf '%s\\n' "${{COMPREPLY[@]}}"
'''
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script], text=True, capture_output=True, check=True)
        self.assertIn('checksums', result.stdout)
        self.assertIn('verify-checksums', result.stdout)
        self.assertIn('plugins', result.stdout)

    def test_completion_follows_theme_options_and_plugin_file_paths(self):
        import shlex
        with tempfile.TemporaryDirectory(prefix='site completion ') as tmp:
            base = Path(tmp)
            (base / 'htdocs/wp-content/themes/my-theme').mkdir(parents=True)
            (base / 'htdocs/wp-content/plugins/plug/sub').mkdir(parents=True)
            (base / 'htdocs/wp-content/plugins/plug/sub/file.php').write_text('fixture')
            script = f"""source {ROOT}/server-tools/lib/site-completion.bash
SITE_BASE={shlex.quote(tmp)}
COMP_WORDS=(policy hold --kind theme my); COMP_CWORD=4; _site_complete; printf '%s\\n' "${{COMPREPLY[@]}}"
COMP_WORDS=(policy allow-file plug sub/f); COMP_CWORD=3; _site_complete; printf '%s\\n' "${{COMPREPLY[@]}}"
COMP_WORDS=(policy checksums plug --mode g); COMP_CWORD=4; _site_complete; printf '%s\\n' "${{COMPREPLY[@]}}"
"""
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script], text=True, capture_output=True, check=True)
        self.assertIn('my-theme', result.stdout)
        self.assertIn('sub/file.php', result.stdout)
        self.assertIn('git', result.stdout)


if __name__ == '__main__':
    unittest.main()
