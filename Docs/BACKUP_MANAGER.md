# Manage backups on the server

Run `wp-backup-manager` as root. Recovery extracts data only.
It does not import a live database, replace live files, create a site, or activate a site.

## Open the menu

```sh
wp-backup-manager
wp-backup-manager --help
```

- [ ] Check the storage table at startup.
- [ ] Select a task. Supply the missing values.
- [ ] Use **13. Show commands [disabled]** to turn command previews on. Select it again to turn them off.
- [ ] Check the result and the task ID.
- [ ] Check the storage table before exit. Exit does not delete data.
- [ ] Use **0. Exit** to close the menu.

Type `cancel` or press Ctrl-C to cancel. Ctrl-C in an operation returns exit code 3.
An interrupted operation can leave a failed or interrupted workspace. Inspect it before cleanup.

## Use complete commands

`--mode run` executes directly. Command display is off by default.
Start the menu with `wp-backup-manager --show-commands` to enable previews.
Menu item 13 toggles display for the current session. There is no display prompt
before each action. Each completed action is followed by a separator.
Comments are blue and commands are cyan on a color terminal. `NO_COLOR`,
`TERM=dumb`, and redirected output disable color.
The menu starts with display off unless a CLI mode is supplied. Saved CLI
preferences do not change the initial menu state.
Read-only actions can display and run without confirmation. Write actions need confirmation.
Use `--yes` for an approved non-interactive operation. It never approves password disclosure.
Use `--mode show` to print a plan without changing backup or recovery data.
Repository discovery still reads snapshots and file lists to produce an exact plan.

```sh
wp-backup-manager list --site example.com
wp-backup-manager create --site example.com --mode run
wp-backup-manager create --all --mode run
wp-backup-manager browse --site example.com --snapshot ID --mode run
```

A missing value causes a prompt in a terminal. Without a terminal, supply the value as an option.
Use full snapshot IDs when possible. An ambiguous ID is an error.
Nightly backups can contain a site without a `site:` tag. The manager reads snapshot trees.
Snapshots without the `complete` tag are listed separately. Their completion is unconfirmed.

## Extract data

- [ ] Select the snapshot and site. The site can be deleted.
- [ ] Check the scope, estimated size, and destination.
- [ ] Read the verification results. Extraction is not the same as a successful SQL import.
- [ ] Review the printed manual instructions.

```sh
wp-backup-manager extract --site example.com --snapshot ID --scope complete --mode run
wp-backup-manager extract --site example.com --snapshot ID --scope database --mode run
wp-backup-manager extract --site example.com --snapshot ID --scope files --mode run
wp-backup-manager extract --site example.com --snapshot ID --scope directory --path htdocs/wp-content/uploads --mode run
wp-backup-manager extract --site example.com --snapshot ID --scope file --path htdocs/robots.txt --mode show
```

| Scope | Extracted data |
|---|---|
| `file` | One regular file, with its relative path |
| `directory` | One directory tree, with its relative path |
| `files` | `htdocs/` and available file-verification manifests; no SQL dump |
| `database` | SQL dump and available manifests |
| `complete` | The whole site tree, including `conf/code-policy.yml` when present |

Each extraction uses a new private `/srv/restore/wm-ID/data/` directory.
No live site account or database is required. Credentials come from the selected restic configuration.
Use `--config /private/path/env` for another repository configuration.
The environment file and repository password file must be root-owned, private regular files.

The manager rejects selected symlinks and special files. Select a narrower path if needed.
Do not execute recovered PHP or Git hooks. Review all recovered data before use.

## Verify a backup

```sh
wp-backup-manager verify --site example.com --snapshot ID --mode run
wp-backup-manager verify --site example.com --snapshot ID --scratch-db --mode run
wp-backup-manager verify --all --mode run
wp-backup-manager compare --site example.com --snapshot ID --mode run
```

`verify --all` checks complete snapshots. Add `--site` to restrict the site.
`--scratch-db` needs a local MariaDB service and its root Unix-socket access.
It uses the existing restricted temporary database/user workflow. It does not import into a live site database.

Read each result separately:

| Check | Meaning |
|---|---|
| Repository check | Restic reads all repository data packs |
| Extraction/read-back | Restic verifies the extracted data |
| File manifest | Listed regular files exist; the legacy manifest has no per-file content hashes |
| SQL integrity | Dump hash, gzip integrity, and completion marker match |
| Scratch import | Only with `--scratch-db`; table names and required WP tables are checked |
| `NOT CHECKED` | The stated check did not run; do not assume success |

Missing verification metadata is an error for `verify`. Extraction can finish with an explicit unverified result.
`compare` reads backed-up files and current files. It does not list live-only files.
No check proves that external integrations or a restored application will work.

## Create a portable archive

```sh
wp-backup-manager export --site example.com --snapshot ID --class kept --unencrypted --yes --mode run
wp-backup-manager export --site example.com --snapshot ID --class temporary --encrypt --export-password-file /root/export.pass --yes --mode run
```

- [ ] Choose `temporary` or `kept`.
- [ ] Prefer encryption when the archive leaves the server.
- [ ] Use a separate private passphrase file, with mode `0600`, for encrypted exports.
- [ ] Copy the completed archive and its `.sha256` file.
- [ ] Verify the checksum on the receiving system.

Archives are stored under `/root/site-archives/wm-ID/`.
An archive includes one site, its SQL dump, manifests, local configuration, and `RECOVERY.txt`.
The manager writes a `.partial` file, verifies it, then publishes the final file and checksum.
Failed exports remain visible. Do not use a `.partial` file as a completed export.
The workspace also retains extracted data. Include it when you review disk usage.

