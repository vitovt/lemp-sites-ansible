# Validation record

This record applies to the changes prepared on 2026-10-29.
The initial disposable target is Debian 13 on Proxmox QEMU/KVM. It is not LXC.
The later LXC run is recorded separately below.
The control environment uses the versions in requirements-dev.txt.

## Control environment

- ansible-core 2.19.14 and ansible-lint 26.10.1.
- Exact Python dependencies are in requirements-dev.txt.
- Exact collection versions are in requirements.yml.
- The VM has PHP 8.3 and 8.4 sites. MariaDB uses a 256 MiB buffer pool in this
  small test guest. These values are not a production capacity recommendation.

## Results

| Check | Result and scope |
| --- | --- |
| Controller regressions | 29 tests passed. They cover validation, static config drift, CLI input and confirmation, monitoring errors, alert chunks, permissions, operation locks, rollback, partial backup handling, retention, and maintenance selection. |
| Static checks | All 7 playbooks passed syntax checks. Python and shell syntax checks passed. ansible-lint passed its production profile with 0 failures and 0 rule warnings. The offline run emitted tool setup notices. |
| Local Nginx regression | Two independent guest requests received different private cookies and MISS responses. A private response was not reused. |
| Deployment | Clean disposable Debian 13 deployment completed. Repeat convergence reported changed=0, unreachable=0, failed=0. Doctor passed without changes. |
| VM scenarios | 10 integration tests passed. They exercise real Nginx, both PHP branches, MariaDB, Valkey, restic, nftables, and systemd. |
| Site boundaries | PHP could not read another site's config, access its FPM socket, write ordinary code, or use another Valkey key prefix/global commands. Writable content remained usable. A cross-site webroot symlink did not disclose config. |
| Network and cache | IPv6 loopback and an unrouted IPv6 network client worked. Direct-mode IPv4 input was rejected. Spoofed visitor/health headers did not bypass maintenance. SMTP rejection, HTTP challenge serving, login limits, private headers, cookies, queries, Authorization, and public cache hits were checked. |
| Failure reporting | Stopped FPM/Valkey services, missing core, a modified real plugin, and a new administrator produced failures. Pending administrator alerts remained pending on repeated runs. |
| Backup and restore | Full backup and verification passed. Restored SQL removed an extra live table and a post created after the snapshot. Hostile cross-schema SQL failed. A special-character password worked with the restricted import user. |
| Clone and update | Missing confirmation was rejected. An injected clone failure restored the original target. A successful clone retained a maintenance gate and separate target credentials. Activation served source content without the old target cache. The checked update path passed with current versions. |
| Lost secrets | Deliberately missing DB and restic controller secrets stopped doctor with changed=0. The test restored the files afterward. |
| Installation recovery | The runner added a third site, interrupted the real installation after download, and resumed installation. Its repair report passed. Removal made an archive and removed the site. Adding the site preserved a Valkey cache sentinel. |
| Final verification | The final verify play reported 31 PASS and 0 FAIL. |

The final SQL-client regression also tests an ambient root client config. The old
extra-file mode allowed that config to replace the restricted account. This was
reproduced on the disposable MariaDB instance. The corrected client rejected the
same cross-schema import. All 10 VM scenarios passed again after the correction.
See the private run logs for the exact command output.

The full runner used an allow-listed project copy and newly generated secrets
under /tmp/lemp-integration-codex. It did not load production inventory, production
application secrets, SSH agents, or normal SSH config. Test logs and generated
credentials remain outside Git. The temporary test key is ignored and untracked.

The GitHub workflow was added but was not run on GitHub. Its controller commands
were run locally. Certbot authenticator tests check arguments; the VM uses an
explicit self-signed test exception. No real CA or Cloudflare credentials were used.

The update check used already current software. This does not prove a real
version transition, a plugin database migration, or recovery after power loss.
Backup verification imports into a scratch schema and checks restored files.
It is not a full-host disaster-recovery test or a complete business workflow test.

## LXC follow-up on 2026-10-30

The new target is Debian 13 in an unprivileged Proxmox LXC. Its UID map starts
at host UID 100000 and contains 65536 IDs. The container permits nftables,
network namespaces, and veth operations. These permissions are specific to this
parent configuration; they are not guaranteed for every LXC installation.

