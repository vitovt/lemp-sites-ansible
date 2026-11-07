"""Root-owned, site-local policy for code updates and integrity checks."""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

import yaml

SLUG = re.compile(r'[a-z0-9][a-z0-9_-]*\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
STATES = {'added', 'modified', 'deleted'}
MODES = {'wordpress_org', 'git'}


def empty():
    return {'version': 1, 'plugins': {}, 'themes': {}}


def safe_path(value):
    if not isinstance(value, str) or not value or value.startswith('/') or '\\' in value:
        return False
    return all(p not in ('', '.', '..') for p in value.split('/'))


def validate(data):
    if not isinstance(data, dict) or set(data) != {'version', 'plugins', 'themes'} or type(data['version']) is not int or data['version'] != 1:
        raise ValueError('Code policy must have version 1, plugins, and themes')
    for kind in ('plugins', 'themes'):
        entries = data[kind]
        if not isinstance(entries, dict):
            raise ValueError(kind + ' must be a mapping')
        for slug, rules in entries.items():
            if not isinstance(slug, str) or not SLUG.fullmatch(slug) or not isinstance(rules, dict):
                raise ValueError('Invalid ' + kind + ' entry')
            if set(rules) - {'updates', 'checksums'} or (kind == 'themes' and 'checksums' in rules):
                raise ValueError('Unknown ' + kind + ' rule')
            if 'updates' in rules:
                update = rules['updates']
                if not isinstance(update, dict) or set(update) != {'hold', 'reason'} or type(update['hold']) is not bool or not isinstance(update['reason'], str) or not update['reason'].strip():
                    raise ValueError('Invalid update hold for ' + slug)
            if 'checksums' in rules:
                checks = rules['checksums']
                if not isinstance(checks, dict) or set(checks) != {'mode', 'exceptions'} or not isinstance(checks['mode'], str) or checks['mode'] not in MODES or not isinstance(checks['exceptions'], dict):
                    raise ValueError('Invalid checksum policy for ' + slug)
                if checks['mode'] == 'git' and checks['exceptions']:
                    raise ValueError('Git mode cannot have checksum exceptions')
                for relative, exception in checks['exceptions'].items():
                    if not safe_path(relative) or not isinstance(exception, dict):
                        raise ValueError('Invalid exception path')
                    state = exception.get('state')
                    expected_keys = {'state'} if state == 'deleted' else {'state', 'sha256'}
                    if state not in STATES or set(exception) != expected_keys or (state != 'deleted' and (not isinstance(exception['sha256'], str) or not HASH.fullmatch(exception['sha256']))):
                        raise ValueError('Invalid exception for ' + relative)
    return data


def path_for(base):
    return Path(base) / 'conf/code-policy.yml'


def load(base):
    path = path_for(base)
    if not path.exists() and not path.is_symlink():
        return empty()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Code policy must be a root-owned regular file without group/other write access')
    return validate(yaml.safe_load(path.read_text()) or {})


def save(base, data):
    validate(data)
    directory = path_for(base).parent
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Site conf must be root-owned and not writable by site users')
    target = path_for(base)
    if target.exists() or target.is_symlink():
        load(base)
    descriptor, name = tempfile.mkstemp(prefix='.code-policy-', dir=directory)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, 'w') as stream:
            yaml.safe_dump(data, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def file_hash(path):
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError('Checksum exception is not a regular file')
    with os.fdopen(descriptor, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def plugin_file(base, slug, relative):
    """Read a plugin file through no-follow directory descriptors."""
    if not SLUG.fullmatch(slug) or not safe_path(relative):
        raise ValueError('Invalid plugin file path')
    parts = ['htdocs', 'wp-content', 'plugins', slug, *relative.split('/')]
    descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=descriptor)
        try:
            if not stat.S_ISREG(os.fstat(leaf).st_mode):
                raise ValueError('Plugin path is not a regular file')
            digest = hashlib.sha256()
            preview = bytearray()
            with os.fdopen(leaf, 'rb', closefd=False) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(chunk)
                    if len(preview) < 4096:
                        preview.extend(chunk[:4096 - len(preview)])
            return digest.hexdigest(), bytes(preview)
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)


