"""Pool resolution, real preflight validation, and mode-specific rendering."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ansible.errors import AnsibleFilterError
import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'filter_plugins'))
from php_fpm import resolve, validate, budget


class PHPFPMTests(unittest.TestCase):
    def test_partial_layers_and_legacy_precedence(self):
        self.assertEqual(resolve({}, {'max_children': 8})['max_children'], 8)
        self.assertEqual(resolve({}, {})['max_requests'], 500)
        cfg = resolve({'php_fpm': {'max_children': 12}, 'pm_max_requests': 0},
                      {'max_children': 8}, {'pm_max_children': 10})
        self.assertEqual(cfg['max_children'], 12)
        self.assertEqual(cfg['max_requests'], 0)
        self.assertEqual(cfg['process_idle_timeout'], '30s')
        self.assertEqual(resolve({'pm_max_children': 5}, {'max_children': 8})['max_children'], 5)
        self.assertEqual(resolve({}, {}, {'pm_idle_timeout': '1m'})['process_idle_timeout'], '1m')

    def test_conflicting_forms_are_rejected(self):
        with self.assertRaisesRegex(AnsibleFilterError, 'either php_fpm.max_children'):
            resolve({'php_fpm': {'max_children': 8}, 'pm_max_children': 9})

    def test_invalid_settings_and_defaults(self):
        invalid = [None, [], {'pm': 'static'}, {'pm': 1}, {'max_children': 0},
                   {'max_children': 65}, {'max_children': True}, {'max_children': '8'},
                   {'max_requests': -1}, {'max_requests': False}, {'max_requests': 1.5},
                   {'process_idle_timeout': ''}, {'process_idle_timeout': '-1s'},
                   {'process_idle_timeout': '3x'}, {'process_idle_timeout': True},
                   {'process_idle_timeout': 1.2}, {'start_servers': 2},
                   {'min_spare_servers': 1}, {'max_spare_servers': 3},
                   {'pm': 'dynamic', 'process_idle_timeout': '30s'},
                   {'pm': 'dynamic', 'min_spare_servers': 4, 'max_spare_servers': 3},
                   {'pm': 'dynamic', 'max_children': 2},
                   {'pm': 'dynamic', 'start_servers': '2'},
                   {'pm': 'dynamic', 'min_spare_servers': 0}, {'typo': 4}]
        for cfg in invalid:
            with self.subTest(cfg=cfg):
                with self.assertRaises(AnsibleFilterError):
                    resolve({'php_fpm': cfg})
                if cfg is not None:
                    self.assertTrue(validate({}, cfg))
        for cap in (0, True, '64'):
            self.assertTrue(validate({}, cap=cap))
        self.assertTrue(validate({}, worker_est=0))
        self.assertTrue(validate({}, legacy_defaults={'pm_max_requests': -1}))

    def test_valid_modes_and_timeouts(self):
        for timeout in (0, 30, '30', '30s', '1m', '1h', '1d'):
            self.assertEqual(resolve({'php_fpm': {'process_idle_timeout': timeout}})['process_idle_timeout'], timeout)
        self.assertEqual(resolve({'php_fpm': {'pm': 'dynamic'}})['start_servers'], 2)
        self.assertEqual(resolve({}, {'pm': 'dynamic'})['pm'], 'dynamic')
        self.assertFalse(validate({'first.example': {'pm_max_children': 5}}))
        self.assertEqual(resolve({}, cap=4)['max_children'], 4)

    def test_template_modes_and_default_bytes(self):
        text = (ROOT / 'roles/site/templates/pool.conf.j2').read_text()
        env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
        def render(cfg):
            return env.from_string(text).render(ansible_managed='managed', site_disable_functions='exec',
                site_cfg=dict(domain='first.example', user='first', php_version='8.4', path='/srv/sites/first.example',
                              memory_limit='256M', upload_max_filesize='64M', php_fpm=cfg))
        ondemand = render(resolve({}))
        self.assertIn('pm = ondemand\npm.max_children = 4\npm.process_idle_timeout = 30s\npm.max_requests = 500\nrequest_terminate_timeout = 300s\n', ondemand)
        self.assertNotIn('pm.start_servers', ondemand)
        self.assertNotIn('spare_servers', ondemand)
        dynamic = render(resolve({'php_fpm': {'pm': 'dynamic'}}))
        self.assertIn('pm.start_servers = 2', dynamic)
        self.assertIn('pm.min_spare_servers = 1', dynamic)
        self.assertIn('pm.max_spare_servers = 3', dynamic)
        self.assertNotIn('process_idle_timeout', dynamic)

    def run_tasks(self, variables, tasks):
        with tempfile.TemporaryDirectory() as directory:
            playbook = Path(directory) / 'test.yml'
            playbook.write_text(yaml.safe_dump([dict(hosts='localhost', gather_facts=False,
                vars=variables, tasks=tasks)], sort_keys=False))
            return subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(playbook)],
                cwd=ROOT, env={**os.environ, 'ANSIBLE_LOCAL_TEMP': str(Path(directory) / 'tmp')},
                capture_output=True, text=True)

    def test_real_preflight_accepts_modes_and_rejects_invalid_defaults(self):
        defaults = yaml.safe_load((ROOT / 'roles/preflight/defaults/main.yml').read_text())
        tasks = yaml.safe_load((ROOT / 'roles/preflight/tasks/main.yml').read_text())[:2]
        cases = [({'pm': 'ondemand', 'max_children': 8}, {}, True),
                 ({'pm': 'dynamic', 'max_children': 8}, {}, True),
                 ({}, {'max_children': 0}, False),
                 ({'max_children': True}, {}, False),
                 ({'pm': 'static'}, {}, False),
                 ({'start_servers': 2}, {}, False),
                 ({'pm': 'dynamic', 'process_idle_timeout': '30s'}, {}, False),
                 ({'pm': 'dynamic', 'min_spare_servers': 4}, {}, False)]
        for nested, server, success in cases:
            with self.subTest(nested=nested, server=server):
                variables = dict(defaults, wp_sites={'first.example': {'php_version': '8.4', 'php_fpm': nested}},
                                 php_fpm_defaults=server)
                result = self.run_tasks(variables, tasks)
                self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)

    def test_site_config_uses_partial_server_defaults_and_legacy_overrides(self):
        tasks = yaml.safe_load((ROOT / 'roles/site/tasks/config.yml').read_text())
        defaults = yaml.safe_load((ROOT / 'roles/site/defaults/main.yml').read_text())
        for cfg, expected in [({}, 8), ({'pm_max_children': 5}, 5), ({'php_fpm': {'max_children': 10}}, 10)]:
            variables = dict(defaults, php_fpm_defaults={'max_children': 8},
                site_item={'key': 'first.example', 'value': {'php_version': '8.4', **cfg}})
            result = self.run_tasks(variables, tasks + [{'ansible.builtin.assert': {'that': [
                f'site_cfg.php_fpm.max_children == {expected}',
                'site_cfg.php_fpm.max_requests == 500', "site_cfg.php_fpm.process_idle_timeout == '30s'"]}}])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_memory_warning_is_advisory(self):
        tasks = yaml.safe_load((ROOT / 'roles/preflight/tasks/main.yml').read_text())
        warning = next(task for task in tasks if task['name'] == 'Warn about PHP-FPM memory capacity')
        sites = {'first.example': {}, 'second.example': {'pm_max_children': 8}}
        self.assertEqual(budget(sites), 12)
        for memory, warns in [(1000, True), (1800, False), (4000, False)]:
            result = self.run_tasks(dict(wp_sites=sites, ansible_facts={'memtotal_mb': memory}), [warning])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual('WARNING: PHP-FPM' in result.stdout, warns, result.stdout)
            if warns:
                for value in ('12 workers', '1800 MiB', '1000 MiB', 'Reduce pool limits or add RAM'):
                    self.assertIn(value, ' '.join(result.stdout.split()))
