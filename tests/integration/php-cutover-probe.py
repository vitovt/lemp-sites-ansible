#!/usr/bin/env python3
"""Sample uncached local HTTP responses during one disposable PHP switch."""
import json
from pathlib import Path
import subprocess
import sys
import time


token, domain = sys.argv[1:3]
assert Path('/etc/wp-hosting-test-id').read_text().strip() == token
assert domain in ('test1.example', 'test2.example')
stop = Path(f'/tmp/lemp-cutover-{token}.stop')
statuses = []
for number in range(360):
    if stop.exists() and statuses:
        break
    result = subprocess.run([
        'curl', '--noproxy', '*', '-sk', '--max-time', '5',
        '--resolve', f'{domain}:443:127.0.0.1', '-o', '/dev/null',
        '-w', '%{http_code}', f'https://{domain}/?php-cutover={number}',
    ], capture_output=True, text=True)
    statuses.append(result.stdout.strip() if result.returncode == 0 else 'transport-error')
    time.sleep(0.25)
print(json.dumps({'count': len(statuses), 'bad': [code for code in statuses if code != '200']}))