def plugin_file_absent(base, slug, relative):
    if not SLUG.fullmatch(slug) or not safe_path(relative):
        return False
    parts = ['htdocs', 'wp-content', 'plugins', slug, *relative.split('/')]
    descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts[:-1]):
            try:
                next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                          dir_fd=descriptor)
            except FileNotFoundError:
                return index >= 4  # Only a subdirectory below the plugin may be absent.
            os.close(descriptor)
            descriptor = next_descriptor
        try:
            os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    finally:
        os.close(descriptor)


def allowed_mismatch(base, policy, item):
    """Return true only for the exact expected WP-CLI mismatch and file state."""
    if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ('plugin_name', 'file', 'message')):
        return False
    slug, relative, message = item['plugin_name'], item['file'], item['message']
    rules = policy['plugins'].get(slug, {}).get('checksums', {})
    exception = rules.get('exceptions', {}).get(relative)
    if not exception or rules.get('mode') != 'wordpress_org':
        return False
    kind = {'File was added': 'added', "File doesn't verify against checksum": 'modified',
            'File does not exist': 'deleted', 'File is missing': 'deleted'}.get(message)
    if kind != exception['state']:
        return False
    try:
        if kind == 'deleted':
            return plugin_file_absent(base, slug, relative)
        return plugin_file(base, slug, relative)[0] == exception['sha256']
    except (OSError, ValueError):
        return False


def identity_prefix(user, *, current=False):
    if current:
        import pwd
        if os.getuid() == 0 or os.geteuid() != os.getuid() or pwd.getpwuid(os.getuid()).pw_name != user:
            raise PermissionError('Current-user checks require the matching unprivileged owner')
        return ['env', '-i', 'PATH=/usr/local/bin:/usr/bin:/bin']
    return ['runuser', '-u', user, '--', 'env', '-i', 'PATH=/usr/local/bin:/usr/bin:/bin']


def git_baseline_issues(base, user, names, *, as_current_user=False):
    issues = []
    for name in sorted(names):
        path = 'wp-content/plugins/' + name
        prefix = identity_prefix(user, current=as_current_user) + [
            'HOME=' + str(Path(base) / 'owner-tmp'), 'git', '-C', str(Path(base) / 'htdocs')]
        tracked = subprocess.run(prefix + ['ls-files', '--', path], text=True, capture_output=True)
        dirty = subprocess.run(prefix + ['status', '--porcelain', '--', path], text=True, capture_output=True)
        if tracked.returncode or not tracked.stdout.strip() or dirty.returncode or dirty.stdout.strip():
            issues.append({'plugin_name': name, 'file': path, 'message': 'Git baseline missing or dirty'})
    return issues


