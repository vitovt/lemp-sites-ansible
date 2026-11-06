"""Check the initial site ignore rules with Git's own matcher."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[2] / 'roles/site/files/wordpress.gitignore'


class SiteGitignoreTest(unittest.TestCase):
    def test_first_commit_keeps_code_and_excludes_generated_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(TEMPLATE, root / '.gitignore')
            paths = {
                'wp-config.php': False,
                '.env.production': False,
                'wp-content/uploads/2026/photo.jpg': False,
                'wp-content/cache/wp-rocket/page.html': False,
                'wp-content/ewww/original.jpg': False,
                'wp-content/wflogs/config.php': False,
                'wp-content/wp-rocket-config/example.php': False,
                'wp-content/updraft/site.zip': False,
                'wp-content/ai1wm-backups/site.wpress': False,
                'wp-content/plugins/ewww-image-optimizer/plugin.php': True,
                'wp-content/plugins/wp-rocket/plugin.php': True,
                'wp-content/advanced-cache.php': True,
                'wp-content/object-cache.php': True,
                'wp-content/themes/example/style.css': True,
                'wp-includes/version.php': True,
            }
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('example\n')
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout
            git('init', '-q')
            git('add', '-A')
            staged = set(git('diff', '--cached', '--name-only').splitlines())
            for name, included in paths.items():
                self.assertEqual(name in staged, included, name)
            self.assertIn('.gitignore', staged)
            git('-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                'commit', '-qm', 'initial')
            git('add', '-A')
            self.assertEqual(git('status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()
