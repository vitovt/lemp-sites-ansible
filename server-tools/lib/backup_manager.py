"""Backup operator interface. Recovery is extraction only."""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import uuid

from backup_recovery import regular, relative_path, verify_manifest
from wp_hosting import DOMAIN, SITES, operation_lock, read_env, run, sha256_file

RESTORE = Path('/srv/restore')
ARCHIVES = Path('/root/site-archives')
PREFERENCES = Path('/var/lib/wp-hosting/backup-manager/preferences.json')
SCOPES = ('file', 'directory', 'files', 'database', 'complete')
ACTIONS = ('list', 'create', 'extract', 'export', 'copy', 'verify', 'browse', 'compare',
           'status', 'retention', 'instructions', 'workspaces', 'cleanup', 'preferences', 'delete-snapshots')


class Cancelled(Exception):
    """Normal operator cancellation."""


def ask(label, choices=None, default=None):
    if not sys.stdin.isatty():
        raise ValueError('Missing value: ' + label + '; supply it as a CLI option')
    suffix = (' [' + '/'.join(choices) + ']') if choices else ''
    value = input(label + suffix + (f' (default {default})' if default else '') + ': ').strip()
    if not value and default is not None:
        value = default
    if not value or value.lower() in ('cancel', 'quit'):
        raise Cancelled()
    if choices and value not in choices:
        raise ValueError('Choose one of: ' + ', '.join(choices))
    return value


def confirm(label, yes=False):
    if yes:
        return
    if not sys.stdin.isatty():
        raise ValueError('Confirmation required; inspect the preview and pass --yes')
    if input(label + ' [y/N] ').strip().lower() != 'y':
        raise Cancelled()


def safe_text(value):
    # Snapshot filenames and hostnames can contain terminal control characters.
    return ''.join(c if c.isprintable() else f'\\x{ord(c):02x}' for c in str(value))