The guest has LAN IPv4 and direct public IPv6. IPv4 NAT exposes a forwarded SSH
port. Direct IPv6 SSH uses port 22. The later NAT intermittency is noted below.
The test policy permits web traffic from the IPv4 parent and direct IPv6 clients.
Other IPv4 web peers remain blocked.

The run found and corrected these additional issues:

- SSH reload failed when ssh.socket owned the listener. The corrected handler
  validates the full config and restarts that service mode. Both SSH paths were
  checked afterward.
- Explicit LAN proxy peers were ignored in direct mode. The firewall and trusted
  header policy now include them. Local Nginx checks verify trusted and untrusted
  visitor headers and the maintenance boundary.
- The base image had active Postfix services. They are stopped and masked under
  the default no-SMTP policy. No SMTP listener remains.
- The test runner assumed sudo and /home/root for a root connection. Root is now
  supported without a preinstalled sudo package or a normal user home path.
- The test backup fixture required /etc/letsencrypt while Certbot was disabled.
  Backup correctly failed. The fixture now backs up its actual self-signed TLS
  files. The missing-path failure remains active and has regression coverage.
- Fresh and interrupted installations could expose the web installer before DB
  setup completed. A root-owned installation gate now blocks HTTP and cron until
  the managed installation and baseline checks finish.
- TCP loopback did not identify a root caller for the maintenance health bypass.
  Privileged checks now use a Unix socket inside a root-only runtime directory.
  PHP-user access and forged TCP headers are denied. Root update health checks
  still work. A local Nginx probe also checks the restricted Cloudflare policy.

Controller checks now contain 38 passing tests. All 7 playbook syntax checks,
Python/shell syntax checks, and the ansible-lint production profile pass.
The local Nginx trusted-header regression passes. The 10 LXC integration
scenarios passed, including backup/restore, clone rollback, permissions, service
failure reporting, and the separate network client. There were no skipped
integration scenarios.

The default host verification reports 30 PASS, 1 FAIL, and 2 certificate SKIP
results. The failure is disk_space: the shared filesystem is 92% used, with
about 280 GiB available. It is below the default 10% reserve. This is a real
environmental alert, not an application test failure. Its threshold remains
unchanged. Review the container storage limit and the parent reserve policy.

The runner uses the explicit `--expect-disk-alert` option to continue only for
this single failure. Its raw verify playbooks still fail. Any different or
additional failure stops the run. A regression checks this restriction. A
successful disposable test run does not mean that the host health is all green.

The repeat deployment reported changed=0, unreachable=0, failed=0. The
interrupted-install regression returned HTTP 503 for wp-admin/install.php even
with the old health header. After resume it returned HTTP 200. Lost DB and restic
secrets stopped doctor without changes. Adding the third site preserved the
Valkey sentinel. Its repair report passed, and removal made an archive.

An IPv4 NAT SSH connection timed out after the successful resume play. IPv6 SSH
remained available; the remaining checks were completed through that endpoint.
Fresh IPv4 SSH connections both succeeded and timed out afterward. The guest
SSH service remained active on port 22. The intermittent NAT path remains an
unresolved environmental condition. The final Nginx policy was applied
and its repeat convergence reported changed=0. The private transport boundary
and checked-update scenarios passed again. Final verification still contains
only the declared disk-space failure.

From the independent control host, public IPv6 HTTPS returned 200, HTTP returned
the canonical redirect, and the HTTP challenge path served its test value.
Spoofed visitor and health headers could not bypass maintenance. The temporary
gate and challenge were removed and the site returned 200 afterward.
These checks used self-signed TLS and did not exercise real certificate issuance.

The parent Nginx [upstream example](examples/proxmox-upstream.conf.example) passed
a local Nginx configuration test. It was not installed on the parent host.
It requires a trusted guest certificate for the forwarded domain. The current
guest test certificate is self-signed, so this is not a ready public TLS setup.

Test inventory, generated credentials, and logs are in /tmp/lemp-integration-lxc,
outside Git. The new test-only private key is in that directory with mode 0600.
Production inventory, application secrets, SSH agents, and normal SSH config
were not used by the integration runner.

Use temporary hostnames through curl's explicit resolver for a manual check:

```sh
curl -6 --noproxy '*' -k \
  --resolve 'test1.example:443:[2001:db8::125]' \
  https://test1.example/
```

