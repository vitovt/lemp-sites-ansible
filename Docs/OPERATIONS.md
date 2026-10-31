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
```

Select the server by directory name. `scripts/site` reads `inventories/<server>/sites.yml`.
For an existing site, edit that file and run `scripts/lemp my-server site-apply example.com`.
For a new site, `scripts/site add` runs the full server playbook.
Use `scripts/lemp my-server site` after changing certificates, PHP branches,
or shared service settings. `site-apply` rejects a Valkey toggle and a
certificate name or DNS token change until the required migration or full
certificate run is complete.
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
permissions. It does not silently change an imported code tree.

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
wp-backup update example.com --yes
systemctl list-timers 'wp-*'
```

Monitoring reports PASS, FAIL, or SKIP. Checker errors are failures.
A missing installed core is a failure. Administrator changes remain pending until
an explicit ack. Telegram delivery errors return failure; local logs remain in
journald. Long messages are split into bounded chunks.

Weekly checked updates are enabled by default. The update runner requires a clean,
reviewed code baseline and valid checksums. It makes a backup, saves rollback data,
applies minor core updates and available plugin/theme updates, checks integrity
and uncached HTTP health, then accepts the new code baseline.
On failure, it restores code and the DB. Maintenance sites are skipped.
Set `maintenance_auto_update: false` to require manual update windows.
Private plugins without checksums can stop the update check. Review them separately.

Sury package updates are included in unattended upgrades by default.
An update can restart PHP. Major WordPress and PHP upgrades need a separate test.
Install an external uptime and heartbeat service. A local service cannot report
its own host power loss.

SMTP ports 25, 465, 587, and 2525 are blocked by default. PHP sendmail is disabled.
The roles do not install an MTA. With SMTP disabled, known MTA services from the
base image are stopped and masked. Future mail support requires an explicit
firewall change and a reviewed transport configuration. HTTP mail APIs are outside
the SMTP port block.
