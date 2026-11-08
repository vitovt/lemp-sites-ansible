# Operations

## Sites

Use `scripts/site` on the control machine. It preserves YAML comments when it
edits the selected server's site list.
The CLI uses one private directory per server, as described in Deployment.

```sh
scripts/site add my-server example.com example 8.4 --dry-run
scripts/site add my-server example.com example 8.4 --install-wp
scripts/site add my-server second.example second 8.4 --install-wp --cloudflare-token-ref customer_b
scripts/site info my-server example.com
scripts/site clone my-server example.com staging.example.com
scripts/site remove my-server staging.example.com
scripts/site remove my-server abandoned.example.com --no-backup
```

Select the server by directory name. `scripts/site` reads `inventories/<server>/sites.yml`.
For an existing site, edit that file and run `scripts/lemp my-server site-apply example.com`.
For a new site, `scripts/site add` runs the full server playbook.
Use `scripts/lemp my-server site` after changing certificates, PHP branches,
or shared service settings. `site-apply` rejects a Valkey toggle and a
certificate name or DNS token change until the required migration or full
certificate run is complete.

To change PHP, set the site's required, quoted `php_version` to a `major.minor`
branch such as `"8.4"`. Run `scripts/lemp my-server site`. The full playbook
checks the target server's Sury package index and installs the required PHP
packages. It creates and tests a new FPM pool before Nginx uses its socket.
It removes the old pool after a local WordPress request succeeds. It keeps an
old FPM service while another site uses it. Run `scripts/lemp my-server verify`
after the change. If the new branch is already installed and has the managed
PHP configuration, `site-apply example.com` can make the same switch for one
site. A missing branch makes `site-apply` stop before it changes the site.
Package availability does not confirm plugin support. Test the new branch on
a staging site before a production change.
If a switch stops after the new route works, the site keeps
`conf/php-switch.json`. Rerun the same requested version to finish old pool
cleanup. Do not change the requested version while this file exists. If the
retry fails, inspect the Nginx route, FPM pools, and service logs before manual
repair. Keep the file until the site serves the requested branch.
A failed site deployment keeps the requested inventory entry for recovery.
Correct the error and run convergence again. Do not assume that a failed play
made no changes to the target.

A clone keeps the target database and Valkey credentials. WordPress users and
their password hashes come from the source database. Confirmation is required
before an installed target is overwritten. The operation saves rollback SQL and files,
uses a scratch database, and rewrites source URLs. On failure, it restores the
original target. It does not treat copied integration settings as safe.

A new clone remains in maintenance mode. Review API keys, payment integrations,
scheduled jobs, administrator accounts, and email settings first. Then activate:

Set the target's `staging: false` in the inventory and converge the target site
to remove the Nginx noindex header. Convergence keeps the maintenance marker.
Then run the following commands on the target.

```sh
# On the target, as root, after review:
runuser -u <owner> -- env HOME=/tmp php<version> /usr/local/bin/wp \
  --path=/srv/sites/<domain>/htdocs config set WP_ENVIRONMENT_TYPE production
wp-git-commit <domain> "reviewed staging activation"
site-fix-perms <domain>
wp-monitor --ack <domain>
rm /srv/sites/<domain>/conf/maintenance
```

`clone_activate=true` in the playbook, or `--activate` in the CLI, permits immediate
activation. Use it only when the copied integrations are safe.
Removal makes a complete restic snapshot and a private archive before deletion.
Passwords on the controller remain available for recovery.
Use `--no-backup` only when you accept permanent loss of this site's current
files and database. It skips both the new restic snapshot and the removal
archive. It works when the site's saved credentials, restic environment, or
backup mount are unavailable. By default, the CLI asks you to type the exact
domain. `--yes` permits automation; the playbook still checks its `confirm`
value. The shared operation lock remains active. Existing older snapshots are not
deleted. The command does not delete controller secret files.

## Migration and permissions

Existing wp-config.php files are preserved. They must use the declared local DB
name, user, and password. The four security and cron constants checked by
`hosting_config` must be true. Dynamic database settings need a separate migration.
A mismatch stops deployment before database changes.

