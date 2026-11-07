#!/usr/bin/env python3
"""Real UID, PTY, Bash, PHP, WP-CLI and sudo tests in a disposable container only."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import pwd
import re
import select
import signal
import subprocess
import tempfile
import termios
import time
import unittest
import uuid

import yaml


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=True, **kwargs)


def install():
    # Reuse the actual narrow sudoers task. Do not grant additional privileges for tests.
    task = next(t for t in yaml.safe_load(Path('/source/roles/site/tasks/one-site.yml').read_text())
                if t.get('name') == 'Allow the site owner to edit only its local code policy')
    play = [{'name': 'Install isolated site tools', 'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
             'vars': {'server_tools_source': '/source/server-tools'}, 'roles': ['server_tools'],
             'tasks': [{'name': 'Install existing policy permission', 'ansible.builtin.include_tasks': '/tmp/policy-task.yml',
                        'loop': ['shell_one', 'shell_two'], 'loop_control': {'loop_var': 'owner'},
                        'vars': {'site_cfg': {'user': '{{ owner }}'}}}]}]
    Path('/tmp/policy-task.yml').write_text(yaml.safe_dump([task]))
    Path('/tmp/shell-install.yml').write_text(yaml.safe_dump(play))
    env = {**os.environ, 'ANSIBLE_ROLES_PATH': '/source/roles', 'ANSIBLE_LOCAL_TEMP': '/tmp/ansible-local'}
    result = run(['ansible-playbook', '-i', 'localhost,', '/tmp/shell-install.yml'], env=env)
    print(result.stdout, flush=True)
    return result.stdout


def terminal(command, text='', end='exit', env=None, mask=None, pass_fds=()):
    master, slave = pty.openpty()
    terminal.last_tty = os.ttyname(slave)
    def control_terminal():
        if mask is not None:
            os.umask(mask)
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    proc = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                            preexec_fn=control_terminal, pass_fds=pass_fds, env={**os.environ, 'TERM': 'dumb', **(env or {})})
    os.close(slave)
    output = b''
    def read_until(needle, timeout=15, start=0):
        nonlocal output
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle and needle in output[start:]:
                return
            if select.select([master], [], [], .1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                output += chunk
                if needle and needle in output[start:]:
                    return
            if proc.poll() is not None:
                return
        raise TimeoutError('Terminal output timed out: ' + output.decode(errors='replace'))
    try:
        read_until(b'] $ ')
        if b'] $ ' not in output:
            return proc.wait(timeout=10), output.decode(errors='replace')
        if proc.poll() is None:
            for line in text.splitlines():
                if not line.strip():
                    continue
                # sudo can consume queued terminal input. Send one complete command
                # at a time; the marker is parsed by Bash before sudo starts.
                marker = '__STEP_' + uuid.uuid4().hex + '__'
                start = len(output)
                os.write(master, (line + "; printf '\\n" + marker + "\\n'\n").encode())
                read_until(('\r\n' + marker + '\r\n').encode(), timeout=30, start=start)
                read_until(b'] $ ', start=start)
            if end == 'exit':
                os.write(master, b'exit\n')
            elif end == 'eof':
                os.write(master, b'\x04')
            elif end == 'interrupt':
                start = len(output)
                command = b"python3 -c 'import time; print(\"__RUNNING__\",flush=True); time.sleep(30)'\n"
                os.write(master, command)
                read_until(b'\r\n__RUNNING__\r\n', start=start)
                start = len(output)
                os.write(master, b'\x03')
                read_until(b'] $ ', start=start)
                os.write(master, b"printf 'INTERRUPTED-SHELL-ALIVE\\n'; exit\n")
            elif end in ('term', 'hup'):
                os.kill(proc.pid, signal.SIGTERM if end == 'term' else signal.SIGHUP)
            read_until(None, timeout=10)
        code = proc.wait(timeout=10)
        return code, output.decode(errors='replace')
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        os.close(master)


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path('/.dockerenv').exists() or Path('/source/inventories').exists():
            raise RuntimeError('Use the network-disabled disposable test runner')
        with Path('/etc/hosts').open('a') as hosts:
            hosts.write('\n127.0.0.1 ' + os.uname().nodename + '\n')
        run(['groupadd', 'valkey'])
        run(['groupadd', 'unrelated'])
        for owner, domain in [('shell_one', 'one.example'), ('shell_two', 'two.example')]:
            base = Path('/srv/sites') / domain
            run(['groupadd', owner])
            run(['useradd', '-g', owner, '-d', str(base), '-s', '/usr/sbin/nologin', owner])
            run(['useradd', '-g', owner, '-d', str(base), '-s', '/usr/sbin/nologin', owner + '-php'])
            base.mkdir(parents=True)
            os.chown(base, 0, pwd.getpwnam(owner).pw_gid)
            base.chmod(0o750)
            for name in ('conf', 'htdocs', 'owner-tmp', 'backup'):
                path = base / name
                path.mkdir()
                path.chmod(0o755 if name == 'conf' else 0o700)
                if name in ('htdocs', 'owner-tmp'):
                    os.chown(path, pwd.getpwnam(owner).pw_uid, pwd.getpwnam(owner).pw_gid)
            (base / 'conf/site.env').write_text(f'DOMAIN={domain}\nSITE_USER={owner}\nDB_NAME={owner}\nPHP_VERSION=8.4\nREDIS=true\n')
            (base / 'conf/site.env').chmod(0o644)
            home = base / 'owner-tmp'
            (home / '.site-shellrc').write_text('id -u > "$HOME/customization-uid"\n')
            os.chown(home / '.site-shellrc', pwd.getpwnam(owner).pw_uid, pwd.getpwnam(owner).pw_gid)
            cache = home / 'wp-cli-cache'
            cache.mkdir(mode=0o755)
            os.chown(cache, pwd.getpwnam(owner).pw_uid, pwd.getpwnam(owner).pw_gid)
            (cache / 'existing-cache-item').write_text('keep this cache data')
            (base / 'htdocs/private.txt').write_text('private generated fixture')
            (base / 'htdocs/private.txt').chmod(0o640)
            os.chown(base / 'htdocs/private.txt', pwd.getpwnam(owner).pw_uid, pwd.getpwnam(owner).pw_gid)
            run(['usermod', '-aG', 'valkey', owner])
        run(['usermod', '-aG', 'unrelated,shell_two', 'shell_one'])
        cls.account = pwd.getpwnam('shell_one')
        cls.base = Path('/srv/sites/one.example')
        Path('/root/private-test-secret').write_text('root fixture')
        Path('/root/private-test-secret').chmod(0o600)
        install()

    def test_01_identity_groups_environment_and_real_tools(self):
        script = '''python3 -c 'import os,json;print("SESSION="+json.dumps({"uid":os.getresuid(),"gid":os.getresgid(),"groups":os.getgroups(),"cwd":os.getcwd(),"tty":os.ttyname(0),"env":dict(os.environ)}))'
php -v
wp --info
git init -q
git config user.name
git config user.email
printf 'fixture\\n' > tracked.txt
git add -- tracked.txt
git commit -qm 'Fixture'
git status --short
site-check git
site-check files
printf 'COMMANDS-DONE\\n'
'''
        poison = {'HOME': '/root', 'SITE_DOMAIN': 'two.example', 'SITE_PHP': '0.1', 'BASH_ENV': '/tmp/attack.sh',
                  'ENV': '/tmp/attack.sh', 'PYTHONPATH': '/tmp/poison', 'ROOT_PASSWORD': 'do-not-inherit',
                  'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'user.name', 'GIT_CONFIG_VALUE_0': 'Root poison'}
        Path('/tmp/attack.sh').write_text('touch /tmp/attack-ran\n')
        code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'], script, env=poison, mask=0o777)
        self.assertEqual(code, 0, out)
        data = json.loads(re.search(r'^SESSION=(\{.*\})\r?$', out, re.M)[1])
        self.assertEqual(data['uid'], [self.account.pw_uid] * 3)
        self.assertEqual(data['gid'], [self.account.pw_gid] * 3)
        valkey_gid = run(['getent', 'group', 'valkey']).stdout.split(':')[2]
        self.assertEqual(set(data['groups']), {self.account.pw_gid, int(valkey_gid)})
        self.assertEqual(data['cwd'], str(self.base / 'htdocs'))
        self.assertNotEqual(data['tty'], terminal.last_tty)
        for key in ('BASH_ENV', 'ENV', 'PYTHONPATH', 'ROOT_PASSWORD', 'SUDO_USER', 'LD_PRELOAD'):
            self.assertNotIn(key, data['env'])
        self.assertEqual(data['env']['SITE_DOMAIN'], 'one.example')
        self.assertIn('PHP 8.4', out)
        self.assertIn('WP-CLI version:', out)
        self.assertIn('Site operator', out)
        self.assertFalse(Path('/tmp/attack-ran').exists())
        self.assertEqual((self.base / 'owner-tmp/customization-uid').read_text().strip(), str(self.account.pw_uid))
        self.assertEqual(os.geteuid(), 0)

    def test_02_private_files_and_no_general_root_access(self):
        code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'], '''cat /srv/sites/two.example/htdocs/private.txt
cat /root/private-test-secret
sudo -n id
/usr/local/sbin/jump-in two.example
printf 'COMMANDS-DONE\\n'
''')
        self.assertEqual(code, 0, out)
        self.assertIn('Permission denied', out)
        self.assertNotIn('uid=0(root)', out)
        self.assertIn('jump-in must run as root', out)

    def test_03_policy_is_site_local_and_narrow(self):
        code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'], '''policy hold local-plugin --reason 'Generated fixture'
policy show
sudo -n /usr/local/sbin/wp-site policy hold two.example local-plugin --reason forbidden
sudo -n /usr/local/sbin/wp-site git one.example status
printf 'COMMANDS-DONE\\n'
''')
        self.assertEqual(code, 0, out)
        self.assertIn('HOLD', out)
        self.assertTrue((self.base / 'conf/code-policy.yml').exists())
        self.assertFalse(Path('/srv/sites/two.example/conf/code-policy.yml').exists())
        self.assertIn('only this site', out)
        self.assertNotIn('uid=0(root)', out)

    def test_04_direct_shell_has_the_same_identity_without_env_site_selection(self):
        valkey = run(['getent', 'group', 'valkey']).stdout.split(':')[2]
        direct = ['/usr/bin/setpriv', '--reuid=' + str(self.account.pw_uid), '--regid=' + str(self.account.pw_gid),
                  '--groups=' + str(self.account.pw_gid) + ',' + valkey, '--inh-caps=-all', '--ambient-caps=-all',
                  '/usr/local/sbin/site-shell']
        code, out = terminal(direct, '''pwd
wp --info
printf 'COMMANDS-DONE\\n'
''', env={'SITE_DOMAIN': 'two.example', 'HOME': '/root'})
        self.assertEqual(code, 0, out)
        self.assertIn(str(self.base / 'htdocs'), out)
        self.assertIn('one.example', out)
        self.assertNotIn('Site: two.example', out)
        self.assertIn('WP-CLI version:', out)

    def test_05_modes_unknown_sites_and_unsafe_paths(self):
        # Non-terminal launch is rejected independently of identity/path checks.
        result = subprocess.run(['/usr/local/sbin/jump-in', 'one.example'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('interactive terminal', result.stderr)
        for domain in ('../one.example', 'missing.example'):
            code, out = terminal(['/usr/local/sbin/jump-in', domain])
            self.assertEqual(code, 2, out)
            self.assertNotIn('needs an interactive terminal', out)
        for args in (['site-shell', '-c', 'id'], ['site-shell', '--login'], ['site-shell']):
            result = subprocess.run(['/usr/local/sbin/' + args[0], *args[1:]], text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        root = self.base / 'htdocs'
        root.rename(self.base / 'saved-htdocs')
        root.symlink_to('/root', target_is_directory=True)
        try:
            code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'])
            self.assertEqual(code, 2, out)
            self.assertIn('directory owned by the site', out)
        finally:
            root.unlink()
            (self.base / 'saved-htdocs').rename(root)

    def test_06_completion_help_and_no_nested_bash(self):
        code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'], '''COMP_WORDS=(policy ch); COMP_CWORD=1; _site_complete; printf 'COMPLETE=%s\\n' "${COMPREPLY[@]}"
COMP_WORDS=(wp core ver); COMP_CWORD=2; _site_complete; printf 'COMPLETE=%s\\n' "${COMPREPLY[@]}"
type _completion_loader
jump-in-help policy
jump-in-help --all
printf 'LEVEL=%s\\n' "$SHLVL"
printf 'COMMANDS-DONE\\n'
''')
        self.assertEqual(code, 0, out)
        self.assertIn('COMPLETE=checksums', out)
        self.assertIn('COMPLETE=verify-checksums', out)
        self.assertIn('allow-file', out)
        self.assertIn('LEVEL=1', out)

    def test_07_exit_eof_and_signals(self):
        for end in ('exit', 'eof', 'term', 'hup'):
            code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'], end=end)
            self.assertIn('Use jump-in-help', out)
            if end in ('exit', 'eof'):
                self.assertEqual(code, 0, out)
            # Signal status belongs to the PTY proxy. Require timely closure,
            # not a platform-specific status, and ensure no owner Bash survives.
            deadline = time.monotonic() + 5
            alive = []
            while time.monotonic() < deadline:
                alive = []
                for process in Path('/proc').iterdir():
                    if not process.name.isdigit():
                        continue
                    try:
                        if process.stat().st_uid != self.account.pw_uid or (process / 'comm').read_text().strip() != 'bash':
                            continue
                        status = (process / 'status').read_text()
                        if not re.search(r'^State:\s+Z', status, re.M):
                            alive.append(process.name)
                    except (FileNotFoundError, ProcessLookupError):
                        pass
                if not alive:
                    break
                time.sleep(.05)
            self.assertEqual(alive, [], out)
        self.assertEqual(os.getuid(), 0)

    def test_08_reinstall_preserves_history_settings_and_nologin(self):
        paths = [self.base / 'owner-tmp/.site-shellrc', self.base / 'owner-tmp/.bash_history']
        before = [p.read_bytes() for p in paths]
        self.assertRegex(install(), r'changed=0\s')
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertEqual(pwd.getpwnam('shell_one').pw_shell, '/usr/sbin/nologin')
        self.assertEqual(pwd.getpwnam('shell_one-php').pw_shell, '/usr/sbin/nologin')
        self.assertEqual((paths[1].stat().st_mode & 0o777), 0o600)
        self.assertEqual((self.base / 'owner-tmp/wp-cli-cache').stat().st_uid, self.account.pw_uid)
        self.assertEqual((self.base / 'owner-tmp/wp-cli-cache').stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.base / 'owner-tmp/wp-cli-cache/existing-cache-item').read_text(), 'keep this cache data')
        runtime = pwd.getpwnam('shell_one-php')
        result = subprocess.run(['/usr/bin/setpriv', '--reuid=' + str(runtime.pw_uid), '--regid=' + str(runtime.pw_gid),
                                 '--groups=' + str(runtime.pw_gid), '/usr/local/sbin/site-php', '-v'], capture_output=True)
        self.assertEqual(result.returncode, 2)
        result = subprocess.run(['/usr/bin/setpriv', '--reuid=' + str(runtime.pw_uid), '--regid=' + str(runtime.pw_gid),
                                 '--groups=' + str(runtime.pw_gid), '/bin/sh', '-c',
                                 'echo forbidden >> /srv/sites/one.example/htdocs/private.txt'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_09_php_branch_comes_from_facts(self):
        fake = Path('/usr/bin/php9.7')
        fake.write_text('#!/bin/sh\nprintf "FIXTURE-PHP-9.7\\n"\n')
        fake.chmod(0o755)
        facts = self.base / 'conf/site.env'
        old = facts.read_text()
        facts.write_text(old.replace('PHP_VERSION=8.4', 'PHP_VERSION=9.7'))
        try:
            code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'], '''php -v
printf 'COMMANDS-DONE\\n'
''', env={'SITE_PHP': '8.4'})
            self.assertEqual(code, 0, out)
            self.assertIn('FIXTURE-PHP-9.7', out)
            self.assertIn('PHP: 9.7', out)
        finally:
            facts.write_text(old)

    def test_10_root_file_descriptors_do_not_reach_the_owner(self):
        descriptor = os.open('/root/private-test-secret', os.O_RDONLY)
        try:
            probe = "import os;\ntry: os.read(" + str(descriptor) + ",1); print('LEAKED-FD')\nexcept OSError: print('CLOSED-FD')"
            import shlex
            code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'],
                                  'python3 -c ' + shlex.quote('exec(' + repr(probe) + ')'), pass_fds=(descriptor,))
            self.assertEqual(code, 0, out)
            self.assertRegex(out, r'(?m)^CLOSED-FD\r?$')
        finally:
            os.close(descriptor)

    def test_11_ctrl_c_stops_the_foreground_command_only(self):
        code, out = terminal(['/usr/local/sbin/jump-in', 'one.example'], end='interrupt')
        self.assertEqual(code, 0, out)
        self.assertIn('INTERRUPTED-SHELL-ALIVE', out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
