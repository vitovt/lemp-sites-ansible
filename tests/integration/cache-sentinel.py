#!/usr/bin/env python3
"""Check that adding an ACL user does not flush another site's cache."""
import os
from pathlib import Path
import subprocess
import sys
assert Path('/etc/wp-hosting-test-id').read_text().strip() == sys.argv[1]
env = {'PATH':'/usr/bin:/bin', 'REDISCLI_AUTH':Path('/root/.valkey-admin.pass').read_text().strip()}
cli = ['valkey-cli', '-s', '/run/valkey/valkey-server.sock', '--user', 'admin', '--raw']
if sys.argv[2] == 'set':
    subprocess.run(cli + ['SET', 'integration-preserve', 'keep'], env=env, check=True, capture_output=True)
else:
    result = subprocess.run(cli + ['GET', 'integration-preserve'], env=env, check=True, capture_output=True)
    assert result.stdout.strip() == b'keep', 'Adding a site cleared the shared cache'
    subprocess.run(cli + ['DEL', 'integration-preserve'], env=env, check=True, capture_output=True)
    print('PASS ACL change preserves existing cache')
