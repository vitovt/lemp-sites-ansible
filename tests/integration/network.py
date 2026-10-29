#!/usr/bin/env python3
"""Test the direct-mode firewall from a separate, unrouted network namespace."""
from pathlib import Path
import subprocess
import sys

assert Path('/etc/wp-hosting-test-id').read_text().strip() == sys.argv[1]
suffix = sys.argv[1][:6]
namespace, host, peer = 'wptest-' + suffix, 'wph-' + suffix, 'wpp-' + suffix


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def request(address, *headers):
    return run('ip', 'netns', 'exec', namespace, 'curl', '--noproxy', '*', '-sk', '--max-time', '3',
        '--resolve', f'test1.example:443:{address}', '-o', '/dev/null', '-w', '%{http_code}',
        *headers, 'https://test1.example/?network-probe=1', check=False)


marker = Path('/srv/sites/test1.example/conf/maintenance')
assert not marker.exists()
created_marker = False
try:
    run('ip', 'netns', 'add', namespace)
    run('ip', 'link', 'add', host, 'type', 'veth', 'peer', 'name', peer)
    run('ip', 'link', 'set', peer, 'netns', namespace)
    run('ip', 'addr', 'add', '10.255.254.1/30', 'dev', host)
    run('ip', '-6', 'addr', 'add', 'fd42:bad5:7777::1/64', 'dev', host, 'nodad')
    run('ip', 'link', 'set', host, 'up')
    run('ip', 'netns', 'exec', namespace, 'ip', 'link', 'set', 'lo', 'up')
    run('ip', 'netns', 'exec', namespace, 'ip', 'addr', 'add', '10.255.254.2/30', 'dev', peer)
    run('ip', 'netns', 'exec', namespace, 'ip', '-6', 'addr', 'add', 'fd42:bad5:7777::2/64', 'dev', peer, 'nodad')
    run('ip', 'netns', 'exec', namespace, 'ip', 'link', 'set', peer, 'up')
    result = request('[fd42:bad5:7777::1]')
    assert result.returncode == 0 and result.stdout == '200', (result.stdout, result.stderr)
    assert request('10.255.254.1').returncode != 0, 'Direct-mode IPv4 should be closed'
    marker.write_text('network regression\n')
    created_marker = True
    result = request('[fd42:bad5:7777::1]', '-H', 'X-WP-Health: 1', '-H', 'CF-Connecting-IP: 127.0.0.1')
    assert result.stdout == '503', 'Untrusted headers bypassed maintenance'
    print('PASS isolated IPv6 input, IPv4 rejection, and untrusted health/IP headers')
finally:
    if created_marker:
        marker.unlink(missing_ok=True)
    run('ip', 'link', 'delete', host, check=False)
    run('ip', 'netns', 'delete', namespace, check=False)
