# WordPress hosting with Ansible

This project installs and manages WordPress hosting on Debian 13 VMs and
unprivileged LXC containers. Each server has one private directory under
`inventories/` with its settings, sites, and controller secrets.

## Features

- Nginx, PHP-FPM, MariaDB, Valkey, and per-site Linux and PHP runtime users.
- Direct public IPv6, a trusted LAN IPv4 proxy, or Cloudflare-only access.
- Let's Encrypt DNS-01 with a default token per server and optional site tokens.
- HTTP-01 through Nginx as an explicit exception.
- Firewall rules, blocked SMTP, page cache, checked updates, and alerts.
- Restic backup, restore, staging clone, site removal, and health checks.
- Controller tests and isolated integration tests on a disposable VM.

## Requirements

Use Python 3.12 or later on the control machine. The target must run Debian 13,
accept SSH keys, and provide a separate mounted backup volume at `/mnt/backups`.
Keep the private SSH key outside this repository. Install the pinned Python and
Ansible dependencies below. Use a console during the first network deployment.

## Fast Start

Clone the repository. Copy the safe example to a new server directory:

```sh
git clone git@github.com:vitovt/lemp-sites-ansible.git
cd lemp-sites-ansible
cp -a inventories/example-server inventories/my-server
chmod 700 inventories/my-server/secrets inventories/my-server/secrets/cloudflare
```

In `inventories/my-server/settings.yml`, rename the `example-server` host key to
`my-server`, set `ansible_host`, and check the SSH port, IPv6 or LAN proxy mode,
and backup mount. In `sites.yml`, replace `site1.example.com` with your domain.
Copy `secrets/cloudflare.ini.example` to `secrets/cloudflare.ini` inside this
server directory, set a zone-scoped DNS edit token, and set mode `0600`.
The default certificate method is Cloudflare DNS-01. Point the domain's AAAA
record to the server; point its A record to the parent proxy if one is used.

These five commands install the controller dependencies, check the server,
install hosting, and verify it:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml -p collections
PATH="$PWD/.venv/bin:$PATH" scripts/lemp my-server doctor
PATH="$PWD/.venv/bin:$PATH" scripts/lemp my-server site
PATH="$PWD/.venv/bin:$PATH" scripts/lemp my-server verify
```

`doctor` checks local secret and SSH key permissions before Ansible connects.
The server name is always explicit; `ansible.cfg` has no default inventory.
Run `export PATH="$PWD/.venv/bin:$PATH"` before the later commands below.
For a site change after installation, edit `inventories/my-server/sites.yml`
and run `scripts/lemp my-server site-apply <domain>`. Use
`scripts/site add my-server <domain> <linux-user> 8.3 --install-wp` to add a site;
it starts a full server convergence. Use `scripts/site info`, `clone`, and
`remove` for the other basic operations. The WordPress administrator password
is in `inventories/my-server/secrets/wp-admin/<domain>.pass`.

## Documentation

- [Deployment](Docs/DEPLOYMENT.md): server, network, certificates, and first run.
- [Example inventory](Docs/EXAMPLES.md): all template fields and copy steps.
- [Operations](Docs/OPERATIONS.md): site commands, backup, and restore.
- [Security](Docs/SECURITY.md): access model and threat boundaries.
- [Reference](Docs/REFERENCE.md): playbooks and roles.
- [Testing](Docs/TESTING.md): local and isolated integration tests.
- [Proxy example](Docs/examples/proxmox-upstream.conf.example): parent Nginx route.
- [Validation](Docs/VALIDATION.md): observed checks and unverified cases.

## License

No license is currently provided.
