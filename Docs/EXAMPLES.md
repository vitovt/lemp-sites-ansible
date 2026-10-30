# Copy a server example

The directory `inventories/examples/example-server/` is a template. Ansible
does not deploy it by default. It has one server settings file and one site
list with two example sites. The site list contains every supported `wp_sites`
parameter. The server file shows the main host settings and optional tuning
values. All role defaults remain in `roles/*/defaults/main.yml`. Ansible reads
all sites for one server from the same `sites.yml`.
Separate site files with separate `wp_sites` mappings would overwrite each
other during variable loading.

For a new server called `wp02`, copy the directory into the production host
variables. Use a new name; do not replace an existing host directory:

```sh
cp -a inventories/examples/example-server inventories/prod/host_vars/wp02
nano inventories/prod/hosts.yml
nano inventories/prod/host_vars/wp02/settings.yml
nano inventories/prod/host_vars/wp02/sites.yml
```

Under `all.children.wp.hosts` in `hosts.yml`, add this line next to `wp01`:

```yaml
        wp02: {}
```

Keep the `wp01` entry as it is. The `wp02: {}` line only adds the new server
name; `settings.yml` supplies its SSH address, user, and port.
In `settings.yml`, check the guest SSH port, the IPv6 and LAN proxy policy,
the PHP branches, and the backup mount. Replace both example domains and
aliases in `sites.yml`. Set administrator email addresses if needed.
If you need only one site, remove the other site entry. Give every site a
different Linux user and database name. Create the AAAA records before you
request certificates. If you use the parent IPv4 proxy, create the A records
for that proxy and set its source LAN address in `nginx_trusted_proxies`.

The first example site uses `secrets/cloudflare.ini`. The second uses
`secrets/cloudflare/customer_b.ini`. Create the named token file on the control
machine, or remove `cloudflare_token_ref` to use the default token:

```sh
mkdir -m 700 -p secrets/cloudflare
cp -n secrets/cloudflare.ini.example secrets/cloudflare/customer_b.ini
chmod 600 secrets/cloudflare/customer_b.ini
nano secrets/cloudflare/customer_b.ini
```

Set a Cloudflare DNS edit token for the second site's zone. Keep the default
token in `secrets/cloudflare.ini` as described in [Deployment](DEPLOYMENT.md).
Mount a separate backup volume at `/mnt/backups` on the server. Then check and
install this server only:

```sh
ansible-playbook -i inventories/prod/hosts.yml playbooks/doctor.yml --limit wp02
ansible-playbook -i inventories/prod/hosts.yml playbooks/site.yml --limit wp02
ansible-playbook -i inventories/prod/hosts.yml playbooks/verify.yml --limit wp02
```

The server inventory name and its host variable directory name must match.
To add another site later, use this command. It writes the entry to this
server's `sites.yml`:

```sh
scripts/site add wp02 <domain> <user> 8.3 --install-wp
```
