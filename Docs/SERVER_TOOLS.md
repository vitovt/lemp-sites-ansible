# Deliver Server Tools

Add a command to `server-tools/bin/`. Add its libraries or shell profiles to
`server-tools/lib/`. Commit the files. Run the full server playbook:

```sh
scripts/lemp my-server site
```

The `server_tools` role runs before roles that use these tools. It copies new
files and updates changed files. It does not delete files or command links when
you remove their source files from Git. Remove obsolete server files manually
when needed. Renaming a command leaves the previous command on the server.

| Repository | Server | Permissions |
| --- | --- | --- |
| `server-tools/bin/COMMAND` | `/usr/local/lib/wp-hosting/bin/COMMAND` | root:root, 0755 |
| `server-tools/lib/FILE` | `/usr/local/lib/wp-hosting/lib/FILE` | root:root, 0644 |
| Tool directories | Same layout below `/usr/local/lib/wp-hosting` | root:root, 0755 |

Each regular file directly in `bin/` gets a link in `/usr/local/sbin/`. Use a
unique command name. Existing unrelated commands cause deployment to stop.
Subdirectories are copied, but only direct files in `bin/` become commands.
Source symlinks are not supported. Python caches are excluded.

A delivered command is not executed during delivery. It gets no new sudo
permission. Declare new system or Python dependencies in Ansible. Keep site
policies, credentials, generated files, and other runtime data outside this
source tree. Local server edits to managed files are replaced on the next run.

The controller's `library/hosting_config.py` is a Git symlink to the shared
source in `server-tools/lib/`. Keep this link when you clone the repository.
The server gets a regular file. The module remains available to Ansible on
the controller without a second source copy.

## Test Delivery

Run local checks with `tests/check.sh`. Run the delivery test only against a
disposable test VM:

```sh
python3 tests/run-tools-integration.py \
  --inventory inventories/my-test-server/settings.yml --limit my-test-server
```

The test uses a unique remote `/tmp/wp-tool-delivery-*` directory. It tests
new commands, changed content, permissions, nested libraries, cache exclusion,
repeat runs, and retention of commands removed from the source. It leaves the
isolated prefix for inspection. It does not replace installed site tools.

## Manual command failures

Manual tools print `HINT` lines after a failure. Hints give review steps and
commands. They do not run repairs, accept changed code, or bypass a safety check.
Controller commands `scripts/lemp` and `scripts/site` use the same library.

Hints go to stderr. Monitor result lines stay on stdout for Ansible. Existing
usage and child exit codes remain available. Ctrl-C is cancellation. Site-owner
commands keep the current UID and trusted site environment. A foreground child
lets the wrapper report failed PHP, WP-CLI, or policy commands.

The shared library is `server-tools/lib/cli_diagnostics.py`. New Python tools
can call `run_cli(TOOL, callback)`. Catch operational exceptions with safe local
messages. Do not print raw subprocess arguments, output, tokens, or database
client exceptions. Add a tool-specific hint when you add a new command.

`SITE`, `SERVER`, and `PATH` in a hint are placeholders. Replace them before use.
Review files before a commit, checksum exception, administrator acknowledgement,
or permission repair. Delivery alone does not grant sudo or secret access.

## Find a command

Run `lemp-help` on the server. It lists controller commands, root commands,
and site-owner commands with one example each. It does not execute commands
or read site secrets. You do not need root to read the reference.

```sh
lemp-help
lemp-help wp-backup-manager
lemp-help --all
```

Use `jump-in-help` for detailed work inside a site shell. Controller commands
run in the cloned repository, not on the hosting server. To read this reference
before deployment, run `PYTHONPATH=server-tools/lib python3 -c 'import lemp_help; lemp_help.main()'`
in the repository. Normal `server_tools` delivery installs the new command.
