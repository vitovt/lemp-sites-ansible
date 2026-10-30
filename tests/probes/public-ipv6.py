"""Check public IPv6 only against an explicitly marked disposable deployment."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--work', type=Path, required=True)
p.add_argument('--address', type=ipaddress.IPv6Address, required=True)
p.add_argument('--disposable', action='store_true', required=True)
a = p.parse_args()
work = a.work.resolve()
if not re.fullmatch(r'/tmp/lemp-integration-[a-zA-Z0-9_-]+', str(work)):
    p.error('Require a /tmp/lemp-integration-* directory')
token = (work / 'test-id').read_text().strip()
assert re.fullmatch('[0-9a-f]{32}', token)
hosts = json.loads((work / 'project/inventory/hosts.yml').read_text())
s = hosts['all']['children']['wp']['hosts']['wptest']
assert s['hosting_test_mode'] is True and s['hosting_test_id'] == token
assert s['secrets_dir'] == str(work / 'secrets')
ssh = ['ssh', '-i', s['ansible_ssh_private_key_file'], '-p', str(s['ansible_port']),
       '-F', '/dev/null', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
       '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
       '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', f'UserKnownHostsFile={work}/known_hosts', f'{s["ansible_user"]}@{s["ansible_host"]}']
env = {'PATH': os.environ['PATH'], 'LANG':'C.UTF-8'}


def remote(body):
    guard = f"from pathlib import Path\nassert Path('/etc/wp-hosting-test-id').read_text().strip() == {token!r}\n"
    command = ('sudo -n ' if s['ansible_user'] != 'root' else '') + 'python3 -'
    subprocess.run(ssh + [command], input=(guard + body).encode(), check=True, env=env, timeout=30)


def request(uri='/', *, http=False, headers=()):
    port = 80 if http else 443
    scheme = 'http' if http else 'https'
    result = subprocess.run(['curl', '-6', '--noproxy', '*', '-sk', '--connect-timeout', '10',
        '--max-time', '20', '--resolve', f'test1.example:{port}:[{a.address}]',
        '--write-out', '\n%{http_code}', *headers, f'{scheme}://test1.example{uri}'],
        capture_output=True, text=True, timeout=25, env=env)
    if result.returncode:
        raise RuntimeError(f'Public IPv6 request failed (curl exit {result.returncode})')
    body, _, code = result.stdout.rpartition('\n')
    return int(code), body


remote("assert Path('/srv/sites/test1.example/conf/site.env').is_file()\n")
assert request('/?public-ipv6=1')[0] == 200
assert request('/path', http=True)[0] == 301
print('PASS external IPv6 HTTPS response and HTTP redirect', flush=True)
challenge = '/var/lib/letsencrypt/.well-known/acme-challenge/public-' + token
marker = '/srv/sites/test1.example/conf/maintenance'
value = 'external-probe-' + token
try:
    remote(f"path=Path({challenge!r})\npath.parent.mkdir(parents=True, exist_ok=True)\nwith path.open('x') as stream: stream.write({value!r})\npath.chmod(0o644)\n")
    assert request('/.well-known/acme-challenge/public-' + token, http=True) == (200, value)
    print('PASS external IPv6 HTTP challenge serving', flush=True)
    remote(f"path=Path({marker!r})\nwith path.open('x') as stream: stream.write({value!r})\npath.chmod(0o644)\n")
    assert request('/?public-maintenance=1', headers=['-H', 'X-WP-Health: 1', '-H', 'CF-Connecting-IP: 127.0.0.1'])[0] == 503
    print('PASS external IPv6 cannot spoof visitor headers to bypass maintenance', flush=True)
finally:
    remote(f"for name in [{challenge!r}, {marker!r}]:\n    path=Path(name)\n    if path.is_file() and path.read_text() == {value!r}: path.unlink()\n")
assert request('/?after-public-probe=1')[0] == 200
print('PASS public IPv6 cleanup; TLS used the explicit self-signed test exception', flush=True)
