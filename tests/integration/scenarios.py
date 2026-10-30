#!/usr/bin/env python3
"""Destructive regressions. Run only on the VM marked by run-integration.py."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

TOKEN = sys.argv.pop(1)
assert os.geteuid() == 0
assert Path('/etc/wp-hosting-test-id').read_text().strip() == TOKEN
assert sorted(p.parent.parent.name for p in Path('/srv/sites').glob('*/conf/site.env')) == ['test1.example', 'test2.example']
assert not Path('/etc/tg-alert.env').exists()
assert not Path('/etc/letsencrypt/cloudflare.ini').exists()
sys.path.insert(0, '/usr/local/lib/wp-hosting')
from wp_hosting import Database, load, site, wp

A, B = site('test1.example'), site('test2.example')


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=900,
                            env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG':'C.UTF-8'})
    if check and result.returncode:
        raise AssertionError(f'{args[0]} failed: {result.stdout[-1500:]} {result.stderr[-1500:]}')
    return result


def write_site(s, relative, text):
    path = s['base'] / 'htdocs' / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    account = pwd.getpwnam(s['SITE_USER'])
    os.chown(path, account.pw_uid, account.pw_gid)
    path.chmod(0o640)
    return path


def request(domain, uri='/', *headers, ipv6=False, http=False):
    address = '[::1]' if ipv6 else '127.0.0.1'
    port = '80' if http else '443'
    return run('curl', '--noproxy', '*', '-sk', '--max-time', '20', '-D', '-', '--resolve',
        f'{domain}:{port}:{address}', *headers, f'{"http" if http else "https"}://{domain}{uri}').stdout


class Scenarios(unittest.TestCase):
    def test_01_services_and_ipv6(self):
        for service in ['nginx', 'mariadb', 'valkey-server', 'php8.3-fpm', 'php8.4-fpm']:
            run('systemctl', 'is-active', '--quiet', service)
        run('nginx', '-t', '-q')
        self.assertIn('200', request(A['domain'], ipv6=True).splitlines()[0])
        self.assertNotIn(':3306', run('ss', '-ltn').stdout)
        for line in run('ss', '-H', '-ltn').stdout.splitlines():
            self.assertNotIn(line.split()[3].rsplit(':', 1)[-1], ['25', '465', '587', '2525'])
        for s in [A, B]:
            # A new installation contains only the declared administrator.
            admins = wp(s, 'user', 'list', '--role=administrator', '--field=user_login').stdout.decode().strip()
            self.assertEqual(admins, 'wpadmin')
            run('wp-monitor', '--ack', s['domain'])

    def test_02_private_and_public_cache(self):
        private = write_site(A, 'private-probe.php', '<?php header("Cache-Control: private, no-store"); setcookie("probe", bin2hex(random_bytes(8))); echo bin2hex(random_bytes(8));')
        public = write_site(A, 'public-probe.php', '<?php echo bin2hex(random_bytes(8));')
        try:
            first = request(A['domain'], '/private-probe.php')
            second = request(A['domain'], '/private-probe.php')
            self.assertNotIn('x-cache: hit', second.lower())
            self.assertNotEqual(first.split('probe=')[1].split(';')[0], second.split('probe=')[1].split(';')[0])
            request(A['domain'], '/public-probe.php')
            self.assertIn('x-cache: hit', request(A['domain'], '/public-probe.php').lower())
            self.assertIn('x-cache: bypass', request(A['domain'], '/public-probe.php', '-H', 'Cookie: custom_session=1').lower())
            self.assertIn('x-cache: bypass', request(A['domain'], '/public-probe.php', '-H', 'Authorization: Bearer test').lower())
            self.assertIn('x-cache: bypass', request(A['domain'], '/public-probe.php?private=1').lower())
        finally:
            private.unlink()
            public.unlink()

    def test_03_filesystem_socket_and_cache_isolation(self):
        runtime = A['SITE_USER'] + '-php'
        for path in [B['base'] / 'htdocs/wp-config.php', Path('/etc/restic/password')]:
            self.assertNotEqual(run('runuser', '-u', runtime, '--', 'cat', str(path), check=False).returncode, 0)
        self.assertNotEqual(run('runuser', '-u', runtime, '--', 'touch', str(A['base'] / 'htdocs/runtime-write'), check=False).returncode, 0)
        writable = A['base'] / 'htdocs/wp-content/uploads/runtime-write'
        run('runuser', '-u', runtime, '--', 'touch', str(writable))
        writable.unlink()
        link = A['base'] / 'htdocs/wp-content/uploads/leak.txt'
        link.symlink_to(B['base'] / 'htdocs/wp-config.php')
        try:
            response = request(A['domain'], '/wp-content/uploads/leak.txt')
            self.assertNotIn('DB_PASSWORD', response)
            self.assertNotIn('200', response.splitlines()[0])
        finally:
            link.unlink()
        socket_probe = 'import socket; s=socket.socket(socket.AF_UNIX); s.connect(' + repr('/run/php/' + B['SITE_USER'] + '.sock') + ')'
        self.assertNotEqual(run('runuser', '-u', runtime, '--', 'python3', '-c', socket_probe, check=False).returncode, 0)
        script = '''$r=new Redis(); $r->connect(WP_REDIS_PATH); $r->auth(WP_REDIS_PASSWORD);
        foreach ([['get','test2_example:foreign'], ['flushAll'], ['config','GET','*']] as $args) {
          try { $result = call_user_func_array([$r,array_shift($args)],$args); echo ($result === false && str_contains((string)$r->getLastError(), "NOPERM")) ? "DENIED\\n" : "ALLOWED\\n"; }
          catch (RedisException $e) { echo "DENIED\\n"; }
        }'''
        self.assertEqual(wp(A, 'eval', script).stdout.decode().count('DENIED'), 3)
        marker = B['base'] / 'conf/maintenance'
        self.assertFalse(marker.exists())
        marker.write_text('private health transport regression\n')
        marker.chmod(0o644)
        try:
            self.assertIn('503', request(B['domain'], '/?tcp-health=1', '-H', 'X-WP-Health: 1').splitlines()[0])
            command = ['curl', '-q', '--noproxy', '*', '-sk', '--max-time', '10', '--unix-socket',
                '/run/wp-hosting/health.sock', '-H', 'X-WP-Health: 1', '-o', '/dev/null', '-w', '%{http_code}',
                'https://' + B['domain'] + '/?private-health=1']
            self.assertNotEqual(run('runuser', '-u', runtime, '--', *command, check=False).returncode, 0)
            self.assertEqual(run(*command).stdout, '200')
        finally:
            marker.unlink()

    def test_04_http_challenge_and_rate_limit(self):
        challenge = Path('/var/lib/letsencrypt/.well-known/acme-challenge/regression')
        challenge.parent.mkdir(parents=True, exist_ok=True)
        challenge.write_text('challenge-sentinel')
        challenge.chmod(0o644)
        try:
            self.assertIn('challenge-sentinel', request(A['domain'], '/.well-known/acme-challenge/regression', http=True))
            self.assertIn('301', request(A['domain'], '/path', http=True).splitlines()[0])
        finally:
            challenge.unlink()
        responses = [request(A['domain'], '/wp-login.php').splitlines()[0] for _ in range(8)]
        self.assertTrue(any('429' in line for line in responses), responses)
        rules = json.loads(run('nft', '-j', 'list', 'table', 'inet', 'wp_hosting').stdout)
        self.assertIn('587', json.dumps(rules))
        # A listening local socket distinguishes firewall rejection from no service.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 587))
            listener.listen()
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', 587), timeout=2)

    def test_04b_untrusted_ipv6_network_client(self):
        run('python3', str(Path(__file__).with_name('network.py')), TOKEN)

    def test_04c_service_failures_are_reported(self):
        service = 'php' + B['PHP_VERSION'] + '-fpm'
        run('systemctl', 'stop', service)
        try:
            result = run('wp-monitor', '--check', B['domain'], check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn('FAIL ' + B['domain'] + ' fpm_pool', result.stdout)
        finally:
            run('systemctl', 'start', service)
        run('systemctl', 'stop', 'valkey-server')
        try:
            result = run('wp-monitor', '--check', check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn('FAIL host host_services', result.stdout)
            self.assertIn('FAIL ' + A['domain'] + ' valkey', result.stdout)
            self.assertIn('200', request(A['domain'], '/?without-cache=1').splitlines()[0])
        finally:
            run('systemctl', 'start', 'valkey-server')

    def test_05_integrity_and_persistent_admin_alert(self):
        version = A['base'] / 'htdocs/wp-includes/version.php'
        temporary = version.with_suffix('.regression')
        version.rename(temporary)
        try:
            result = run('wp-monitor', '--check', A['domain'], check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn('FAIL ' + A['domain'] + ' wordpress', result.stdout)
        finally:
            temporary.rename(version)
        wp(A, 'plugin', 'install', 'hello-dolly')
        try:
            result = run('wp-monitor', '--check', A['domain'], check=False)
            self.assertIn('PASS ' + A['domain'] + ' plugin_checksums', result.stdout)
            plugin = A['base'] / 'htdocs/wp-content/plugins/hello-dolly/hello.php'
            with plugin.open('a') as stream:
                stream.write('\n// regression mutation\n')
            result = run('wp-monitor', '--check', A['domain'], check=False)
            self.assertIn('FAIL ' + A['domain'] + ' plugin_checksums', result.stdout)
        finally:
            wp(A, 'plugin', 'delete', 'hello-dolly')
        baseline = Path('/var/lib/wp-monitor/' + A['domain'] + '.admins')
        original = baseline.read_bytes()
        uid = wp(A, 'user', 'create', 'regression_admin', 'regression@example.invalid', '--role=administrator', '--porcelain').stdout.decode().strip()
        try:
            for _ in range(2):
                result = run('wp-monitor', A['domain'], check=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn('admin_users', result.stdout)
                self.assertEqual(baseline.read_bytes(), original)
        finally:
            wp(A, 'user', 'delete', uid, '--yes')

    def test_06_backup_restore_exact_schema_and_scoped_sql(self):
        run('wp-backup')
        run('wp-backup', 'verify')
        restored = run('wp-backup', 'restore', A['domain']).stdout
        dump = Path(next(line[6:] for line in restored.splitlines() if line.startswith('dump: ')))
        self.assertNotEqual(run('wp-backup', 'restore-db', A['domain'], str(dump), check=False).returncode, 0)
        database = Database()
        # Site-controlled credentials must not inject root client options.
        with database.scratch() as (name, _):
            password = 'test\nuser=root\npassword=\n#"\\value\t'
            database.query('ALTER USER %s@localhost IDENTIFIED BY %s', (name, password))
            with tempfile.TemporaryDirectory(prefix='credential-sql-') as directory:
                sql = Path(directory) / 'data.sql.gz'
                with gzip.open(sql, 'wb') as stream:
                    stream.write(b'CREATE TABLE credential_probe (id int);\n')
                load(sql, name, name, password)
                self.assertIn('credential_probe', database.tables(name))
                with gzip.open(sql, 'wb') as stream:
                    stream.write(b'USE mysql; SELECT COUNT(*) FROM user;\n')
                # A root client config must not override the restricted account.
                ambient = Path('/root/.my.cnf')
                self.assertFalse(ambient.exists(), 'Unexpected root client config in the isolated fixture')
                try:
                    ambient.write_text('[client]\nuser=root\nprotocol=socket\n')
                    ambient.chmod(0o600)
                    with self.assertRaises(RuntimeError):
                        load(sql, name, name, password)
                finally:
                    ambient.unlink()
        database.query('CREATE TABLE `test1_example`.`wp_regression_orphan` (id int)')
        marker = wp(A, 'post', 'create', '--post_title=restore-regression', '--post_status=publish', '--porcelain').stdout.decode().strip()
        run('wp-backup', 'restore-db', A['domain'], str(dump), '--yes')
        self.assertNotIn('wp_regression_orphan', database.tables(A['DB_NAME']))
        self.assertNotEqual(wp(A, 'post', 'get', marker, check=False).returncode, 0)
        manifest = json.loads((dump.parent / 'manifest.json').read_text())
        with tempfile.TemporaryDirectory(prefix='bad-sql-') as directory:
            bad = Path(directory) / 'db.sql.gz'
            with gzip.open(bad, 'wb') as stream:
                stream.write(b'USE mysql; DROP TABLE user;\n-- Dump completed\n')
            manifest['sha256'] = hashlib.sha256(bad.read_bytes()).hexdigest()
            (bad.parent / 'manifest.json').write_text(json.dumps(manifest))
            before = database.tables(A['DB_NAME'])
            self.assertNotEqual(run('wp-backup', 'restore-db', A['domain'], str(bad), '--yes', check=False).returncode, 0)
            self.assertEqual(database.tables(A['DB_NAME']), before)
            self.assertTrue(database.query("SELECT COUNT(*) FROM mysql.user")[0][0] > 0)
        self.assertNotEqual(run('wp-backup', 'verify', 'absent.example', check=False).returncode, 0)

    def test_07_clone_confirmation_rollback_and_staging(self):
        wp(A, 'option', 'update', 'blogname', 'source-clone-sentinel')
        wp(B, 'option', 'update', 'blogname', 'original-target')
        request(B['domain'])  # Prime the old target page cache before clone.
        self.assertNotEqual(run('wp-backup', 'clone', A['domain'], B['domain'], check=False).returncode, 0)
        self.assertEqual(wp(B, 'option', 'get', 'blogname').stdout.decode().strip(), 'original-target')
        bad = write_site(A, 'wp-content/mu-plugins/regression.php', '<?php if (defined("WP_CLI") && WP_CLI) { exit(91); }')
        bad.parent.chmod(0o2750)
        account = pwd.getpwnam(A['SITE_USER'])
        os.chown(bad.parent, account.pw_uid, account.pw_gid)
        try:
            self.assertNotEqual(run('wp-backup', 'clone', A['domain'], B['domain'], '--yes', check=False).returncode, 0)
            self.assertEqual(wp(B, 'option', 'get', 'blogname').stdout.decode().strip(), 'original-target')
            self.assertFalse((B['base'] / 'conf/maintenance').exists())
        finally:
            bad.unlink()
            bad.parent.rmdir()
        run('wp-backup', 'clone', A['domain'], B['domain'], '--yes')
        self.assertEqual(wp(B, 'option', 'get', 'home').stdout.decode().strip(), 'https://' + B['domain'])
        self.assertEqual(wp(A, 'option', 'get', 'home').stdout.decode().strip(), 'https://' + A['domain'])
        self.assertIn('503', request(B['domain']).splitlines()[0])
        self.assertIn('200', request(A['domain']).splitlines()[0])
        self.assertTrue(list((B['base'] / 'backup').glob('clone-*/before.sql.gz')))
        # Explicit activation after review. No integration secrets exist in this fixture.
        wp(B, 'config', 'set', 'WP_ENVIRONMENT_TYPE', 'production')
        (B['base'] / 'conf/maintenance').unlink()
        run('wp-git-commit', B['domain'], 'reviewed integration clone')
        run('site-fix-perms', B['domain'])
        self.assertIn('200', request(B['domain']).splitlines()[0])
        self.assertIn('source-clone-sentinel', request(B['domain']))
        self.assertNotIn('original-target', request(B['domain']))

    def test_08_final_health(self):
        run('wp-backup', 'update', A['domain'], '--yes')
        for s in [A, B]:
            run('site-fix-perms', s['domain'], '--check')
            result = run('wp-monitor', '--check', s['domain'])
            self.assertNotIn('FAIL ', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