The installer checks each stage. A failed download, DB setup, or theme install
can be retried. Existing code needs a reviewed Git baseline. Never accept code
changes only to clear a security alert.

An incomplete installation stays behind a maintenance gate before code download.
HTTP returns 503 and cron is blocked until installation and baseline checks finish.
An error keeps the gate closed for the next attempt. The installer does not take
over a maintenance gate owned by clone, restore, or another operation.

```sh
site-fix-perms example.com --check
site-fix-perms example.com
wp-git-commit example.com "reviewed migration"
wp-monitor --ack example.com
```

The permissions tool refuses symbolic and hard links. Normal convergence checks
permissions. It does not silently change an imported code tree. In uploads,
cache, and upgrade directories, the check accepts safe files created by the
site's PHP runtime user. Code outside these directories still needs the site
owner and the managed modes.

## Backups

The nightly timer makes database dumps, manifests, and restic snapshots.
Only a successful snapshot receives the `complete` tag. Incomplete snapshots
produce a failure and do not start retention pruning.

```sh
wp-backup                         # all sites and configured host paths
wp-backup example.com              # one site
wp-backup verify                   # restore and check all managed sites
wp-backup verify example.com
wp-backup restore example.com      # restore to a private staging directory
wp-backup restore-db example.com /srv/restore/<run>/srv/sites/example.com/backup/db.sql.gz --yes
```

Verify imports each dump into a scratch DB with a restricted account. It compares
the dump hash, the file list, the table list, and required WordPress tables.
It also reads part of the restic data. It writes temporary data; it is not a
read-only operation. It does not prove that every business workflow is correct.

Database restore stages and checks the dump before it replaces the live DB.
The replacement uses a clean database, so old tables do not survive.
An error during promotion starts rollback from the saved original dump.
A successful full backup removes local rollback copies older than 14 days.
Set `backup_restic_recovery_days` to change that limit. Restic retention is separate.
HTTP and scheduled work are blocked during replacement. Site runtime processes
are stopped to prevent existing requests from writing during the change.

The managed SQL workflow supports base tables. Views, triggers, stored routines,
and events require a separate migration. This restriction avoids unsafe DEFINER
and schema references. SQL imports have access to one database and cannot use
client shell commands or local file import.

Legacy snapshots do not have a complete tag or a manifest. Restore them in a
separate MariaDB instance for review. Do not remove CREATE DATABASE or USE lines
with sed and import the result as root.

For a remote copy, configure:

```yaml
backup_restic_copy_repo: "sftp:backup-host:/repository"
backup_restic_copy_password_file: /etc/restic/remote-password
```

Provision the remote repository, credentials, and a root-only password file first.
The nightly job copies snapshots before local pruning. A copy error fails the job.
Use a separate account and an append-only or immutable backend. A second writable
path with the same root access does not protect against root compromise.
The repository does not provision the external storage service.

## Monitoring and updates

```sh
wp-monitor --check [domain]
wp-monitor --files example.com
wp-monitor --ack example.com
wp-update plan example.com
sudo wp-update apply example.com --scope all --policy minor
systemctl list-timers 'wp-*'
```

Monitoring reports PASS, FAIL, or SKIP. Checker errors are failures.
A missing installed core is a failure. Administrator changes remain pending until
an explicit ack. Telegram delivery errors return failure; local logs remain in
journald. Long messages are split into bounded chunks.

Weekly checked updates are enabled by default. The runner requires a clean Git
baseline and a completed backup of the site from today. It does not create a
backup. The scheduled run applies minor updates. It skips held components in
`/srv/sites/<domain>/conf/code-policy.yml`. Set holds with `wp-site policy` on
the managed server. See [Work With Site Code](SITE_CODE_POLICY.md).

For a manual run, use `--skip-backup-check` only when you accept that risk.
The updater stages only the updated component. A dirty target needs
`--allow-dirty-target` and stays uncommitted. `--no-maintenance` is manual only.
Run `wp-update --help` for examples. On failure, read the site update log and
the alert. There is no automatic rollback. Use the separate restore procedure.
Existing sites keep their Git repository and `.gitignore`. A backup made before
this version has no completion marker for the updater. Run the standard backup
once before the next scheduled update, or wait for the next nightly backup.
Set `maintenance_auto_update: false` to require manual update windows.
Hold private or locally patched plugins until you review their update source.

