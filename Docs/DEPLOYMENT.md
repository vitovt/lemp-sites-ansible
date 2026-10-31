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

The SSH handler validates the full configuration first. It restarts ssh.service
when ssh.socket is active. A regular listener is reloaded. Reloading a socket-
activated listener can fail to bind its inherited socket.

For an LXC container, the parent host must permit nftables operations.
If the parent host owns the firewall, set `firewall_manage: false` and apply
matching input and SMTP output rules on that host. Do not disable both firewalls.
LXC validation is still a separate release check.

Copy `inventories/example-server/` to `inventories/<server>/`. Rename the host
key in `settings.yml` to the directory name and set its SSH address. Every
command must select that `settings.yml` explicitly. See [Example inventory](EXAMPLES.md).
Declare all sites in `inventories/<server>/sites.yml`:

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
| `cloudflare` | Cloudflare address ranges and explicit trusted proxies | `CF-Connecting-IP` |
| `proxy` | Explicit `nginx_trusted_proxies` | `X-Forwarded-For` |
| `direct` | Public IPv6 clients and explicit trusted proxies | `CF-Connecting-IP` only from Cloudflare and trusted proxies |

In direct mode, `nginx_public_ipv4: false` blocks other non-local IPv4 web clients
in the managed firewall. Explicit trusted proxy addresses remain permitted.
Set it to true only if public IPv4 access is required.
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
proxy_set_header CF-Connecting-IP $remote_addr;
proxy_ssl_server_name on;
proxy_ssl_name $host;
proxy_ssl_verify on;
proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
proxy_pass https://192.0.2.20;
```

The parent proxy and its certificates are outside this repository.
For public IPv6 without Cloudflare proxying, use `nginx_access_mode: direct`.
Do not trust visitor-supplied IP headers from arbitrary internet addresses.

For direct IPv6 together with a LAN IPv4 parent proxy:

```yaml
nginx_access_mode: direct
nginx_public_ipv4: false
nginx_trusted_proxies: [192.0.2.1/32]
```

Use the actual source address of the parent proxy. The parent must overwrite
`CF-Connecting-IP` with its validated visitor address. This does not trust that
header from other IPv4 or IPv6 clients. See the
[parent upstream example](examples/proxmox-upstream.conf.example).

## Certificates

DNS-01 is the default:

```yaml
certbot_cloudflare_method: dns-cloudflare
```

Put a zone-scoped Cloudflare DNS edit token in `inventories/<server>/secrets/cloudflare.ini`.
The role copies it to the target with mode 0600. DNS-01 also works when the DNS
record is not proxied. No inbound HTTP request is needed for this challenge.

The default token serves every site without an override. To use another
Cloudflare account for a site, put a second token in a named file on the
control machine:

```sh
mkdir -m 700 -p inventories/my-server/secrets/cloudflare
cp inventories/my-server/secrets/cloudflare/customer_b.ini.example inventories/my-server/secrets/cloudflare/customer_b.ini
chmod 600 inventories/my-server/secrets/cloudflare/customer_b.ini
```

Set `dns_cloudflare_api_token` in that file. Then add the file name without
`.ini` to the site entry:

```yaml
wp_sites:
  second.example:
    user: second
    php_version: "8.3"
    cloudflare_token_ref: customer_b
```

For a new WordPress site, the site CLI can set this reference before its first
certificate request:

```sh
scripts/site add my-server second.example second 8.3 --install-wp --cloudflare-token-ref customer_b
```

Use a simple reference name: lowercase letters, digits, `_`, and `-`; start
with a letter. Do not put API tokens in the inventory. The role keeps the
default credentials at `/etc/letsencrypt/cloudflare.ini` and installs named
credentials under `/etc/letsencrypt/cloudflare-credentials/` with root-only
access. It checks that each required source file exists before deployment.
Certbot records one credential path per certificate. When a site's reference
changes, the role tests and updates that certificate's renewal settings.
Test renewal with `certbot renew --dry-run` after a token change. A certificate
for a primary name and its aliases uses one token; that token must have DNS
edit access to every zone in the certificate.

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

Keep each server directory outside Git and back up its secrets securely.
Missing secrets for an existing site or service stop preflight.
Do not generate replacement passwords to recover a lost secret directory.
Recover the original values. Use a separate procedure for credential rotation.

Mount the backup volume at `/mnt/backups`. Configure an independent remote copy
before you rely on the service for important data. See Operations.

```sh
scripts/lemp my-server doctor
scripts/lemp my-server site
scripts/lemp my-server verify
```

A syntax check is not a deployment test. A dry run on an empty host cannot read
files that later tasks will create. Validate new inventory on a disposable guest.

## Telegram alerts

Telegram is optional. With no `telegram.env`, alerts go to syslog only. To
enable Telegram for one server, create a bot with BotFather and obtain the chat
ID of the private alert chat or group. Set these two values in a copy of the
server template:

```sh
cp inventories/my-server/secrets/telegram.env.example inventories/my-server/secrets/telegram.env
chmod 600 inventories/my-server/secrets/telegram.env
nano inventories/my-server/secrets/telegram.env
```

Set `TG_BOT_TOKEN` and `TG_CHAT_ID`. Do not put them in `settings.yml`, Git, or
command arguments. Run `scripts/lemp my-server site` to install the file. The
file is root-only on the server. Test alert delivery from that server before
relying on it.

## PHP branches

Site configuration accepts `8.3`, `8.4`, and `8.5`. Set `php_default_version`
and the base `php_versions` list in `settings.yml`; a full server run also
installs branches requested in `sites.yml`. The current integration fixture
uses 8.3 and 8.4. PHP 8.5 is accepted by preflight but is not covered by that
fixture. Test plugins and extensions before changing a live site's branch.
