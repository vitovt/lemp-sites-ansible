# PHP Version Selection and Safe Site Switching

Status: implementation specification. This document does not change server behavior.

## Goal

An operator sets one `php_version` for each site. A full `site.yml` run installs
the required PHP branches from the configured Sury repository. A site can move
to another installed branch without loss of service or another site's settings.
An interrupted run can continue or restore the previous working branch.

## Inventory contract

- Every `wp_sites` entry in `sites.yml` MUST contain `php_version` as a quoted
  string. An empty site list is valid. A missing, null, empty, or unsupported
  value MUST fail before any host change.
- The supported branch list MUST have one repository-owned source. The current
  policy accepts `8.3`, `8.4`, and `8.5`. This list is a validation policy. It
  does not promise that a package is available from Sury at run time.
- Remove `php_default_version` and `php_versions` from server settings and role
  defaults. Do not use an implicit `8.3` in roles, playbooks, verification, or
  `scripts/site`. Reject the retired server keys with a clear migration error.
- Update tracked examples and test fixtures. For each private existing
  inventory, require the operator to set the branch of every site before the
  next deployment. Do not silently assign a branch. `scripts/site add` MUST
  require a branch. A clone MAY inherit the source site's explicit branch
  unless the operator supplies another branch.
- A new server with no sites MUST not install or enable a default PHP branch.

Example:

```yaml
wp_sites:
  example.com:
    php_version: "8.4"  # Required. Select one supported branch.
```

## Commands

- `scripts/lemp <server> doctor` validates the inventory and the current site
  state. It MUST allow a requested branch to differ from the current branch
  when a valid switch is planned. It makes no changes.
- After an operator changes a site's `php_version`,
  `scripts/lemp <server> site` installs and switches the branch.
- `scripts/lemp <server> site-apply <domain>` can switch only to a branch that
  the full playbook has already installed and configured on that server. It
  MUST refuse a missing or incomplete target branch before it changes files or
  services. The error MUST name the site and branch and direct the operator to
  `scripts/lemp <server> site`.
- `scripts/lemp <server> verify` checks the running branch, its socket, the
  site response, and the absence of stale managed pools.

## Full playbook preparation

1. Read all explicit site branches. Install their required packages and
   extensions from Sury. Keep installed old packages; do not purge them as
   part of a site switch.
2. Read the current branch from the deployed site state and confirm it agrees
   with the active pool. Include old branches in the transition plan even when
   no site requests them in the new inventory.
3. Do not stop an old FPM service while any site still uses its pool. Do not
   validate a new FPM branch before it has a pool. Remove the early global
   `flush_handlers` in the Valkey role. Apply Valkey changes without flushing
   pending PHP handlers.
4. Keep the current PHP tuning, extension policy, Unix users, file permissions,
   and site isolation on the target branch. A target branch is ready only when
   the complete managed package set and configuration are present.

## Per-site switch

Use a version-specific socket for each new pool, for example
`/run/php/<site-user>-php<version>.sock`. The current unversioned socket can
remain active during a first switch. The two FPM masters MUST NOT bind the same
socket. A site that keeps its current branch but still uses an unversioned
socket needs a separate, safe adoption step. That step must not change its PHP
branch.

For an existing site, use this order:

1. Use the existing site operation lock. Record the old branch, socket, pool
   path, and vhost in a root-only migration state. On a retry, inspect this
   state and the real services before choosing the next step.
2. Create the target pool with the same site runtime user and isolation rules
   on a different socket. Keep the old pool and old Nginx route active.
3. Validate the target FPM configuration. Start or reload the target service.
   Confirm that the target socket accepts a PHP request and reports the target
   branch. A CLI-only version check is insufficient.
4. Render the new Nginx route. Run `nginx -t`, then reload Nginx. Use a local
   request that bypasses FastCGI cache to confirm WordPress reaches the target
   FPM socket. Keep the old pool available during this check. A diagnostic PHP
   probe MUST be local-only and short-lived. Remove it on success or failure.
5. If the check succeeds, update `site.env`, remove the old pool, and reload
   the old FPM service. Stop and disable an old FPM service only when no other
   site uses it. Confirm the old pool and socket are gone. Clear the migration
   state after all checks pass.

The switch MUST keep the site's database, WordPress files, credentials, and
administrator account unchanged. A PHP branch switch MUST NOT trigger a
WordPress core or plugin update. A switch of one site MAY reload a shared FPM
service, but it MUST NOT edit another site's pool or interrupt its requests.

## Failure and retry

- Before the Nginx switch, a failure MUST leave the old route and old pool
  active. Remove an incomplete target pool only when it is safe to do so.
- After the Nginx switch, a failed health check MUST restore the old vhost,
  reload Nginx, and confirm the old FPM route works. Keep the old pool until
  this confirmation succeeds.
- If cleanup fails after a healthy switch, keep the new site route active.
  Report the extra old pool and complete cleanup on the next run.
- Never report success when the requested branch differs from the branch that
  serves the site. Preserve the migration state when automatic recovery fails.
  Give the operator a clear error and a manual recovery path.
- A second run with no requested change MUST report `changed=0`. A retry after
  an interrupted switch MUST reach one consistent state without duplicate
  pools or sockets.

## Tests and acceptance

Run integration tests only on a disposable VM with generated sites and
credentials. The VM must have no production inventory, keys, or secrets.

| Test | Required result |
| --- | --- |
| Missing or invalid `php_version`; retired server keys | `doctor` and deployment reject the input before host changes. |
| New site on each supported branch | Full `site.yml` installs the packages, starts FPM, and serves WordPress on the selected branch. |
| `8.3 -> 8.4`, `8.4 -> 8.3`, and `8.4 -> 8.5` | The new branch serves uncached requests; the old pool is removed after health checks. |
| `site-apply` with a missing or incomplete target branch | It fails before a file or service change and names the full `site` command. |
| `site-apply` with a managed target branch | It uses the same safe switch and rollback sequence as the full playbook. |
| Failure before and after Nginx reload | The old branch serves the site after rollback; a retry completes. |
| Two sites on one or different branches | The peer site keeps its response, pool, socket, and credentials. A shared service stays active while a peer uses it. |
| Requests during a healthy switch | The site and its peer return no 5xx responses during the controlled cutover. |
| Repeated full and site-only runs | `changed=0`, `failed=0`, and no stale managed pool or socket. |
| Direct local uncached PHP and WordPress probes | They report the configured FPM branch and a healthy site response. |

Update the controller tests, example inventory comments, README commands,
Deployment, Operations, Testing, and Validation documents with the new
contract. Record which branches and failure cases were tested on a VM. State
separately that plugin and theme compatibility needs application testing;
infrastructure checks cannot prove it.
