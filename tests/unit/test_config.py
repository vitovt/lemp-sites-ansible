import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('config', Path(__file__).resolve().parents[2] / 'library/hosting_config.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.text = "<?php\ndefine('DB_NAME', 'site'); define('DB_USER', 'site'); define('DB_PASSWORD', 'secret'); define('DB_HOST', 'localhost');\n"
        for name in ('DISABLE_WP_CRON', 'DISALLOW_FILE_EDIT', 'DISALLOW_FILE_MODS', 'AUTOMATIC_UPDATER_DISABLED'):
            self.text += f"define('{name}', true);\n"

    def test_matching_settings(self):
        config.validate(self.text, 'site', 'secret', '/run/mysqld/mysqld.sock')

    def test_drift_and_dynamic_settings_fail_closed(self):
        for text in [self.text.replace("'secret'", "'old'"), self.text.replace("'site'", "'other'"),
                     self.text.replace("'localhost'", "'remote.example'"), self.text.replace('true', 'false'),
                     self.text + "define('DB_NAME', 'site');", self.text.replace("'site'", "getenv('DB_NAME')")]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                config.validate(text, 'site', 'secret', '/run/mysqld/mysqld.sock')
