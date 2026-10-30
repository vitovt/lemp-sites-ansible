"""Check the public state of the deliberately interrupted third test site."""
import os
from pathlib import Path
import subprocess
import sys

assert os.geteuid() == 0
assert Path('/etc/wp-hosting-test-id').read_text().strip() == sys.argv[1]
assert sys.argv[2] in ['closed', 'open']
base = Path('/srv/sites/test3.example')
assert (base / 'htdocs/wp-includes/version.php').is_file()
closed = sys.argv[2] == 'closed'
assert (base / 'conf/maintenance').exists() == closed
assert (base / 'conf/wordpress-installed').exists() != closed
uri = '/wp-admin/install.php' if closed else '/?installation-gate-test=1'
result = subprocess.run(['curl', '--noproxy', '*', '-sk', '--max-time', '20', '--resolve',
    'test3.example:443:127.0.0.1', '-o', '/dev/null', '-w', '%{http_code}',
    '-H', 'X-WP-Health: 1',
    'https://test3.example' + uri], check=True, capture_output=True, text=True, timeout=25)
assert result.stdout == ('503' if closed else '200'), result.stdout
print('PASS interrupted installer blocked by HTTP 503' if closed else 'PASS completed installation opened only after checks')
