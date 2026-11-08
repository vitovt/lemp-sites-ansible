# Operations cheat sheet

## Backup manager (server, root)

- [ ] Run `wp-backup-manager` to select a task and inspect its command.
- [ ] Run `wp-backup-manager workspaces --mode run` to review temporary data.
- [ ] Use recovery to extract only. Review and apply live changes separately.

See [Backup manager](BACKUP_MANAGER.md) for CLI examples, exports, verification, and cleanup.

Use these commands from the repository root. Replace `my-server`, domains,
addresses, and users with your values. Run controller commands as your normal
user. Run `wp-backup` and `wp-monitor` on the managed server as root.

## Work on one site's code

- [ ] On the managed server, enter the site as its file owner.
- [ ] Review Git changes and plugin checksum errors before you accept them.

```sh
jump-in example.com
jump-in-help
php -v
git status --short
wp plugin list
site-check plugins
policy                 # Show local rules and choose an action.
exit
wp-site check example.com plugins
```

Inside the shell, Tab completes commands and component names. Run
`jump-in-help policy` for policy examples. See [site shell](SITE_SHELL.md).

### Exclude a plugin from managed updates

- [ ] Use the plugin directory name as its slug, for example `akismet`.
- [ ] Run from root on the server:

```sh
wp-site policy hold example.com akismet --reason "Local patch; keep this version"
wp-site policy show example.com
```

Or enter the site shell:

```sh
jump-in example.com
policy hold akismet --reason "Local patch; keep this version"
policy show
# Permit managed updates again:
policy unhold akismet
exit
```

A hold applies only to this site's `wp-update` runs. It does not disable
checksum checks. Raw `wp plugin update` calls do not honour the hold. Local
rules are site data; do not put them in Ansible. Use `allow-file` only after
you review the exact file state. See [site code operations](SITE_CODE_POLICY.md).

### Accept reviewed local plugin changes

These rules apply to one site. They do not change plugin files or create Git commits.
An update hold and an integrity exception are separate settings.

#### Check the current state

```sh
jump-in example.com
site-check plugins wordpress-seo
policy show
policy show --yaml
git status --short -- wp-content/plugins/wordpress-seo/
git diff -- wp-content/plugins/wordpress-seo/
```

Compare unexpected changes with the trusted release of the installed version.
If changes are already committed, review their Git commit; `git diff` only shows
uncommitted changes. Do not accept differences that you cannot explain.

#### Accept one exact file state

Exact file exceptions require `wordpress_org` mode. Use a path relative to
the plugin directory, not to `htdocs`. After review:

```sh
policy checksums wordpress-seo --mode wordpress_org
policy allow-file wordpress-seo inc/class-addon-manager.php --yes
policy show --yaml
site-check plugins wordpress-seo
```

Repeat `allow-file` for each approved file. The rule records the mismatch type
and SHA-256 for an added or modified file, or expected absence for a deleted
file. A later content change fails the check again. Reaccept only after review.
This mode keeps upstream checksum checks for the rest of the plugin.

#### Remove a file exception

```sh
policy remove-file wordpress-seo inc/class-addon-manager.php
policy show --yaml
site-check plugins wordpress-seo
```

This removes permission to accept the difference. It does not restore the file.
If the file still differs from the official release, expect `FAIL` again.
To accept a newly reviewed state instead, run `allow-file` again for that path.

#### Use Git as the baseline for a locally maintained plugin

Use this alternative when you maintain the whole plugin locally. First ensure
its code is tracked and reviewed changes are committed. Then:

```sh
policy checksums wordpress-seo --mode git
site-check plugins wordpress-seo
```

Git mode replaces upstream checksum verification for this plugin with a local
tracked, clean Git baseline. It is broader than exact file exceptions. Changing
to Git mode clears existing file exceptions. New commits become the reviewed
baseline; the operator is responsible for their content.

To return from Git mode to upstream checks:

```sh
policy checksums wordpress-seo --mode wordpress_org
site-check plugins wordpress-seo
```

