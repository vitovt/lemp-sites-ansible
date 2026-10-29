import ast
from pathlib import Path
import unittest
from jinja2 import Environment
import yaml

ROOT = Path(__file__).resolve().parents[2]


class ACMETests(unittest.TestCase):
    def test_authenticator_arguments(self):
        tasks = yaml.safe_load((ROOT / 'roles/certbot_cloudflare/tasks/certs.yml').read_text())
        template = next(task['ansible.builtin.command']['argv'] for task in tasks if task['name'] == 'Request the certificate')
        env = Environment()
        for mode in ['dns-cloudflare', 'webroot']:
            argv = ast.literal_eval(env.from_string(template).render(
                item={'item':{'domain':'example.com','names':['example.com','www.example.com']}},
                certbot_cloudflare_method=mode, certbot_cloudflare_credentials='/private/test-token.ini',
                certbot_cloudflare_propagation_seconds=30, certbot_cloudflare_webroot='/var/lib/letsencrypt',
                certbot_cloudflare_email='', certbot_cloudflare_extra_args=[]))
            self.assertEqual('--dns-cloudflare' in argv, mode == 'dns-cloudflare')
            self.assertEqual('--webroot' in argv, mode == 'webroot')
            self.assertEqual('/private/test-token.ini' in argv, mode == 'dns-cloudflare')
            self.assertIn('example.com,www.example.com', argv)
