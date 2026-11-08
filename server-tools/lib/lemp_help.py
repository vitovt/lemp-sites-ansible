"""Read-only reference for hosting commands. No site or secrets are loaded."""
import argparse
from site_help import supports_color

# name, purpose, example. Keep controller and server commands separate.
GROUPS = {
    'Controller — run in the cloned repository': [
        ('scripts/lemp', 'Check inventory; install or apply server configuration; verify sites.',
         'scripts/lemp SERVER doctor',
         'Actions: doctor, site, site-apply, verify, backup.\n'
         'site can install packages and change server resources. site-apply applies site resources only.\n'
         'Pass --check --diff to preview Ansible changes. Use --help for flags.'),
        ('scripts/site', 'Add, change, list, or remove a site in one server inventory.',
         'scripts/site --help',
         'Run on the controller. Review inventory changes before deployment.\n'
         'Removal can delete site data. Read the action help before use.'),
    ],
    'Server administrator — run as root on the hosting server': [
        ('jump-in', 'Open a shell as the site code owner.', 'jump-in example.com',
         'Starts in htdocs. Use exit to return to root. This is not a jail or SSH login.'),
        ('wp-site', 'Manage site Git, checks, and local code policy.', 'wp-site git example.com status --short',
         'No arguments opens a menu. Use wp-site --help for policy examples.\n'
         'Review local changes before accepting a checksum exception or a Git baseline.'),
        ('wp-monitor', 'Check services, code integrity, Git, and administrators.', 'wp-monitor --check example.com',
         'Use --check for a report. Use --ack SITE only after reviewing intended administrator changes.'),
        ('wp-update', 'Plan or apply managed WordPress updates.', 'wp-update plan example.com',
         'Apply example: wp-update apply example.com --scope core --policy minor\n'
         'Apply checks today\'s completed backup. It does not create one. Read --help before bypassing checks.'),
        ('wp-backup-manager', 'Browse backups; extract, verify, export, and clean workspaces.', 'wp-backup-manager',
         'No arguments opens the menu. --show-commands enables command previews.\n'
         'Recovery extracts into a workspace. It does not replace a live site or import its database.'),
        ('wp-backup', 'Run standard backups and low-level recovery or removal actions.', 'wp-backup example.com',
         'No arguments backs up all configured sites. Prefer wp-backup-manager for recovery review.\n'
         'restore-db, clone, and remove can change live data. Read Docs/BACKUP_MANAGER.md and Docs/CHEATSHEET.md.'),
        ('wp-git-commit', 'Commit eligible site code changes as the code owner.', 'wp-git-commit example.com',
         'Stages eligible changes. Review every changed and untracked path first.\n'
         'For selected paths, use Git inside jump-in instead.'),
        ('site-fix-perms', 'Check or repair site ownership and permissions.', 'site-fix-perms example.com --check',
         'Without --check, this changes permissions. Inspect the report before repair.'),
        ('tg-alert', 'Write a local alert and send it through configured Telegram.', 'tg-alert "Manual test alert"',
         'This sends a real notification. Configure credentials through Ansible. Never put tokens in the message.'),
        ('valkey-acl-sync', 'Apply Valkey ACL credentials to the configured socket.', 'valkey-acl-sync --help',
         'Infrastructure helper used by Ansible. It changes ACL state. Use only with the configured files and socket.'),
    ],
    'Site owner — enter with jump-in SITE first': [
        ('git', 'Review and commit selected code changes.', 'git status --short',
         'Review with git diff. Stage selected paths with git add -- PATH. Review git diff --cached before commit.'),
        ('site-wp', 'Run WP-CLI with this site path and PHP version.', 'site-wp plugin list',
         'Inside the site shell, wp is the short name. Raw WP-CLI updates bypass managed update holds.'),
        ('site-php', 'Run the PHP branch selected for this site.', 'site-php -v',
         'Inside the site shell, php is the short name. Example: php -l FILE.php'),
        ('site-check', 'Inspect Git, core, plugin checksums, or writable PHP files.', 'site-check plugins',
         'Checks: git, core, plugins [PLUGIN], files. It does not accept root monitoring baselines.'),
        ('site-policy', 'Manage local update holds and checksum exceptions.', 'site-policy show',
         'Inside the site shell, policy is the short name. No arguments opens a menu.\n'
         'Example: policy hold PLUGIN --reason "Local patch"\n'
         'Holds affect managed updates. They do not disable checksum checks.'),
        ('site-shell', 'Open the site environment for the current code-owner UID.', 'site-shell',
         'Normally started by jump-in. It cannot switch users or select another site. Requires an interactive terminal.'),
        ('jump-in-help', 'Show the site-owner reference and detailed examples.', 'jump-in-help policy',
         'No arguments lists topics. --all prints the full site reference.'),
        ('lemp-help', 'Show this complete hosting command map.', 'lemp-help wp-backup-manager',
         'No arguments shows every command. --all includes all notes. This reference does not execute commands.'),
    ],
}
ALIASES = {'wp': 'site-wp', 'php': 'site-php', 'policy': 'site-policy',
           'lemp': 'scripts/lemp', 'site': 'scripts/site'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', help='Show notes for one command')
    parser.add_argument('--all', action='store_true', help='Show every command with all notes')
    args = parser.parse_args(argv)
    command = ALIASES.get(args.command, args.command)
    names = {row[0] for rows in GROUPS.values() for row in rows}
    if command and command not in names:
        parser.error('Unknown command. Run lemp-help for the command list.')
    if command and args.all:
        parser.error('Choose a command or --all.')
    color = supports_color()

    def heading(text):
        print(('\033[1;36m' + text + '\033[0m') if color else text)

    heading('LEMP HOSTING COMMANDS')
    print('Examples use SERVER, example.com, PLUGIN, and PATH. Replace them before use.')
    print('Commands below are examples only. Nothing is executed.')
    for group, rows in GROUPS.items():
        selected = [row for row in rows if not command or row[0] == command]
        if not selected:
            continue
        heading('\n' + group)
        for name, purpose, example, notes in selected:
            heading(name + ' — ' + purpose)
            print('  ' + example)
            if command or args.all:
                print(notes + '\n')
    print('\nUse lemp-help COMMAND for notes, or lemp-help --all for the full reference.')
    return 0