def check_plugins(base, user, php_version, wpcli, *, as_current_user=False, component=None, details=False):
    """Return a PASS/FAIL line for the site's plugin checksum check."""
    import json
    base = Path(base)
    policy = load(base)
    if component and not SLUG.fullmatch(component):
        raise ValueError('Invalid plugin slug')
    home = base / ('owner-tmp' if as_current_user else 'tmp')
    prefix = identity_prefix(user if as_current_user else user + '-php', current=as_current_user) + [
              'HOME=' + str(home), 'WP_CLI_CACHE_DIR=' + str(home / 'wp-cli-cache'),
              'php' + php_version, wpcli, '--path=' + str(base / 'htdocs'),
              '--skip-plugins', '--skip-themes', '--no-color']
    result = subprocess.run(prefix + ['plugin', 'verify-checksums', component or '--all', '--format=json'],
                            text=True, capture_output=True, timeout=120)
    if details:
        for line in result.stderr.splitlines():
            if 'Could not retrieve the checksums' in line:
                print('UNAVAILABLE ' + line.removeprefix('Warning: '))
    expected_stderr = (
        r"Warning: Couldn't fetch response from https://downloads\.wordpress\.org/plugin-checksums/.* \(HTTP code 404\)\.",
        r'Warning: Could not retrieve the checksums for version .* skipping\.',
        r'Error: Only verified \d+ of \d+ plugins \(\d+ failed, \d+ skipped\)\.',
        r'Error: No plugins verified \(\d+ failed(?:, \d+ skipped)?\)\.',
    )
    if any(not any(re.fullmatch(pattern, line) for pattern in expected_stderr)
           for line in result.stderr.splitlines() if line.strip()):
        return 'FAIL plugin_checksums: checker reported an unexpected error'
    try:
        rows = json.loads(result.stdout)
    except (ValueError, TypeError):
        rows = None
    if rows is not None and not isinstance(rows, list):
        rows = None
    git_modes = {name for name, rule in policy['plugins'].items()
                 if (component is None or name == component) and rule.get('checksums', {}).get('mode') == 'git'}
    expected = {(name, relative) for name, rule in policy['plugins'].items()
                if component is None or name == component
                for relative in rule.get('checksums', {}).get('exceptions', {})}
    if rows is None:
        if expected:
            return 'FAIL plugin_checksums: expected local exceptions are absent from checker output'
        if git_baseline_issues(base, user, git_modes, as_current_user=as_current_user):
            return 'FAIL plugin_checksums: local Git baseline missing or dirty'
        inventory = subprocess.run(prefix + ['plugin', 'list', '--format=json'],
                                   text=True, capture_output=True, timeout=120)
        match = re.fullmatch(r'Success: Verified (\d+) of (\d+) plugins\.\s*', result.stdout)
        try:
            plugins = json.loads(inventory.stdout)
            count = sum(p.get('status') in ('active', 'inactive') and (component is None or p.get('name') == component) for p in plugins)
        except (ValueError, TypeError, AttributeError):
            count = -1
        skipped_warnings = len(re.findall(r'Could not retrieve the checksums for version .* skipping\.', result.stderr))
        if result.returncode == 0 and inventory.returncode == 0 and (
                (count == 0 and result.stdout.strip() == 'Success: Plugin already verified.') or
                (match and int(match[2]) == count and int(match[1]) + skipped_warnings == count)):
            return f'PASS plugin_checksums: {count - skipped_warnings} verified, {skipped_warnings} unavailable'
        return f'FAIL plugin_checksums: checker returned invalid output (exit {result.returncode})'
    unexpected = []
    accepted = []
    seen = set()
    for row in rows:
        name = row.get('plugin_name') if isinstance(row, dict) else None
        if isinstance(row, dict) and (name, row.get('file')) in expected:
            seen.add((name, row['file']))
        if name in git_modes:
            accepted.append(row)
        elif allowed_mismatch(base, policy, row):
            accepted.append(row)
        else:
            unexpected.append(row)
    for name, relative in sorted(expected - seen):
        unexpected.append({'plugin_name': name, 'file': relative,
                           'message': 'expected local exception is absent or changed'})
    unexpected.extend(git_baseline_issues(base, user, git_modes, as_current_user=as_current_user))
    if details:
        for row in accepted:
            if isinstance(row, dict):
                print(f'ACCEPTED {row.get("plugin_name")} {row.get("file")}: {row.get("message")}')
        for row in unexpected:
            if isinstance(row, dict):
                print(f'FAIL {row.get("plugin_name")} {row.get("file")}: {row.get("message")}')
    if unexpected:
        detail = '; '.join(f'{r.get("plugin_name", "?")}/{r.get("file", "?")}: {r.get("message", "?")}'
                           for r in unexpected[:5] if isinstance(r, dict))
        return f'FAIL plugin_checksums: {len(unexpected)} unexpected mismatch(es): {detail}'
    if result.returncode not in (0, 1):
        return f'FAIL plugin_checksums: checker exit {result.returncode}'
    skipped_warnings = len(re.findall(r'Could not retrieve the checksums for version .* skipping\.', result.stderr))
    if result.returncode == 1:
        summary = re.search(r'Only verified \d+ of \d+ plugins \((\d+) failed, (\d+) skipped\)', result.stderr)
        if not summary:
            summary = re.search(r'No plugins verified \((\d+) failed(?:, (\d+) skipped)?\)', result.stderr)
        failed_plugins = {r.get('plugin_name') for r in accepted if isinstance(r, dict)}
        if not summary or int(summary[1]) != len(failed_plugins) or int(summary[2] or 0) != skipped_warnings:
            return 'FAIL plugin_checksums: checker failed beyond accepted mismatches'
        skipped = int(summary[2] or 0)
    else:
        skipped = skipped_warnings
    return f'PASS plugin_checksums: no unexpected mismatches; {len(accepted)} accepted, {skipped} unavailable'


if __name__ == '__main__':
    if len(sys.argv) != 6 or sys.argv[1] != 'check-plugins':
        raise SystemExit('usage: code_policy.py check-plugins BASE SITE_USER PHP_VERSION WPCLI')
    try:
        print(check_plugins(*sys.argv[2:]))
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print('FAIL plugin_checksums: ' + str(exc))
