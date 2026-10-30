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

The commands below run on the control machine. This example calls the server
`wp01` and the site `example.com`. Replace the example address and domain with
your own values. Use Python 3.12 or later on the control machine. The server
must run Debian 13, accept SSH keys, and have a separate volume mounted at
`/mnt/backups`. Keep access to the server console during the first deployment.

### 1. Prepare the control machine

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
```

Set `dns_cloudflare_api_token` in `secrets/cloudflare.ini` to a Cloudflare token
with DNS edit access to the site's zone. This file stays on the control machine.
The default certificate method uses DNS-01; the Cloudflare proxy can be on or off.

### 2. Add and install a server

Open the production inventory:

```sh
nano inventories/prod/hosts.yml
```

Set the `wp01` entry to the server's reachable SSH address. This can be its
public IPv6 address or its NAT IPv4 address. Use the external forwarded port
for `ansible_port` if SSH is behind NAT:

```yaml
all:
  children:
    wp:
      hosts:
        wp01:
          ansible_host: "YOUR_SERVER_IPV6"
          ansible_user: root
          ansible_port: 22
```

Set the network mode in `inventories/prod/group_vars/all.yml`. This example
accepts direct IPv6 and an optional parent Nginx proxy over LAN IPv4:

```sh
nano inventories/prod/group_vars/all.yml
```

```yaml
nginx_access_mode: direct
nginx_public_ipv4: false
nginx_trusted_proxies: []
firewall_ssh_ports: [22]
```

If the parent proxy connects from LAN IPv4, replace `[]` with its source address,
for example `[192.168.77.1/32]`. Set `firewall_ssh_ports` to the SSH port on the
server itself; this can differ from the NAT port in `ansible_port`. Configure the
parent proxy as shown in [Deployment](Docs/DEPLOYMENT.md).

For a new server, replace the sample `jenet.info` entry in
`inventories/prod/host_vars/wp01/sites.yml` with an empty site list:

```sh
nano inventories/prod/host_vars/wp01/sites.yml
```

```yaml
wp_sites: {}
```

Keep the existing entry if this server already hosts that site. Check the
configuration, then install the server services:

```sh
ansible-playbook -i inventories/prod/hosts.yml playbooks/doctor.yml --limit wp01
ansible-playbook -i inventories/prod/hosts.yml playbooks/site.yml --limit wp01
```

To add another server, copy the `wp01` block under `hosts`, rename it `wp02`,
and set its own SSH address and port. Create its site list and install it:

```sh
nano inventories/prod/hosts.yml
mkdir -p inventories/prod/host_vars/wp02
printf 'wp_sites: {}\n' > inventories/prod/host_vars/wp02/sites.yml
ansible-playbook -i inventories/prod/hosts.yml playbooks/doctor.yml --limit wp02
ansible-playbook -i inventories/prod/hosts.yml playbooks/site.yml --limit wp02
```

The new server inherits `group_vars/all.yml`. If its network settings differ,
put its overrides in `inventories/prod/host_vars/wp02/network.yml`.

### 3. Create and check a site

Point the site's AAAA record to the server's public IPv6 address. If you use a
parent IPv4 proxy, point the A record to that proxy. Replace `example.com` below
with a real domain in the token's Cloudflare zone. The command creates the site,
installs WordPress, and asks for confirmation:

```sh
scripts/site add wp01 example.com example 8.3 --install-wp
scripts/site info wp01 example.com
cat secrets/wp01/wp-admin/example.com.pass
ansible-playbook -i inventories/prod/hosts.yml playbooks/verify.yml --limit wp01 -e wp_site_only=example.com
```

The WordPress administrator is `wpadmin` by default. Its generated password is
in `secrets/wp01/wp-admin/example.com.pass` on the control machine. Sign in at
`https://example.com/wp-admin/` to edit pages and posts. WordPress cannot install
or edit plugins and themes through the web interface. For checked core, plugin,
and theme updates, run `wp-backup update example.com --yes` as root on the server.
See [Operations](Docs/OPERATIONS.md) for the update and rollback rules.

### 4. Change site parameters

Edit the site's entry in `inventories/prod/host_vars/wp01/sites.yml`. The `add`
command already created this entry. For example, add an alias and change the
PHP-FPM process limit after you create the alias DNS record:

```yaml
wp_sites:
  example.com:
    user: example
    db_name: example
    php_version: "8.3"
    wp_install: true
    aliases: [www.example.com]
    pm_max_children: 6
```

Keep the existing values that you do not intend to change. Apply and check the
site. Changing its domain, Linux user, or database name needs a migration; do
not rename those values in this file.

```sh
nano inventories/prod/host_vars/wp01/sites.yml
ansible-playbook -i inventories/prod/hosts.yml playbooks/site.yml --limit wp01 -e wp_site_only=example.com
ansible-playbook -i inventories/prod/hosts.yml playbooks/verify.yml --limit wp01 -e wp_site_only=example.com
```

### 5. Back up or remove a site

Run a backup when needed. The remove command asks you to type the domain. It
creates a backup and a private archive before it deletes the site and its
inventory entry:

```sh
ansible-playbook -i inventories/prod/hosts.yml playbooks/site-backup.yml --limit wp01 -e wp_site_only=example.com
scripts/site remove wp01 example.com
```

Read [Deployment](Docs/DEPLOYMENT.md), [Operations](Docs/OPERATIONS.md),
[Security](Docs/SECURITY.md), [Reference](Docs/REFERENCE.md), and [Testing](Docs/TESTING.md).
See [Audit](Docs/AUDIT.md) and [Validation](Docs/VALIDATION.md) for findings,
tested behavior, and remaining limits.
