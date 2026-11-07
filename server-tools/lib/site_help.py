"""Short, task-based reference for the unprivileged site environment."""
import argparse
import os
import sys

TOPICS = {
    'git': ('Review and commit code', 'git status --short', '''git status --short                    Show changed files.
git diff -- PATH                      Review one path.
git diff --summary                    Review mode changes.
git add -- PATH                       Stage only an approved path.
git diff --cached                     Review the staged changes.
git commit -m "Explain the change"     Commit the staged changes.
git log --oneline -10                  Show recent commits.

Git runs as the code owner. The session supplies a default operator identity.
Do not stage unrelated or unexplained changes. No helper commits files automatically.'''),
    'wp': ('Run WP-CLI for this site', 'wp plugin list', '''wp --info                             Show PHP and WP-CLI paths.
wp core version                       Show the WordPress version.
wp core verify-checksums --include-root
wp plugin list                        List plugins.
wp plugin verify-checksums --all       Show upstream checksum errors.
wp theme list                         List themes.
wp option get siteurl                 Read the site URL.
wp user list --role=administrator      List administrators.

The wrapper uses this UID's site path and PHP branch. It skips plugins and themes
by default. It cannot select another path, SSH target or HTTP target.
Raw WP-CLI update commands can bypass update holds. Use root wp-update for the
managed update workflow. Do not put database passwords in command arguments.'''),
    'php': ('Run the site PHP branch', 'php -v', '''php -v                                Show the selected version.
php -l wp-content/plugins/PLUGIN/file.php

php calls site-php. Each call reads the current root-owned facts.
After a PHP branch change, exit and enter again to refresh the prompt summary.'''),
    'check': ('Inspect code without root', 'site-check plugins', '''site-check                            Run all available owner checks.
site-check git                        Show Git status.
site-check core                       Verify core checksums.
site-check plugins [PLUGIN]           Show checksum errors and local policy results.
site-check files                      List PHP in writable content directories.

These checks run as the code owner. They do not change root baselines or send alerts.
A checksum unavailable from WordPress.org does not verify a plugin.
Host services, certificate keys, admin baselines and acknowledgement require root
wp-monitor. A clean Git status alone does not prove that code is safe.'''),
    'policy': ('Manage this site code policy', 'policy show', '''policy                                Show the table and select an action.
policy show --yaml                    Show the saved rules.
policy hold PLUGIN --reason "Local fix"
policy unhold PLUGIN
policy hold THEME --kind theme --reason "Local fix"
policy checksums PLUGIN --mode git
policy checksums PLUGIN --mode wordpress_org
policy allow-file PLUGIN PATH --yes    Accept a reviewed exact file state.
policy remove-file PLUGIN PATH         Remove a file exception.

policy is site-policy. It selects the site from UID, then uses the existing narrow
sudo interface. It does not grant a root shell or let you edit another site.
A hold affects managed updates. It does not disable integrity checks.
Git mode requires a tracked, clean plugin baseline. Review code before acceptance.
allow-file records a hash for an added/changed file, or expected absence for a
deleted file. A later change fails verification. Supply all values or use the menu.'''),
    'files': ('Read and edit site files', 'ls -la', '''pwd                                   Show the current directory.
ls -la                                List files, including hidden files.
cd wp-content/plugins                 Enter the plugin directory.
less FILE                             Read a file.
vim FILE                              Edit a file.
find wp-content/uploads -type f -name '*.php'

Linux permissions are the boundary. This is not a jail or chroot.
The code owner can edit this site's code. PHP-FPM remains a separate runtime UID.
Other sites' private files and root-owned backup or service secrets remain protected.'''),
    'history': ('Control session history', 'history 10', '''history 10                            Show recent commands.
  COMMAND                             A leading space omits a command from history.

History is private: $HOME/.bash_history, mode 0600. HOME is the site's owner-tmp.
No root history or password is copied into the session. Do not type inline secrets.
Optional user settings: $HOME/.site-shellrc. They run only as the site owner.
Ansible updates system tools and profiles. It does not overwrite user settings.
Tab completes Git, common WP-CLI tasks, policy actions and installed components.'''),
    'exit': ('Return to the administrator', 'exit', '''exit                                  Close this site shell.
Ctrl-D                                Close the shell with EOF.
Ctrl-C                                Stop a foreground command; keep the shell.

The parent administrator session stays open. jump-in does not change account
login shells, enable SSH, add sudo rules, or configure Jailkit.'''),
    'site-shell': ('Open the environment for this UID', 'site-shell', """site-shell                            Open an interactive environment for the current owner UID.

This is the unprivileged shell entry point used by jump-in. It cannot switch users
or select a domain. Explicit -c, -l, --login and non-terminal modes are rejected.
A manual call inside an existing site shell creates a nested shell. There is no
automatic shell restart. Exit each shell separately. Normally use jump-in from root."""),
    'jump-in-help': ('Read this command reference', 'jump-in-help policy', '''jump-in-help                          Show all topics with one example each.
jump-in-help COMMAND                  Show one topic.
jump-in-help --all                    Show the full reference.

Aliases: site-wp -> wp; site-php -> php; site-check -> check;
site-policy -> policy; cd/ls/less/vim/find -> files.'''),
}
ALIASES = {'site-wp': 'wp', 'site-php': 'php', 'site-check': 'check', 'site-policy': 'policy',
           'cd': 'files', 'ls': 'files', 'pwd': 'files', 'less': 'files', 'vim': 'files', 'find': 'files'}


def supports_color():
    if not sys.stdout.isatty() or 'NO_COLOR' in os.environ or os.environ.get('TERM', 'dumb') == 'dumb':
        return False
    try:
        import curses
    except ImportError:
        return False
    try:
        curses.setupterm()
        return curses.tigetnum('colors') >= 8
    except curses.error:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='Site-owner cheat sheet. No command is executed.')
    parser.add_argument('command', nargs='?')
    parser.add_argument('--all', action='store_true', help='Print the full reference, without a pager')
    args = parser.parse_args(argv)
    command = ALIASES.get(args.command, args.command)
    if command and command not in TOPICS:
        parser.error('Unknown command; run jump-in-help for the topic list')
    if command and args.all:
        parser.error('Choose one command or --all')
    color = supports_color()
    def heading(text):
        print(('\033[1;36m' + text + '\033[0m') if color else text)
    heading('SITE COMMANDS — code owner, no root shell')
    if not command and not args.all:
        for name, (title, example, _) in TOPICS.items():
            heading(name + ' — ' + title)
            print('  ' + example)
        print('\nUse jump-in-help COMMAND for details, or jump-in-help --all for all topics.')
    else:
        for name in ([command] if command else TOPICS):
            title, _, body = TOPICS[name]
            heading('\n' + name.upper() + ' — ' + title)
            print(body)
    return 0
