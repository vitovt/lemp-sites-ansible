# Validation record

This record applies to the changes prepared on 2026-10-29.
The disposable target is Debian 13 on Proxmox QEMU/KVM. It is not LXC.
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

## Required checks before production

- Run the suite in an unprivileged LXC with the intended parent firewall policy.
- Test real Cloudflare DNS-01 issuance and renewal with a scoped test-zone token.
- Test real public HTTP-01 issuance through the selected network path.
- Test public IPv6 routing, DNS, and firewall behavior from an independent host.
- Test the parent LAN Nginx proxy, its TLS verification, and visitor IP headers.
- Test Telegram delivery and failure recovery with a dedicated test bot.
- Test the mounted backup volume, remote copy credentials, and immutable retention.
- Restore a full host on a second guest. Measure the recovery time.
- Load-test the intended number of sites and tune the host resource budget.
- Test real plugin and theme upgrades and the site's business workflows.

These checks need infrastructure or service credentials that are not included in
the isolated test run. Production inventory and production secrets are not test fixtures.
