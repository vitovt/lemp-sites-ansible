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