Replace the documentation IPv6 address before use. `-k` is the explicit
self-signed test exception. A real domain and certificate
are still required before a public deployment. The two demo sites are retained.
WordPress administrator credentials are private generated test data. After the
clone scenario, the second site contains the first site's WordPress users.

## Required checks before production

- Repeat LXC checks if the parent firewall or namespace permissions change.
- Test guest reboot and parent autostart with the intended backup mount policy.
- Test real Cloudflare DNS-01 issuance and renewal with a scoped test-zone token.
- Test real public HTTP-01 issuance through the selected network path.
- Test production DNS and Cloudflare routing with the actual site domains.
- Test the parent LAN Nginx proxy, its TLS verification, and visitor IP headers.
- Test Telegram delivery and failure recovery with a dedicated test bot.
- Test the mounted backup volume, remote copy credentials, and immutable retention.
- Restore a full host on a second guest. Measure the recovery time.
- Load-test the intended number of sites and tune the host resource budget.
- Test real plugin and theme upgrades and the site's business workflows.

These checks need infrastructure or service credentials that are not included in
the isolated test run. Production inventory and production secrets are not test fixtures.

## Per-site Cloudflare tokens on 2026-10-30

Controller tests cover the default token, named token selection, host filtering,
unsafe reference rejection, missing or group-readable credential files, Certbot
arguments, saved renewal paths, and reconfiguration only when the saved path
differs. The reconfiguration test uses a local fake Certbot command.
All 45 controller tests and seven playbook syntax checks passed. Targeted
Ansible lint passed on the changed role tasks. The workstation's unpinned
system linter still reports 12 existing whole-repository findings in role
metadata and the GitHub workflow. The pinned linter was not installed for this
run. No production tokens or inventory were used.

The disposable integration fixture disables Certbot. It did not issue a public
certificate or test renewal against Cloudflare. Before production use, issue a
certificate for each account with scoped test-zone tokens, change one existing
site from the default to a named token, and run `certbot renew --dry-run` on the
disposable host. Check the saved credential path for every certificate.

## Server inventory and site-only run on 2026-10-31

The public `main` history was rewritten with `git-filter-repo` before this
refactor. The old `inventories/prod/` and `inventories/test/` paths are absent
from reachable `main` history. The rewritten branch was force-pushed.
The two old server configurations were converted into separate, ignored local
directories. Their server and site settings were compared with the source
versions. Existing passwords were intentionally not migrated. Each directory
has an empty private secrets directory for new credentials. A copied directory
also needs its external SSH key and controller dependencies on the new machine.

The controller passed 60 unit tests and syntax checks for eight playbooks.
The pinned offline `ansible-lint` passed the production profile with no
findings. Local Nginx cache and visitor-IP probes passed. The example YAML
loads as a one-server inventory. A copy of it supports the site CLI and keeps
its comments. Two copies load only their own sites. `doctor` rejects unsafe
secret and external key modes. Git tracks only the safe example under
`inventories/`; both local server directories and the test SSH key are ignored.

A clean Debian 13 QEMU deployment used a generated inventory and secrets in a
private `/tmp` work directory. The runner did not copy real inventories, keys,
or secrets into the fixture. The second full convergence reported `changed=0`.
A selected site change ran through `site-apply`; its repeat reported
`changed=0`. The other site's PHP pool and Nginx vhost, and the shared Valkey
ACL file, kept the same checksums. `doctor` passed after the site was restored.

The first lifecycle run found that restarting Valkey for every ACL change
cleared another site's cache when a new site was added. The handler now loads
ACL changes without a restart when the installed admin password is unchanged.
A first installation or admin password change still restarts Valkey. The
clean QEMU deployment and repeat convergence passed after this fix.

The final lifecycle phase passed all 10 service and application scenarios.
It exercised exact-schema backup and restore, rejected a cross-schema SQL
import, tested clone confirmation and rollback, and checked the staging gate.
Adding a third site kept another site's Valkey cache value. The deliberate
missing-secret checks stopped before changes. The deliberate interrupted
installation kept the HTTP gate closed and then resumed. The repair report,
archived removal, and final `verify` passed. Final verification reported
`31 PASS, 0 FAIL`.

This refactor has not been deployed to the current LXC server. The QEMU fixture
did not use a public IPv6 address, a real Cloudflare token, or a public CA
certificate. Real DNS-01 issue and renewal, public HTTP-01, the parent Nginx
proxy, Telegram delivery, a mounted backup volume, remote backup copy, and a
full-host restore still need separate checks. The fixture checks application
restore and clone safety; it does not prove disaster recovery after host loss.

