"""Read and verify extracted backup data. Never apply it to a live site."""
import gzip
import json
from pathlib import Path, PurePosixPath
import stat

from wp_hosting import Database, load, require_wordpress_tables, sha256_file


def relative_path(value):
    if not isinstance(value, str) or not value or '\x00' in value or '\\' in value:
        raise ValueError('Invalid relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Path must be relative, without dot components')
    return path


def regular(root, relative):
    path = Path(root)
    for part in relative_path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError('Symlinks are not verification inputs')
    if not path.is_file() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Required file is missing: ' + relative)
    return path


def verify_manifest(root, *, files=True, database=True, scratch=False, db=None):
    """Return distinct check results; missing metadata is not successful verification."""
    root = Path(root)
    report = []
    manifest_path = root / 'backup/manifest.json'
    if not manifest_path.exists():
        return ['NOT CHECKED manifest: missing; integrity is unverified']
    manifest = json.loads(regular(root, 'backup/manifest.json').read_text())
    if not isinstance(manifest, dict):
        raise ValueError('Invalid backup manifest')
    if files:
        if 'files_sha256' in manifest:
            path = regular(root, 'backup/files.jsonl')
            if sha256_file(path) != manifest['files_sha256']:
                raise ValueError('File manifest hash differs')
            entries = [json.loads(line) for line in path.read_text().splitlines()]
        elif 'files' in manifest:
            entries = manifest['files']
        else:
            entries = None
        if entries is None:
            report.append('NOT CHECKED file availability: no file list')
        else:
            for name in entries:
                regular(root, 'htdocs/' + str(relative_path(name)))
            report.append(f'PASS file availability: {len(entries)} files; legacy manifest has no content hashes')
    if database:
        path = regular(root, 'backup/db.sql.gz')
        if not manifest.get('sha256'):
            raise ValueError('SQL manifest hash is missing')
        if sha256_file(path) != manifest['sha256']:
            raise ValueError('SQL dump hash differs')
        tail = b''
        with gzip.open(path, 'rb') as stream:
            while data := stream.read(1024 * 1024):
                tail = (tail + data)[-4096:]
        if b'-- Dump completed' not in tail:
            raise ValueError('SQL dump has no completion marker')
        report.append('PASS SQL dump hash and gzip integrity')
        if scratch:
            db = db or Database()
            with db.scratch() as (name, password):
                load(path, name, name, password)
                tables = db.tables(name)
                if tables != manifest.get('tables'):
                    raise ValueError('Scratch database table list differs')
                if manifest.get('installed'):
                    require_wordpress_tables(tables, manifest['prefix'])
            report.append('PASS scratch import and manifest table list')
            report.append('PASS required WordPress table checks' if manifest.get('installed') else
                          'NOT CHECKED WordPress tables: manifest marks site not installed')
        else:
            report.append('NOT CHECKED scratch import and WordPress tables; use verify --scratch-db')
    return report
