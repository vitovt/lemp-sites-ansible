"""Exercise trusted proxy headers against a private local Nginx process."""
import http.client
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
policy = Environment().from_string((ROOT / 'roles/nginx/templates/cloudflare.conf.j2').read_text()).render(
    ansible_managed='test', nginx_access_mode='direct', nginx_trusted_proxies=['127.0.0.2/32'],
    nginx_cf_ranges=['192.0.2.0/24'], nginx_cloudflare_only=False, nginx_real_ip_header='CF-Connecting-IP')

with tempfile.TemporaryDirectory(prefix='lemp-real-ip-') as directory:
    base = Path(directory)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    config = base / 'nginx.conf'
    config.write_text(f'''pid {base}/nginx.pid;
error_log {base}/error.log;
daemon off;
master_process off;
events {{ worker_connections 32; }}
http {{
    access_log off;
    client_body_temp_path {base}/body;
    {policy}
    server {{
        listen 127.0.0.1:{port};
        return 200 "$remote_addr|$realip_remote_addr|$wp_internal_health";
    }}
}}
''')
    process = subprocess.Popen(['nginx', '-p', str(base) + '/', '-c', str(config)], stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            if process.poll() is not None:
                raise RuntimeError(process.stderr.read().decode())
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1):
                    break
            except OSError:
                time.sleep(.1)
        for source, expected in [('127.0.0.2', '198.51.100.15|127.0.0.2|0'),
                                 ('127.0.0.3', '127.0.0.3|127.0.0.3|0'),
                                 ('127.0.0.1', '127.0.0.1|127.0.0.1|1')]:
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5, source_address=(source, 0))
            try:
                connection.request('GET', '/', headers={'CF-Connecting-IP':'198.51.100.15', 'X-WP-Health':'1'})
                response = connection.getresponse()
                assert response.status == 200
                assert response.read().decode() == expected, source
            finally:
                connection.close()
    finally:
        process.terminate()
        process.communicate(timeout=5)
print('PASS trusted proxy IP, untrusted header rejection, and local-only maintenance bypass')
