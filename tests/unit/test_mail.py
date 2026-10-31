"""A SysV service alias must not be sent to systemd_service."""
from pathlib import Path
import unittest

from jinja2 import Environment
import yaml


ROOT = Path(__file__).resolve().parents[2]


class MailServiceTests(unittest.TestCase):
    def test_only_installed_systemd_mail_units_are_masked(self):
        tasks = yaml.safe_load((ROOT / 'roles/common/tasks/mail.yml').read_text())
        selected = next(task for task in tasks if 'ansible.builtin.systemd_service' in task)
        env = Environment()
        env.filters['bool'] = bool
        services = {'postfix.service': {'source': 'sysv'},
                    'exim4.service': {'source': 'systemd'}}
        def accepted(name):
            variables = {'item': name, 'ansible_facts': {'services': services},
                         'firewall_smtp_enabled': False}
            return all(env.compile_expression(condition)(**variables)
                       for condition in selected['when'])
        self.assertFalse(accepted('postfix.service'))
        self.assertTrue(accepted('exim4.service'))
        self.assertFalse(accepted('sendmail.service'))
