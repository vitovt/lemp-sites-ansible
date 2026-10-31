# Server inventory example

`inventories/example-server/` is the only server directory in Git. It is a
safe template. Its address and domains are placeholders. Ansible has no default
inventory, so it does not deploy this example without an explicit selection.

One server directory contains:

```text
settings.yml                 one-host Ansible inventory and server settings
sites.yml                    one wp_sites mapping for all sites
secrets/cloudflare.ini       server default DNS token (create locally)
secrets/cloudflare/*.ini     optional site tokens (create locally)
secrets/telegram.env         optional Telegram alert settings (create locally)
secrets/db/                  generated site database passwords
secrets/valkey/              generated Valkey passwords
secrets/wp-admin/            generated WordPress admin passwords
secrets/restic.pass          generated backup password
```

The `.example` files in `secrets/` are safe templates. Real files in any server
secret directory are ignored by Git. Every other server directory is ignored
as a whole. Do not force-add it. Back up the entire private server directory.

Copy the example and rename the host key to match the directory name:

```sh
cp -a inventories/example-server inventories/my-server
chmod 700 inventories/my-server/secrets inventories/my-server/secrets/cloudflare
nano inventories/my-server/settings.yml
nano inventories/my-server/sites.yml
cp inventories/my-server/secrets/cloudflare.ini.example inventories/my-server/secrets/cloudflare.ini
chmod 600 inventories/my-server/secrets/cloudflare.ini
nano inventories/my-server/secrets/cloudflare.ini
```

The directory name and the only host key under `all.children.wp.hosts` must be
identical. `settings.yml` is a valid Ansible inventory. Every playbook loads
`sites.yml` from that inventory's directory. The server's `secrets_dir` is
fixed to `{{ inventory_dir }}/secrets`. These paths prevent one server from
loading another server's files.

The active example uses direct public IPv6, blocks public guest IPv4, and uses
one Cloudflare token. Comments in `settings.yml` show LAN IPv4 proxy,
Cloudflare-only, and HTTP-01 options. The site file has one active fresh site.
It documents every accepted site key. The second site is commented so the
initial copy needs only one DNS token. Uncomment it and create its named token
file if that site uses another Cloudflare account. Replace all example names.
`scripts/site` keeps these comments when it adds or removes a site.

An SSH private key can stay in `~/.ssh` or another path outside this directory.
Set `ansible_ssh_private_key_file` in `settings.yml` if Ansible does not use your
normal SSH identity. `scripts/lemp <server> doctor` requires mode 0600 on that
key and on all real files in this server's `secrets/` directory. After a ZIP
copy, restore those modes before running the command.

For each server, select its inventory explicitly:

```sh
scripts/lemp my-server doctor
scripts/lemp my-server site
scripts/lemp my-server verify
```
