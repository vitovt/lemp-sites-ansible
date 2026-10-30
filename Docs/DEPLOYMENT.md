# Deployment

## Control machine

Use Python 3.12 or later. Run commands from the repository root.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml -p collections
export PATH="$PWD/.venv/bin:$PATH"
```

The requirements files pin the tested control tools and collections.
Review dependency changes before you update them.

## Target

Use a dedicated Debian 13 machine. Configure key-based SSH and passwordless sudo.
The default SSH role installs a small key-only configuration.
Set `common_ssh_admin_keys_file` for the account used by Ansible.
Set `firewall_ssh_ports` to the SSH ports on the guest, not the NAT ports on the host.
Keep a console session available when you change network access.

For an LXC container, the parent host must permit nftables operations.
If the parent host owns the firewall, set `firewall_manage: false` and apply
matching input and SMTP output rules on that host. Do not disable both firewalls.
LXC validation is still a separate release check.

Set the real address in `inventories/prod/hosts.yml`.
Declare sites in `inventories/prod/host_vars/<host>/sites.yml`:

```yaml
wp_sites:
  example.com:
    user: example
    db_name: example
    php_version: "8.4"
    aliases: [www.example.com]
    wp_install: true
```

The site root is `/srv/sites`. Each site has `htdocs`, `conf`, `logs`, `tmp`, and `backup`.
PHP can write only to selected content directories, logs, and its temporary directory.
The owner account has no SSH shell. Use root and the supplied tools for maintenance.
Do not use a system account or a system database name for a site.

## Network modes

Select one `nginx_access_mode`.

| Mode | Accepted web peers | Visitor IP header |
| --- | --- | --- |
| `cloudflare` | Cloudflare address ranges | `CF-Connecting-IP` |
| `proxy` | Explicit `nginx_trusted_proxies` | `X-Forwarded-For` |
| `direct` | Public IPv6 clients | Cloudflare header only from Cloudflare peers |

In direct mode, `nginx_public_ipv4: false` blocks non-local IPv4 web clients in the
managed firewall. Set it to true only if public IPv4 access is required.
An input deny policy is used for both IP families. SSH, loopback, established
connections, and ICMP including IPv6 neighbor discovery remain available.
Unknown host names do not select a WordPress vhost.

For a LAN proxy:

```yaml
nginx_access_mode: proxy
nginx_trusted_proxies: [10.10.10.1/32]
```

Trust only the actual proxy addresses. On the parent Nginx proxy, use validated
Cloudflare real-IP ranges if Cloudflare is present. Replace incoming forwarding
headers with the validated address:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_ssl_server_name on;
proxy_ssl_name $host;
proxy_ssl_verify on;
proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
proxy_pass https://10.10.10.20;
```

The parent proxy and its certificates are outside this repository.
For public IPv6 without Cloudflare proxying, use `nginx_access_mode: direct`.
Do not trust visitor-supplied IP headers from arbitrary internet addresses.

## Certificates

DNS-01 is the default:

```yaml
certbot_cloudflare_method: dns-cloudflare
```

Put a zone-scoped Cloudflare DNS edit token in `secrets/cloudflare.ini`.
The role copies it to the target with mode 0600. DNS-01 also works when the DNS
record is not proxied. No inbound HTTP request is needed for this challenge.

For the explicit HTTP-01 exception:

```yaml
certbot_cloudflare_method: webroot
```

No DNS API token is used. Public DNS must point to the reachable Nginx endpoint.
Port 80 must reach the guest challenge location. A LAN proxy must forward
`/.well-known/acme-challenge/` to the guest. The shared webroot is
`/var/lib/letsencrypt`. A temporary HTTP vhost supports first issuance.
Other HTTP paths redirect to HTTPS after site setup.

Certificate errors stop deployment. `certbot_cloudflare_require_valid: true` is
the default. Disable the certificate requirement only in an isolated test.
The Debian certbot timer renews certificates and reloads Nginx after validation.
Use Cloudflare Full (strict) for the origin connection.

## Secrets and first run

Keep controller secrets outside Git. Back up the secret directory separately.
Missing secrets for an existing site or service stop preflight.
Do not generate replacement passwords to recover a lost secret directory.
Recover the original values. Use a separate procedure for credential rotation.

Mount the backup volume at `/mnt/backups`. Configure an independent remote copy
before you rely on the service for important data. See Operations.

```sh
ansible-playbook -i inventories/prod/hosts.yml playbooks/doctor.yml
ansible-playbook -i inventories/prod/hosts.yml playbooks/site.yml
ansible-playbook -i inventories/prod/hosts.yml playbooks/verify.yml
```

A syntax check is not a deployment test. A dry run on an empty host cannot read
files that later tasks will create. Validate new inventory on a disposable guest.
