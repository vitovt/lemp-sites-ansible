# Work in a site shell

## Enter and leave

Run the launcher as root on the server:

```sh
jump-in example.com
```

- [ ] Check the user and site in the prompt.
- [ ] Run `jump-in-help` for commands.
- [ ] Work as the site's Linux code owner.
- [ ] Run `exit` or press Ctrl-D to return to the administrator session.

Ctrl-C stops a foreground command. It does not close the shell.
SIGHUP and SIGTERM close an idle shell. The exit status follows Bash and the terminal proxy.
The parent administrator shell stays open.

The account keeps `/usr/sbin/nologin`. This feature does not enable SSH,
change login shells, configure Jailkit, or create a chroot.
Linux file permissions are the current boundary. The shell is not a sandbox.

## Use the commands

```sh
jump-in-help
jump-in-help policy
jump-in-help --all
php -v
wp --info
wp plugin list
git status --short
site-check plugins
policy show
```

| Command | Purpose |
|---|---|
| `php` / `site-php` | Use the PHP branch in this site's facts |
| `wp` / `site-wp` | Use that PHP branch and this site's WordPress path |
| `git` | Use normal Git as the code owner |
| `site-check` | Inspect Git, core, plugins, and PHP in writable content directories |
| `policy` / `site-policy` | Read or change this site's local code policy |
| `jump-in-help [COMMAND]` | Show one topic or the topic list |
| `jump-in-help --all` | Print the complete reference |

Git, common WP-CLI commands, policy actions, plugins, and themes have Bash completion.
Tab does not run sudo, PHP, or site code in the custom completion functions.
Git completion comes from the distribution's `bash-completion` package.

A private root-owned PATH directory also binds `env php`, `env wp`, and child commands
to these wrappers. The global system commands are not replaced. Session `SHELL` is `/bin/bash` for child programs;
the account login shell remains `nologin`.

The WP wrapper rejects `--path`, `--ssh`, and `--http` overrides.
It skips plugins and themes by default, as the previous operator shell did.
Raw WP-CLI commands run as the code owner. They can change code and bypass managed update holds.
Use the root `wp-update` workflow when you need its update policy and checks.

## Review and commit a change

```sh
git status --short
git diff -- wp-content/plugins/example-plugin
git add -- wp-content/plugins/example-plugin
git diff --cached
git commit -m "Fix the local compatibility issue"
```

Stage only reviewed paths. The shell does not stage or commit files automatically.
Git receives a session default identity: `Site operator <operator@localhost>`.
A developer can set another identity in their own session settings.

## Check code

```sh
site-check
site-check git
site-check core
site-check plugins
site-check plugins example-plugin
site-check files
```

Checks run as the code owner. Plugin checks reuse the existing local code policy.
They show accepted exceptions, unexpected mismatches, and unavailable checksums.
The core and plugin checks need access to WordPress.org for upstream checksums.

`site-check git` shows changes; it does not accept them or set a root baseline.
`site-check files` lists PHP in the standard writable content directories for review.
It is not the full host monitor. Run `wp-monitor` as root for host services,
certificate keys, administrator baselines, and acknowledgement.
See [site code policy](SITE_CODE_POLICY.md) for policy rules and file acceptance.

## Set a local code policy

```sh
policy
policy show --yaml
policy hold example-plugin --reason "Local compatibility fix"
policy unhold example-plugin
policy hold example-theme --kind theme --reason "Local patch"
policy checksums example-plugin --mode git
policy allow-file example-plugin includes/file.php --yes
```

The site comes from your UID. Do not add a domain argument.
The wrapper uses the existing narrow `sudo wp-site policy` interface.
It does not grant a root shell, Git as root, or policy access to another site.
Review an exact file before you accept its state.

## Keep personal settings

| Item | Path |
|---|---|
| HOME | `/srv/sites/<domain>/owner-tmp/` |
| History | `$HOME/.bash_history`, mode `0600` |
| WP-CLI cache | `$HOME/wp-cli-cache/`, mode `0700` |
| Optional user profile | `$HOME/.site-shellrc` |

Ansible does not overwrite these user files. On entry, an older owner-owned cache directory
with group/other access is tightened to `0700`. Its contents are retained.
The user profile runs after the UID/GID change, as the code owner.
Use it for editor settings, aliases, or a personal Git identity.
Do not put passwords in prompt text or inline command arguments.
A leading space omits a typed command from the default Bash history.
Root history, SSH agents, and inherited password variables are not copied into the session.

## Install and extend

Apply the full server playbook from the controller:

```sh
scripts/lemp SERVER site
```

`common` installs Bash, `bash-completion`, and `util-linux` for `setpriv`.
`server_tools` delivers commands, libraries, profiles, and completion files.
The existing site role supplies the narrow policy permission.
No new sudo rule or scheduled job is created by these shell tools.

- Add commands to `server-tools/bin/`.
- Add shared libraries or profiles to `server-tools/lib/`.
- Keep the private `php`/`wp` PATH wrappers under `server-tools/bin/site-session/`; do not put them at the top level.
- Add optional reference topics to `site_help.py` and completion rules to `site-completion.bash`.
- Keep privileged permissions and package dependencies in Ansible.

A new script needs no individual Ansible copy task or command registration.
Delivery does not grant sudo, access to secrets, or scheduled execution.
Old server files are retained when a source file is removed.

## Identity and failure rules

`jump-in` requires root. It reads literal root-owned `conf/site.env` facts.
It does not source shell code or execute `wp-config.php`.
It validates the account, primary group, home path, document root, and executable paths.
It creates a private PTY with `runuser` and sets explicit owner IDs and groups.
`setpriv` then clears capabilities before the owner command starts.
The root terminal is not the owner shell terminal. This avoids shared-terminal injection
into the administrator session. See [runuser security notes](https://manpages.debian.org/trixie/util-linux/runuser.1.en.html).
Supplementary groups are the site group plus `valkey` only when the facts enable Redis.
Unexpected account groups and root's groups are not inherited.
Inheritable and ambient capabilities are cleared. All non-standard file descriptors are closed.
All three standard streams must be terminals, so redirected private root files are not passed to Bash.
The host needs Linux/glibc `close_range` support, as supplied by the supported Debian platform.

`site-shell` does not change credentials. It rejects root, runtime UIDs,
saved root IDs, unexpected groups, and process capabilities.
It maps the current UID through the passwd home and trusted facts.
A domain argument or `SITE_*` environment variable cannot select another site.
A non-terminal launch, `-c`, or explicit login-shell flags return an error.
No unsupported call starts an interactive Bash automatically.

The root-owned profile loads only after validation. The profile, helpers, and libraries
stay root-owned. PHP-FPM permissions are unchanged.

## Contract for a future jail or SSH entry

The current feature works without Jailkit or SSH. A future launcher can call the same
unprivileged `site-shell` after authentication, isolation, and credential setup.

The future environment must provide:

- The same absolute site home and `htdocs` path.
- Root-owned facts at that site's `conf/site.env` path.
- A unique owner UID, its primary group, and the expected optional Valkey group.
- Matching passwd/group records inside the jail.
- A private terminal when the parent session is privileged.
- Python, Bash, the selected PHP binary, WP-CLI, Git, libraries, and shared profiles.
- A writable private owner home and history file.
- A separately reviewed route for the narrow policy interface.

Site detection does not need a `/proc` mount. No launcher token or domain environment
variable is needed. SSH authentication, jail construction, remote `-c` commands,
and file transfer modes are separate work for the next version.
