#!/usr/bin/python3
"""Stage a site FPM pool, switch Nginx, and retain a working rollback route."""

from ansible.module_utils.basic import AnsibleModule
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile
import time


VERSION = re.compile(r"[1-9][0-9]*\.[0-9]+\Z")
USER = re.compile(r"[a-z][a-z0-9_]*\Z")
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]+\Z")
PHP_ROOT = Path('/etc/php')
FPM_BIN_ROOT = Path('/usr/sbin')
NGINX_AVAILABLE = Path('/etc/nginx/sites-available')
NGINX_ENABLED = Path('/etc/nginx/sites-enabled')
SOCKET_ROOT = Path('/run/php')
LOCK_PATH = Path('/run/lock/wp-hosting.lock')
HEALTH_SOCKET = Path('/run/wp-hosting/health.sock')


def run(argv, *, env=None):
    result = subprocess.run(argv, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed: {(result.stderr or result.stdout)[-500:]}")
    return result.stdout.strip()


def replace(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        out.write(content)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def current(path):
    return path.read_bytes() if path.exists() else None


def restore(path, data, mode=0o644):
    if data is None:
        path.unlink(missing_ok=True)
    else:
        replace(path, data, mode)


def pool_paths(user):
    return sorted(set(PHP_ROOT.glob(f'*/fpm/pool.d/{user}.conf')) |
                  set(PHP_ROOT.glob(f'*/fpm/pool.d/{user}-wp.conf')))


def service_ready(version, socket):
    if not socket.is_socket():
        return False
    for action in ('is-active', 'is-enabled'):
        if subprocess.run(['systemctl', action, '--quiet', f'php{version}-fpm'],
                          capture_output=True).returncode:
            return False
    return True


def php_probe(socket, user, version, site):
    directory = site / 'tmp'
    descriptor, name = tempfile.mkstemp(prefix='.php-switch-', suffix='.php', dir=directory)
    path = Path(name)
    try:
        try:
            os.write(descriptor, b'<?php header("Content-Type: text/plain"); echo PHP_MAJOR_VERSION,".",PHP_MINOR_VERSION;')
            os.fchmod(descriptor, 0o640)
            account = pwd.getpwnam(user + '-php')
            os.fchown(descriptor, account.pw_uid, account.pw_gid)
        finally:
            os.close(descriptor)
        env = {**os.environ, 'SCRIPT_FILENAME': str(path), 'SCRIPT_NAME': '/' + path.name,
               'REQUEST_METHOD': 'GET', 'CONTENT_LENGTH': '0', 'SERVER_PROTOCOL': 'HTTP/1.1'}
        output = run(['cgi-fcgi', '-bind', '-connect', str(socket)], env=env)
        if not re.search(r'(?:\r?\n\r?\n)' + re.escape(version) + r'\s*\Z', output):
            raise RuntimeError(f'Target FPM socket did not report PHP {version}')
    finally:
        path.unlink(missing_ok=True)


def wait_for_php(socket, user, version, site):
    deadline = time.monotonic() + 10
    while True:
        try:
            php_probe(socket, user, version, site)
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def nginx_probe(domain, site):
    if (not (site / 'htdocs/wp-includes/version.php').is_file() or
            (site / 'conf/install-in-progress').exists()):
        return
    status = run(['curl', '--noproxy', '*', '--silent', '--show-error', '--insecure',
                  '--max-time', '20', '--unix-socket', str(HEALTH_SOCKET),
                  '-H', 'X-WP-Health: 1', '-H', 'Cache-Control: no-cache',
                  '-o', '/dev/null', '-w', '%{http_code}',
                  f'https://{domain}/?php-switch=1'])
    if status != '200':
        raise RuntimeError(f'WordPress health request returned HTTP {status}')


def converge(domain, user, version, site, pool_content, vhost_content, env_content):
    if not DOMAIN.fullmatch(domain) or not USER.fullmatch(user) or not VERSION.fullmatch(version):
        raise ValueError('Invalid site name, user, or PHP branch')
    site = Path(site)
    pool = PHP_ROOT / version / 'fpm/pool.d' / f'{user}-wp.conf'
    vhost = NGINX_AVAILABLE / f'{domain}.conf'
    link = NGINX_ENABLED / f'{domain}.conf'
    site_env = site / 'conf/site.env'
    marker = site / 'conf/php-switch.json'
    socket = SOCKET_ROOT / f'{user}-php{version}.sock'
    if not pool.parent.is_dir() or not (FPM_BIN_ROOT / f'php-fpm{version}').is_file():
        raise RuntimeError(f'PHP {version} FPM is not installed; run the full site playbook')
    if any(path.is_symlink() for path in (pool, vhost, site_env, marker)):
        raise RuntimeError('Refuse a symbolic link at a managed PHP or Nginx path')
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or link.resolve() != vhost:
            raise RuntimeError('Refuse an unmanaged Nginx site link')
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'a+b') as handle:
        info = os.fstat(handle.fileno())
        if info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise RuntimeError('Unsafe shared operation lock')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another site operation holds the shared lock') from exc
        old_pool = current(pool)
        old_vhost = current(vhost)
        old_env = current(site_env)
        old_marker = current(marker)
        old_link = link.is_symlink()
        target_ready = service_ready(version, socket)
        previous = [p for p in pool_paths(user) if p != pool]
        pending_versions = {path.parents[2].name for path in previous}
        if marker.exists():
            saved = json.loads(marker.read_text())
            if saved.get('domain') != domain or saved.get('version') != version:
                raise RuntimeError('Finish the recorded PHP switch before changing the requested branch')
            pending_versions.update(saved.get('old_versions', []))
        if not all(VERSION.fullmatch(value) for value in pending_versions):
            raise RuntimeError('Invalid PHP switch recovery state')
        wanted_pool = pool_content.encode()
        wanted_vhost = vhost_content.encode()
        wanted_env = env_content.encode()
        changed = (old_pool != wanted_pool or old_vhost != wanted_vhost or
                   old_env != wanted_env or not old_link or not target_ready or
                   bool(previous) or marker.exists())
        if not changed:
            return False
        replace(marker, json.dumps({'domain': domain, 'version': version,
                                    'old_versions': sorted(pending_versions)}).encode(), 0o600)
        switched = False
        try:
            if old_pool != wanted_pool:
                replace(pool, wanted_pool)
            run([f'php-fpm{version}', '-t'])
            if old_pool != wanted_pool or not target_ready:
                run(['systemctl', 'enable', '--now', f'php{version}-fpm'])
                run(['systemctl', 'reload', f'php{version}-fpm'])
            wait_for_php(socket, user, version, site)
            if old_vhost != wanted_vhost:
                replace(vhost, wanted_vhost)
            if not old_link:
                link.symlink_to(vhost)
            run(['nginx', '-t', '-q'])
            if old_vhost != wanted_vhost or not old_link or marker.exists():
                run(['systemctl', 'reload', 'nginx'])
            switched = True
            nginx_probe(domain, site)
            if old_env != wanted_env:
                replace(site_env, wanted_env)
        except Exception:
            # Keep the old pool until the old route is restored and tested.
            restore(vhost, old_vhost)
            if not old_link:
                link.unlink(missing_ok=True)
            run(['nginx', '-t', '-q'])
            if switched or old_vhost != wanted_vhost:
                run(['systemctl', 'reload', 'nginx'])
                if old_vhost is not None:
                    nginx_probe(domain, site)
            restore(site_env, old_env)
            restore(pool, old_pool)
            if old_pool is None and not list(pool.parent.glob('*.conf')):
                run(['systemctl', 'disable', '--now', f'php{version}-fpm'])
            elif old_pool != wanted_pool:
                run(['systemctl', 'reload', f'php{version}-fpm'])
            restore(marker, old_marker, 0o600)
            raise
        # A failed cleanup leaves the healthy new route and a retry marker.
        for old in previous:
            old.unlink(missing_ok=True)
        for old_version in sorted(pending_versions):
            old_pool_dir = PHP_ROOT / old_version / 'fpm/pool.d'
            if list(old_pool_dir.glob('*.conf')):
                run(['systemctl', 'reload', f'php{old_version}-fpm'])
            else:
                run(['systemctl', 'disable', '--now', f'php{old_version}-fpm'])
        marker.unlink(missing_ok=True)
        return True


def main():
    module = AnsibleModule(argument_spec=dict(
        domain=dict(type='str', required=True), user=dict(type='str', required=True),
        version=dict(type='str', required=True), site=dict(type='path', required=True),
        pool_content=dict(type='str', required=True), vhost_content=dict(type='str', required=True),
        env_content=dict(type='str', required=True)), supports_check_mode=False)
    try:
        changed = converge(**{key: module.params[key] for key in module.params})
    except Exception as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(changed=changed)


if __name__ == '__main__':
    main()