Plain `.tar.gz` archives are **not encrypted** and contain site secrets.
Encrypted `.tar.gz.gpg` archives use GPG AES256 and the export passphrase.
Neither format needs the original restic password or original server.
Repository passwords are not included. Encrypted recovery kits with repository credentials are not implemented.

```sh
sha256sum -c example.com.tar.gz.gpg.sha256
gpg --output example.com.tar.gz --decrypt example.com.tar.gz.gpg
```

## Copy snapshots to another repository

```sh
wp-backup-manager copy --snapshot ID --destination /mnt/second-restic --destination-password-file /root/second.pass --init-destination --yes --mode run
wp-backup-manager copy --snapshot ID --snapshot OTHER_ID --destination-config /root/remote-restic.env --yes --mode run
```

Use `--init-destination` only for a new repository.
The destination needs separate credentials. A local destination cannot overlap live sites,
manager workspaces, removed-site archives, or the source repository. For remote backends, use a private destination environment file.
Set its `RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE`, and required backend credentials.
For a remote destination, the manager uses its backend credentials. A remote source can copy to a local destination with source backend credentials.
For two remote backends with different credential sets, first copy to a separate local repository, then copy from that repository to the final destination.
The manager refuses a direct copy with conflicting backend credentials.
Repository URLs must not contain passwords.

The manager reports destination snapshot IDs and runs a destination data check.
It does not delete source snapshots. Destination retention is independent.
Keep destination credentials separately. Test access from the receiving server.

## Recover manually

```sh
wp-backup-manager instructions --site example.com --mode run
```

- [ ] Review files and compare them with the target.
- [ ] Prepare a missing target with Ansible on the controller.
- [ ] Read target credentials without executing source `wp-config.php`.
- [ ] Save the current target database with `mariadb-dump`.
- [ ] Review the printed `mariadb` import command. Replace every placeholder.
- [ ] Copy only the selected files. Check ownership and permissions.
- [ ] Review domains, URLs, cron, email, payments, webhooks, and other integrations.
- [ ] Activate the target separately after these checks pass.

The helper prints instructions only. Source credentials are not assumed valid for the target.
A private MariaDB client file or an interactive `-p` prompt keeps passwords out of command arguments.

## Inspect and clean workspaces

```sh
wp-backup-manager workspaces --mode run
wp-backup-manager workspaces --full --mode run
wp-backup-manager cleanup --task wm-ID --yes --mode run
wp-backup-manager cleanup --state failed --yes --mode run
wp-backup-manager cleanup --older-than 14 --yes --mode run
wp-backup-manager cleanup --all --yes --mode run
wp-backup-manager cleanup --task wm-ID --delete-kept --yes --mode run
```

The table shows exact paths, task state, selection, creation time, and allocated bytes.
`--full` shows the full selected file list. Sizes do not follow symlinks.
Failed tasks stay visible until their data is removed.

Normal cleanup excludes active tasks, kept archives, live sites, repositories, removed-site archives,
and site rollback data. `--delete-kept` requires exact task IDs and separate approval.
A workspace that contains a restic repository requires manual review and cannot be removed by normal cleanup.
Unregistered files and legacy verification directories are review-only. Inspect and remove them manually.
Cleanup and data operations share `/run/lock/wp-hosting.lock`. A competing operation fails after a short wait.

## Check status and retention

```sh
wp-backup-manager status --mode run
wp-backup-manager retention --mode run
wp-backup-manager delete-snapshots --snapshot ID --mode show
wp-backup-manager delete-snapshots --snapshot ID --yes --mode run
```

Snapshot deletion shows IDs and dates, runs `forget --dry-run`, and requires approval.
Deletion removes the whole snapshot, including every site in a multi-site snapshot.
It does not prune repository packs. Snapshot deletion is separate from workspace cleanup.
A `keep` tag does not protect a snapshot under the current retention policy.

Schedule and retention are Ansible-managed:

| Inventory key | Purpose |
|---|---|
| `backup_restic_on_calendar` | Backup timer schedule |
| `backup_restic_keep_daily` | Daily snapshot count |
| `backup_restic_keep_weekly` | Weekly snapshot count |
| `backup_restic_keep_monthly` | Monthly snapshot count |
| `backup_restic_recovery_days` | Existing per-site recovery-file retention |

Edit the server inventory on the controller. Apply it with `scripts/lemp SERVER site`.
The manager shows service/timer state, recent warnings, mount state, free space, and available copy status.
It does not silently edit configuration.

## Read results

| Exit code | Result |
|---|---|
| `0` | Action finished, or preview printed |
| `2` | Usage or operation failed |
| `3` | Operator cancelled |

Task metadata and logs are root-owned. Each data task prints its ID and `result.log` path.
`checks.json` records available verification results. Failure logs omit exception data and credentials.
The terminal identifies the failed check. Failed data tasks also use the existing `tg-alert` facility.
A lost process is shown as interrupted. No automatic cleanup occurs at TUI exit.

Command previews use quoted arguments and credential-file references. They do not enter shell history.
A future preview with an inline password must request a separate disclosure approval.
`--show-secrets` approves disclosure only for that invocation; `--yes` does not.

## Completed snapshot IDs

Restic changes a snapshot ID when tags change. The backup command records the
final ID after all required steps succeed. New completion records also contain
the unique run tag.

The updater accepts old completion records through restic's `original` ID.
It requires one matching complete snapshot and the selected site's SQL dump
and manifest. It does not change old records or accept an unrelated backup.
No new backup is required only to correct an old recorded ID.
