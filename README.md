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

Read [Deployment](Docs/DEPLOYMENT.md), [Operations](Docs/OPERATIONS.md),
[Security](Docs/SECURITY.md), [Reference](Docs/REFERENCE.md), and [Testing](Docs/TESTING.md).
See [Audit](Docs/AUDIT.md) and [Validation](Docs/VALIDATION.md) for findings,
tested behavior, and remaining limits.
