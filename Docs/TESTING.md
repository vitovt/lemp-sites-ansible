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
