# Configuration reference

## Roles and stored state

| Role | Main state |
| --- | --- |
| preflight | Site schema, platform, ports, free space, original secrets, static DB settings |
| common | Base packages, key-only SSH, timezone, Debian and Sury updates |
| nginx | Vhosts, safe FastCGI cache, peer policy, limits, security headers |
| firewall | The `inet wp_hosting` nftables table and `wp-firewall.service` |
| php | Sury repository, multiple PHP branches, FPM tuning, pinned WP-CLI |
| mariadb | Local socket service, site DBs, escaped database grants |
| valkey | Unix socket, hashed ACL credentials, site key prefixes |
| certbot_cloudflare | DNS-01 or webroot HTTP-01, renewal, strict certificate checks |
| site | Owner/runtime users, directories, config checks, cron, optional installation |
| backup_restic | Dumps, manifests, complete snapshots, staged restore, recovery archives |
| wp_monitor | Local health, integrity, administrators, content volume, alerts |
| maintenance | Checked weekly updates with rollback |

Read the role defaults before you override a setting. The site root is fixed at
`/srv/sites`. Some helpers use fixed system paths. Do not move these paths by
changing a single role variable.

A site uses these users:

- `<owner>` owns code. It has no interactive shell.
- `<owner>-php` runs PHP and cron. It cannot write ordinary code files.
- `www-data` reads content through the site group. It cannot follow webroot symlinks.

Code directories use mode 2750. Ordinary files use 0640. Writable content
directories use 2770 and their files use 0660. Runtime temporary storage and
owner CLI temporary storage use separate 0700 directories. The backup directory
is root-only. Do not copy files as root and assume that the existing permissions
are safe. Run the permission checker after a reviewed import.

## Cache plugins

Fresh installation does not install all application plugins. Existing sites keep
their own plugins. Install and review these plugins when the application needs them:

- Redis Object Cache for the Valkey object cache.
- Nginx Helper for page cache invalidation.

Valkey uses `/run/valkey/valkey-server.sock`. The site flag remains `redis: true`
for plugin compatibility. The template supplies the ACL user, password, and
`<owner>:` key prefix. Global FLUSHDB and FLUSHALL are denied.

Some Redis Object Cache versions call FLUSHDB during activation. In that case,
copy the plugin's object-cache.php drop-in as the site owner instead:

```sh
runuser -u <owner> -- cp \
  /srv/sites/<domain>/htdocs/wp-content/plugins/redis-cache/includes/object-cache.php \
  /srv/sites/<domain>/htdocs/wp-content/object-cache.php
site-fix-perms <domain>
wp-git-commit <domain> "reviewed object cache activation"
```

Use the selective prefix flush supported by the configured plugin version.
Test `wp cache flush` before production. Do not grant global flush rights to fix
a plugin error. A shared Valkey memory budget is not a per-site memory quota.

In Nginx Helper, select FastCGI cache and the GET PURGE method. PHP calls the local
`/purge/<url>` path. The host file maps cached site domains to loopback.
A trusted TLS certificate is needed for the plugin's local HTTPS request.
Purge through that plugin is not validated by the self-signed VM fixture.
The root lifecycle tools flush the target Valkey prefix and clear the shared
page cache before they release maintenance. This also works without a purge
plugin. Other sites can have a temporary increase in cache misses.

Any request with a cookie, Authorization, or a query string bypasses page cache.
The default rules also exclude common WordPress and shop routes. Add exceptions
for localized or custom cart, checkout, account, API, and private routes.
Nginx still respects upstream private/no-store and Set-Cookie response headers.

## Commands through Ansible

Use an explicit inventory and host limit for one-shot operations:

```sh
ansible-playbook -i inventories/prod/hosts.yml playbooks/verify.yml --limit wp01
ansible-playbook -i inventories/prod/hosts.yml playbooks/site-backup.yml --limit wp01 -e wp_site_only=example.com
ansible-playbook -i inventories/prod/hosts.yml playbooks/site-repair-core.yml --limit wp01 -e repair_site=example.com
```

Core repair without confirmation is a report. Add `-e confirm=example.com` to
replace core files with the same version. Add `-e quarantine=true` to move listed
extra files to a root-only quarantine directory. It does not accept the resulting
Git changes. Review the files, plugins, and database after an incident.

`site_fix_perms` is an explicit repair tag:

```sh
ansible-playbook -i inventories/prod/hosts.yml playbooks/site.yml --limit wp01 \
  -t site,site_fix_perms -e wp_site_only=example.com
```

Cron runs due WordPress events every five minutes. Native WP-Cron is disabled in
managed configs. Site logs are in `logs/`; rotation is weekly. Monitor output uses
PASS/FAIL/SKIP lines. Use the service logs when a command reports a generic failure.
Do not publish the contents of configs, database dumps, or secret directories.
