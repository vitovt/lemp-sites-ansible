#!/usr/bin/env python3
"""Deploy only an explicit disposable target with a separate inventory and secrets."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--host', required=True)
p.add_argument('--port', type=int, default=22)
p.add_argument('--user', default='vitovt')
p.add_argument('--trusted-proxy', action='append', default=[], type=lambda value: str(ipaddress.ip_network(value, strict=False)))
p.add_argument('--public-ipv6', type=ipaddress.IPv6Address)
p.add_argument('--expect-disk-alert', action='store_true',
               help='Continue only for the single known host disk-space alert; keep the monitor policy unchanged')
p.add_argument('--key', type=Path, required=True)
p.add_argument('--disposable', action='store_true', required=True)
p.add_argument('--reset', action='store_true')
p.add_argument('--work', type=Path)
p.add_argument('--phase', choices=['deploy', 'all', 'lifecycle'], default='all')
a = p.parse_args()
work = a.work.resolve() if a.work else Path(tempfile.mkdtemp(prefix='lemp-integration-'))
if not re.fullmatch(r'/tmp/lemp-integration-[a-zA-Z0-9_-]+', str(work)):
    p.error('--work must be a /tmp/lemp-integration-* directory')
work.mkdir(mode=0o700, exist_ok=True)
key = a.key.resolve(strict=True)
token_path = work / 'test-id'
if not token_path.exists():
    token_path.write_text(uuid.uuid4().hex)
token = token_path.read_text().strip()
ssh = ['ssh', '-i', str(key), '-p', str(a.port), '-F', '/dev/null', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none', '-o', 'BatchMode=yes',
       '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
       '-o', f'UserKnownHostsFile={work}/known_hosts', '-o', 'StrictHostKeyChecking=accept-new', f'{a.user}@{a.host}']

def remote(command, data=None):
    return subprocess.run(ssh + [command], input=data, check=True, env={'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8'})


def privileged(command):
    return command if a.user == 'root' else 'sudo -n ' + command


remote(privileged('sh -c ' + shlex.quote('umask 077; printf %s ' + shlex.quote(token) + ' > /etc/wp-hosting-test-id')))
if a.reset:
    remote(privileged('python3 - ' + shlex.quote(token)), (ROOT / 'tests/integration/reset.py').read_bytes())
# An allow-list excludes production inventory, local secrets, SSH keys, and .git.
project = work / 'project'
project.mkdir(exist_ok=True)
for name in ['roles', 'playbooks', 'library', 'scripts', 'tests']:
    shutil.copytree(ROOT / name, project / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
if (ROOT / 'collections').is_dir():
    shutil.copytree(ROOT / 'collections', project / 'collections', dirs_exist_ok=True)
for name in ['ansible.cfg', 'requirements.yml']:
    shutil.copy2(ROOT / name, project / name)
config = (project / 'ansible.cfg').read_text().replace('inventories/prod/hosts.yml', 'inventory/hosts.yml')
(project / 'ansible.cfg').write_text(config)
(work / 'secrets').mkdir(mode=0o700, exist_ok=True)
inv = project / 'inventory'
inv.mkdir(exist_ok=True)
settings = dict(ansible_host=a.host, ansible_port=a.port, ansible_user=a.user,
    ansible_ssh_private_key_file=str(key), ansible_become=True,
    ansible_ssh_common_args=f'-F /dev/null -o IdentitiesOnly=yes -o IdentityAgent=none -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o UserKnownHostsFile={work}/known_hosts -o StrictHostKeyChecking=yes',
    ansible_ssh_retries=2,
    secrets_dir=str(work / 'secrets'), test_artifact_root=str(work), hosting_test_mode=True, hosting_test_id=token,
    common_ssh_provider='minimal', common_ssh_admin_keys_file=('/root' if a.user == 'root' else f'/home/{a.user}') + '/.ssh/authorized_keys',
    common_timezone='Europe/Berlin', wp_site_only='', nginx_access_mode='direct', nginx_cloudflare_fetch=False,
    nginx_trusted_proxies=a.trusted_proxy,
    certbot_cloudflare_enabled=False, backup_require_mount=False, certbot_cloudflare_require_valid=False,
    backup_restic_paths=['/srv/sites', '/etc/nginx', '/etc/php', '/etc/mysql',
                        '/etc/ssl/certs/ssl-cert-snakeoil.pem', '/etc/ssl/private/ssl-cert-snakeoil.key'],
    mariadb_buffer_pool_size='256M', maintenance_auto_update=False, php_versions=['8.4'], php_default_version='8.4',
    wp_sites={'test1.example': {'php_version':'8.4', 'wp_install':True},
              'test2.example': {'php_version':'8.3', 'wp_install':True}})
(inv / 'hosts.yml').write_text(json.dumps({'all': {'children': {'wp': {'hosts': {'wptest': settings}}}}}, indent=2))
env = {'PATH': os.environ['PATH'], 'LANG':'C.UTF-8', 'ANSIBLE_CONFIG': str(project / 'ansible.cfg'), 'ANSIBLE_LOCAL_TEMP': str(work / 'ansible-local'), 'ANSIBLE_SSH_CONTROL_PATH_DIR': str(work / 'ssh-control'), 'ANSIBLE_NOCOWS':'1', 'ANSIBLE_FORCE_COLOR':'0'}

collections = Path(shutil.which('ansible-playbook')).resolve().parent.parent / 'collections'
if collections.is_dir():
    env['ANSIBLE_COLLECTIONS_PATH'] = str(collections)
env['ANSIBLE_HOME'] = str(work / 'ansible-home')

def disk_alert_only(output):
    summary = re.search(r'^\s+msg: [\'\"]?1 FAIL: FAIL host disk_space:', output, re.M)
    recap = re.search(r'^wptest\s+:\s+ok=\d+\s+changed=\d+\s+unreachable=0\s+failed=1\b', output, re.M)
    return bool(summary and recap)


def play(name, log, *args, expect_failure=False):
    command = ['ansible-playbook', '-i', str(inv / 'hosts.yml'), f'playbooks/{name}.yml', *args]
    with (work / log).open('w') as out:
        proc = subprocess.Popen(command, cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            print(line, end='', flush=True)
            out.write(line)
        rc = proc.wait()
        if bool(rc) != expect_failure:
            out.flush()
            if name == 'verify' and a.expect_disk_alert and rc and disk_alert_only((work / log).read_text()):
                print('EXPECTED host disk_space alert: the default monitor remains FAIL; continuing disposable lifecycle tests', flush=True)
                return
            raise SystemExit(f'Unexpected status {rc}: {name}; see {work / log}')

print(f'Isolated results and generated secrets: {work}', flush=True)
if a.phase != 'lifecycle':
    play('site', 'deploy.log')
    play('site', 'idempotence.log')
    if not re.search(r'changed=0\s+unreachable=0\s+failed=0', (work / 'idempotence.log').read_text()):
        raise SystemExit('FAIL second convergence changed the target')
    play('doctor', 'doctor.log')
if a.phase in ['all', 'lifecycle']:
    archive = work / 'probes.tar'
    with tarfile.open(archive, 'w') as tar:
        for name in ['roles', 'tests', 'library']:
            tar.add(project / name, arcname=name, filter=lambda item: None if '__pycache__' in item.name else item)
    remote('mkdir -p /tmp/wp-hosting-probes && tar -xf - -C /tmp/wp-hosting-probes', archive.read_bytes())
    if a.phase == 'all':
        remote(privileged('python3 /tmp/wp-hosting-probes/tests/integration/scenarios.py ' + token))
        play('verify', 'verify.log')
    # Prove that lost controller secrets cannot rotate live credentials.
    for relative in ['wptest/db/test1.example.pass', 'wptest/restic.pass']:
        original = work / 'secrets' / relative
        missing = original.with_suffix('.missing')
        original.rename(missing)
        try:
            play('doctor', 'missing-' + original.parent.name + '.log', expect_failure=True)
            assert 'changed=0' in (work / ('missing-' + original.parent.name + '.log')).read_text()
        finally:
            missing.rename(original)
    extra_sites = dict(settings['wp_sites'])
    extra_sites['test3.example'] = {'user':'test3_example', 'db_name':'test3_example', 'php_version':'8.4', 'wp_install':False}
    overrides = work / 'extra-sites.json'
    overrides.write_text(json.dumps({'wp_sites':extra_sites}))
    sentinel = (project / 'tests/integration/cache-sentinel.py').read_bytes()
    remote(privileged('python3 - ' + token + ' set'), sentinel)
    play('site', 'add.log', '-e', '@' + str(overrides), '-e', 'wp_site_only=test3.example')
    remote(privileged('python3 - ' + token + ' check'), sentinel)
    # Execute the real download task, then interrupt before database installation.
    import yaml
    install = yaml.safe_load((project / 'roles/site/tasks/install.yml').read_text())
    install_block = next(task for task in install if 'block' in task)
    partial = [{'hosts':'wp','become':True,'gather_facts':False,
        'vars':{'site_cfg':{'domain':'test3.example','user':'test3_example','path':'/srv/sites/test3.example',
                           'php_version':'8.4','wp_locale':'en_US','wp_version':'latest'}},
        'pre_tasks':[{'ansible.builtin.import_tasks':'tasks/test-guard.yml'}],
        'tasks':install[:install.index(install_block)] + [{'vars':install_block['vars'], 'block':[install_block['block'][0]]},
                 {'ansible.builtin.fail':{'msg':'intentional interruption after core download'}}]}]
    (project / 'playbooks/interrupted-install.yml').write_text(yaml.safe_dump(partial, sort_keys=False))
    play('interrupted-install', 'interrupted-install.log', expect_failure=True)
    remote(privileged('python3 /tmp/wp-hosting-probes/tests/integration/install-state.py ' + token + ' closed'))
    extra_sites['test3.example']['wp_install'] = True
    overrides.write_text(json.dumps({'wp_sites':extra_sites}))
    play('site', 'resume-install.log', '-e', '@' + str(overrides), '-e', 'wp_site_only=test3.example')
    remote(privileged('python3 /tmp/wp-hosting-probes/tests/integration/install-state.py ' + token + ' open'))
    play('site-repair-core', 'repair-report.log', '-e', 'repair_site=test3.example')
    play('site-remove', 'remove.log', '-e', 'wp_site_only=test3.example', '-e', 'confirm=test3.example')
    play('verify', 'final-verify.log')
    if a.public_ipv6:
        subprocess.run(['python3', str(project / 'tests/probes/public-ipv6.py'), '--work', str(work),
                        '--address', str(a.public_ipv6), '--disposable'], env=env, check=True)
print('PASS isolated integration run', flush=True)
