# Yoast llms.txt on a Protected WordPress Site

Generate Yoast's `llms.txt` in a PHP-writable content directory. Keep WordPress
core, plugins, themes, and the document root protected from web-process writes.

Copy this README to `wp-content/mu-plugins/README-site-llms-path.md` beside
`site-llms-path.php` in the site's Git repository. Replace `example.com` in
server paths and commands with the site's domain.

## Problem

Yoast normally writes `llms.txt` in the site's document root. This hosting
layout uses two Linux accounts:

| Account | Access |
| --- | --- |
| Site code owner | Edit code and run approved CLI operations |
| PHP-FPM runtime user | Read code; write only in selected content directories |

The PHP runtime cannot create files in the document root or `wp-content/`.
Yoast also calls `WP_Filesystem()` without a directory context. WordPress tests
`wp-content/` when it selects a filesystem method. Direct access can therefore
be unavailable even if an individual `llms.txt` has mode `0666`.

An existing manually created file can also prevent generation: Yoast avoids
overwriting an `llms.txt` that it does not recognise as its own. Do not create
an empty placeholder as a permission workaround.

References:

- [Yoast llms.txt specification](https://developer.yoast.com/features/llms-txt/functional-specification/)
- [WordPress filesystem method selection](https://developer.wordpress.org/reference/functions/get_filesystem_method/)
- [Yoast filesystem adapter](https://github.com/Yoast/wordpress-seo/blob/trunk/src/llms-txt/infrastructure/file/wordpress-file-system-adapter.php)

## Solution

| Component | Purpose |
| --- | --- |
| Site-local MU plugin | Set the Yoast output directory through `wpseo_llmstxt_filesystem_path` |
| `FS_METHOD=direct` | Use the direct WordPress filesystem backend |
| Writable uploads directory | Store the generated file without granting code-directory writes |
| Exact Nginx location | Serve the generated file at `/llms.txt` |

`FS_METHOD` selects a backend. It does not grant Linux permissions. Keep the
separate PHP user, `DISALLOW_FILE_MODS`, and existing code permissions. Other
filesystem API calls in this site will also use the direct backend; Linux
permissions remain the write boundary.

## Install the MU Plugin

- [ ] Confirm that the installed Yoast version supports
  `wpseo_llmstxt_filesystem_path`.
- [ ] As the code owner, create `wp-content/mu-plugins/` if it is absent.
- [ ] Save this code as `wp-content/mu-plugins/site-llms-path.php`:

```php
<?php
/**
 * Store generated Yoast llms.txt in the PHP-writable uploads directory.
 */
add_filter('wpseo_llmstxt_filesystem_path', static function ($path) {
    $directory = WP_CONTENT_DIR . '/uploads/yoast-llms';

    if (!is_dir($directory)) {
        wp_mkdir_p($directory);
    }

    return $directory;
});
```

WordPress loads a top-level MU plugin automatically. Do not edit Yoast's plugin
files. Keep this MU plugin under Git and protected from PHP runtime writes.
The path above assumes this hosting layout's standard `wp-content/uploads/`.

## Select the Filesystem Backend

Edit this site's private `wp-config.php` as the code owner. Before loading
`wp-settings.php`, set:

```php
define('FS_METHOD', 'direct');
```

If the constant already exists, edit its definition. Do not add a duplicate.
Do not commit `wp-config.php` or weaken its permissions.

## Configure Nginx

As root, create:

```text
/srv/sites/example.com/conf/nginx/llms.conf
```

Use this content:

```nginx
location = /llms.txt {
    alias /srv/sites/example.com/htdocs/wp-content/uploads/yoast-llms/llms.txt;
    default_type text/plain;
}
```

The hosting vhost already includes `conf/nginx/*.conf`. Keep this directory and
configuration root-owned. This configuration is outside the site's `htdocs`
Git repository; preserve it with the server configuration and backups.

Validate before reloading:

```sh
nginx -t && systemctl reload nginx
```

Do not replace the alias with a symlink. This hosting configuration uses
`disable_symlinks on`.

## Generate and Verify

- [ ] Preserve any needed content from a manually created root `llms.txt`, then
  remove that placeholder. Do not leave it world-writable.
- [ ] Do not pre-create the replacement file as root or the code owner.
- [ ] Disable and re-enable Yoast's llms.txt feature in WordPress, then save.
- [ ] Let the web process create the file with its own write permissions.

From the server, check:

```sh
curl -i https://example.com/llms.txt
stat -c '%A %U:%G %n' \
  /srv/sites/example.com/htdocs/wp-content/uploads/yoast-llms/llms.txt
```

Expect HTTP `200`, generated text, and a file writable by the PHP runtime
account. Check the public response after any CDN cache has expired or been
purged if it still shows old content.

Yoast schedules regeneration weekly. The hosting system runs due WordPress
cron events as the PHP runtime user. Check `logs/cron.log` if later generation
fails. A wrapper that skips all plugins will not load Yoast's event callback.

## Git and Maintenance

- Commit `site-llms-path.php` and this README after review.
- Keep the generated `wp-content/uploads/yoast-llms/llms.txt` out of Git.
- Keep `wp-config.php` out of Git. Record its required setting in this README.
- Preserve `conf/nginx/llms.conf` outside the htdocs repository.
- Test generation and public delivery after Yoast upgrades.
- To remove this integration, first disable the Yoast feature. Review the
  Nginx mapping, MU plugin, generated files, and shared `FS_METHOD` setting
  separately. Other site features may also depend on direct filesystem access.
