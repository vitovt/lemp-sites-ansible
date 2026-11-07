"""Unprivileged site commands. Site selection always comes from the current UID."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from site_session import current_site, docroot, environment, php_path

POLICY_ACTIONS = ('menu', 'show', 'hold', 'unhold', 'checksums', 'allow-file', 'remove-file')


def wp_command(s, args):
    if any(arg in ('--path', '--ssh', '--http') or arg.startswith(('--path=', '--ssh=', '--http=')) for arg in args):
        raise ValueError('This command is bound to the current site; --path, --ssh and --http are not supported')
    return [php_path(s), '/usr/local/bin/wp', '--path=' + str(docroot(s)), '--skip-plugins', '--skip-themes', *args]


def run_current(s, command):
    return subprocess.run(command, env=environment(s), cwd=docroot(s), check=False).returncode


def check(s, args):
    from code_policy import SLUG, check_plugins
    parser = argparse.ArgumentParser(description='Read-only code checks as this site owner. Host checks require root.')
    parser.add_argument('area', nargs='?', choices=('all', 'git', 'core', 'plugins', 'files'), default='all')
    parser.add_argument('plugin', nargs='?')
    a = parser.parse_args(args)
    if a.plugin and (a.area != 'plugins' or not SLUG.fullmatch(a.plugin)):
        parser.error('Supply a plugin slug only with plugins')
    result = 0
    if a.area in ('all', 'git'):
        print('Git: review changes; no files are staged or committed.', flush=True)
        result |= run_current(s, ['/usr/bin/git', '-C', str(docroot(s)), 'status', '--short', '--untracked-files=all'])
    if a.area in ('all', 'core'):
        result |= run_current(s, wp_command(s, ['core', 'verify-checksums', '--include-root']))
    if a.area in ('all', 'plugins'):
        summary = check_plugins(s['base'], s['SITE_USER'], s['PHP_VERSION'], '/usr/local/bin/wp',
                                as_current_user=True, component=a.plugin, details=True)
        print(summary)
        result |= int(summary.startswith('FAIL'))
    if a.area in ('all', 'files'):
        print('PHP in writable content directories (review each file):')
        found = []
        for name in ('wp-content/uploads', 'wp-content/cache', 'wp-content/upgrade'):
            root = docroot(s) / name
            if root.is_symlink():
                print('SKIP symlink directory:', name)
                continue
            for path in root.rglob('*') if root.is_dir() else []:
                if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.php', '.phtml', '.php3', '.php4', '.php5', '.php7', '.php8', '.phar'):
                    found.append(str(path.relative_to(docroot(s))))
        for path in sorted(found):
            print('REVIEW', path)
        if not found:
            print('(none)')
    print('Host services, certificates, root baselines and administrator acknowledgement: use root wp-monitor.')
    return min(result, 1)


def main(name, argv):
    try:
        if name == 'site-policy' and argv == ['--help']:
            print('Usage: site-policy [menu|show|hold|unhold|checksums|allow-file|remove-file] [COMPONENT] [FILE] [OPTIONS]\n'
                  'The site is selected by your UID. No arguments opens the existing policy menu.\n'
                  'Example: site-policy hold example-plugin --reason "Local patch"\n'
                  'Example: site-policy show --yaml\n'
                  'Use jump-in-help policy for all options and examples.')
            return 0
        s = current_site()
        os.umask(0o027)
        if name == 'site-php':
            os.execve(php_path(s), [php_path(s), *argv], environment(s))
        elif name == 'site-wp':
            command = wp_command(s, argv)
            os.chdir(docroot(s))
            os.execve(command[0], command, environment(s))
        elif name == 'site-policy':
            action = argv[0] if argv else 'menu'
            if action not in POLICY_ACTIONS:
                raise ValueError('Unknown policy action; use site-policy --help')
            command = ['/usr/bin/sudo', '-n', '/usr/local/sbin/wp-site', 'policy', action, s['domain'], *argv[1:]]
            os.execve(command[0], command, environment(s))
        else:
            return check(s, argv)
        return 0
    except (ValueError, PermissionError, OSError, KeyError) as exc:
        print(name + ': ' + str(exc), file=sys.stderr)
        return 2
