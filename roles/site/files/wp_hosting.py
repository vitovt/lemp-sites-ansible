"""Shared, root-owned operations for managed WordPress sites.

SQL imports run as temporary users with access to one scratch database.
Only base tables are supported. Never execute site PHP as root.
"""
import contextlib
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

from hosting_config import constant

SITES = Path('/srv/sites')
RUNTIME = Path('/run')
CACHE_ROOT = Path('/var/cache/nginx/fastcgi')
RESERVED = {'root', 'mysql', 'sys', 'information_schema', 'performance_schema', 'test', 'admin', 'default', 'nobody', 'daemon', 'valkey'}
DOMAIN = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:[a-z]{2,}|xn--[a-z0-9-]+)\Z')
NAME = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')


def run(args, *, data=None, env=None, check=True, timeout=600):
    result = subprocess.run([str(x) for x in args], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, timeout=timeout)
    if check and result.returncode:
        # Do not disclose command arguments, site data or credentials in diagnostics.
        raise RuntimeError(f'{Path(args[0]).name} failed (exit {result.returncode})')
    return result


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest()


def read_env(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        key, sep, raw = line.partition('=')
        if not sep or not re.fullmatch('[A-Z_]+', key):
            raise ValueError('Invalid facts file')
        value = shlex.split(raw)
        if len(value) != 1:
            raise ValueError(f'Invalid value: {key}')
        values[key] = value[0]
    return values


def site(domain):
    if not DOMAIN.fullmatch(domain):
        raise ValueError('Invalid domain')
    base = SITES / domain
    if base.is_symlink() or base.resolve() != base:
        raise ValueError('Site path must not contain symlinks')
    conf = base / 'conf/site.env'
    if conf.is_symlink() or conf.stat().st_uid != 0 or conf.stat().st_mode & 0o022:
        raise ValueError('Site facts must be root-owned and read-only to site users')
    values = read_env(conf)
    for key in ['SITE_USER', 'DB_NAME']:
        if not NAME.fullmatch(values[key]) or values[key] in RESERVED:
            raise ValueError(f'Unsafe {key}')
    if values['DOMAIN'] != domain or not re.fullmatch(r'8\.[0-9]', values['PHP_VERSION']):
        raise ValueError('Invalid site facts')
    values['base'] = base
    values['domain'] = domain
    return values


def wp(s, *args, path=None, check=True):
    try:
        return run(['runuser', '-u', s['SITE_USER'], '--', 'env', f'HOME={s["base"]}/owner-tmp',
        f'WP_CLI_CACHE_DIR={s["base"]}/owner-tmp/wp-cli-cache', 'php' + s['PHP_VERSION'], '/usr/local/bin/wp',
        '--path=' + str(path or s['base'] / 'htdocs'), '--skip-plugins', '--skip-themes', *args], check=check)
    except RuntimeError as exc:
        raise RuntimeError('WP-CLI ' + ' '.join(args[:2]) + ' failed') from exc


def credentials(s):
    text = (s['base'] / 'htdocs/wp-config.php').read_text()
    if constant(text, 'DB_NAME') != s['DB_NAME'] or constant(text, 'DB_USER') != s['DB_NAME']:
        raise ValueError('Database facts and WordPress config do not match')
    if constant(text, 'DB_HOST') not in ('localhost', 'localhost:/run/mysqld/mysqld.sock'):
        raise ValueError('WordPress must use the managed local database')
    return constant(text, 'DB_PASSWORD')


class Database:
    def __init__(self):
        import pymysql
        self.connection = pymysql.connect(unix_socket='/run/mysqld/mysqld.sock', user='root', autocommit=True)

    def query(self, sql, args=()):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, args)
            return cursor.fetchall()

    def tables(self, database):
        rows = self.query('SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME', (database,))
        if any(kind != 'BASE TABLE' for _, kind in rows):
            raise ValueError('Views are not supported by the managed restore workflow')
        for table, field in [('TRIGGERS', 'TRIGGER_SCHEMA'), ('ROUTINES', 'ROUTINE_SCHEMA'), ('EVENTS', 'EVENT_SCHEMA')]:
            if self.query(f'SELECT COUNT(*) FROM information_schema.{table} WHERE {field}=%s', (database,))[0][0]:
                raise ValueError('Triggers, routines and events require a separate migration')
        return [name for name, _ in rows]

    def recreate(self, name):
        if not NAME.fullmatch(name) or name in RESERVED:
            raise ValueError('Unsafe database name')
        self.query(f'DROP DATABASE IF EXISTS `{name}`')
        self.query(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')

    @contextlib.contextmanager
    def scratch(self):
        name = 'wpstage_' + secrets.token_hex(8)
        password = secrets.token_hex(24)
        self.recreate(name)
        try:
            self.query('CREATE USER %s@localhost IDENTIFIED BY %s', (name, password))
            escaped = name.replace('_', r'\_')
            self.query(f'GRANT ALL ON `{escaped}`.* TO %s@localhost', (name,))
            yield name, password
        finally:
            self.query(f'DROP DATABASE IF EXISTS `{name}`')
            self.query('DROP USER IF EXISTS %s@localhost', (name,))


def dump(database, destination, db):
    tables = db.tables(database)
    destination = Path(destination)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    # A database name is an argument, not --databases: no CREATE DATABASE or USE.
    args = ['mariadb-dump', '--single-transaction', '--quick', '--skip-add-locks', '--hex-blob',
            '--skip-triggers', '--skip-routines', '--skip-events', '--default-character-set=utf8mb4', database]
    try:
        with temporary.open('wb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb') as output:
                process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                shutil.copyfileobj(process.stdout, output)
                error = process.stderr.read()
                if process.wait():
                    raise RuntimeError('Database dump failed')
        os.chmod(temporary, 0o600)
        with gzip.open(temporary, 'rb') as check:
            tail = b''
            while data := check.read(1024 * 1024):
                tail = (tail + data)[-4096:]
            if b'-- Dump completed' not in tail:
                raise ValueError('Incomplete database dump')
        os.replace(temporary, destination)
        return tables
    finally:
        temporary.unlink(missing_ok=True)


def load(path, name, user, password):
    # Credentials never appear in argv or an environment variable.
    with tempfile.TemporaryDirectory(prefix='wp-import-', dir=RUNTIME) as temporary:
        config = Path(temporary) / 'client.cnf'
        config.write_text(f'[client]\nuser={user}\npassword={password}\nprotocol=socket\nsocket=/run/mysqld/mysqld.sock\n')
        config.chmod(0o600)
        with gzip.open(path, 'rb') as source, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(['mariadb', '--defaults-extra-file=' + str(config), '--binary-mode', '--local-infile=0', name],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                shutil.copyfileobj(source, process.stdin)
                process.stdin.close()
            except BaseException:
                process.kill()
                process.wait()
                raise
            if process.wait():
                raise RuntimeError('Scoped SQL import failed; live data was not accepted')


@contextlib.contextmanager
def operation_lock(timeout=3600):
    path = RUNTIME / 'lock/wp-hosting.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'a') as lock:
        info = os.fstat(lock.fileno())
        if info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise ValueError('Unsafe operation lock ownership or link')
        os.fchmod(lock.fileno(), 0o644)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another hosting operation holds the lock')
                time.sleep(.1)
        yield


@contextlib.contextmanager
def maintenance(s, release=True):
    marker = s['base'] / 'conf/maintenance'
    if marker.exists():
        raise RuntimeError('Site is already in maintenance; inspect its recovery files')
    marker.write_text('managed operation\n')
    marker.chmod(0o644)
    # Stop site runtime processes after blocking new HTTP and cron work.
    # FPM ondemand starts new workers after maintenance is removed.
    try:
        uid = pwd.getpwnam(s['SITE_USER'] + '-php').pw_uid
        run(['pkill', '-TERM', '-u', str(uid)], check=False)
        for _ in range(50):
            if run(['pgrep', '-u', str(uid)], check=False).returncode:
                break
            time.sleep(.1)
        else:
            run(['pkill', '-KILL', '-u', str(uid)], check=False)
        yield
    except BaseException:
        # The caller clears this only after a successful rollback.
        raise
    else:
        if release:
            marker.unlink()


def require_wordpress_tables(tables, prefix):
    required = {prefix + x for x in ['options', 'posts', 'postmeta', 'users', 'usermeta', 'terms',
        'termmeta', 'term_taxonomy', 'term_relationships', 'comments', 'commentmeta', 'links']}
    if not required.issubset(tables):
        raise ValueError('The database does not contain all required WordPress tables')


def prefix(s):
    text = (s['base'] / 'htdocs/wp-config.php').read_text()
    match = re.search(r"\$table_prefix\s*=\s*['\"]([a-zA-Z0-9_]+)['\"]\s*;", text)
    if not match:
        raise ValueError('Require a static table prefix')
    return match[1]


def backup_site(s, db):
    password = credentials(s)
    path = s['base'] / 'backup/db.sql.gz'
    tables = dump(s['DB_NAME'], path, db)
    installed = (s['base'] / 'conf/wordpress-installed').exists()
    if installed:
        require_wordpress_tables(tables, prefix(s))
    files_path = s['base'] / 'backup/files.jsonl'
    with files_path.open('w') as stream:
        for item in (s['base'] / 'htdocs').rglob('*'):
            relative = str(item.relative_to(s['base'] / 'htdocs'))
            if item.is_file() and not item.is_symlink() and not relative.startswith('wp-content/cache/'):
                stream.write(json.dumps(relative) + '\n')
    manifest = dict(tables=tables, installed=installed, prefix=prefix(s),
                    files_sha256=sha256_file(files_path), sha256=sha256_file(path))
    (s['base'] / 'backup/manifest.json').write_text(json.dumps(manifest))
    return path


def flush_cache(s):
    # Do not depend on an optional WordPress plugin to remove stale data.
    if s.get('REDIS') == 'true':
        env = {**os.environ, 'REDISCLI_AUTH': Path('/root/.valkey-admin.pass').read_text().strip()}
        script = 'local c="0" repeat local r=redis.call("SCAN",c,"MATCH",ARGV[1],"COUNT",1000) c=r[1] for _,k in ipairs(r[2]) do redis.call("UNLINK",k) end until c=="0" return 1'
        result = run(['valkey-cli', '-s', '/run/valkey/valkey-server.sock', '--user', 'admin',
                      'EVAL', script, '0', s['SITE_USER'] + ':*'], env=env)
        if result.stdout.strip() != b'1':
            raise RuntimeError('Valkey did not confirm the prefix flush')
    cache = CACHE_ROOT
    if cache.exists():
        descriptor = os.open(cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for _, _, files, directory_fd in os.fwalk('.', dir_fd=descriptor, follow_symlinks=False):
                for name in files:
                    try:
                        os.unlink(name, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass  # The nginx cache manager can remove the same entry.
        finally:
            os.close(descriptor)



def restore_database(s, path, db, manifest=None):
    password = credentials(s)
    with db.scratch() as (stage, stage_password):
        load(path, stage, stage, stage_password)
        tables = db.tables(stage)
        if manifest is not None and tables != manifest['tables']:
            raise ValueError('The restored table list does not match the backup manifest')
        if (s['base'] / 'conf/wordpress-installed').exists():
            require_wordpress_tables(tables, prefix(s))
        with tempfile.TemporaryDirectory(prefix='wp-restore-', dir=RUNTIME) as temporary:
            canonical = Path(temporary) / 'staged.sql.gz'
            dump(stage, canonical, db)
            with maintenance(s):
                before = s['base'] / 'backup' / ('db-before-restore-' + str(time.time_ns()) + '.sql.gz')
                dump(s['DB_NAME'], before, db)
                try:
                    db.recreate(s['DB_NAME'])
                    load(canonical, s['DB_NAME'], s['DB_NAME'], password)
                    if db.tables(s['DB_NAME']) != tables:
                        raise RuntimeError('Restored schema differs from the staged schema')
                    flush_cache(s)
                except BaseException:
                    db.recreate(s['DB_NAME'])
                    load(before, s['DB_NAME'], s['DB_NAME'], password)
                    flush_cache(s)
                    (s['base'] / 'conf/maintenance').unlink(missing_ok=True)
                    raise


def clone(source, target, db, *, confirm=False, activate=False):
    if source['domain'] == target['domain']:
        raise ValueError('Source and target must differ')
    if (target['base'] / 'htdocs/wp-includes/version.php').exists() and not confirm:
        raise ValueError('An installed target requires explicit confirmation')
    # No source filesystem link can escape into another site during a copy.
    for path in (source['base'] / 'htdocs').rglob('*'):
        if path.is_symlink():
            raise ValueError('Clone source contains a symlink; review it first')
    source_prefix = prefix(source)
    target_password = credentials(target)
    stamp = str(time.time_ns())
    recovery = target['base'] / ('backup/clone-' + stamp)
    recovery.mkdir(mode=0o700)
    with maintenance(source), maintenance(target, release=activate):
        backup_site(source, db)
        before = backup_site(target, db)
        shutil.copy2(before, recovery / 'before.sql.gz')
        staged = target['base'] / ('clone-stage-' + stamp)
        old = target['base'] / ('clone-old-' + stamp)
        switched = False
        try:
            run(['rsync', '-rlt', '--exclude=/wp-config.php', '--exclude=/.git/', '--exclude=/wp-content/cache/',
                 str(source['base'] / 'htdocs') + '/', str(staged) + '/'])
            shutil.copy2(target['base'] / 'htdocs/wp-config.php', staged / 'wp-config.php')
            # Stage code is private until its ownership and configuration are set.
            run(['chown', '-R', target['SITE_USER'] + ':' + target['SITE_USER'], str(staged)])
            run(['chmod', '0750', str(staged)])
            wp(target, 'config', 'set', 'table_prefix', source_prefix, path=staged)
            wp(target, 'config', 'set', 'WP_ENVIRONMENT_TYPE', 'production' if activate else 'staging', '--anchor=require_once', path=staged)
            with db.scratch() as (stage_db, stage_password):
                load(source['base'] / 'backup/db.sql.gz', stage_db, stage_db, stage_password)
                require_wordpress_tables(db.tables(stage_db), source_prefix)
                canonical = recovery / 'source.sql.gz'
                dump(stage_db, canonical, db)
                db.recreate(target['DB_NAME'])
                load(canonical, target['DB_NAME'], target['DB_NAME'], target_password)
            os.rename(target['base'] / 'htdocs', old)
            os.rename(staged, target['base'] / 'htdocs')
            switched = True
            run(['/usr/local/sbin/site-fix-perms', target['domain']])
            for a, b in [('//', '//'), ('\\/\\/', '\\/\\/')]:
                wp(target, 'search-replace', a + source['domain'], b + target['domain'], '--all-tables-with-prefix', '--skip-columns=guid')
            if not activate:
                wp(target, 'option', 'update', 'blog_public', '0')
            run(['/usr/local/sbin/wp-git-commit', target['domain'], 'clone of ' + source['domain']])
            (target['base'] / 'conf/wordpress-installed').write_text('installed\n')
            # Keep the original files with the rollback SQL, readable by root only.
            flush_cache(target)
            os.rename(old, recovery / 'htdocs')
        except BaseException:
            db.recreate(target['DB_NAME'])
            load(recovery / 'before.sql.gz', target['DB_NAME'], target['DB_NAME'], target_password)
            if switched:
                shutil.rmtree(target['base'] / 'htdocs')
                os.rename(old, target['base'] / 'htdocs')
            shutil.rmtree(staged, ignore_errors=True)
            flush_cache(target)
            (source['base'] / 'conf/maintenance').unlink(missing_ok=True)
            (target['base'] / 'conf/maintenance').unlink(missing_ok=True)
            raise
    if not activate:
        # Require a deliberate activation. This also blocks integrations and cron.
        (target['base'] / 'conf/maintenance').write_text('staging clone: review integrations before activation\n')
        (target['base'] / 'conf/maintenance').chmod(0o644)
    print(f'clone recovery: {recovery}')


def remove_site(s, db, archive_dir):
    """Remove one site only after its caller made a complete restic backup."""
    import tarfile
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive = archive_dir / (s['domain'] + '-' + str(time.time_ns()) + '.tar.gz')
    with maintenance(s):
        with tarfile.open(archive, 'w:gz') as tar:
            def include(member):
                relative = member.name.partition('/')[2]
                if relative == 'tmp' or relative.startswith('tmp/') or relative == 'htdocs/wp-content/cache' or relative.startswith('htdocs/wp-content/cache/'):
                    return None
                return member
            tar.add(s['base'], arcname=s['domain'], filter=include)
        with tarfile.open(archive) as tar:
            if s['domain'] + '/backup/manifest.json' not in tar.getnames():
                raise RuntimeError('The removal archive is incomplete')
        for folder in ['sites-enabled', 'sites-available']:
            (Path('/etc/nginx') / folder / (s['domain'] + '.conf')).unlink(missing_ok=True)
        run(['nginx', '-t', '-q'])
        run(['systemctl', 'reload', 'nginx'])
        for pool in Path('/etc/php').glob('*/fpm/pool.d/' + s['SITE_USER'] + '.conf'):
            version = pool.parents[2].name
            pool.unlink()
            if list(pool.parent.glob('*.conf')):
                run(['systemctl', 'reload', 'php' + version + '-fpm'])
            else:
                run(['systemctl', 'disable', '--now', 'php' + version + '-fpm'])
        for filename in ['/etc/cron.d/wp-' + s['SITE_USER'], '/etc/logrotate.d/wp-' + s['SITE_USER'], '/var/lib/wp-monitor/' + s['domain'] + '.admins']:
            Path(filename).unlink(missing_ok=True)
        hosts = Path('/etc/hosts')
        hosts.write_text(''.join(line for line in hosts.read_text().splitlines(True) if not line.rstrip().endswith('# wp-site ' + s['domain'])))
        db.query(f'DROP DATABASE `{s["DB_NAME"]}`')
        db.query('DROP USER IF EXISTS %s@localhost', (s['DB_NAME'],))
        acl = Path('/etc/valkey/users.acl')
        acl.write_text(''.join(line for line in acl.read_text().splitlines(True) if not line.startswith('user ' + s['SITE_USER'] + ' ')))
        env = {**os.environ, 'REDISCLI_AUTH': Path('/root/.valkey-admin.pass').read_text().strip()}
        run(['valkey-cli', '-s', '/run/valkey/valkey-server.sock', '--user', 'admin', 'ACL', 'LOAD'], env=env)
        if (Path('/etc/letsencrypt/renewal') / (s['domain'] + '.conf')).exists():
            run(['certbot', 'delete', '--non-interactive', '--cert-name', s['domain']])
        for user in [s['SITE_USER'] + '-php', s['SITE_USER']]:
            run(['userdel', user])
        run(['groupdel', s['SITE_USER']])
    shutil.rmtree(s['base'])
    print('removed archive: ' + str(archive))


def update_site(s, db):
    """Update only reviewed code. Keep a local rollback copy and validate health."""
    git = ['runuser', '-u', s['SITE_USER'], '--', 'git', '-C', str(s['base'] / 'htdocs')]
    if run(git + ['status', '--porcelain']).stdout:
        raise ValueError('Review the uncommitted code before updates')
    wp(s, 'core', 'verify-checksums')
    wp(s, 'plugin', 'verify-checksums', '--all', '--strict')
    recovery = s['base'] / ('backup/update-' + str(time.time_ns()))
    recovery.mkdir(mode=0o700)
    password = credentials(s)
    with maintenance(s):
        shutil.copytree(s['base'] / 'htdocs', recovery / 'htdocs', symlinks=True)
        dump(s['DB_NAME'], recovery / 'before.sql.gz', db)
        try:
            wp(s, 'core', 'update', '--minor')
            wp(s, 'core', 'update-db')
            wp(s, 'plugin', 'update', '--all')
            wp(s, 'theme', 'update', '--all')
            wp(s, 'core', 'verify-checksums')
            wp(s, 'plugin', 'verify-checksums', '--all', '--strict')
            run(['/usr/local/sbin/site-fix-perms', s['domain']])
            flush_cache(s)
            status = run(['curl', '--noproxy', '*', '-sk', '--max-time', '20', '-o', '/dev/null', '-w', '%{http_code}',
                '--resolve', s['domain'] + ':443:127.0.0.1', '-H', 'X-WP-Health: 1',
                'https://' + s['domain'] + '/?wp-update-health=1']).stdout
            if status != b'200':
                raise RuntimeError('Updated site failed its uncached HTTP health check')
            run(['/usr/local/sbin/wp-git-commit', s['domain'], 'checked WordPress updates'])
            run(['/usr/local/sbin/site-fix-perms', s['domain']])
        except BaseException:
            shutil.rmtree(s['base'] / 'htdocs')
            shutil.copytree(recovery / 'htdocs', s['base'] / 'htdocs', symlinks=True)
            db.recreate(s['DB_NAME'])
            load(recovery / 'before.sql.gz', s['DB_NAME'], s['DB_NAME'], password)
            run(['/usr/local/sbin/site-fix-perms', s['domain']])
            flush_cache(s)
            (s['base'] / 'conf/maintenance').unlink(missing_ok=True)
            raise
    print('PASS checked updates; recovery: ' + str(recovery))