Local differences will fail again unless you restore the release or add reviewed
exact file exceptions. Returning to upstream mode does not remove an update hold.
For existing upstream-mode exceptions, use `remove-file` to revoke each rule.

Finish the full check as root:

```sh
exit
wp-monitor --check example.com
```

`accepted` counts permitted mismatch rows; check `policy show --yaml` to see
whether acceptance uses exact hashes or Git mode. `unavailable` means upstream
checksums could not be obtained. It is not proof that those plugins are safe.

## 1. Prepare the control machine

- [ ] Clone the repository and install dependencies.

```sh
git clone git@github.com:vitovt/lemp-sites-ansible.git
cd lemp-sites-ansible
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml -p collections
export PATH="$PWD/.venv/bin:$PATH"
```

## 2. Create or restore a server inventory

| Task | Action |
| --- | --- |
| New server | `cp -a inventories/example-server inventories/my-server` |
| Restore controller archive | `7z x lemp-sites-ansible-YYYYMMDD-HHMMSS.zip` from the repository root |
| Export controller archive | Install `7zip`, then run `./archive.sh` |

- [ ] For a new server, change the host key, `ansible_host`, SSH port, network
  mode, and backup mount in `inventories/my-server/settings.yml`.
- [ ] Set the domain and a quoted `php_version` in
  `inventories/my-server/sites.yml`.
- [ ] Put the Cloudflare DNS token in
  `inventories/my-server/secrets/cloudflare.ini`. Use the `.example` file as
  the format guide. Use `chmod 600` for the token and SSH private key.
- [ ] Run `scripts/lemp my-server doctor`. Fix each reported error.

The server directory contains its settings, sites, and secrets. Keep private
inventories and keys out of Git. See [controller transfer](ARCHIVE.md).

## 3. Install a new server

- [ ] Set DNS, the backup mount, and SSH access. For a LAN proxy, configure
  the [parent Nginx route](examples/proxmox-upstream.conf.example).
- [ ] Check the inventory. Apply the full playbook. Verify the result.

```sh
scripts/lemp my-server doctor
scripts/lemp my-server site
scripts/lemp my-server verify
```

## 4. Add a site

- [ ] Check DNS and the certificate token.
- [ ] Create the site. The command runs the full playbook.
- [ ] Verify the server.

```sh
scripts/site add my-server new.example.com newsite 8.4 --install-wp
scripts/lemp my-server verify
```

For another Cloudflare account, add a private token file at
`inventories/my-server/secrets/cloudflare/customer_b.ini` (mode `0600`). Add
`--cloudflare-token-ref customer_b` to the `scripts/site add` command.

## 5. Change a site

| Change | Edit | Apply |
| --- | --- | --- |
| Site setting, alias, or pool limit | `inventories/my-server/sites.yml` | `scripts/lemp my-server site-apply example.com` |
| PHP branch | Quoted `php_version` in `sites.yml` | `scripts/lemp my-server site` |
| Certificate method | `certbot_cloudflare_method` in `settings.yml` | `scripts/lemp my-server site` |
| Cloudflare token for one site | `cloudflare_token_ref` in `sites.yml` and its token file | `scripts/lemp my-server site` |

- [ ] Run `scripts/lemp my-server verify` after each change.
- [ ] After a token change, run `certbot renew --dry-run` on the server.

New sites start with shared self-signed TLS. ACME failure prints a warning and
deployment continues. Fix the certificate setup, then repeat
`scripts/lemp SERVER site DOMAIN`. Success switches Nginx to the new certificate.
Self-signed TLS is not publicly trusted.