## Removal without backup on 2026-10-31

The controller passed 64 unit tests, syntax checks for eight playbooks, and
the pinned offline `ansible-lint` production profile. The new tests confirm
that emergency removal does not read the restic environment, check its mount,
or call backup. Normal removal still calls backup.

The disposable QEMU VM was checked against its existing test marker. The local
server inventory had no saved DB or Valkey password for `test1.example`. The
updated helper and command were installed on that VM. The operator command
`scripts/site remove 241-ans01-presta test1.example --no-backup` required the
exact domain by default and completed with one changed task. The remote site
directory, database, Linux user, Nginx vhost, and PHP pool were removed. No new
removal archive appeared. The other site's directory and database remained,
and its site facts, vhost, and PHP pool kept the same SHA-256 hashes. The local
site list now contains only `test2.example`.

The test did not inspect remote restic snapshot metadata before and after the
operation. The playbook selected the no-backup branch, and the command's unit
test proves that branch does not call the snapshot function. Old snapshots, if
present, were not deleted. A failed removal after partial changes still needs
manual recovery before another convergence run.

## PHP branch switching on 2026-11-02

The controller passed 93 unit tests, syntax checks for eight playbooks, and
the pinned offline `ansible-lint` production profile with no findings. Unit
tests cover required quoted `php_version` values, dynamic Sury package
selection, incomplete `site-apply` branches, rollback before and after the
Nginx switch, retry after failed old-service cleanup, stopped FPM recovery,
and unsafe path rejection.

The disposable Debian 13 QEMU VM used a generated inventory and test secrets
under `/tmp/lemp-integration-*`. The runner did not copy private server
inventories or production secrets. Sites started on PHP 8.4 and 8.3. The full
playbook and its repeat passed; the repeat reported `changed=0`. An
uninstalled PHP 99.99 branch made `site-apply` stop with `changed=0` and kept
the active pool, vhost, and site facts unchanged. An invalid FPM setting
failed validation and restored the old pool and route. This run found and
fixed a race between `systemctl reload` and creation of the new FPM socket.

The VM completed 8.4 to 8.3 to 8.4 with `site-apply`. The full playbook then
installed PHP 8.5 from Sury and moved the first site to it. `site-apply`
returned the site to 8.4. Checks confirmed the requested pool and socket,
removal of the old pool and socket, stable checksums for the peer site, and a
working peer HTTP response. The final full repeat reported `changed=0`.
During a separate 8.4 to 8.3 switch, 164 uncached local HTTP requests returned
200 with no errors. The site was then restored to 8.4, and read-only
verification reported `33 PASS, 0 FAIL`.

The lifecycle run passed all 10 service and application scenarios, including
site isolation, backup and restore, and clone rollback. Its interrupted
WordPress installation exposed a false HTTP 302 health failure after core
download. After the fix, a resumed run completed the installation, repair
check, archived removal of the third site, and final `verify` with
`33 PASS, 0 FAIL`. The lifecycle proof used a resume after this deliberate
failure; it was not one uninterrupted run.

PHP branches other than 8.3, 8.4, and 8.5 were checked for schema acceptance
only. The tests do not prove compatibility of WordPress plugins or themes with
another branch. This change was not deployed to the LXC host. Public IPv6,
Cloudflare DNS-01 renewal, HTTP-01, the parent proxy, Telegram delivery, a
mounted backup volume, and full-host disaster recovery remain outside this
PHP validation run.

## Site-local code policy on 2026-11-06 to 2026-11-07

Local checks passed: 120 unit tests, Python and shell syntax checks, the site
playbook syntax check, and the offline `ansible-lint` production profile with
zero findings. The unit tests cover exact added, modified, and deleted file
rules; changed hashes; unknown files; Git mode without a clean baseline;
per-site update holds; and full or partial helper commands.

On the disposable VM 241, a targeted playbook installed only the helper files,
monitor, updater, and one test sudo rule. A second run reported `changed=0`.
`jump-in test2.example` ran as `test2_example` in that site's `htdocs`; the
site-local `policy` command worked from its shell. A test hold was written as
root-owned `0644` policy and removed from the site shell.

