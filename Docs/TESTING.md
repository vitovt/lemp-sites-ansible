# Tests

## Controller checks

Install the pinned tools and collections as described in Deployment.

```sh
export PATH="$PWD/.venv/bin:$PATH"
tests/check.sh
ansible-lint --offline
python3 tests/probes/cache.py
python3 tests/probes/real-ip.py
```

Controller tests do not connect to a server. The cache probe starts a private
Nginx process and a mock FastCGI server on temporary localhost ports.
It needs the local nginx binary and permission to bind local sockets.
The removal tests check that `--no-backup` skips restic environment and mount
checks, while normal removal still calls backup. They also check that the site
CLI passes the override only for removal and keeps inventory after a failure.

## Disposable VM

Use an empty, dedicated Debian 13 VM or LXC. It must have SSH and Python 3.
Connect as root, or use a dedicated account with passwordless sudo.
The runner needs its own SSH key. Do not use a production key.

```sh
python3 tests/run-integration.py \
  --host <test-address> --port <ssh-port> --user <test-user> \
  --key /path/to/test-key --disposable --reset \
  --work /tmp/lemp-integration-example
```

For root, use `--user root`. No sudo package is needed before deployment.
Add `--trusted-proxy <parent-address>/32` to test direct IPv6 with an allowed
IPv4 parent proxy. Repeat this option only for actual trusted proxy addresses.
The network test uses a namespace held by a process. It does not need the shared
mount used by named network namespaces. Namespace creation and veth operations
must still be allowed by the parent host.
The fixture uses the Debian self-signed TLS files and backs them up explicitly.
It does not require /etc/letsencrypt when certificate management is disabled.
Missing configured backup paths remain failures.

Use `--public-ipv6 <address>` to add checks from the control machine through the
public IPv6 path. These checks use `curl --resolve` with temporary hostnames.
They check HTTP, HTTPS, the challenge path, and maintenance header spoofing.
They use the explicit self-signed test exception and do not prove public CA trust.

For a known shared-filesystem reserve alert, `--expect-disk-alert` permits only
that one verification failure so lifecycle tests can continue. The monitor and
its default threshold do not change. Any other failure still stops the runner.
The alert remains a real failure in the raw verification log and must be stated
in the validation report. Do not use the option to claim full host health.

`--reset` deletes previous hosting data on the marked VM. The runner creates a
random target marker and checks it before playbooks and destructive scenarios.
Only run this command against a guest that can be destroyed.

The runner copies an allow-list of project files to a private /tmp directory.
It excludes every real server inventory, controller secrets, .git, and SSH keys.
It generates one isolated server inventory and new secrets. It uses a reduced environment,
ignores SSH config and agents, and uses only the explicit test identity.
Results and generated credentials stay in the selected private work directory.
Do not commit that directory or print its secrets in a public log.

If collections are installed next to the active virtual environment under
`<venv>/collections`, the runner uses that directory. Otherwise the runner copies the project collections directory into the test
work directory. Install collections as shown in Deployment first. Do not pass production application configuration to tests.

`--phase deploy` runs deployment, repeat convergence, site and PHP transition
tests, and doctor.
`--phase transitions` resumes only the site update and PHP switch tests on
the same marked fixture after a deployment. Keep its work directory and test
identity. This phase is useful after a test assertion fails.
`--phase cutover` samples uncached HTTP responses while one site moves from
PHP 8.4 to 8.3, then restores 8.4. Use it with an existing marked fixture.
`--phase lifecycle` runs the service, application, backup, restore, clone, and
removal checks on an existing marked fixture. It does not repeat deployment. Use the
same work directory, identity, and explicit target as the earlier run.
`--phase resume` finishes the interrupted-install case on the same marked
fixture after a failure in that case. It checks repair, removal, and final
verification.
The full phase adds HTTP, IPv6 loopback and an unrouted network namespace, cache, permissions, Unix socket and Valkey
isolation, SMTP rejection, integrity alerts, exact-schema restore, hostile SQL,
clone confirmation, clone rollback, staging, and final health checks. It also tests lost secrets, a resumed installation,
site addition, a repair report, and archived removal.
The interrupted-install test requests wp-admin/install.php while the database is
not installed. It requires HTTP 503, then HTTP 200 after a successful resume.
The legacy `tests/verify.sh` requires the same disposable marker token.

