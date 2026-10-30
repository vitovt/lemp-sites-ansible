# Security model

This system is for sites that share one administrator and one Linux kernel.
Separate users reduce the effect of a compromised PHP process. They do not give
the same boundary as separate VMs.

- Site PHP cannot write code or read another site's private files.
- Nginx must read all site content. Symlink following is disabled in webroots.
- Writable content directories cannot execute PHP through Nginx.
- PHP pools have separate users, temporary paths, sessions, and sockets.
- Database accounts have grants only for their own database. MariaDB uses a socket.
- Valkey accounts have separate key prefixes and cannot use global flush/admin commands.
- Requests with cookies or Authorization bypass page cache. Upstream private cache
  headers remain active. Custom application routes can need extra cache exclusions.
- Only configured peers can supply trusted visitor addresses.
- Root tools read data files without sourcing shell code. SQL imports use restricted users.
- Destructive operations share a lock. Cron uses a non-blocking shared lock.

Maintenance health requests use /run/wp-hosting/health.sock. Its parent directory
is root-only. A TCP loopback client cannot bypass maintenance by sending the
health header. This prevents a compromised site's PHP from using that bypass
against another site. The private transport also works in restricted peer modes.

Shared Nginx, MariaDB, Valkey memory, and PHP masters remain shared resources.
A site can cause CPU, memory, storage, or database pressure for other sites.
Set pool sizes for the total host budget. Use separate containers or VMs for
untrusted customers. open_basedir is an extra check, not an OS security boundary.

Back up secrets off the control machine. Keep a separate disaster-recovery copy.
Protect backup deletion rights from the WordPress host. Test full-host recovery.
A local encrypted snapshot does not protect against every storage failure.

File checks do not prove that a site is malware-free. WP-CLI can load site code,
including mu-plugins. A compromised application can affect application-level
checks. Use offline checks and trusted files during an incident.

Static config checks cover conventional literal definitions. They do not evaluate
PHP control flow, includes, or computed names. Review imported configuration and
application code before you trust its effective database settings.

Core repair reads version metadata without executing version.php. It does not
remove all persistence, repair every plugin, or clean malicious database content.
Quarantine needs explicit confirmation. Review the result before a Git commit.

A killed process or a host failure can interrupt rollback. Inspect the private
recovery directory and maintenance marker before you enable traffic again.
Use VM/storage snapshots and an external backup for stronger recovery guarantees.