The test installed Hello Dolly and added one extra file. Both diagnostics and
monitoring reported a checksum failure. After the helper accepted only that
file's exact hash, both reported success. Changing its content made both fail
again. The file, test plugin, and test policy were removed. The final Git
status and plugin checksum checks passed for the test site.

The full `scripts/lemp 241-ans01-presta doctor` stopped before changes because
the controller lacks saved DB and Valkey passwords for the existing test site.
Thus, the full server playbook and `site-apply` were not exercised for this
change. Backup, restore, and clone of a policy file were checked in code and
documentation only, not by a live lifecycle run. Server 125 was not accessed
or changed.

## Shared server tool delivery on 2026-11-07

`tests/check.sh` passed 120 unit tests, all playbook syntax checks, and Python
and shell syntax checks including `server-tools/`. Offline Ansible lint passed
its production profile with zero findings.

The dedicated delivery test ran on VM 241 in an isolated
`/tmp/wp-tool-delivery-*` prefix. It copied no site data or production secrets.
It delivered a new command without an Ansible change, executed it through its
link, updated its content, and retained the file and link after its source was
removed. Both repeat runs reported `changed=0`. Root ownership, library mode
`0644`, nested directory delivery, and Python cache exclusion passed.

A final check-mode run of the role reported `changed=0`, `failed=0`. A delivered
`wp-site --help` loaded the delivered library through the isolated prefix. The
controller's linked `hosting_config` module also packaged and executed through
Ansible on the VM and locally.

This test did not reinstall the complete hosting stack or run backup, restore,
clone, public certificates, or systemd timers from the new layout. The installed
tools and sites on VM 241 were not replaced. Server 125 was not accessed.

## Backup manager on 2026-11-07

### Passed

- Controller unit suite: 156 tests. All playbook, Python, and shell syntax checks passed.
- `ansible-lint --offline`: production profile, no failures or lint warnings.
- Disposable Debian 13 container: 9 integration tests passed. Runtime networking was disabled.
  The build context excluded inventories, keys, and controller secrets. There were no host mounts.
- Real restic tests: single-site and multi-site snapshots, deleted sites, incomplete snapshots,
  all five extraction scopes, literal special-character paths, manifest checks, plain/GPG exports,
  archive contents and checksums, copied snapshot IDs, and destination data checks.
- Real MariaDB tests: standard single-site and all-site backup functions, scratch imports,
  required WordPress table checks, and removal of scratch databases after verification.
  Database names, data, and repository passwords were generated inside the container.
- Installed CLI and terminal-menu startup/exit, compare, status fallback, retention display,
  preferences, all-snapshot verification, and dry-run snapshot deletion.
- Interrupted export data remained visible. Cleanup removed only selected task data.
  A concurrent operation was rejected by the shared lock. Live fixture files stayed unchanged.
- Unit regressions: path traversal, symlinks, private metadata, active/kept workspace protection,
  corrupt/missing SQL and manifests, secret masking/disclosure, partial prompts, preview-only behavior,
  one confirmation per action, cancellation state, failed-task logs, and separate destination credentials.
- `server_tools` installed the manager automatically. A repeat container installation had `changed=0`.
- After the user reconfirmed VM 241 as disposable, isolated `/tmp` delivery passed four stages:
  initial copy, unchanged repeat, changed command, and retained remote command after source removal.
  The isolated `wp-backup-manager --help` command also passed. Installed tools and sites were not changed.
- Server 125 was not accessed.

### Resolved failures

- A synthetic table manifest and MariaDB returned the same names in different orders.
  Verification now compares names without depending on SQL collation order.
- Real dumps exposed unclosed subprocess pipes. Dump failure now closes, kills, and reaps the child.
- Minimal replacement environments can lack `systemctl` and `journalctl`.
  Status now reports these checks as unavailable instead of stopping the report.
- The first test harness used an unescaped SQL wildcard and lacked permission to stop its own
  MariaDB child after a UID change. The query is parameterized and the container has `CAP_KILL`.

### Not tested

- Production data, server 125, LXC deployment, or a complete hosting-stack reinstall.
- Remote S3/SFTP/cloud accounts, cross-host network interruption, Telegram delivery,
  actual systemd timer scheduling, backup mount failure, disk exhaustion, or a power loss.
- A real WordPress application after recovery. Recovery is extraction-only by design.
- GitHub-hosted execution of the new CI job. Its commands passed locally.