Sury package updates are included in unattended upgrades by default.
An update can restart PHP. Major WordPress and PHP upgrades need a separate test.
Install an external uptime and heartbeat service. A local service cannot report
its own host power loss.

SMTP ports 25, 465, 587, and 2525 are blocked by default. PHP sendmail is disabled.
The roles do not install an MTA. With SMTP disabled, known MTA services from the
base image are stopped and masked. Future mail support requires an explicit
firewall change and a reviewed transport configuration. HTTP mail APIs are outside
the SMTP port block.

## Update command results

`wp-update apply` prints its result and the exact site log path. A failed backup
check prints `FAIL No completed site backup from today` and returns exit code 1.
It does not start an update. Review the result before you retry.

Logs are private files under `/srv/sites/<domain>/logs/wp-update-*.log`.
If the log cannot be written, the command prints a warning. Use
`journalctl -t wp-update` to check syslog where available.

Each update failure includes `HINT` lines with review steps and commands.
Skipped components also include advice. The plan shows advice for missing
backups and dirty Git. CLI errors refer to full help and a valid example.
Hints appear in the update log as well as the terminal. They do not run commands.

For a missing backup, run `wp-backup example.com`, then retry. Manual apply
can use `--skip-backup-check` if you accept the recovery risk. Automatic runs
cannot skip this check. A dirty target can use `--allow-dirty-target` only
in manual mode; the update can overwrite local edits and leaves the target
without an automatic commit. Review files before you use either option.

### Translation files during updates

The updater includes language packs for the selected component. Core uses
language files directly under `wp-content/languages/`. Plugins and themes use
only their own slug and locale files under `languages/plugins/` and
`languages/themes/`. Other components and arbitrary PHP files are not allowed.

A pre-existing change to a selected language pack is a dirty target. Manual
`--allow-dirty-target` permits the update but prevents its automatic commit.
Unrelated staged changes remain staged. The updater compares Git-visible file
contents, execute bits, links, and index entries before and after each update.
A failure lists the changed paths outside the selected component. Ignored data
is outside this comparison. No automatic rollback is performed.

### Git permissions after updates

Updater child commands use `umask 0027` for manual and automatic runs.
The update service also sets `UMask=0027`. New Git index files use `0640`;
new loose objects can use `0440`. The code owner keeps access. Other users
get no access through the world permission bits.

This change does not repair existing files on an undeployed server. After
installation, run `site-fix-perms SITE` once, then repeat with `--check`.
Keep the automatic update timer enabled if automatic updates are required.

### Permission monitoring

`wp-monitor` includes `permissions` in its default checks. It runs
`site-fix-perms SITE --check` and prints only `PASS` or `FAIL` for this check.
It does not repair files. To see details, run `site-fix-perms SITE --check`
as root. Scheduled failures use the existing combined Telegram alert.
If you override `wp_monitor_checks`, add `permissions` to that list.

### Review a new site before deployment

`scripts/site add` saves the new entry in `sites.yml` first. It prints the entry,
certificate method, and Cloudflare token file path when DNS-01 is selected.
It does not print the token value. Answer `y` to deploy. Answer `n` to keep the
configuration without deployment. Edit the saved entry, then run:

```bash
scripts/lemp SERVER site SITE
```

Use `--yes` to deploy without the prompt. `--dry-run` does not write or deploy.

### PHP handlers after an interrupted deployment

PHP reload handlers inspect pool files at execution time. A newly installed
branch without pools is skipped if certificate issuance stops site creation.
Existing pools must still pass `php-fpm -t` before any reload.

Validation: local handler tests cover an empty branch after an early failure,
an active pool, and an invalid pool. The existing PHP transaction tests cover
cutover, rollback, and repeat runs. Service commands are stubbed in local tests.
Live validation on 241 is pending: its configured SSH endpoint refused three
connection attempts. No changes were made to server 125.
