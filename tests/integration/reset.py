"""Remove hosting state on an explicitly marked disposable VM. Run as root."""
import pathlib
import re
import shutil
import subprocess
import sys

assert pathlib.Path('/etc/wp-hosting-test-id').read_text().strip() == sys.argv[1]
subprocess.run(['systemctl', 'stop', 'wp-monitor.timer', 'wp-backup.timer', 'wp-update.timer',
    'wp-monitor.service', 'wp-backup.service', 'wp-update.service', 'certbot.timer', 'nginx', 'php*-fpm.service'], check=False)
for env in pathlib.Path('/srv/sites').glob('*/conf/site.env'):
    text = env.read_text()
    user = re.search(r'^SITE_USER=([a-z][a-z0-9_]*)$', text, re.M)
    db = re.search(r'^DB_NAME=([a-z][a-z0-9_]*)$', text, re.M)
    if db and db[1] not in ('root', 'mysql', 'sys', 'test'):
        subprocess.run(['mariadb', '-e', f"DROP DATABASE IF EXISTS `{db[1]}`; DROP USER IF EXISTS '{db[1]}'@'localhost'"], check=True)
    if user and user[1] not in ('root', 'mysql', 'valkey', 'nobody', 'admin', 'default'):
        for name in [user[1] + '-php', user[1]]:
            subprocess.run(['userdel', name], check=False, capture_output=True)
        subprocess.run(['groupdel', user[1]], check=False, capture_output=True)
for path in ['/srv/sites', '/etc/restic', '/mnt/backups/restic', '/mnt/backups/removed', '/var/lib/wp-monitor']:
    shutil.rmtree(path, ignore_errors=True)
for path in ['/root/.valkey-admin.pass', '/etc/valkey/users.acl', '/etc/tg-alert.env', '/etc/letsencrypt/cloudflare.ini']:
    pathlib.Path(path).unlink(missing_ok=True)
for base, pattern in [('/etc/php', '*/fpm/pool.d/*.conf'), ('/etc/nginx/sites-enabled', '*'), ('/etc/nginx/sites-available', '*.conf'), ('/etc/cron.d', 'wp-*')]:
    for path in pathlib.Path(base).glob(pattern):
        if path.is_file() or path.is_symlink():
            path.unlink()
print('PASS disposable hosting state reset')