### Remaining limits

- Selected symlinks and special files are rejected. Select a narrower tree for extraction.
- Legacy file manifests prove availability, not per-file content hashes. Restic read-back
  verifies extracted content against repository data. Missing metadata is reported separately.
- Two remote repositories with different backend credentials require an intermediate local repository.
  The manager rejects conflicting direct-copy credentials. This transfer path needs a real cloud test.
- Unregistered legacy workspaces require manual review and deletion. Normal cleanup does not remove them.
- Exports retain their private extraction workspace. Review allocated space before and after export.
- Recovery kits that include repository passwords are not implemented. Standard archives omit them.

## Local site shell on 2026-11-07

### Passed

- Controller suite: 180 unit tests. Playbook, Python, shell, and shared Bash profile syntax checks passed.
- `ansible-lint --offline` passed the production profile.
- Eleven PTY integration tests passed in a disposable Debian 13 container.
  Runtime networking was disabled. The container received no inventories, keys, host mounts, or production secrets.
- Real site owners and runtime accounts: real/effective/saved UID and GID, explicit supplementary groups,
  current directory, environment cleanup, and inherited umask isolation.
- Another site's private file and a root private file were unreadable. General sudo and another site's
  policy were denied. The PHP runtime could not enter the owner environment or write the code fixture.
- The private PATH also bound child `env php` and `env wp` calls. Global commands were not replaced.
- Actual PHP 8.4, pinned WP-CLI 2.12.0 (`--info`), Git init/add/commit/status, and owner code-check commands.
- PHP selection from changed facts used a generated `php9.7` test executable. Environment values did not
  select another PHP branch or site. The fixture did not test a real PHP 9.7 release.
- The privileged checksum helper used the runtime-owned temporary home/cache, without loosening owner-home permissions.
- The current policy sudoers task was reused. Own-site policy writes passed; cross-site writes and
  a privileged Git command were denied.
- Completion, summary help, detailed help, full help, direct UID-bound entry, and one initial Bash session.
- Exit, EOF, SIGHUP, and SIGTERM closed the session. The parent remained root.
  Idle signal status follows the shell and terminal proxy.
- An older `0755` owner cache was tightened to `0700`; existing cache data remained.
  History stayed `0600`. Repeated delivery had `changed=0` and preserved user profile/history bytes.
  Both account login shells stayed `/usr/sbin/nologin`.
- The owner shell used a separate PTY from its root parent.
- Inherited non-standard descriptors were closed; an open root-only fixture descriptor did not reach the owner.
- Unit regressions cover hostile environment values, saved root IDs, process capabilities, unsafe paths,
  runtime identity, unsupported shell modes, literal facts, current-owner checks, and completion context.

### Resolved test failures

- The restricted test container cannot chmod another UID's file after chown. Fixture setup now sets mode first.
- Sudo can consume queued PTY input. The driver now sends one command at a time and waits for its result.
- The terminal driver missed a prompt already present in its read buffer. It now checks buffered output first.
- Signal status differs between idle Bash and the PTY proxy, including proxy termination by the system.
  Signal tests require timely closure, no surviving owner Bash, and preservation of the parent session;
  they do not assume a fixed numeric proxy status.
- The private-PTY test needs `CAP_FOWNER` so runuser can set the new terminal owner and mode.

### Not tested or not implemented

- No server deployment was run. Servers 125 and 241 were not accessed for this change.
- A complete WordPress application, database, upstream checksum network access, and a real second PHP branch
  were not tested in the PTY suite. Existing policy behavior and the new owner path passed unit tests.
- Jailkit, chroot, SSH authentication, remote command mode, SFTP, and system login-shell changes are not implemented.
  The future filesystem and identity contract is described in `SITE_SHELL.md`.
- GitHub-hosted execution of the added CI job was not run. The same local container command passed.

## Backup identity and updater diagnostics (2026-11-07)

### Passed

- `tests/check.sh`: 197 unit tests, all playbook syntax checks, Python parsing,
  and shell syntax checks.
- `ansible-lint --offline`: production profile; no failures or warnings.
- `tests/run-backup-manager-integration.sh`: 9 integration tests passed.
  The disposable container had no network access or host mounts. Credentials
  and MariaDB data were generated inside the container.
