#!/usr/bin/env python3
"""Test the direct-mode firewall from a separate, unrouted network namespace."""
from pathlib import Path
import os
import subprocess
import sys
import time

assert Path('/etc/wp-hosting-test-id').read_text().strip() == sys.argv[1]
suffix = sys.argv[1][:6]
host, peer = 'wph-' + suffix, 'wpp-' + suffix


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def request(address, *headers):
    return run(*enter, 'curl', '--noproxy', '*', '-sk', '--max-time', '3',
        '--resolve', f'test1.example:443:{address}', '-o', '/dev/null', '-w', '%{http_code}',
        *headers, 'https://test1.example/?network-probe=1', check=False)


marker = Path('/srv/sites/test1.example/conf/maintenance')
assert not marker.exists()
created_marker = False
namespace = None
try:
    # Keep the namespace alive by PID. Named namespaces need a shared mount,
    # which is not available in some unprivileged LXC configurations.
    namespace = subprocess.Popen(['unshare', '--net', 'sleep', '120'], stderr=subprocess.PIPE)
    path = f'/proc/{namespace.pid}/ns/net'
    for _ in range(100):
        if namespace.poll() is not None:
            raise RuntimeError('Cannot create the test network namespace: ' + namespace.stderr.read().decode())
        if os.readlink(path) != os.readlink('/proc/self/ns/net'):
            break
        time.sleep(.01)
    else:
        raise TimeoutError('The test namespace did not start')
    enter = ['nsenter', '--net=' + path, '--']
    run('ip', 'link', 'add', host, 'type', 'veth', 'peer', 'name', peer)
    run('ip', 'link', 'set', peer, 'netns', str(namespace.pid))
    run('ip', 'addr', 'add', '10.255.254.1/30', 'dev', host)
    run('ip', '-6', 'addr', 'add', 'fd42:bad5:7777::1/64', 'dev', host, 'nodad')
    run('ip', 'link', 'set', host, 'up')
    run(*enter, 'ip', 'link', 'set', 'lo', 'up')
    run(*enter, 'ip', 'addr', 'add', '10.255.254.2/30', 'dev', peer)
    run(*enter, 'ip', '-6', 'addr', 'add', 'fd42:bad5:7777::2/64', 'dev', peer, 'nodad')
    run(*enter, 'ip', 'link', 'set', peer, 'up')
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
    if namespace is not None:
        namespace.terminate()
        namespace.communicate(timeout=5)
