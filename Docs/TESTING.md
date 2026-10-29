# Tests

## Controller checks

Install the pinned tools and collections as described in Deployment.

```sh
export PATH="$PWD/.venv/bin:$PATH"
tests/check.sh
ansible-lint --offline
python3 tests/probes/cache.py
```

Controller tests do not connect to a server. The cache probe starts a private
Nginx process and a mock FastCGI server on temporary localhost ports.
It needs the local nginx binary and permission to bind local sockets.

## Disposable VM

Use an empty, dedicated Debian 13 VM. It must have SSH, Python 3, and passwordless
sudo. The runner needs its own SSH key. Do not use a production key.

```sh
python3 tests/run-integration.py \
  --host <test-address> --port <ssh-port> --user <test-user> \
  --key /path/to/test-key --disposable --reset \
  --work /tmp/lemp-integration-example
```

`--reset` deletes previous hosting data on the marked VM. The runner creates a
random target marker and checks it before playbooks and destructive scenarios.
Only run this command against a guest that can be destroyed.

The runner copies an allow-list of project files to a private /tmp directory.
It excludes production inventory, controller secrets, .git, and SSH keys.
It generates one test inventory and new secrets. It uses a reduced environment,
ignores SSH config and agents, and uses only the explicit test identity.
Results and generated credentials stay in the selected private work directory.
Do not commit that directory or print its secrets in a public log.

If collections are installed next to the active virtual environment under
`<venv>/collections`, the runner uses that directory. Otherwise the runner copies the project collections directory into the test
work directory. Install collections as shown in Deployment first. Do not pass production application configuration to tests.

`--phase deploy` runs deployment, repeat convergence, and doctor only.
The full phase adds HTTP, IPv6 loopback and an unrouted network namespace, cache, permissions, Unix socket and Valkey
isolation, SMTP rejection, integrity alerts, exact-schema restore, hostile SQL,
clone confirmation, clone rollback, staging, and final health checks. It also tests lost secrets, a resumed installation,
site addition, a repair report, and archived removal.
The legacy `tests/verify.sh` requires the same disposable marker token.

Repeat convergence must report `changed=0`, `unreachable=0`, and `failed=0`.
No failed or skipped real-environment test is proof of a working feature.
See Validation for the recorded run and remaining coverage.