The run also changes one site with `site-apply`, repeats that command, and
checks that another site's pool and vhost plus the shared Valkey ACL did not
change. It rejects a missing PHP branch without changing the deployed route.
It switches a site between installed PHP branches, installs another Sury branch
through the full playbook, and checks rollback to the original branch. The
repeated command must report `changed=0`.
The runner also interrupts deployment before a new branch has a site pool.
Forced handlers must skip the empty branch, validate and reload active pools,
and leave both test sites available. Only the intentional failure is allowed.

Repeat convergence must report `changed=0`, `unreachable=0`, and `failed=0`.
No failed or skipped real-environment test is proof of a working feature.
See Validation for the recorded run and remaining coverage.

For shared server tool delivery, run this command against a disposable VM:

```sh
python3 tests/run-tools-integration.py \
  --inventory inventories/my-test-server/settings.yml --limit my-test-server
```

The test uses an isolated remote `/tmp` prefix. See
[server tool delivery](SERVER_TOOLS.md) for the tested sequence and file layout.

## Backup manager

Run unit and syntax checks without a server connection:

```sh
tests/check.sh
ansible-lint --offline
```

Run real restic and MariaDB tests in a disposable Docker container:

```sh
tests/run-backup-manager-integration.sh
```

The build context contains only `server-tools/`, the `server_tools` role, and the test files.
It excludes inventories, SSH keys, and controller secrets. Image creation needs package download access.
The test container has no network and no host mounts. It generates its own repository passwords and databases.
It is removed after the test. The image remains available for a later run.

Tests cover installation idempotence, single-site and multi-site discovery, deleted sites,
incomplete snapshots, all extraction scopes, encrypted and plain archives, snapshot copies,
standard single-site and all-site backups, real MariaDB scratch imports, CLI/TUI, cleanup,
interruption, and operation-lock conflicts. Unit tests cover path attacks, masking, metadata failures,
kept/active task protection, and partial command prompts.
The GitHub Actions `backup-manager` job runs the same container test.

## Local site shell

```sh
python3 -m unittest discover -s tests/unit -p 'test_site_*.py'
tests/run-site-shell-integration.sh
```

The container build includes only public tools, the delivery role, the existing narrow policy task,
and test files. It downloads the pinned WP-CLI artifact and checks the PHP role's SHA-512 value.
Runtime networking is disabled. No inventory, SSH key, production secret, or host directory is supplied.

The PTY suite creates two site owners and separate runtime users. It checks real IDs and groups,
private-file access, a hostile environment and umask, PHP selection, actual WP-CLI and Git,
policy authorization, completion, help, exit/EOF/signals, and repeat installation.
The second PHP branch is a generated test executable; it tests branch selection, not a second PHP release.
Checksum policy parsing and owner execution are covered by unit tests. The PTY suite does not install
or activate a WordPress application or contact WordPress.org during checks.

## Self-signed bootstrap and ACME retry

Use an existing disposable `run-integration.py` fixture. From its private
`project/` directory, use its isolated Ansible environment and run:

```sh
ansible-playbook -i inventories/wptest/settings.yml tests/integration/tls.yml
```

The test requires the target marker and `test1.example`. It uses a private fake
Certbot. It tests failure, continued deployment, retry, the served certificate,
and no duplicate issuance. It restores shared self-signed TLS and removes its
private files. It does not access Cloudflare or Let's Encrypt. Public CA trust
is outside this fixture; the fixture disables public-chain validation.
