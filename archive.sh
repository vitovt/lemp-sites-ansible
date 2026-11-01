#!/usr/bin/env bash
# Make a portable, encrypted copy of the controller's private project data.
set -euo pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: ./archive.sh [--password PASSWORD] [--delete-after-archive]

Create lemp-sites-ansible-YYYYMMDD-HHMMSS.zip in this repository.
The ZIP uses AES-256. Extract it with 7z x ARCHIVE.zip from the root of a
fresh clone. The common unzip command cannot read this encrypted ZIP format.
The public inventories/example-server/ template is omitted.

Without --password, the script asks for a password twice. A password given on
the command line can remain in shell history and process listings.

--delete-after-archive  After archive verification, run clean.sh with its
                        confirmed inventory deletion mode. Also remove the
                        archived root secrets and repository-local SSH keys.
                        SSH keys outside this repository are kept.
EOF
}

password_set=false
delete_after=false
while (($#)); do
    case "$1" in
        --password)
            if (($# < 2)); then printf '%s\n' '--password needs a value.' >&2; exit 2; fi
            password=$2
            password_set=true
            shift 2
            ;;
        --password=*) password=${1#--password=}; password_set=true; shift ;;
        --delete-after-archive) delete_after=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

repo_root=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
if ! command -v 7z >/dev/null 2>&1; then
    printf '%s\n' 'Install the 7zip package (7z command) before archiving.' >&2
    exit 1
fi
if [[ -x "$repo_root/.venv/bin/python3" ]]; then
    python="$repo_root/.venv/bin/python3"
else
    python=$(command -v python3 || true)
fi
if [[ -z "$python" ]] || ! "$python" -c 'import ruamel.yaml' 2>/dev/null; then
    printf '%s\n' 'Install the controller Python dependencies from requirements-dev.txt first.' >&2
    exit 1
fi

if [[ "$password_set" != true ]]; then
    printf 'Archive password: ' >&2
    IFS= read -r -s password || exit 2
    printf '\nConfirm password: ' >&2
    IFS= read -r -s confirmation || exit 2
    printf '\n' >&2
    if [[ "$password" != "$confirmation" ]]; then
        printf '%s\n' 'Passwords do not match.' >&2
        exit 2
    fi
    unset confirmation
fi
if [[ -z "$password" || "$password" == *$'\n'* || "$password" == *$'\r'* ]]; then
    printf '%s\n' 'Use a nonempty, single-line password.' >&2
    exit 2
fi

archive_name="lemp-sites-ansible-$(date +%Y%m%d-%H%M%S).zip"
archive="$repo_root/$archive_name"
partial="$repo_root/.$archive_name.partial"
if [[ -e "$archive" || -e "$partial" || -L "$archive" || -L "$partial" ]]; then
    printf 'Archive name already exists: %s\n' "$archive_name" >&2
    exit 1
fi
if [[ -d /dev/shm && -w /dev/shm ]]; then
    stage_base=/dev/shm
else
    stage_base=/tmp
fi
stage=$(mktemp -d "$stage_base/lemp-archive.XXXXXXXX")
cleanup() {
    rm -rf -- "$stage"
    rm -f -- "$partial"
}
trap cleanup EXIT

# Stage only project-specific user data. 7z performs all ZIP creation and
# verification. The Python step only copies files and makes SSH key paths
# portable in the staged inventory; it never changes the working inventory.
"$python" - "$repo_root" "$stage" <<'PY'
import os
from pathlib import Path
import shutil
import stat
import sys

from ruamel.yaml import YAML

root = Path(sys.argv[1])
stage = Path(sys.argv[2])
source = root / 'inventories'
if not source.is_dir() or source.is_symlink():
    raise SystemExit('Require a regular inventories directory.')

def ignore_example(current, names):
    return {'example-server'} if Path(current) == source else set()

for directory in (source, root / 'secrets'):
    if not directory.exists():
        continue
    if not directory.is_dir() or directory.is_symlink():
        raise SystemExit(f'Refuse a linked or non-directory source: {directory}')
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise SystemExit(f'Refuse a symlink in archive input: {path}')
    shutil.copytree(
        directory, stage / directory.name,
        copy_function=shutil.copy2,
        ignore=ignore_example if directory == source else None,
    )

yaml = YAML(typ='rt')
yaml.preserve_quotes = True
extra_keys = set()
for server_dir in sorted(source.iterdir()):
    if server_dir.name == 'example-server':
        continue
    settings = server_dir / 'settings.yml'
    if not server_dir.is_dir() or not settings.is_file():
        continue
    try:
        document = yaml.load(settings)
        hosts = document['all']['children']['wp']['hosts']
        config = hosts[server_dir.name]
    except (KeyError, TypeError) as exc:
        raise SystemExit(f'Invalid server inventory: {settings}') from exc
    key_value = config.get('ansible_ssh_private_key_file')
    if not key_value:
        continue  # An SSH agent or default identity is intentionally not archived.
    key = Path(os.path.expandvars(str(key_value))).expanduser()
    if not key.is_absolute():
        key = root / key
    if key.is_symlink() or not key.is_file():
        raise SystemExit(f'Configured SSH key is missing or linked: {server_dir.name}')
    key = key.resolve(strict=True)
    if stat.S_IMODE(key.stat().st_mode) != 0o600:
        raise SystemExit(f'Configured SSH key must have mode 0600: {server_dir.name}')
    try:
        relative_key = key.relative_to(server_dir.resolve())
    except ValueError:
        relative_key = Path('secrets/ssh/controller.key')
        destination = stage / 'inventories' / server_dir.name / relative_key
        if destination.exists():
            raise SystemExit(f'Archive key target already exists: {server_dir.name}')
        destination.parent.mkdir(parents=True, mode=0o700)
        shutil.copy2(key, destination)
        if key.is_relative_to(root):
            extra_keys.add(key.relative_to(root).as_posix())
    config['ansible_ssh_private_key_file'] = (
        Path('inventories') / server_dir.name / relative_key
    ).as_posix()
    with (stage / 'inventories' / server_dir.name / 'settings.yml').open('w') as output:
        yaml.dump(document, output)

(stage / '.cleanup-keys').write_bytes(
    b''.join(value.encode() + b'\0' for value in sorted(extra_keys))
)
PY

inputs=(inventories)
if [[ -d "$stage/secrets" ]]; then inputs+=(secrets); fi
(
    cd "$stage"
    printf '%s\n' "$password" | 7z a -tzip -mem=AES256 -mx=5 -sse -bd -p \
        "$partial" "${inputs[@]}"
)
printf '%s\n' "$password" | 7z t -bd "$partial"

# A hard link in the same directory publishes the verified archive without
# overwriting another run that chose the same timestamp.
ln -- "$partial" "$archive"
rm -f -- "$partial"
printf 'Created and verified: %s\n' "$archive"

if [[ "$delete_after" == true ]]; then
    printf '%s\n' 'The archive is verified. The next confirmation deletes private inventories.' >&2
    "$repo_root/clean.sh" --clear-inventories
    if [[ -d "$repo_root/secrets" && ! -L "$repo_root/secrets" ]]; then
        rm -rf -- "$repo_root/secrets"
    fi
    while IFS= read -r -d '' relative_key; do
        rm -f -- "$repo_root/$relative_key"
    done < "$stage/.cleanup-keys"
    printf '%s\n' 'Removed archived project data after confirmation.'
fi