The default certificate method is `dns-cloudflare`. For HTTP-01, set
`certbot_cloudflare_method: webroot`. Make port 80 and
`/.well-known/acme-challenge/` reach the guest Nginx. See
[certificate setup](DEPLOYMENT.md#certificates). A new PHP branch needs a full
`site` run. `site-apply` refuses a branch that is not installed and managed.

## 6. Make a backup outside the schedule

- [ ] Connect to the server as an administrator.
- [ ] Back up one site and check its archive.

```sh
sudo wp-backup example.com
sudo wp-backup verify example.com
```

## 7. Restore a site from an archive

- [ ] Stage the site files from a restic snapshot.
- [ ] Check the staged path and dump. Restore the database only when the
  source and target are correct.

```sh
sudo wp-backup restore example.com
sudo wp-backup restore-db example.com /srv/restore/<run>/srv/sites/example.com/backup/db.sql.gz --yes
sudo wp-backup verify example.com
```

`restore` stages files; it does not replace the live site files. Use the
[restore procedure](OPERATIONS.md#backups) before a live recovery.

## 8. Remove a site

- [ ] Confirm the exact domain. Use the normal command to keep an archive.

```sh
scripts/site remove my-server old.example.com
scripts/lemp my-server verify
```

If the site credentials are lost and a new archive is impossible, use
`scripts/site remove my-server old.example.com --no-backup`. This removes the
site without a new backup.

## 9. Copy one site to another site

- [ ] Add DNS and a valid certificate path for the target.
- [ ] Clone the source to the new domain.

```sh
scripts/site clone my-server example.com staging.example.com
scripts/lemp my-server verify
```

The clone starts in maintenance mode. Review external API keys and payment
integrations before activation. See [clone rules](OPERATIONS.md#sites).
For a snapshot from another source, first restore it to a private staging
path. There is no one-command cross-site snapshot restore.

## 10. Change the backup schedule and retention

- [ ] Edit these keys in `inventories/my-server/settings.yml`.

```yaml
backup_restic_on_calendar: "*-*-* 03:15:00"
backup_restic_keep_daily: 7
backup_restic_keep_weekly: 4
backup_restic_keep_monthly: 6
```

- [ ] Apply and inspect the timer on the server.

```sh
scripts/lemp my-server site
sudo systemctl list-timers 'wp-*'
```

## 11. Change web access and SSH firewall rules

| Access | Settings in `settings.yml` |
| --- | --- |
| Direct IPv6 | `nginx_access_mode: direct`, `nginx_public_ipv4: false` |
| LAN proxy | `nginx_access_mode: proxy`, `nginx_trusted_proxies: [192.0.2.1/32]` |
| Cloudflare-only web access | `nginx_access_mode: cloudflare` |
| Guest SSH port | `firewall_ssh_ports: [22]` |

- [ ] Keep an existing SSH session and console access during a firewall
  change. Use the **guest** SSH port, not the NAT port on the Proxmox host.
- [ ] Edit `settings.yml`, run `scripts/lemp my-server site`, then test a new
  SSH connection before you close the old one.

The managed firewall has an input deny policy. `firewall_manage: false`
delegates these rules to the parent host. See [network modes](DEPLOYMENT.md#network-modes).

## 12. Enable Telegram alerts

### Create the bot and select the chat

- [ ] In Telegram, contact [BotFather](https://t.me/BotFather). Run `/newbot`.
- [ ] Save the token privately. Open your new bot and send `/start`.
- [ ] Obtain your chat ID. The code below runs on the control machine. It asks
  for the token without echoing it or putting it in shell history.

```sh
python3 - <<'PYCODE'
import getpass
import json
import urllib.request

token = getpass.getpass("Bot token: ").strip()
try:
    url = "https://api.telegram.org/bot" + token + "/getUpdates"
    with urllib.request.urlopen(url, timeout=20) as response:
        data = json.load(response)
except Exception:
    raise SystemExit("Request failed. Check the token and network access.") from None

chats = {}
for update in data.get("result", []):
    chat = update.get("message", {}).get("chat", {})
    if "id" in chat:
        chats[chat["id"]] = chat.get("title") or chat.get("first_name", "")
for chat_id, name in chats.items():
    print(chat_id, name)
if not chats:
    print("Send /start to your bot, then retry.")
PYCODE
```

Use the ID of the intended chat. Do not use the bot's own ID. See the
[Telegram tutorial](https://core.telegram.org/bots/tutorial) and
[getUpdates reference](https://core.telegram.org/bots/api#getupdates).

### Store and deliver credentials

- [ ] From the repository root, copy the safe template. `cp -n` preserves an
  existing credentials file.

```sh
cp -n inventories/example-server/secrets/telegram.env.example \
  inventories/my-server/secrets/telegram.env
chmod 600 inventories/my-server/secrets/telegram.env
nano inventories/my-server/secrets/telegram.env
```

Set these values in the file:

```dotenv
TG_BOT_TOKEN=YOUR_BOT_TOKEN
TG_CHAT_ID=YOUR_CHAT_ID
```

- [ ] Keep this file out of Git and chat messages.
- [ ] On an installed server, deliver only the Telegram configuration:

```sh
ansible-playbook \
  -i inventories/my-server/settings.yml playbooks/site.yml \
  --limit my-server --tags telegram_alert --skip-tags preflight,server_tools
```

This focused run skips the normally always-run preflight and tool delivery
roles. It does not deploy other local helper changes. A full
`scripts/lemp my-server site` run also installs the credentials, including on
a new server. The installed file is `/etc/tg-alert.env`, root-owned, mode `0600`.

### Test delivery on the server

Run as root. This sends a real Telegram message:

```sh
tg-alert "Manual test alert"
echo $?
```

Expect the message in the selected chat and exit code `0`. If delivery fails:

```sh
journalctl -t tg-alert -n 30 --no-pager
```

Check token, chat ID, DNS, and HTTPS access to `api.telegram.org`. Without a
credentials file, the helper still logs locally but cannot deliver Telegram
messages and returns failure. `wp-monitor --check` does not send a test alert.
See [Telegram setup](DEPLOYMENT.md#telegram-alerts).

## 13. Check health and updates

```sh
scripts/lemp my-server verify
scripts/site info my-server example.com
sudo wp-monitor --check example.com
sudo wp-monitor --files example.com
sudo wp-update plan example.com
sudo wp-update apply example.com --scope all --policy minor
```

Run the `sudo` commands on the server. Updates run from a root-owned timer;
WordPress cannot edit its own code. See [operations](OPERATIONS.md#monitoring-and-updates).

### Read a monitor report

- [ ] Run on the managed server as root. Omit `sudo` in a root session.

```sh
# One site:
sudo wp-monitor --check example.com
echo $?

# Host checks and all sites:
sudo wp-monitor --check
```

`--check` reports the configured checks. It does not repair files, create Git
commits, accept administrator changes, or send alerts. The Valkey connectivity
check briefly writes and removes a test key.

Example output only:

```text
PASS example.com db_connect
PASS example.com http_home
FAIL example.com git_status: 1 changed/untracked
SKIP example.com valkey: redis: false
```

| Result | Meaning | Next action |
| --- | --- | --- |
| `PASS` | This check passed | Continue reading the report |
| `FAIL` | A check failed or could not verify its target | Read the detail and `HINT`; investigate before accepting changes |
| `SKIP` | The check was not performed | Read the reason; this is not a successful verification |

| Site check | What it checks |
| --- | --- |
| `db_connect` | Database query through the site's PHP runtime user |
| `http_home` | Local HTTPS home-page response |
| `fpm_pool` | PHP-FPM service, socket, and an uncached HTTP response |
| `cert_expiry` | Served certificate, configured certificate, and expiry threshold |
| `valkey` | Site credentials and socket access; test-key write, read, and deletion |
| `core_checksums` | Core checksums and unexpected files under the configured rules |
| `plugin_checksums` | Plugin integrity under the site's code policy |
| `stray_php` | Unexpected PHP-like files in selected locations and `.user.ini` files |
| `php_in_writable` | PHP-like files in configured writable content directories |
| `git_status` | Changed or untracked site files |
| `admin_users` | Current administrators against the saved baseline |
| `content_spike` | New content against the configured time and count limits |

Enabled checks and thresholds come from `/etc/wp-monitor.conf`. Host checks
run when no domain is supplied. `PASS valkey` proves backend access; it does
not prove that WordPress has an active persistent object-cache drop-in.
Local HTTP checks do not verify the public Cloudflare or parent-proxy route.

Exit code `0` means no reported check failed; review any `SKIP` lines. Code `1`
means a check failed. Code `2` indicates a usage error or unknown site. Capture
`echo $?` immediately after the command, before running another command.

For details, enter `jump-in example.com` and use `site-check plugins` or Git.
Use root `wp-monitor --files example.com` for the extra/stray PHP file report.
Use `wp-monitor --ack example.com` only after reviewing intentional
administrator changes. It does not accept code or Git changes.

## 14. Investigate a failed verification

`scripts/lemp 125-wp-lnmp verify` is a read-only check. `8 FAIL` means eight
checks failed. It does not mean that Ansible changed eight files. The final
`All checks pass` task joins the failures into one long message. Read the
earlier `Result` task for separate `PASS`, `FAIL`, and `SKIP` lines.

- [ ] On the control machine, run the full check again if you need the Ansible
  inventory checks.
- [ ] On the server, run `wp-monitor --check` for one site. It prints one line
  per check. It does not change the administrator baseline or send an alert.

```sh
# Control machine:
scripts/lemp 125-wp-lnmp verify

# Managed server, as root:
wp-monitor --check site1.tech
wp-monitor --check site2.info
wp-monitor --files site2.info
```

`--files` lists extra core PHP and stray PHP paths. It does not list plugin
checksum mismatches. Use the WP-CLI command below for those paths. Investigate
each `FAIL` before you accept a new baseline. Do not run `wp-git-commit` on a
site with unrelated changes: it stages **all** changes in `htdocs`.

### Example: eight failures on two sites

| Check | Example | What to do |
| --- | --- | --- |
| `plugin_checksums` | `site1.tech`: two mismatches | List the exact plugin and file names. Check the files and their Git history. |
| `git_status` | `site1.tech`: `M commit_changes_to_git.sh` | Review the diff and file mode. Commit only this path if the change is approved. |
| `admin_users` | `site1.tech`: two added, one removed | Confirm the current administrators. Use `--ack` only if the change is approved. |
| `core_checksums` | `site2.info`: extra `footer.php` | Inspect the file in the WordPress root. Quarantine it only after review. |
| `plugin_checksums` | `site2.info`: eight mismatches | List all affected plugin files and review each plugin. |
| `stray_php` | `site2.info`: `wp-content/w3-total-cache-config.php` | Review why this PHP file is in `wp-content`. A known plugin name does not make a file safe. |
| `git_status` | `site2.info`: 203 changed or untracked paths | Review status, staged changes, and diffs in batches. Do not accept all paths at once. |
| `admin_users` | `site2.info`: `Vitovt` added, `wpadmin` removed | Confirm the accounts and IDs. Then use `--ack` if the change is approved. |

### List plugin checksum differences

Run these commands on the managed server as root. Change `SITE` for the other
site. The PHP runtime user can read the code but cannot update it.

```sh
SITE=site1.tech
BASE="/srv/sites/$SITE"
SITE_USER=$(sed -n 's/^SITE_USER=//p' "$BASE/conf/site.env")
PHP_VERSION=$(sed -n 's/^PHP_VERSION=//p' "$BASE/conf/site.env")
runuser -u "$SITE_USER-php" -- env \
  HOME="$BASE/tmp" WP_CLI_CACHE_DIR="$BASE/tmp/wp-cli-cache" \
  "php$PHP_VERSION" /usr/local/bin/wp --path="$BASE/htdocs" \
  --skip-plugins --skip-themes plugin verify-checksums --all
```

For `site1.tech`, this command reported `File was added` for
`all-in-one-wp-migration/storage/index.php` and `storage/index.html`. Both
contained `Kangaroos cannot jump here`. Check their current content, Git
status, and history before you treat them as known plugin files:

```sh
SITE=site1.tech
BASE="/srv/sites/$SITE"
SITE_USER=$(sed -n 's/^SITE_USER=//p' "$BASE/conf/site.env")
P=wp-content/plugins/all-in-one-wp-migration/storage
stat -c '%a %U:%G %n' "$BASE/htdocs/$P/index.php" "$BASE/htdocs/$P/index.html"
sha256sum "$BASE/htdocs/$P/index.php" "$BASE/htdocs/$P/index.html"
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" status --short -- "$P"
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" log -1 -- "$P"
```

This example does not approve other files or the whole plugin. A plugin
checksum `404` for a commercial plugin means that WordPress.org did not
provide a checksum. It does not prove that the plugin is safe. The current
`wp-monitor` uses the site-local code policy for checksum exceptions. Use
`jump-in SITE`, then `policy` to review or add an exact file exception. See
"Accept reviewed local plugin changes" above. Do not edit the installed monitor
to hide a warning; Ansible will replace that edit.

### Review and commit one Git change

Run as root on the managed server. The commands run Git as the site file owner.
`--only` limits the commit to the named path, even if other paths are staged.
Use this example only after you approve the diff or file mode change.

```sh
SITE=site1.tech
BASE="/srv/sites/$SITE"
SITE_USER=$(sed -n 's/^SITE_USER=//p' "$BASE/conf/site.env")
P=commit_changes_to_git.sh
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" status --short
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" diff -- "$P"
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" diff --summary -- "$P"
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" diff --cached -- "$P"
# After review, commit this path only:
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" \
  -c user.name='Site operator' -c user.email='operator@example.invalid' \
  commit --only -m 'Review script file mode' -- "$P"
wp-monitor --check "$SITE"
```

If the file has only a mode change, `git diff` can show no content change.
`git diff --summary` shows the mode change. Use a real operator identity for
the commit. For many changed paths, inspect both `git diff` and
`git diff --cached` before any commit.

### Review administrator changes

List the current administrators as the PHP runtime user. Compare account IDs
and login names with the alert. If the change came from an approved restore,
accept the new baseline with `--ack`.

```sh
SITE=site1.tech
BASE="/srv/sites/$SITE"
SITE_USER=$(sed -n 's/^SITE_USER=//p' "$BASE/conf/site.env")
PHP_VERSION=$(sed -n 's/^PHP_VERSION=//p' "$BASE/conf/site.env")
runuser -u "$SITE_USER-php" -- env \
  HOME="$BASE/tmp" WP_CLI_CACHE_DIR="$BASE/tmp/wp-cli-cache" \
  "php$PHP_VERSION" /usr/local/bin/wp --path="$BASE/htdocs" \
  --skip-plugins --skip-themes user list \
  --role=administrator --fields=ID,user_login,user_email
# Only after you confirm every administrator:
wp-monitor --ack "$SITE"
wp-monitor --check "$SITE"
```

`--ack` accepts the administrator list. It does not accept a checksum or Git
failure. Run the same review for `site2.info` before you accept its list.

### Inspect extra or stray PHP

These commands read metadata and Git state. They do not execute the PHP files.
Review the content with a trusted editor before you remove, quarantine, or
accept a file.

```sh
SITE=site2.info
BASE="/srv/sites/$SITE"
SITE_USER=$(sed -n 's/^SITE_USER=//p' "$BASE/conf/site.env")
wp-monitor --files "$SITE"
stat -c '%a %U:%G %n' "$BASE/htdocs/footer.php" \
  "$BASE/htdocs/wp-content/w3-total-cache-config.php"
sha256sum "$BASE/htdocs/footer.php" \
  "$BASE/htdocs/wp-content/w3-total-cache-config.php"
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" status --short
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" diff --cached --stat
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" diff --stat
runuser -u "$SITE_USER" -- git -C "$BASE/htdocs" status --short -- \
  footer.php wp-content/w3-total-cache-config.php
```

For a report-only Ansible inspection, use `site-repair-core.yml` without
`confirm`. That playbook does not repair or quarantine without confirmation:

```sh
# Control machine:
ansible-playbook -i inventories/125-wp-lnmp/settings.yml \
  playbooks/site-repair-core.yml --limit 125-wp-lnmp \
  -e repair_site=site2.info
```

## 15. Enable Redis Object Cache (Valkey)

Valkey is the server backend. Redis Object Cache is the WordPress plugin.
This is an object cache. It can work with the WP Rocket page cache.

### Check the environment

- [ ] The site inventory has `redis: true` (the default).
- [ ] Ansible has configured the site's Valkey account and socket access.
- [ ] `wp-config.php` has the site's `WP_REDIS_*` settings and ACL credentials.
  New installations get these settings from Ansible. Ansible does not overwrite
  an existing `wp-config.php`. Changing inventory alone does not add them there.
- [ ] Do not copy another site's password or key prefix. Do not publish credentials.
- [ ] Review existing Git changes. Do not include unrelated changes in this commit.

On the server, as root:

```sh
jump-in example.com
```

Run the following commands inside this site shell. `php` uses the site's PHP
version. The normal `wp` wrapper skips plugins and themes. For plugin-provided
`redis` commands, call WP-CLI through `php` and omit `--skip-plugins`.
This loads active plugins; run it only after reviewing the site code.

```sh
git status --short
wp plugin list --fields=name,status,version
ls -ld wp-content
```

### Install the plugin and drop-in

- [ ] Install the plugin. If already installed, activate it without reinstalling.

```sh
wp plugin install redis-cache --activate
# Already installed instead:
# wp plugin activate redis-cache
```

- [ ] Inspect the current status. An absent drop-in reports `Not enabled`.

```sh
php /usr/local/bin/wp --path="$PWD" --skip-themes redis status
```

`Filesystem: File modifications are not allowed` is expected. Keep
`DISALLOW_FILE_MODS` enabled. Do not edit the plugin source to bypass it.
Install the plugin's supplied drop-in as the code owner:

```sh
if [ -e wp-content/object-cache.php ] || [ -L wp-content/object-cache.php ]; then
    echo 'STOP: object-cache.php exists. Review it before replacement.'
else
    install -m 0640 \
      wp-content/plugins/redis-cache/includes/object-cache.php \
      wp-content/object-cache.php
fi
```

- [ ] If `STOP` appears, inspect the existing file. Do not overwrite another
  cache provider's drop-in. This branch does not install or enable anything.
- [ ] Confirm that the file is not empty and matches the plugin's supplied file.

```sh
test -s wp-content/object-cache.php && \
  cmp wp-content/plugins/redis-cache/includes/object-cache.php \
      wp-content/object-cache.php
php /usr/local/bin/wp --path="$PWD" --skip-themes redis status
```

Expected: `Status: Connected`, `Drop-in: Valid`, `Ping: 1`, and `Errors: []`.
Keep `WP_REDIS_SELECTIVE_FLUSH` enabled for this shared Valkey instance.
Do not run `redis-cli FLUSHALL` or `FLUSHDB`.

### Test persistence and commit

- [ ] Write and read a temporary key through separate WP-CLI processes.

```sh
CHECK_KEY="lemp-check-$(date +%s)-$$"
wp cache set "$CHECK_KEY" valkey-ok lemp-check 60
wp cache get "$CHECK_KEY" lemp-check
wp cache delete "$CHECK_KEY" lemp-check
unset CHECK_KEY
```

The read must print `valkey-ok`. The delete must succeed. A further read after
that delete returns `not found` and exit code `1`. This is expected; the wrapper
can also print a generic failure hint. It does not mean that Valkey failed.

- [ ] Review and commit only the plugin and drop-in. Do not use `git add .`.

```sh
git add -- wp-content/plugins/redis-cache wp-content/object-cache.php
git diff --cached --stat
git diff --cached
# Confirm that no unrelated paths were already staged. Then commit:
git commit -m "Enable Redis Object Cache"
exit
```

- [ ] As root, check permissions and runtime connectivity:

```sh
site-fix-perms example.com --check
wp-monitor --check example.com
```

The owner-side test does not prove that PHP-FPM can access Valkey. Review the
monitor's Valkey result and open the site. Investigate failures before repair.

After a Redis Object Cache plugin update, review whether its supplied
`includes/object-cache.php` differs from the installed drop-in. Update the
drop-in deliberately, repeat these checks, and commit the reviewed change.
Do not assume that a plugin update replaced the drop-in.

Reference: [Redis Object Cache CLI implementation](https://github.com/rhubarbgroup/redis-cache/blob/develop/includes/cli/class-commands.php).
