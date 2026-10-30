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

The guest has LAN IPv4 192.168.77.125 and public IPv6
2a01:4f9:3051:41ea:1111::125. IPv4 NAT exposes SSH port 22125. Direct IPv6 SSH
uses port 22. The later NAT intermittency is noted below. The test policy permits web traffic
from the IPv4 parent address 192.168.77.1/32 and from direct IPv6 clients.
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
  --resolve 'test1.example:443:[2a01:4f9:3051:41ea:1111::125]' \
  https://test1.example/
```

`-k` is the explicit self-signed test exception. A real domain and certificate
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
