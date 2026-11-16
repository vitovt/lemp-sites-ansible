import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'library'))
import site_provision_intent as provision


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.site = str(self.root / 'site')
        patcher = patch.object(provision, 'ROOT', self.root / 'intents')
        patcher.start()
        self.addCleanup(patcher.stop)
        # Root-owned fixture metadata without requiring root test execution.
        original_stat, original_lstat = Path.stat, Path.lstat
        def owned(original):
            def call(path, *args, **kwargs):
                info = original(path, *args, **kwargs)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
            return call
        for name, original in [('stat', original_stat), ('lstat', original_lstat)]:
            patcher = patch.object(Path, name, owned(original))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fresh_intent_allows_retry_with_matching_accounts(self):
        with patch.object(provision, 'account', return_value=None):
            self.assertEqual(provision.intent('demo.example', 'demo', self.site), (True, True))
        entry = SimpleNamespace(pw_uid=1001, pw_gid=1001, pw_dir=self.site, pw_shell='/usr/sbin/nologin')
        with patch.object(provision, 'account', return_value=entry), patch.object(provision.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=1001)):
            self.assertEqual(provision.intent('demo.example', 'demo', self.site), (False, True))
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                provision.intent('demo.example', 'other', self.site)

    def test_resume_rejects_wrong_account_identity_and_group(self):
        with patch.object(provision, 'account', return_value=None):
            provision.intent('demo.example', 'demo', self.site)
        for home, shell, uid, gid in [('/other', '/usr/sbin/nologin', 1001, 1001),
                                      (self.site, '/bin/bash', 1001, 1001),
                                      (self.site, '/usr/sbin/nologin', 0, 1001),
                                      (self.site, '/usr/sbin/nologin', 1001, 2000)]:
            entry = SimpleNamespace(pw_uid=uid, pw_gid=gid, pw_dir=home, pw_shell=shell)
            with patch.object(provision, 'account', return_value=entry), \
                 patch.object(provision.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=1001)):
                with self.assertRaises(ValueError):
                    provision.intent('demo.example', 'demo', self.site)

    def test_intent_precedes_users_and_route_precedes_cleanup(self):
        import yaml
        path = Path(__file__).resolve().parents[2] / 'roles/site/tasks/one-site.yml'
        tasks = yaml.safe_load(path.read_text())
        names = [task['name'] for task in tasks]
        self.assertLess(names.index('Record or validate site provisioning intent'),
                        next(i for i, name in enumerate(names) if name.startswith('Users (')))
        self.assertLess(next(i for i, name in enumerate(names) if name.startswith('PHP and Nginx route (')),
                        names.index('Clear completed site registration intent'))

    def test_doctor_warning_matches_only_unregistered_site_roots(self):
        import yaml
        from jinja2 import Environment, StrictUndefined
        path = Path(__file__).resolve().parents[2] / 'roles/preflight/tasks/main.yml'
        task = next(task for task in yaml.safe_load(path.read_text()) if task['name'] == 'Report incomplete site registration')
        env = Environment(undefined=StrictUndefined)
        conditions = [env.compile_expression(value) for value in task['when']]
        for root_exists, registered, expected in [(True, False, True), (False, False, False), (True, True, False)]:
            context = dict(item={'item': {'key': 'demo.example'}, 'stat': {'exists': root_exists}},
                           preflight_site_state={'results': [{'item': {'key': 'demo.example'}, 'stat': {'exists': registered}}]})
            self.assertEqual(all(condition(**context) for condition in conditions), expected)

    def test_existing_account_without_intent_is_never_adopted(self):
        with patch.object(provision, 'account', return_value=object()):
            with self.assertRaisesRegex(ValueError, 'explicit recovery'):
                provision.intent('demo.example', 'demo', self.site)
        self.assertFalse(provision.ROOT.exists())

    def test_symlink_marker_is_rejected(self):
        provision.ROOT.mkdir(mode=0o700)
        (provision.ROOT / 'demo.example.json').symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            provision.intent('demo.example', 'demo', self.site)

    def test_registration_clears_intent_without_changing_site_env(self):
        with patch.object(provision, 'account', return_value=None):
            provision.intent('demo.example', 'demo', self.site)
        conf = Path(self.site) / 'conf'
        conf.mkdir(parents=True, mode=0o755)
        conf.chmod(0o755)
        env = conf / 'site.env'
        env.write_text('facts')
        env.chmod(0o644)
        self.assertEqual(provision.intent('demo.example', 'demo', self.site), (True, False))
        self.assertEqual(env.read_text(), 'facts')

    def test_check_mode_does_not_create_state(self):
        with patch.object(provision, 'account', return_value=None):
            self.assertEqual(provision.intent('demo.example', 'demo', self.site, check=True), (True, True))
        self.assertFalse(provision.ROOT.exists())
