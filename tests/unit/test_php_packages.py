"""Check dynamic Sury package selection with repository and Debian candidates."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


class PHPPackageTests(unittest.TestCase):
    def test_selects_exact_sury_versions_without_a_branch_list(self):
        tasks = yaml.safe_load((ROOT / 'roles/php/tasks/main.yml').read_text())
        select = next(t for t in tasks if t.get('name') == 'Select PHP packages from Sury')
        install = next(t for t in tasks if t.get('name') == 'Install available PHP packages and FastCGI probe tool')
        self.assertIn('php_sury_package_versions', install['ansible.builtin.apt']['name'])
        self.assertTrue(install['ansible.builtin.apt']['allow_downgrade'])
        rows = [
            'php10.0-fpm | 10.0.3-1 | mirror+file:/debian trixie/main amd64 Packages',
            'php10.0-fpm | 10.0.2-1 | https://packages.sury.org/php trixie/main amd64 Packages',
        ]
        variables = {
            'php_sury_repo_url': 'https://packages.sury.org/php/',
            'php_sury_candidates': {'stdout': '\n'.join(rows), 'stdout_lines': rows},
        }
        play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': variables,
                 'tasks': [select, {'ansible.builtin.assert': {'that': [
                     "php_sury_available_packages == ['php10.0-fpm']",
                     "php_sury_package_versions == ['php10.0-fpm=10.0.2-1']",
                 ]}}]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'check.yml'
            path.write_text(yaml.safe_dump(play, sort_keys=False))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(path)],
                env={**os.environ, 'ANSIBLE_LOCAL_TEMP': directory + '/ansible'},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
