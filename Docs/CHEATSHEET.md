# Operations cheat sheet

Use these commands from the repository root. Replace `my-server`, domains,
addresses, and users with your values. Run controller commands as your normal
user. Run `wp-backup` and `wp-monitor` on the managed server as root.

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

- [ ] Copy `inventories/my-server/secrets/telegram.env.example` to
  `telegram.env` in the same directory.
- [ ] Add the bot token and chat ID. Set file mode `0600`.
- [ ] Apply and run a health check.

```sh
chmod 600 inventories/my-server/secrets/telegram.env
scripts/lemp my-server site
sudo wp-monitor --check example.com
```

Run the last command on the server. Without `telegram.env`, alerts go to
syslog only. See [Telegram setup](DEPLOYMENT.md#telegram-alerts).

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
