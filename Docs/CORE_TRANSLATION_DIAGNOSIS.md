# Core translation locale and false-success diagnosis

The WordPress Ukrainian locale is `uk`, not `uk_UA`. GlotPress defines
`wp_locale = 'uk'`; its separate Facebook locale is `uk_UA`. The server's
`WPLANG=uk_UA` does not establish where that value originated. Do not rewrite
it automatically or infer that all language/region codes can be shortened.

Primary sources inspected for this change:

- [GlotPress locale registry](https://github.com/GlotPress/GlotPress/blob/develop/locales/locales.php): Ukrainian `wp_locale` is `uk`.
- [WP-CLI translation lookup](https://github.com/wp-cli/language-command/blob/main/src/WP_CLI/CommandWithTranslation.php): language packs are matched by exact language code. An unavailable code returns `WP_Error('not_found', ...)` before installation.
- [WP-CLI core language install](https://github.com/wp-cli/language-command/blob/main/src/Core_Language_Command.php): `not_found` prints a warning and `Language 'CODE' not installed.`, increments skips rather than errors, then reports batch results.
- [WP-CLI batch results](https://github.com/wp-cli/wp-cli/blob/main/php/utils.php): skips without errors use the success path, e.g. `Success: Installed 0 of 1 languages (1 skipped).`; there is no failure exit on that path.
- `is-installed` uses installed translation metadata through `wp_get_installed_translations`, unlike `language core list` which can mark a locale active just because it equals `get_locale()`.

These upstream code paths explain how an unknown locale can return success
without installing a language. Combined with the owner's server observations
(`uk` available, `uk_UA` missing, unchanged WPLANG), they explain the old
wrapper's false PASS. The exact installed WP-CLI version and original suppressed
installation output were not captured, so its precise message is not a recorded
runtime observation. No live site was modified during this investigation.

## Result accounting

Core checksum/version/database, permissions, health and scope checks remain
mandatory. Translation availability and installation are separate outcomes.
An unknown locale is rejected before installation. A successful install exit
must be followed by successful `is-installed`, repeated after translation update.
WPLANG is checked even when installation fails. English uses the built-in
`en_US` strings and requires no downloaded language pack.

If translations fail but core passes all checks, core is committed separately,
excluding every `wp-content/languages/` path. The report includes `PASS core:core`
and `FAIL translations:core`, returns failure overall, and leaves translation
changes uncommitted for review. Core checksums are repeated after the translation
attempt. Unexpected WPLANG changes, checksum failures, health failures or changes
outside the allowed scope prevent the core commit. Dirty core targets retain
the existing no-automatic-commit behavior.

## Explicit recovery

For the reported site, when the current installed core's API confirms `uk` is
available, the tool prints these manual instructions. These are operator actions,
not actions performed automatically by the updater:

```sh
jump-in allourworld.info
wp language core install uk
wp language core is-installed uk
# Continue only if is-installed returned exit 0, and if you choose this migration:
wp option update WPLANG uk
exit
wp-update translations allourworld.info
```

The translation-only command needs today's backup by default; the explicit
`--skip-backup-check` bypass is also supported. It updates no core files and uses
the operation lock, owned maintenance gate, Git baseline, clean translation
target, scope, permission and health checks. A successful repair commits only
core translation paths. A failed repair makes no commit, returns failure, and
removes only its own maintenance gate. Inspect any partial translation edits
before retrying; the command does not overwrite dirty translation targets.
