# Recover a Core Update Stopped by Bundled Themes

Use this procedure when `wp-update` reports changes outside core under
`wp-content/themes/twenty*/` and the matching theme language paths.
Do not accept other paths without investigating them.

## Prevent another bundled-theme installation

Managed `wp-update` core updates set `CORE_UPGRADE_SKIP_NEW_BUNDLED=true` through
WP-CLI `--exec`. This selects WordPress's no-new-bundles behaviour. It covers
manual core updates and the core stage of `all` and automatic runs.

The setting is local to that process. It does not rewrite `wp-config.php`,
change existing themes, delete themes, or affect web requests. If the constant
is already defined with a conflicting value, the core update stops before
copying core files. Review the site configuration before retrying.

Raw `wp core update` calls outside `wp-update` do not inherit this policy.
`--skip-themes` alone skips theme loading; it does not prevent installation.
The outside-path guard remains enabled. Do not permit the whole theme tree
as part of a core commit.

References: [WordPress core upgrade](https://developer.wordpress.org/reference/classes/core_upgrader/upgrade/),
[WP-CLI core update](https://developer.wordpress.org/cli/commands/core/update/).

## Check the interrupted update

- [ ] Read the failed `logs/wp-update-*.log` as root. Record the backup snapshot.
- [ ] Enter the site shell and check the installed core:

```sh
jump-in example.com
wp core version
wp core verify-checksums
wp theme list --fields=name,status,version
wp option get template
wp option get stylesheet
git status --short
exit
```

Core checksums do not verify extra theme files or prove application compatibility.
Do not repeat the updater merely to create a missing commit. An update can have
changed core and the database before the final scope check stopped it.

## Restore public access

From root, test the site through the internal health socket:

```sh
curl --noproxy '*' --unix-socket /run/wp-hosting/health.sock \
  -skS --max-time 20 -o /dev/null -w 'HTTP %{http_code}\n' \
  -H 'X-WP-Health: 1' \
  'https://example.com/?wp-update-health=1'
```

- [ ] Require HTTP 200 and successful core checksums.
- [ ] Investigate an error before removing maintenance.
- [ ] After review, remove only this site's hosting gate:

```sh
rm -- /srv/sites/example.com/conf/maintenance
```

- [ ] Test the public home page, a post, admin login, and important site actions.
- [ ] Keep the recorded pre-update backup. Do not undo a database upgrade with
  a file-only `git restore`. Plan rollback with the matching database backup.

## Record the core update

```sh
jump-in example.com
git diff --cached --name-status
git diff --stat
git diff -- wp-admin/ wp-includes/
```

- [ ] If the index is not empty, finish or separate its existing staged changes first.
- [ ] Verify and stage the core directories, including new files:

```sh
git add -- wp-admin/ wp-includes/
```

- [ ] Use `git status --short` to identify changed root core files. Add those
  files by exact name. Do not add `wp-config.php`, custom root PHP, or a wildcard
  such as `*.php`.
- [ ] Review changed core language files in `wp-content/languages/`. Add only
  the exact core language paths. Plugin and theme language packs belong in
  separate commits. Do not stage the entire languages directory.
- [ ] Review the complete staged diff, then record the actual version:

```sh
git diff --cached --stat
git diff --cached
git commit -m "fix(core): update WordPress to VERSION" \
  -m "Record the verified core update after reviewing the interrupted run."
```

Replace `VERSION` with the output of `wp core version`.

## Resolve the added themes

Choose one action per theme. Do not ignore theme code to make Git clean.

| Choice | Action |
| --- | --- |
| Keep | Compare the added files with the trusted matching theme release. Review the exact theme and language paths. Commit them separately from core. |
| Remove | Confirm the theme is inactive and is not the parent of the active theme. Delete that theme by exact slug with `wp theme delete SLUG`. Review leftover translation paths separately. |

`wp option get template` identifies the active parent theme. `stylesheet`
identifies the active theme or child theme. Do not remove either. Keep any
required fallback or other intentionally installed theme.

Core checksum success does not validate theme contents. Do not run a theme
checksum command unless it is actually supported by the installed tooling.

Finish with `git status --short`. Automatic updates still require a clean
repository. Review remaining local files individually; do not bulk-commit them.

## Update installed themes later

Run managed updates as root:

```sh
wp-update plan example.com --scope themes --policy minor
wp-update apply example.com --scope themes --policy minor
wp-update apply example.com --scope theme:allium --policy latest
```

`minor` keeps the first version number for themes. It does not guarantee
compatibility. `latest` permits major updates. Local holds and standard backup,
Git, checksum where supported, and HTTP checks still apply. A theme scope
updates installed themes; it does not install a missing theme.

## Validation

Tests cover core-only flag injection, unchanged theme/plugin commands, and
executable PHP fixtures for unset, true, and conflicting constant values.
Fixtures check that no new bundled theme or theme language file is written,
while existing theme files and site configuration remain unchanged.

The PHP fixture models the documented bundle gate. It is not a real WordPress
major-version update. No live server was accessed or changed during this fix.

Local results: `tests/check.sh` passed 250 unit tests and all syntax checks.
The isolated committed feature source passed 41 updater tests. PHP policy tests
ran with the local PHP CLI. A real WordPress major upgrade remains untested.