def table(headers, rows):
    rows = [[safe_text(x) for x in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    print('  '.join(h.ljust(w) for h, w in zip(headers, widths)))
    for row in rows:
        print('  '.join(x.ljust(w) for x, w in zip(row, widths)))
    if not rows:
        print('(none)')


def private_directory(path):
    path = Path(path)
    # Inspect each existing ancestor before mkdir; never follow substituted links.
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise ValueError('Managed directory contains a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Managed directory must be private and root-owned: ' + str(path))
    return path


def private_file(path):
    path = Path(path)
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise ValueError('Credential or metadata path contains a symlink')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise ValueError('Require a private root-owned regular file: ' + str(path))
    return path


def save_json(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def environment(path):
    private_file(path)
    values = read_env(path)
    for key in ('RESTIC_REPOSITORY', 'RESTIC_COPY_REPOSITORY'):
        if re.search(r'://[^/@]*:[^/@]*@', values.get(key, '')):
            raise ValueError('Repository URLs must not contain passwords; use backend credential configuration')
    if not values.get('RESTIC_REPOSITORY') or not values.get('RESTIC_PASSWORD_FILE'):
        raise ValueError('Restic repository and password file are required')
    private_file(values['RESTIC_PASSWORD_FILE'])
    # Do not inherit another repository, plaintext password, password command or backend credentials.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('RESTIC_', 'AWS_', 'B2_', 'AZURE_'))}
    return {**env, **values}


class Repository:
    def __init__(self, env):
        self.env = env
        self._nodes = {}

    def command(self, *args):
        return ['restic', *map(str, args)]

    def call(self, *args):
        return run(self.command(*args), env=self.env, timeout=7200)

    def snapshots(self):
        return json.loads(self.call('snapshots', '--json').stdout)

    def nodes(self, snapshot):
        sid = snapshot['id']
        if sid not in self._nodes:
            values = [json.loads(line) for line in self.call('ls', '--json', sid).stdout.splitlines() if line]
            self._nodes[sid] = [n for n in values if n.get('struct_type') == 'node' or 'path' in n]
        return self._nodes[sid]

    def sites(self, snapshot):
        names = set()
        for node in self.nodes(snapshot):
            path = PurePosixPath(node['path'])
            try:
                parts = path.relative_to(SITES).parts
            except ValueError:
                continue
            if parts and DOMAIN.fullmatch(parts[0]):
                names.add(parts[0])
        return sorted(names)

    def select(self, domain, selector):
        matches = [s for s in self.snapshots() if s['id'].startswith(selector)]
        if len(matches) != 1:
            raise ValueError('Snapshot ID is missing or ambiguous')
        snap = matches[0]
        if domain not in self.sites(snap):
            raise ValueError('Site does not exist in this snapshot')
        return snap

    def selected_nodes(self, snap, domain, scope, selected=None):
        base = SITES / domain
        if scope in ('file', 'directory'):
            path = base / str(relative_path(selected))
        else:
            path = base
        result = []
        for node in self.nodes(snap):
            raw = node['path']
            if any(ord(c) < 32 or ord(c) == 127 for c in raw) or not raw.startswith('/') or str(PurePosixPath(raw)) != raw or '..' in PurePosixPath(raw).parts:
                raise ValueError('Unsafe path in snapshot')
            candidate = PurePosixPath(raw)
            if candidate != path and path not in candidate.parents:
                continue
            rel = str(candidate.relative_to(base))
            if scope == 'files' and not (rel == 'htdocs' or rel.startswith('htdocs/') or rel in ('backup', 'backup/manifest.json', 'backup/files.jsonl')):
                continue
            if scope == 'database' and rel not in ('backup', 'backup/db.sql.gz', 'backup/manifest.json', 'backup/files.jsonl'):
                continue
            if node['type'] not in ('file', 'dir'):
                raise ValueError('Selected tree contains a symlink or special file; use a narrower selection')
            result.append(node)
        if not result or not any(n['type'] == 'file' for n in result):
            raise ValueError('Selection has no files')
        if scope == 'file' and (len(result) != 1 or result[0]['type'] != 'file'):
            raise ValueError('Selection is not one regular file')
        if scope == 'directory' and not any(n['path'] == str(path) and n['type'] == 'dir' for n in result):
            raise ValueError('Selection is not a directory')
        return result


def allocated(path):
    """Count allocated blocks without following symlinks or double-counting hardlinks."""
    total, seen = 0, set()
    pending = [Path(path)]
    while pending:
        item = pending.pop()
        info = item.lstat()
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        total += info.st_blocks * 512
        if stat.S_ISDIR(info.st_mode):
            pending.extend(item.iterdir())
    return total


def process_identity(pid):
    try:
        # starttime plus boot ID prevents PID reuse from marking a task active.
        text = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + text[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def local_repository(value):
    if value.startswith('local:'):
        return Path(value[6:]).resolve()
    if ':' not in value:
        return Path(value).resolve()
    return None


def validate_destination(value, env):
    path = local_repository(value)
    if path is None:
        return
    protected = [SITES, RESTORE, ARCHIVES, Path(env.get('REMOVED_DIR', '/mnt/backups/removed'))]
    source = local_repository(env['RESTIC_REPOSITORY'])
    if source:
        protected.append(source)
    for item in protected:
        item = item.resolve()
        if path == item or path in item.parents or item in path.parents:
            raise ValueError('Destination repository overlaps live, temporary, removed-site, or source data')


class Tasks:
    def __init__(self, env):
        self.env = env
        self.roots = (RESTORE, ARCHIVES)
        for root in self.roots:
            # Workspace roots cannot overlap live data or the configured local repositories.
            protected = [SITES, Path(env.get('BACKUP_MOUNT', '/mnt/backups')),
                         Path(env.get('REMOVED_DIR', '/mnt/backups/removed'))]
            for key in ('RESTIC_REPOSITORY', 'RESTIC_COPY_REPOSITORY'):
                if env.get(key):
                    repository = local_repository(env[key])
                    if repository:
                        protected.append(repository)
            protected = [p.resolve() for p in protected]
            if not root.is_absolute() or any(root == p or root in p.parents or p in root.parents for p in protected):
                raise ValueError('Workspace root overlaps protected data')

    def create(self, purpose, domain='', snapshot='', kept=False, selected=None, task_id=None):
        root = private_directory(ARCHIVES if purpose == 'export' else RESTORE)
        task_id = task_id or ('wm-' + uuid.uuid4().hex)
        if not re.fullmatch(r'wm-[0-9a-f]{32}', task_id):
            raise ValueError('Invalid task ID')
        path = root / task_id
        path.mkdir(mode=0o700)
        meta = {'id': path.name, 'path': str(path), 'site': domain, 'snapshot': snapshot,
                'purpose': purpose, 'kept': kept, 'selected': selected or [], 'state': 'active',
                'created': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(),
                'process': process_identity(os.getpid())}
        save_json(path / 'task.json', meta)
        return meta

    def write(self, task, state):
        self.validate(task)
        task['state'] = state
        save_json(Path(task['path']) / 'task.json', task)

    def validate(self, task):
        path = Path(task['path'])
        if path.parent not in self.roots or not re.fullmatch(r'wm-[0-9a-f]{32}', path.name) or task['id'] != path.name:
            raise ValueError('Not a managed task path')
        if not path.is_dir():
            raise ValueError('Managed task is missing')
        private_directory(path)
        private_file(path / 'task.json')
        if task['purpose'] not in ('extraction', 'verification', 'export', 'transfer'):
            raise ValueError('Unknown task purpose')
        return path

    def inventory(self):
        result = []
        for root in self.roots:
            if not root.exists() and not root.is_symlink():
                continue
            private_directory(root)
            for path in sorted(root.iterdir()):
                try:
                    if path.is_symlink():
                        raise ValueError('symlink')
                    meta = json.loads(private_file(path / 'task.json').read_text())
                    self.validate(meta)
                    if Path(meta['path']) != path:
                        raise ValueError('task metadata path mismatch')
                    if meta['state'] == 'active' and process_identity(meta['pid']) != meta.get('process'):
                        meta = {**meta, 'state': 'interrupted'}
                    meta['allocated'] = allocated(path)
                except (ValueError, OSError, KeyError, TypeError):
                    meta = {'id': 'unregistered', 'path': str(path), 'site': '?', 'snapshot': '?',
                            'purpose': 'review only', 'state': 'unknown', 'created': '?', 'kept': True,
                            'selected': [], 'allocated': allocated(path)}
                result.append(meta)
        # Known legacy leftovers are visible, but never eligible for normal cleanup.
        for path in Path('/srv').glob('wp-verify-*'):
            result.append({'id': 'unregistered', 'path': str(path), 'site': '?', 'snapshot': '?',
                           'purpose': 'legacy verification', 'state': 'unknown', 'created': '?',
                           'kept': True, 'selected': [], 'allocated': allocated(path)})
        return result

    def show(self, full=False):
        values = self.inventory()
        table(['Task', 'Site', 'Snapshot', 'Purpose', 'State', 'Class', 'Created', 'Allocated bytes', 'Path', 'Selection'],
              [[t['id'], t['site'], t['snapshot'][:12], t['purpose'], t['state'],
                'kept' if t['kept'] else 'temporary', t['created'], t['allocated'], t['path'],
                ', '.join(t['selected']) if full else ', '.join(t['selected'][:4])] for t in values])
        print('Total allocated bytes:', sum(t['allocated'] for t in values))
        print('Unregistered data requires manual review. Removed archives and site rollback data are not scratch.')
        return values

    def remove(self, task, archive=False):
        path = self.validate(task)
        current = json.loads(private_file(path / 'task.json').read_text())
        if current['state'] == 'active' and process_identity(current['pid']) == current.get('process'):
            raise ValueError('Workspace is active')
        if current['kept'] and not archive:
            raise ValueError('Kept archive requires --delete-kept')
        # No arbitrary paths, repository roots, or symlink traversal. Python rmtree uses dirfd protection.
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError('This platform has no safe recursive removal')
        for config in path.rglob('config'):
            if config.is_file() and not config.is_symlink() and all((config.parent / name).is_dir() for name in ('data', 'index', 'keys', 'snapshots')):
                raise ValueError('Workspace contains a restic repository; manual review is required')
        shutil.rmtree(path)


@contextlib.contextmanager
def task_run(tasks, purpose, domain='', snapshot='', kept=False, selected=None, task_id=None):
    task = tasks.create(purpose, domain, snapshot, kept, selected, task_id)
    print('Task:', task['id'], 'Log:', str(Path(task['path']) / 'result.log'), flush=True)
    try:
        yield task
        tasks.write(task, 'complete')
        message = 'PASS task ' + task['id']
    except BaseException as exc:
        cancelled = isinstance(exc, (Cancelled, KeyboardInterrupt, InterruptedError))
        tasks.write(task, 'interrupted' if cancelled else 'failed')
        message = ('CANCELLED task ' if cancelled else 'FAIL task ') + task['id'] + '; inspect ' + str(Path(task['path']) / 'result.log')
        (Path(task['path']) / 'result.log').write_text(message + '\nError class: ' + type(exc).__name__ + '\nSee terminal output for the failed check.\n')
        (Path(task['path']) / 'result.log').chmod(0o600)
        notify(message, failed=not cancelled)
        raise
    else:
        (Path(task['path']) / 'result.log').write_text(message + '\n')
        (Path(task['path']) / 'result.log').chmod(0o600)
        notify(message)


def notify(message, failed=False):
    # Only static status, task IDs and controlled paths. No command output or secrets.
    try:
        run(['logger', '-t', 'wp-backup-manager', message], check=False)
        if failed:
            run(['tg-alert', message], check=False)
    except OSError:
        pass


def instructions(path=None, domain='SITE'):
    root = Path(path) if path else Path('WORKSPACE/data')
    print('RECOVERY IS MANUAL. The manager does not apply these commands.')
    print('1. Review the extracted files. Do not run restored PHP or Git hooks.')
    print(shlex.join(['find', str(root), '-type', 'f']))
    print('2. Compare selected files. Review both paths before copying.')
    print(shlex.join(['diff', '-u', str(SITES / domain / 'htdocs/FILE'), str(root / 'htdocs/FILE')]))
    print('3. For a deleted or remote site, prepare the target through Ansible first.')
    print('   On the controller: scripts/lemp TARGET-SERVER site')
    print('4. Read TARGET credentials from the target config without executing PHP.')
    print('   Source wp-config.php contains SOURCE credentials. Do not use them for a new target.')
    print('   Create /root/target-client.cnf with mode 0600. Use explicit target values:')
    print('[client]\nuser=TARGET_DB_USER\npassword=TARGET_DB_PASSWORD\nprotocol=socket\nsocket=/run/mysqld/mysqld.sock')
    print('5. Save the current target database BEFORE a manual import:')
    print('mariadb-dump --defaults-file=/root/target-client.cnf --single-transaction -- TARGET_DB > /root/target-before.sql')
    print('6. Import only after review. TARGET_DB is an explicit placeholder:')
    print(shlex.join(['gzip', '-dc', str(root / 'backup/db.sql.gz')]) +
          ' | mariadb --defaults-file=/root/target-client.cnf --binary-mode --local-infile=0 -- TARGET_DB')
    print('   Alternative: mariadb --protocol=socket --socket=/run/mysqld/mysqld.sock -u TARGET_DB_USER -p TARGET_DB')
    print('7. Copy selected files manually. Replace FILE and TARGET_OWNER; inspect both paths first:')
    print(shlex.join(['install', '-o', 'TARGET_OWNER', '-g', 'TARGET_OWNER', '-m', '0640', str(root / 'htdocs/FILE'), str(SITES / domain / 'htdocs/FILE')]))
    print('   Review ownership and runtime read-only permissions.')
    print('   Use site-fix-perms SITE --check after your permission changes.')
    print('8. Review domains, URLs, salts, cron, payment, email, webhooks and external integrations.')
    print('   Keep the target inactive until these checks pass. Never copy source credentials blindly.')
    print('TRANSFER: copy the completed archive and its .sha256 file over SSH to the target.')
    print('   On the target: sha256sum -c ARCHIVE.sha256')
    print('   For .gpg: gpg --output site.tar.gz --decrypt site.tar.gz.gpg')
    print('   Archive recovery does not need the original restic password. Keep an export passphrase separately.')
    print('   For a copied restic repository, transfer its destination credentials separately and test access.')


def quote_pattern(path):
    # Restic uses Go filepath patterns. Quote metacharacters to select literal paths.
    return ''.join('\\' + c if c in '*?[]\\' else c for c in path)


def restore_command(snap, domain, scope, selected, destination):
    command = ['restic', 'restore', snap['id'] + ':' + str(SITES / domain), '--target', str(destination), '--verify']
    includes = {'files': ['htdocs', 'backup/manifest.json', 'backup/files.jsonl'], 'database': ['backup/db.sql.gz', 'backup/manifest.json', 'backup/files.jsonl']}
    paths = [selected] if scope in ('file', 'directory') else includes.get(scope, [])
    for path in paths:
        command += ['--include', quote_pattern('/' + path)]
    return command


def extract(repo, snap, domain, scope, selected, task, nodes):
    destination = Path(task['path']) / 'data'
    destination.mkdir(mode=0o700)
    needed = sum(n.get('size', 0) for n in nodes if n['type'] == 'file')
    if shutil.disk_usage(destination).free < needed:
        raise ValueError('Insufficient free space for extraction')
    run(restore_command(snap, domain, scope, selected, destination), env=repo.env, timeout=7200)
    # Ensure actual files match preflight metadata without traversing links.
    for node in nodes:
        rel = str(PurePosixPath(node['path']).relative_to(SITES / domain))
        if rel == '.':
            continue
        if node['type'] == 'file':
            path = regular(destination, rel)
            if path.stat().st_size != node.get('size', 0):
                raise ValueError('Extracted size differs: ' + rel)
    print('PASS extraction and restic --verify:', destination)
    # Restored permissions may grant access. The parent workspace stays private.
    report = []
    if scope in ('complete', 'database'):
        report = verify_manifest(destination, files=scope == 'complete')
    elif scope == 'files':
        report = verify_manifest(destination, files=True, database=False)
    save_json(Path(task['path']) / 'checks.json', {'extraction': 'complete', 'restic_readback': 'passed', 'checks': report})
    for line in report:
        print(line)
    return destination


def portable(task, domain, encrypted, passfile):
    root = Path(task['path'])
    data = root / 'data'
    recovery = root / 'RECOVERY.txt'
    from io import StringIO
    stream = StringIO()
    with contextlib.redirect_stdout(stream):
        instructions(Path(domain), domain)
    recovery.write_text(stream.getvalue())
    # Build a private tar, verify each member, then publish atomically.
    partial = root / (domain + '.tar.gz.partial')
    with tarfile.open(partial, 'w:gz', dereference=False) as archive:
        archive.add(data, arcname=domain)
        archive.add(recovery, arcname='RECOVERY.txt')
    with tarfile.open(partial, 'r:gz') as archive:
        for member in archive:
            if not (member.isfile() or member.isdir()) or member.name.startswith('/') or '..' in PurePosixPath(member.name).parts:
                raise ValueError('Unsafe portable archive member')
            if member.isfile():
                # Full read checks gzip CRC and archive readability.
                with archive.extractfile(member) as source:
                    while source.read(1024 * 1024):
                        pass
    if encrypted:
        private_file(passfile)
        encrypted_partial = root / (domain + '.tar.gz.gpg.partial')
        run(['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', '--passphrase-file', str(passfile),
             '--symmetric', '--cipher-algo', 'AES256', '--output', str(encrypted_partial), str(partial)], timeout=7200)
        # Verify decrypted bytes against the checked tar without materializing another plaintext archive.
        digest = hashlib.sha256()
        with tempfile_output() as errors:
            process = subprocess.Popen(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase-file', str(passfile),
                                        '--decrypt', str(encrypted_partial)], stdout=subprocess.PIPE, stderr=errors)
            try:
                while chunk := process.stdout.read(1024 * 1024):
                    digest.update(chunk)
            finally:
                process.stdout.close()
            if process.wait() or digest.hexdigest() != sha256_file(partial):
                raise ValueError('Encrypted export verification failed')
        partial.unlink()
        partial = encrypted_partial
    final = partial.with_suffix('')
    os.replace(partial, final)
    checksum = final.with_name(final.name + '.sha256')
    checksum.write_text(sha256_file(final) + '  ' + final.name + '\n')
    print('PASS portable copy:', final)
    print('Checksum:', checksum)
    print('Encrypted with GPG; use the export passphrase, not the restic password.' if encrypted else
          'UNENCRYPTED: contains site credentials. No restic password is needed. Keep it private.')
    return final


def tempfile_output():
    import tempfile
    return tempfile.TemporaryFile()


def source_backup():
    # Load the installed standard implementation, without running its CLI main.
    path = Path(__file__).resolve().parent.parent / 'bin/wp-backup'
    loader = importlib.machinery.SourceFileLoader('_standard_wp_backup', str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def show_snapshots(repo, domain=None):
    rows, incomplete = [], []
    now = datetime.now(timezone.utc)
    for snap in sorted(repo.snapshots(), key=lambda s: s['time'], reverse=True):
        sites = repo.sites(snap)
        available = domain in sites if domain else bool(sites)
        date = datetime.fromisoformat(snap['time'].replace('Z', '+00:00'))
        row = [snap['id'][:12], date.isoformat(), f'{int((now-date).total_seconds()/3600)}h',
               snap.get('hostname', '?'), ','.join(snap.get('tags', [])),
               'complete' if 'complete' in snap.get('tags', []) else 'incomplete/unconfirmed',
               ','.join(snap.get('paths', [])), ('yes' if available else 'no') if domain else ','.join(sites)]
        (rows if 'complete' in snap.get('tags', []) else incomplete).append(row)
    headers = ['ID', 'Time (offset)', 'Age', 'Host', 'Type/tags', 'Status', 'Paths', 'Site availability']
    print('COMPLETE SNAPSHOTS')
    table(headers, rows)
    print('INCOMPLETE OR UNCONFIRMED SNAPSHOTS')
    table(headers, incomplete)


def status_commands():
    return [
        ['systemctl', 'show', 'wp-backup.service', '-p', 'ActiveState', '-p', 'Result', '-p', 'ExecMainStatus'],
        ['systemctl', 'show', 'wp-backup.timer', '-p', 'ActiveState', '-p', 'NextElapseUSecRealtime'],
        ['journalctl', '-u', 'wp-backup.service', '-p', 'warning', '-n', '10', '--no-pager'],
    ]


def status(repo):
    env = repo.env
    accessible = True
    try:
        snapshots = repo.snapshots()
    except RuntimeError:
        snapshots, accessible = [], False
    complete = [s for s in snapshots if 'complete' in s.get('tags', [])]
    print('Last successful snapshot:', max((s['time'] for s in complete), default='none'))
    print('Incomplete/unconfirmed snapshots:', len(snapshots) - len(complete))
    table(['Recent incomplete/unconfirmed ID', 'Time'], [[s['id'][:12], s['time']] for s in sorted(
        (s for s in snapshots if 'complete' not in s.get('tags', [])), key=lambda s: s['time'], reverse=True)[:10]])
    print('Repository:', env['RESTIC_REPOSITORY'], 'accessible=' + str(accessible))
    mount = env.get('BACKUP_MOUNT')
    print('Backup mount:', mount or 'not configured', 'mounted=' + str(bool(mount and os.path.ismount(mount))))
    space_path = mount or env['RESTIC_REPOSITORY']
    if space_path.startswith('/') and Path(space_path).exists():
        print('Free bytes:', shutil.disk_usage(space_path).free)
    for cmd in status_commands():
        try:
            result = run(cmd, check=False)
        except FileNotFoundError:
            print('NOT AVAILABLE:', cmd[0], '(not installed)')
            continue
        if result.returncode:
            print('NOT AVAILABLE:', cmd[0], ' '.join(cmd[1:3]), '(exit ' + str(result.returncode) + ')')
        else:
            print(safe_text(result.stdout.decode(errors='replace')))
    retention(env)
    print('Remote copy:', env.get('RESTIC_COPY_REPOSITORY', 'disabled'))
    print('Copy failures: inspect service Result and journal above; no independent historical copy marker exists.')
    if not accessible:
        raise RuntimeError('Repository is inaccessible; service status is shown above')


def retention(env):
    table(['Current value', 'Inventory key'], [
        [env.get('KEEP_DAILY', '?'), 'backup_restic_keep_daily'],
        [env.get('KEEP_WEEKLY', '?'), 'backup_restic_keep_weekly'],
        [env.get('KEEP_MONTHLY', '?'), 'backup_restic_keep_monthly'],
        [env.get('RECOVERY_KEEP_DAYS', '?'), 'backup_restic_recovery_days']])
    print('Schedule: backup_restic_on_calendar. Read the timer for its current schedule.')
    print('Edit inventories/SERVER/settings.yml on the controller, then run scripts/lemp SERVER site.')
    print('Standard retention applies to complete-tag snapshots. A keep tag does not protect them.')
    print('Destination repositories have their own retention. Copy does not apply source retention there.')


def preview(commands, notes=(), *, show_secrets=False):
    # Passwords are never included by generated commands. Credential files remain references.
    # This general guard also protects future commands which supply a password argument.
    for command in commands:
        command = list(map(str, command))
        secret_indexes = [i for i, arg in enumerate(command) if
                          (i > 0 and command[i - 1] in ('--password', '--passphrase', '-p')) or
                          re.match(r'(?i)^--(?:password|passphrase)=', arg)]
        disclose = show_secrets
        if secret_indexes and not disclose and sys.stdin.isatty():
            disclose = input('Print this command with the password visible? [y/N] ').strip().lower() == 'y'
        if secret_indexes and not disclose:
            for i in secret_indexes:
                command[i] = command[i].split('=', 1)[0] + '=<MASKED>' if '=' in command[i] else '<MASKED>'
        print(shlex.join(command))
    for note in notes:
        print('# ' + note)


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples (run as root):
  wp-backup-manager
  wp-backup-manager list --site example.com
  wp-backup-manager extract --site example.com --snapshot ID --scope database
  wp-backup-manager export --site example.com --snapshot ID --class kept --yes
  wp-backup-manager verify --site example.com --snapshot ID --scratch-db
  wp-backup-manager copy --snapshot ID --destination /mnt/copy --destination-password-file /root/copy.pass
  wp-backup-manager cleanup --state failed --yes
  wp-backup-manager cleanup --task wm-ID --delete-kept --yes
  wp-backup-manager extract --site example.com --snapshot ID --scope file --path htdocs/robots.txt --mode show
Exit codes: 0 success; 2 operational/usage failure; 3 cancellation.
Recovery extracts data only. Live imports and activation are manual.''')
    p.add_argument('action', nargs='?', choices=ACTIONS)
    p.add_argument('--site')
    p.add_argument('--all', action='store_true', help='All sites (create), snapshots (verify), or eligible tasks (cleanup)')
    p.add_argument('--snapshot', action='append', help='Snapshot ID; repeat for copy or deletion')
    p.add_argument('--scope', choices=SCOPES)
    p.add_argument('--path', help='Literal path relative to the site root, e.g. htdocs/robots.txt')
    p.add_argument('--mode', choices=('run', 'show', 'show-run'), help='Use saved preference if omitted')
    p.add_argument('--default-mode', choices=('run', 'show', 'show-run'), help='New default for the preferences action')
    p.add_argument('--yes', action='store_true', help='Approve operation; does not approve secret disclosure')
    p.add_argument('--show-secrets', action='store_true', help='Approve password display for this command only')
    p.add_argument('--config', default='/etc/restic/env', help='Private root-owned restic configuration')
    p.add_argument('--class', dest='archive_class', choices=('temporary', 'kept'))
    p.add_argument('--encrypt', action='store_true', default=None, help='GPG symmetric export; requires --export-password-file')
    p.add_argument('--unencrypted', dest='encrypt', action='store_false', help='Create a plaintext archive with site credentials')
    p.add_argument('--export-password-file')
    p.add_argument('--destination-config', help='Separate private destination environment, including backend credentials')
    p.add_argument('--destination')
    p.add_argument('--destination-password-file')
    p.add_argument('--init-destination', action='store_true', help='Initialize a NEW destination repository')
    p.add_argument('--scratch-db', action='store_true', help='Also import into an isolated MariaDB scratch database')
    p.add_argument('--task', action='append', help='Exact managed task ID; repeat to select several')
    p.add_argument('--state', choices=('complete', 'failed', 'interrupted'))
    p.add_argument('--older-than', type=int, help='Minimum age in days for cleanup')
    p.add_argument('--delete-kept', action='store_true', help='Explicit archive deletion; also requires --task')
    p.add_argument('--full', action='store_true', help='Show full task selections or file lists')
    return p


def manager_command(args, snap=None, snaps=None, tasks=None):
    if args.action == 'cleanup' and not tasks:
        return ['wp-backup-manager', 'workspaces', '--config', args.config, '--mode', 'run']
    command = ['wp-backup-manager', args.action, '--config', args.config, '--mode', 'run']
    for field, option in [('site', '--site'), ('scope', '--scope'), ('path', '--path'),
                          ('archive_class', '--class'), ('export_password_file', '--export-password-file'),
                          ('destination', '--destination'), ('destination_password_file', '--destination-password-file'),
                          ('destination_config', '--destination-config'), ('default_mode', '--default-mode')]:
        if getattr(args, field, None) is not None:
            command += [option, str(getattr(args, field))]
    ids = [snap['id']] if snap else [s['id'] for s in snaps or []] or args.snapshot or []
    for sid in ids:
        command += ['--snapshot', sid]
    for field, option in [('all', '--all'), ('scratch_db', '--scratch-db'), ('full', '--full'),
                          ('delete_kept', '--delete-kept'), ('init_destination', '--init-destination')]:
        if getattr(args, field, False) and not (field == 'all' and args.action == 'cleanup'):
            command.append(option)
    if args.encrypt is not None:
        command.append('--encrypt' if args.encrypt else '--unencrypted')
    if args.action == 'cleanup':
        for task in tasks or []:
            command += ['--task', task['id']]
    elif args.task:
        for task in args.task:
            command += ['--task', task]
    if args.yes:
        command.append('--yes')
    return command


def resolve_selection(args, repo):
    if not args.site:
        print('Available backup sites:')
        names = sorted({name for snap in repo.snapshots() for name in repo.sites(snap)})
        print(', '.join(names) or '(none)')
        args.site = ask('Site')
    if not DOMAIN.fullmatch(args.site):
        raise ValueError('Invalid site domain')
    if not args.snapshot:
        show_snapshots(repo, args.site)
        args.snapshot = [ask('Snapshot ID')]
    if len(args.snapshot) != 1:
        raise ValueError('This action needs exactly one snapshot')
    return repo.select(args.site, args.snapshot[0])


def cleanup_selection(args, tasks):
    values = tasks.show(full=args.full)
    if args.delete_kept and not args.task:
        raise ValueError('--delete-kept requires exact --task selection')
    if args.older_than is not None and args.older_than < 0:
        raise ValueError('Age must not be negative')
    if not (args.task or args.state or args.all or args.older_than is not None):
        choice = ask('Cleanup selection', ['task', 'completed', 'failed', 'interrupted', 'age', 'all', 'kept'])
        if choice in ('task', 'kept'):
            args.task = ask('Task IDs, separated by commas').split(',')
            args.delete_kept = choice == 'kept'
        elif choice == 'age':
            args.older_than = int(ask('Minimum age in days'))
            if args.older_than < 0:
                raise ValueError('Age must not be negative')
        elif choice == 'all':
            args.all = True
        else:
            args.state = 'complete' if choice == 'completed' else choice
    result = []
    for task in values:
        if task['id'] == 'unregistered' or task['state'] == 'active':
            continue
        if args.task and task['id'] not in args.task:
            continue
        if task['kept'] and not args.delete_kept:
            continue
        if args.state and task['state'] != args.state:
            continue
        if args.older_than is not None:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(task['created'])
            if age.total_seconds() < args.older_than * 86400:
                continue
        result.append(task)
    if args.task and set(args.task) != {t['id'] for t in result}:
        raise ValueError('A selected task is absent, active, protected, or does not match the filters')
    print('Selected paths to delete:')
    for task in result:
        print(task['path'], task['id'], task['state'], task['purpose'], task['allocated'], 'bytes')
    print('Estimated bytes to release:', sum(t['allocated'] for t in result))
    return result


def copy_snapshots(repo, args, snaps):
    # No inherited source repository/password variables can override destination options.
    env = args.destination_environment
    source_local = ':' not in repo.env['RESTIC_REPOSITORY'] or repo.env['RESTIC_REPOSITORY'].startswith('local:')
    destination_local = ':' not in args.destination or args.destination.startswith('local:')
    if not source_local and destination_local:
        env = {**repo.env, 'RESTIC_REPOSITORY': args.destination, 'RESTIC_PASSWORD_FILE': args.destination_password_file}
    if args.init_destination:
        run(['restic', 'init'], env=env)
    run(['restic', 'copy', '--from-repo', repo.env['RESTIC_REPOSITORY'],
         '--from-password-file', repo.env['RESTIC_PASSWORD_FILE'], *[s['id'] for s in snaps]], env=env, timeout=7200)
    destination = Repository(env)
    matches = destination.snapshots()
    for source in snaps:
        candidates = [s for s in matches if s.get('original', s['id']) == source.get('original', source['id']) or s['id'] == source['id']]
        if len(candidates) != 1:
            raise ValueError('Cannot identify copied snapshot at destination')
        copied = candidates[0]
        # Listing tests tree access; check reads all destination pack data for full readability.
        destination.nodes(copied)
        print('Destination snapshot:', copied['id'])
    destination.call('check', '--read-data')
    print('PASS destination readability. Source snapshots remain unchanged.')
    print('Destination retention is independent. No automatic destination retention was applied.')


def compare_files(repo, snap, domain, task, nodes):
    root = extract(repo, snap, domain, 'files', None, task, nodes)
    live = SITES / domain
    if not live.exists():
        raise ValueError('Current site does not exist; extracted data remains available')
    # Never read through live symlinks and never execute Git hooks or restored programs.
    rows = []
    for node in nodes:
        if node['type'] != 'file':
            continue
        rel = str(PurePosixPath(node['path']).relative_to(SITES / domain))
        if not rel.startswith('htdocs/'):
            continue
        restored = regular(root, rel)
        try:
            current = regular(live, rel)
            state = 'same' if sha256_file(restored) == sha256_file(current) else 'changed'
        except ValueError:
            state = 'missing or unsafe live path'
        rows.append([rel, state])
    table(['Backup file', 'Current site'], rows)
    print('Comparison covers backed-up files only. Live-only files are not listed.')


def dispatch(args, env):
    repo, tasks = Repository(env), Tasks(env)
    mode = args.mode or preference()
    approved = args.yes
    commands, notes = [], []
    action = args.action
    task_id = 'wm-' + uuid.uuid4().hex
    task_path = (ARCHIVES if action == 'export' else RESTORE) / task_id
    snaps, snap, nodes, chosen, jobs = [], None, [], [], []
    if action == 'create':
        if not args.site and not args.all:
            scope = ask('Backup scope', ['site', 'all'])
            args.all = scope == 'all'
            if not args.all:
                args.site = ask('Site')
        if args.site and args.all:
            raise ValueError('Choose --site or --all')
        if args.site and not DOMAIN.fullmatch(args.site):
            raise ValueError('Invalid site')
        commands = []
        notes = ['Call the existing wp-backup backup function with the selected configuration.',
                 'Backup paths: ' + (str(SITES / args.site) if args.site else env.get('BACKUP_PATHS', 'not configured')),
                 'Use the standard dump, manifest, snapshot, verification and configured copy workflow.',
                 'The shared operation lock protects this entire operation. No success is reported on failure.']
    elif action in ('extract', 'export', 'verify', 'browse', 'compare'):
        if action == 'verify' and args.all:
            snaps = [s for s in repo.snapshots() if 'complete' in s.get('tags', [])]
            if not snaps:
                raise ValueError('No complete snapshots')
            commands = [['restic', 'check', '--read-data']]
            for selected_snap in snaps:
                for domain in repo.sites(selected_snap):
                    if args.site and domain != args.site:
                        continue
                    job_id = 'wm-' + uuid.uuid4().hex
                    jobs.append((selected_snap, domain, job_id))
                    commands.append(restore_command(selected_snap, domain, 'complete', None, RESTORE / job_id / 'data'))
            if not jobs:
                raise ValueError('No backed-up sites match this selection')
            notes = ['Extract and verify every listed site from each complete snapshot. Each has a separate task.',
                     'Scratch SQL import: ' + ('enabled' if args.scratch_db else 'NOT performed')]
        else:
            snap = resolve_selection(args, repo)
            if action in ('extract', 'export', 'verify', 'compare'):
                if 'complete' not in snap.get('tags', []):
                    if mode == 'show':
                        print('WARNING: snapshot is incomplete/unconfirmed.')
                    else:
                        confirm('Snapshot is incomplete/unconfirmed. Extract or inspect it anyway?', args.yes)
                if action == 'extract':
                    args.scope = args.scope or ask('Scope', list(SCOPES), 'complete')
                    if args.scope in ('file', 'directory'):
                        args.path = args.path or ask('Path relative to the site root')
                else:
                    args.scope = 'files' if action == 'compare' else 'complete'
                nodes = repo.selected_nodes(snap, args.site, args.scope, args.path)
                size = sum(n.get('size', 0) for n in nodes if n['type'] == 'file')
                print('Snapshot:', snap['id'], 'Site:', args.site, 'Scope:', args.scope)
                print('Destination:', task_path / 'data', 'Estimated data bytes:', size,
                      '(plus filesystem overhead; exports also need archive space)')
                commands = [restore_command(snap, args.site, args.scope, args.path, task_path / 'data')]
                notes = ['Preflight checks reject traversal, symlinks and special files.',
                         'Internal checks compare file availability and SQL hashes when metadata exists.']
                if action == 'verify':
                    commands.insert(0, ['restic', 'check', '--read-data'])
                    notes.append('Scratch import: ' + ('enabled, temporary restricted MariaDB user' if args.scratch_db else 'NOT performed'))
                if action == 'export':
                    password_paths = {str(Path(value).resolve()) for key, value in env.items() if key.startswith('RESTIC_') and key.endswith('PASSWORD_FILE')}
                    if any(n['path'] in password_paths for n in nodes):
                        raise ValueError('Selected site contains a configured repository password file; move repository credentials outside the site')
                    args.archive_class = args.archive_class or ask('Archive class', ['temporary', 'kept'])
                    if args.encrypt is None:
                        args.encrypt = ask('Export protection', ['encrypted', 'plain'], 'encrypted') == 'encrypted' if sys.stdin.isatty() else False
                    if args.encrypt:
                        args.export_password_file = args.export_password_file or ask('Private export password file')
                        private_file(args.export_password_file)
                    notes += ['Create a verified tar.gz via a .partial file; publish it atomically and write SHA256.',
                              'Encryption: ' + ('GPG AES256 with separate export passphrase.' if args.encrypt else 'NONE. Archive includes site secrets.'),
                              'Archive class: ' + args.archive_class + '. No restic password is included.']
                    notes.append('Internal: tarfile packs data as ' + args.site + '/ and adds RECOVERY.txt.')
                    notes.append('Internal: read every tar member; reject links and special files; check gzip CRC.')
                    notes.append('Archive staging: ' + str(task_path / (args.site + '.tar.gz.partial')))
                    if args.encrypt:
                        commands += [['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', '--passphrase-file', args.export_password_file,
                                      '--symmetric', '--cipher-algo', 'AES256', '--output',
                                      str(task_path / (args.site + '.tar.gz.gpg.partial')),
                                      str(task_path / (args.site + '.tar.gz.partial'))],
                                     ['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase-file', args.export_password_file,
                                      '--decrypt', str(task_path / (args.site + '.tar.gz.gpg.partial'))]]
                        notes.append('Internal: hash decrypted bytes; compare with the checked tar; remove plaintext tar.')
                    suffix = '.tar.gz.gpg' if args.encrypt else '.tar.gz'
                    notes.append('Internal: atomic rename to ' + str(task_path / (args.site + suffix)) + '; write .sha256.')
            else:
                commands = [['restic', 'ls', '--json', snap['id']]]
    elif action == 'copy':
        if not args.snapshot:
            show_snapshots(repo, args.site)
            args.snapshot = [ask('Snapshot ID')]
        all_snaps = repo.snapshots()
        for selector in args.snapshot:
            found = [s for s in all_snaps if s['id'].startswith(selector)]
            if len(found) != 1:
                raise ValueError('Snapshot ID is missing or ambiguous')
            snaps.extend(found)
        if args.destination_config:
            args.destination_environment = environment(args.destination_config)
            args.destination = args.destination or args.destination_environment['RESTIC_REPOSITORY']
            args.destination_password_file = args.destination_password_file or args.destination_environment['RESTIC_PASSWORD_FILE']
        else:
            args.destination_environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR')}
        args.destination = args.destination or ask('Destination repository')
        args.destination_password_file = args.destination_password_file or ask('Destination password file')
        if re.search(r'://[^/@]*:[^/@]*@', args.destination):
            raise ValueError('Use a private destination configuration for backend credentials')
        validate_destination(args.destination, env)
        args.destination_environment.update(RESTIC_REPOSITORY=args.destination, RESTIC_PASSWORD_FILE=args.destination_password_file)
        private_file(args.destination_password_file)
        if args.destination == env['RESTIC_REPOSITORY'] or Path(args.destination_password_file).resolve() == Path(env['RESTIC_PASSWORD_FILE']).resolve():
            raise ValueError('Use a separate destination repository and credentials')
        source_remote = ':' in env['RESTIC_REPOSITORY'] and not env['RESTIC_REPOSITORY'].startswith('local:')
        destination_remote = ':' in args.destination and not args.destination.startswith('local:')
        if source_remote and destination_remote:
            backend_prefixes = ('AWS_', 'AZURE_', 'B2_', 'GOOGLE_', 'OS_', 'RCLONE_')
            source_auth = {k: v for k, v in env.items() if k.startswith(backend_prefixes)}
            destination_auth = {k: v for k, v in args.destination_environment.items() if k.startswith(backend_prefixes)}
            if source_auth != destination_auth:
                raise ValueError('Different remote backend credentials need an intermediate local repository; copy source to local, then local to destination')
        if args.init_destination:
            commands.append(['restic', '--repo', args.destination, '--password-file', args.destination_password_file, 'init'])
        commands += [['restic', '--repo', args.destination, '--password-file', args.destination_password_file, 'copy',
                      '--from-repo', env['RESTIC_REPOSITORY'], '--from-password-file', env['RESTIC_PASSWORD_FILE'], *[s['id'] for s in snaps]],
                     ['restic', '--repo', args.destination, '--password-file', args.destination_password_file, 'check', '--read-data']]
        notes = ['Load separate destination credentials from ' + (args.destination_config or args.destination_password_file) + '.',
                 'Identify destination IDs by original snapshot ID; list each tree. Do not remove source snapshots.',
                 'Destination retention is independent. Source retention is not applied.']
    elif action == 'cleanup':
        chosen = cleanup_selection(args, tasks)
        notes = ['Acquire the shared operation lock; recheck ownership and active process identity.',
                 'Safely remove only these registered task directories: ' + ', '.join(t['path'] for t in chosen)]
    elif action == 'delete-snapshots':
        if not args.snapshot:
            show_snapshots(repo, args.site)
            args.snapshot = [ask('Snapshot ID to delete')]
        available = repo.snapshots()
        for selector in args.snapshot:
            found = [s for s in available if s['id'].startswith(selector)]
            if len(found) != 1:
                raise ValueError('Snapshot ID is missing or ambiguous')
            snaps.extend(found)
        table(['Delete snapshot', 'Date'], [[s['id'], s['time']] for s in snaps])
        commands = [['restic', 'forget', '--dry-run', *[s['id'] for s in snaps]],
                    ['restic', 'forget', *[s['id'] for s in snaps]]]
        notes = ['No prune is performed. This deletes snapshot records, not temporary workspaces.']
    elif action == 'list':
        commands = [['restic', 'snapshots', '--json']]
        notes = ['List snapshot trees to find sites. Tags alone do not establish site availability.']
    elif action in ('status', 'retention'):
        commands = [['restic', 'snapshots', '--json'], *status_commands()] if action == 'status' else []
        notes = ['Read configuration and report status. Do not edit Ansible-managed settings.']
    elif action == 'workspaces':
        notes = ['Read registered task metadata and allocated blocks. No data is deleted.']
    elif action == 'instructions':
        notes = ['Print recovery instructions only. Do not apply recovered data.']
    elif action == 'preferences':
        args.default_mode = args.default_mode or ask('Default command display', ['run', 'show', 'show-run'])
        notes = ['Atomically save display preference in ' + str(PREFERENCES)]
    if action in ('create', 'extract', 'export', 'verify', 'copy', 'compare'):
        notes.append('Internal task metadata and log directory: ' + str(task_path))
    if mode in ('show', 'show-run'):
        # Prefix restic previews with actual source configuration; execution never uses a shell.
        commands = [[*c[:1], '--repo', env['RESTIC_REPOSITORY'], '--password-file', env['RESTIC_PASSWORD_FILE'], *c[1:]]
                    if c and c[0] == 'restic' and '--repo' not in c else c for c in commands]
        print('# Equivalent manager command (includes validation and locking):')
        preview([manager_command(args, snap, snaps, chosen)], show_secrets=args.show_secrets)
        print('# Internal steps; do not run these separately:')
        preview(commands, notes, show_secrets=args.show_secrets)
        if mode == 'show':
            return
        if action not in ('list', 'browse', 'status', 'retention', 'instructions', 'workspaces', 'delete-snapshots'):
            confirm('Run the displayed action?', args.yes)
            approved = True
    if action == 'list':
        show_snapshots(repo, args.site)
    elif action == 'browse':
        base = str(SITES / args.site)
        table(['Path', 'Type', 'Bytes'], [[n['path'], n['type'], n.get('size', '')] for n in repo.nodes(snap)
              if n['path'] == base or n['path'].startswith(base + '/')])
    elif action == 'workspaces':
        tasks.show(full=args.full)
    elif action == 'instructions':
        instructions(domain=args.site or 'SITE')
    elif action == 'retention':
        retention(env)
    elif action == 'status':
        status(repo)
    elif action == 'preferences':
        private_directory(PREFERENCES.parent)
        save_json(PREFERENCES, {'mode': args.default_mode})
        print('Saved command display:', args.default_mode)
    else:
        if action == 'delete-snapshots':
            result = repo.call('forget', '--dry-run', *[s['id'] for s in snaps])
            print(safe_text(result.stdout.decode(errors='replace')))
        if action in ('cleanup', 'delete-snapshots', 'export', 'copy') and not approved:
            confirm('Proceed with ' + action + '?', args.yes)
        # Lock only execution, never menu browsing or operator confirmation.
        with operation_lock(timeout=1):
            if action == 'create':
                standard = source_backup()
                if env.get('BACKUP_REQUIRE_MOUNT', 'true') == 'true' and not os.path.ismount(env['BACKUP_MOUNT']):
                    raise ValueError('Backup disk is not mounted')
                sites = [standard.site(args.site)] if args.site else [standard.site(p.name) for p in SITES.iterdir() if (p / 'conf/site.env').exists()]
                if not sites:
                    raise ValueError('No configured sites')
                print('Creating backup:', args.site or 'all configured sites', flush=True)
                with task_run(tasks, 'verification', args.site or '*', task_id=task_id) as task:
                    before = {s['id'] for s in repo.snapshots()}
                    standard.backup(sites, env, standard.Database(), full=not bool(args.site))
                    after = [s for s in repo.snapshots() if s['id'] not in before and 'complete' in s.get('tags', [])]
                    if len(after) != 1:
                        raise RuntimeError('Cannot identify one new completed snapshot')
                    task['snapshot'] = after[0]['id']
                    print('Complete snapshot ID:', after[0]['id'])
            elif action == 'cleanup':
                for task in chosen:
                    tasks.remove(task, args.delete_kept)
                    print('Removed:', task['id'])
            elif action == 'delete-snapshots':
                repo.call('forget', *[s['id'] for s in snaps])
                print('Deleted snapshot records. No prune was run.')
            elif action == 'copy':
                with task_run(tasks, 'transfer', snapshot=','.join(s['id'] for s in snaps),
                              selected=[args.destination], task_id=task_id):
                    copy_snapshots(repo, args, snaps)
            elif action == 'verify' and args.all:
                with task_run(tasks, 'verification', args.site or '*', snapshot=','.join(s['id'] for s in snaps),
                              selected=['repository --read-data'], task_id=task_id):
                    repo.call('check', '--read-data')
                    print('PASS repository accessibility and full integrity check')
                    failures = []
                    for selected_snap, domain, job_id in jobs:
                        try:
                            verify_one(repo, tasks, selected_snap, domain, args.scratch_db,
                                       task_id=job_id, check_repository=False)
                        except (ValueError, RuntimeError, OSError):
                            failures.append(selected_snap['id'] + ':' + domain)
                    if failures:
                        raise ValueError('Verification failed: ' + ', '.join(failures))
            elif action == 'verify':
                verify_one(repo, tasks, snap, args.site, args.scratch_db, task_id=task_id)
            elif action in ('extract', 'export', 'compare'):
                purpose = 'export' if action == 'export' else 'extraction'
                with task_run(tasks, purpose, args.site, snap['id'], args.archive_class == 'kept',
                              [n['path'] for n in nodes], task_id=task_id) as task:
                    if action == 'compare':
                        compare_files(repo, snap, args.site, task, nodes)
                    else:
                        result = extract(repo, snap, args.site, args.scope, args.path, task, nodes)
                        if action == 'export':
                            portable(task, args.site, args.encrypt, args.export_password_file)
                        instructions(result, args.site)


def verify_one(repo, tasks, snap, domain, scratch, task_id=None, check_repository=True):
    nodes = repo.selected_nodes(snap, domain, 'complete')
    with task_run(tasks, 'verification', domain, snap['id'], selected=[n['path'] for n in nodes], task_id=task_id) as task:
        if check_repository:
            repo.call('check', '--read-data')
            print('PASS repository accessibility and full integrity check')
        destination = extract(repo, snap, domain, 'complete', None, task, nodes)
        report = verify_manifest(destination, scratch=scratch)
        save_json(Path(task['path']) / 'checks.json', {'repository_read_data': 'passed', 'checks': report})
        for line in report:
            print(line)
        if any('manifest: missing' in line or 'file availability: no file list' in line for line in report):
            raise ValueError('Verification metadata is missing; task remains available for review')
        print('Checks not performed: application activation, external services and live recovery.')


def preference():
    if PREFERENCES.exists():
        value = json.loads(private_file(PREFERENCES).read_text()).get('mode')
        if value not in ('run', 'show', 'show-run'):
            raise ValueError('Invalid command display preference')
        return value
    return 'show-run'


def tui(args, env):
    tasks = Tasks(env)
    print('RECOVERY AND EXPORT STORAGE AT STARTUP')
    tasks.show()
    labels = ['List backups', 'Create a backup', 'Extract data', 'Create a portable copy',
              'Copy snapshots to another repository', 'Verify a backup', 'Browse contents',
              'Compare with current site', 'Backup system status', 'Retention and snapshot deletion',
              'Recovery and transfer instructions', 'List and clean temporary files', 'Command display preferences', 'Exit']
    try:
        while True:
            for i, label in enumerate(labels, 1):
                print(f'{i:2}. {label}')
            selection = ask('Task', [str(i) for i in range(1, 15)])
            if selection == '14':
                return
            selected = parser().parse_args([])
            selected.config = args.config
            selected.action = ACTIONS[int(selection) - 1]
            try:
                if selected.action == 'retention':
                    option = ask('Action', ['review', 'delete'], 'review')
                    if option == 'delete':
                        selected.action = 'delete-snapshots'
                if selected.action == 'copy':
                    selected.init_destination = ask('Destination state', ['existing', 'new'], 'existing') == 'new'
                if selected.action == 'verify':
                    selected.all = ask('Verification scope', ['selected', 'all'], 'selected') == 'all'
                    selected.scratch_db = ask('Scratch database import', ['yes', 'no'], 'no') == 'yes'
                if selected.action == 'workspaces':
                    option = ask('Action', ['list', 'list-full', 'cleanup'], 'list')
                    selected.full = option == 'list-full'
                    if option == 'cleanup':
                        selected.action = 'cleanup'
                selected.mode = ask('Command display', ['run', 'show', 'show-run'], preference())
                dispatch(selected, env)
            except Cancelled:
                print('Cancelled.')
            except (ValueError, RuntimeError, OSError) as exc:
                print('FAIL:', safe_text(exc), file=sys.stderr)
    finally:
        print('RECOVERY AND EXPORT STORAGE AT EXIT')
        tasks.show()


def main(argv=None):
    args = parser().parse_args(argv)
    if os.geteuid() != 0:
        raise ValueError('Run as root')
    os.umask(0o077)
    env = environment(args.config)
    if args.action:
        dispatch(args, env)
    else:
        if not sys.stdin.isatty():
            raise ValueError('No terminal. Supply an action; use --help for examples')
        tui(args, env)
    return 0


def entrypoint():
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        return main()
    except (Cancelled, KeyboardInterrupt, EOFError):
        print('Cancelled.', file=sys.stderr)
        return 3
    except Exception as exc:
        # Subprocess diagnostics contain no command output. Paths are sanitized for terminals.
        print('FAIL wp-backup-manager:', safe_text(exc), file=sys.stderr)
        return 2
