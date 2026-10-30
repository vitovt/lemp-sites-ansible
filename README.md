# WordPress hosting with Ansible

This project installs and manages WordPress hosting on Debian 13.
The target is a dedicated VM or an unprivileged LXC container.

Features:

- Nginx, PHP-FPM, MariaDB, and Valkey.
- Separate users, database accounts, and PHP pools for each site.
- Direct public IPv6, a trusted LAN Nginx proxy, or both.
- Optional Cloudflare-only access and trusted visitor IP headers.
- An IPv4 and IPv6 firewall. SMTP is blocked by default.
- Let's Encrypt certificates with Cloudflare DNS-01, or HTTP-01 through Nginx.
- Safe page caching and access restrictions for writable files.
- Restic backups, restore checks, and controlled database restore.
- Site creation, staging clones, removal archives, and code repair.
- Integrity checks, persistent security alerts, and checked scheduled updates.
- Controller tests and integration tests for an explicit disposable VM.

## Fast Start

Use Python 3.12 or later on the control machine and a Debian 13 server with SSH
key access. Mount a separate backup volume at
`/mnt/backups` on the server. Point the site's AAAA record to its public IPv6
address. If you use a parent Nginx proxy for IPv4, point the A record to that
proxy. Run these commands on the control machine:

```sh
git clone git@github.com:vitovt/lemp-sites-ansible.git
cd lemp-sites-ansible
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml -p collections
export PATH="$PWD/.venv/bin:$PATH"
cp secrets/cloudflare.ini.example secrets/cloudflare.ini
chmod 600 secrets/cloudflare.ini
nano secrets/cloudflare.ini
nano inventories/prod/hosts.yml
nano inventories/prod/group_vars/all.yml
nano inventories/prod/host_vars/wp01/sites.yml
```

Set `dns_cloudflare_api_token` in `secrets/cloudflare.ini` to a token with DNS
edit access to your zone. In `hosts.yml`, set `ansible_host` to the reachable
server address and set `ansible_port` if SSH uses a nonstandard port. In
`group_vars/all.yml`, set `nginx_access_mode: direct` for public IPv6. Keep
`nginx_public_ipv4: false`. If you use a parent proxy, set
`nginx_trusted_proxies: [<proxy-LAN-IPv4>/32]` to its actual source address.
In `sites.yml`, replace the sample site with `wp_sites: {}`. Use the guest SSH
port in `firewall_ssh_ports` if it differs from 22.

Replace `example.com` below with a domain in your Cloudflare zone. DNS-01 is
the default certificate method. The `add` command installs WordPress and
prompts for confirmation.

```sh
ansible-playbook playbooks/doctor.yml --limit wp01
ansible-playbook playbooks/site.yml --limit wp01
scripts/site add wp01 example.com example 8.3 --install-wp
ansible-playbook playbooks/verify.yml --limit wp01 -e wp_site_only=example.com
scripts/site info wp01 example.com
ansible-playbook playbooks/site-backup.yml --limit wp01 -e wp_site_only=example.com
```

To change a site, edit its entry in `sites.yml`. For example, add
`aliases: [www.example.com]` after you create the DNS record. Apply the change
and check the site. To remove a site, use the last command; it makes a backup
and an archive before deletion, and asks you to confirm the domain.

```sh
nano inventories/prod/host_vars/wp01/sites.yml
ansible-playbook playbooks/site.yml --limit wp01 -e wp_site_only=example.com
ansible-playbook playbooks/verify.yml --limit wp01 -e wp_site_only=example.com
scripts/site remove wp01 example.com
```

Read [Deployment](Docs/DEPLOYMENT.md), [Operations](Docs/OPERATIONS.md),
[Security](Docs/SECURITY.md), [Reference](Docs/REFERENCE.md), and [Testing](Docs/TESTING.md).
See [Audit](Docs/AUDIT.md) and [Validation](Docs/VALIDATION.md) for findings,
tested behavior, and remaining limits.