- Real manual and nightly backups recorded the final complete snapshot ID.
  The installed updater accepted those records, old pre-tag records, and new
  records after another tag change. Reading did not rewrite old records.
- Ambiguous, incomplete, unrelated, wrong-site, and old-date records were
  rejected by unit regressions. Missing site manifests did not pass the gate.
- A missing backup produced a visible failure and private log path without
  calling an update. Log and syslog failures did not hide the original error.
- Repeated tool installation reported `changed=0`.

### Not tested

- Servers 125 and 241 were not accessed or changed.
- No live WordPress core, plugin, or theme update was run.
- Real Telegram delivery, remote restic repositories, and GitHub Actions were
  not tested for this change. Existing server records were not modified.

## Updater action hints (2026-11-07)

- Passed: 201 unit tests and playbook, Python, and shell syntax checks through
  `tests/check.sh`. The 13 updater tests also passed after the final hint edits.
- Regressions cover advice for failure categories, backup bypass warnings,
  quoted site values, hints in private logs, invalid CLI help, and plan errors.
- This change adds print-only advice. No server was accessed. Live updates,
  Telegram delivery, and execution of the suggested recovery commands were not tested.

## Manual command hints and backup menu (2026-11-07)

### Passed

- `tests/check.sh`: 211 unit tests, playbook syntax checks, Python parsing,
  and shell syntax checks.
- `tests/run-backup-manager-integration.sh`: 9 tests passed with real restic
  and container-local MariaDB. The installed TUI exited through option 0.
- `tests/run-site-shell-integration.sh`: 11 tests passed. Site-owner identity,
  policy permissions, terminal behavior, and repeat delivery remained valid.
- Both integration containers had no runtime network or host mounts. They used
  generated fixtures. Repeat installation reported `changed=0`.
- Unit regressions cover command-specific hints, preserved child/usage exit
  codes, cancellation, clean stdout, and omission of subprocess argv/output.
- Menu regressions cover default display off, CLI enablement, toggling both
  ways, no per-action display question, separators, exit 0, and distinct preview
  colors. Redirected output has no ANSI escapes.

### Not tested

- Servers 125 and 241 were not accessed or changed.
- Live WordPress updates, real Telegram delivery, remote repositories, and
  execution of the suggested manual repairs were not tested.
- Native PHP/WP-CLI error text comes from the child tool. Wrapper hints add
  general checks; they do not diagnose every third-party plugin failure.


## Site preview and PHP handlers: 2026-11-08

Target: disposable Debian 13 QEMU VM 241. The runner used generated credentials
and temporary test sites. No server 125 connection was made.

- Controller: 256 unit tests passed. Playbook, Python, and shell syntax passed.
- Initial deployment: `failed=0`. Repeat: `changed=0`, `failed=0`.
- Site apply: repeat convergence passed. Other-site resources stayed unchanged.
- Missing PHP branch and invalid FPM configuration were rejected. The active
  pool, Nginx configuration, and site facts stayed unchanged.
- PHP transitions: 8.4 to 8.3, back to 8.4, then 8.5 and back to 8.4 passed.
  All 217 sampled uncached HTTP requests passed during the 8.4 to 8.3 switch.
- Full convergence after the transitions: `changed=0`, `failed=0`.
- Intentional interruption with an empty 8.5 pool directory: only the intended
  failure occurred. Active 8.4 validated and reloaded. Both sites stayed available.
- Fresh branch: PHP 8.2 was absent before the test. The actual PHP role installed
  its packages, removed the default pool, and encountered an intentional failure
  before site-pool creation. Handlers skipped 8.2 and validated and reloaded 8.4.
  Both site login pages returned successful HTTP responses after the test.
- Doctor passed with `changed=0`, `failed=0` before and after the fresh-branch test.

Initial setup stopped on legacy regular command files at `/usr/local/sbin/`.
They were moved to a private temporary directory on the marked disposable VM.
The normal tool-delivery role was not changed to overwrite unrelated files.
The first fresh-branch fixture did not select any versions. It was corrected
and rerun with site-derived versions before the successful result above.

Logs: `/tmp/lemp-integration-php-handler-241/`. Generated credentials in that
workspace must stay outside Git. The fresh-branch result is in
`interrupted-php-fresh.log`; its single failure is intentional.

Not tested: real Cloudflare API calls, public ACME issuance, production data,
or server 125. This run did not repeat the full backup/restore lifecycle suite.
