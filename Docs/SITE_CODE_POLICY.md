# Work With Site Code

Use these commands on the managed server as root. A site owner has a separate
account for code changes. PHP-FPM cannot write to the site's code directories.

## Enter One Site

```sh
jump-in example.com
git status --short
wp plugin list
exit
```

`jump-in` opens Bash as the site owner in `htdocs`. The prompt shows the site,
owner, and PHP branch. `wp` uses that PHP branch and site path. Git has a
session-only operator identity. The site account keeps `nologin`; this command
does not enable SSH access for it.

From the root shell, use `wp-site git example.com status --short` to run one
Git command as the site owner. Use `wp-site check example.com` for the monitor
report. Use `wp-site check example.com plugins [PLUGIN]` to see plugin checksum
errors and unavailable WordPress.org checksums.

## Set Local Code Rules

The file is `/srv/sites/<domain>/conf/code-policy.yml`. Root owns it. Ansible
installs the helpers and does not write the file. The normal site owner and the
PHP runtime account cannot edit it directly. The `policy` command inside
`jump-in` uses a restricted privileged helper for that site only.
Run `policy` with no arguments inside `jump-in` to see the table and choose an
action from a menu.

```sh
wp-site policy example.com
wp-site policy show example.com --yaml
wp-site policy hold example.com example-plugin --reason "Local compatibility fix"
wp-site policy unhold example.com example-plugin
wp-site policy checksums example.com custom-plugin --mode git
wp-site policy allow-file example.com example-plugin includes/patch.php --yes
wp-site policy remove-file example.com example-plugin includes/patch.php
```

Use `--kind theme` with `hold` and `unhold` for themes. The helper asks for
missing arguments when a terminal is present. Without a terminal, give all
arguments. `allow-file` also needs `--yes` for a non-interactive call. Review
the file before you accept it. The helper displays its checksum and first
4096 bytes before it saves the rule.

`hold` stops automatic updates of that component. It does not change the
integrity check. `checksums --mode git` uses the site's tracked, clean Git
state as the local baseline for one plugin. Use it for a private or fully
custom plugin. Other plugins still use WordPress.org checksums.

`allow-file` accepts one current WordPress.org mismatch. It stores the exact
path and current SHA-256 for an added or modified file. For a deleted file, it
stores the expected absence. An unexpected file or a new file hash remains a
failure. The policy file is not a substitute for reviewing and committing code
to the site's Git repository.

## Back Up and Clone

The normal restic site backup includes `conf/code-policy.yml` because it backs
up the site directory. A clone copies the source code policy to the target
site. Review it after cloning, especially if target-specific plugins differ.
Keep a copy of this file when you move a site outside the managed backup flow.

## Read a Failure

```sh
wp-site check example.com plugins
wp-site git example.com status --short
wp-site git example.com diff -- wp-content/plugins/example-plugin
```

The first command shows each checksum mismatch. It reports accepted local
rules separately. It also shows plugins for which WordPress.org has no
checksum. A skipped checksum does not verify that plugin. Check its Git history
and release source before you accept changes.
