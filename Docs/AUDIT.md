# Critical review

The initial review used commit `f53bc9b`. It covered the tracked scripts,
playbooks, roles, templates, inventories, and test entry points. It found
deployment failures, possible private response leaks, misleading health results,
and unsafe data replacement. The fixes below have regression coverage.
See [Validation](VALIDATION.md) for the tests that actually ran and their limits.

This is a shared hosting system for sites with one trusted administrator.
It has useful controls for a compromised PHP process. It does not provide a
separate kernel or a hard resource boundary for each customer.

## Confirmed defects

Severity describes the original behavior. A configuration error is not evidence
of a remote exploit. No production compromise was investigated or confirmed.

| Severity | Original defect and effect | Correction and evidence |
| --- | --- | --- |
| High | Cache ignored private response headers and Set-Cookie. Another guest could receive the first guest's response. | Respect upstream headers; bypass cookies, Authorization, queries, and dynamic routes. Local FastCGI and VM HTTP regressions pass. |
| High | Preflight used pipefail under Debian's default /bin/sh and stopped normal deployment. | Select Bash explicitly. Doctor and full deployment run on Debian 13. |
| High | Failed checksum commands could report PASS. Missing installed core could report SKIP. | Preserve exit status, validate checker output, and record expected installation state. Error, tamper, and missing-core tests pass. |
| High | Site configuration accepted reserved users and system database names. Existing system accounts could be modified. | Validate names, identity ownership, UID, and managed state before changes. Controller regressions reject unsafe inputs. |
| High | Lost controller secrets could silently rotate live passwords. | Require original secrets for existing resources and compare config values. Deliberately missing DB and restic secrets stop doctor with no changes. |
| High | Existing WordPress DB settings could differ from the database being backed up. | Read static constants without executing PHP and require agreement before changes. Dynamic settings require a separate migration. |
| High | Shared Nginx could follow a site-created symlink to private content. | Disable webroot symlink access. A VM cross-site symlink test cannot retrieve the target config. |
| High | Clone overwrote target data without rollback or a maintenance barrier. | Lock operations, freeze writers, save recovery data, stage imports, and restore the target after failure. Injected clone failure and staging tests pass. |
| High | The maintenance exception added in the earlier hardening pass trusted a loopback health header. Another site's PHP could use it to bypass maintenance. | Use a Unix listener inside a root-only directory. The LXC test denies PHP-user access and TCP header bypass while root update checks still work. |
| High | A downloaded core could expose the web installer before automated DB setup completed. An interrupted installation could leave that window open. | Claim a private installation maintenance marker before download and release it only after installation checks. Resume tests require HTTP 503 while incomplete. |
| High | Root SQL imports relied on removing USE lines. SQL could address another schema. | Import with an account restricted to one database. Reject unsupported SQL object types. Hostile USE statements fail in MariaDB tests. |
| High | A site-controlled password could add root client options through a newline. Ambient root client settings could also override the import account. These were found during the fixes. | Quote option-file values, reject NUL, and load only the private import config. Special-password and restricted-schema regressions cover these cases. |
| Medium | Restore left tables that were absent from the snapshot. | Replace the schema from a staged, checked dump and retain rollback data. An extra table disappears after restore. |
| Medium | Partial installation was skipped once version.php existed. | Check each installation stage and retain a private installation intent. An interrupted download stage resumes through a working installation. |
| Medium | Administrator changes were accepted even when alert delivery failed. Telegram errors returned success. | Keep administrator changes pending until explicit acknowledgement; propagate delivery failure and split messages. Repeated-alert and message tests pass. Real Telegram delivery is untested. |
| Medium | A partial restic snapshot could be followed by pruning and a successful result. Verify accepted weak or empty checks. | Mark only successful snapshots complete; stop on partial backup; compare hashes, file lists, required tables, and exact schema. Unit and VM restore tests pass. |
| Medium | Automatic Git commits could accept unrelated code changes. Imported permissions were not checked. | Require a reviewed baseline, keep migration changes visible, and fail permission checks. Refuse links during root permission repair. Regression checks cover these boundaries. |
| Medium | Destructive tests could use active inventory and secrets. | Use an allow-listed project copy, generated inventory and secrets, an explicit SSH identity, and a disposable marker. The legacy entry point requires the marker token. |
| Medium | Certificate errors could leave a successful deployment with unusable origin TLS. | Fail closed on certificate errors; use DNS-01 by default and explicit HTTP-01 as an exception. Authenticator command tests pass; real CA issuance remains untested. |

## Remaining security and reliability risks

- Shared services have shared resource budgets. A busy or hostile site can affect
  other sites. Measure capacity and use separate guests for untrusted customers.
- Root compromise can delete a locally writable backup. Configure an independent
  copy with limited deletion rights and perform a full-host recovery drill.
- Local monitoring cannot detect every external outage. Add independent uptime
  checks and a heartbeat. WP-CLI checks can load site code, including mu-plugins.
- A crash can interrupt rollback. Keep the maintenance gate closed until the
  administrator reviews recovery data. Power-loss recovery is not tested.
- Custom private routes need application-specific cache tests. A generic rule
  set cannot identify every plugin's session and business behavior.
- Checked updates are enabled, but a real application upgrade and its business
  workflows still need a test with the intended plugins and theme.

These are material limits. They are not presentation or usability issues.

The import client uses `--defaults-file` to select only its private option file.
See the [MariaDB client option-file reference](https://github.com/mariadb-corporation/mariadb-docs/blob/main/server/clients-and-utilities/mariadb-client/mariadb-command-line-client.md#option-files).

## Operational inconveniences

| Item | Effect and decision |
| --- | --- |
| Fixed system paths | Helpers use /srv/sites and standard binary/config paths. Do not move them by changing one role variable. |
| YAML CLI writes | The parser removes YAML comments and can change formatting. It validates values and replaces the file atomically. Review inventory diffs. |
| Conservative page cache | Cookies and queries reduce cache hit rate. Keep this default unless application tests justify narrower exclusions. |
| Shared page-cache clearing | Data replacement clears the page cache for all sites. Other sites can have a short increase in cache misses. Valkey ACL changes do not restart the service or clear its data. |
| No SMTP | Local email and password-reset messages need an explicit future transport. SMTP remains blocked by design. HTTP mail APIs are separate. |
| Restricted SQL workflow | Views, triggers, routines, and events need a separate migration. Legacy snapshots need review in a separate database instance. |
| Manual trust decisions | Imported code, clone integrations, and incident recovery need review. An automatic baseline would hide important changes. |

The obsolete privileged Docker smoke test is no longer the test entry point.
The first tested VM was QEMU/KVM. The later unprivileged LXC run and its limits
are recorded in Validation. The actual parent proxy and public CA flows still
need separate checks.
