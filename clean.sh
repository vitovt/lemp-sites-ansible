#!/usr/bin/env bash
# Remove only generated controller files. Private inventories need explicit consent.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./clean.sh [--clear-inventories [--force-danger-clear-inventories]]

Without options, remove local Python and Ansible caches and installed dependencies.
Recreate dependencies with the Fast Start commands in README.md.

--clear-inventories               Also permanently delete private server inventories.
                                  The tracked example-server template is kept.
--force-danger-clear-inventories  Skip the inventory deletion prompt. Requires
                                  --clear-inventories.
EOF
}

clear_inventories=false
force_clear=false
for arg in "$@"; do
    case "$arg" in
        --clear-inventories) clear_inventories=true ;;
        --force-danger-clear-inventories) force_clear=true ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$arg" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "$force_clear" == true && "$clear_inventories" != true ]]; then
    printf '%s\n' '--force-danger-clear-inventories requires --clear-inventories.' >&2
    exit 2
fi

repo_root=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
inventory_root="$repo_root/inventories"

if [[ "$clear_inventories" == true ]]; then
    if [[ -L "$inventory_root" || ! -d "$inventory_root" ]]; then
        printf 'Expected a real inventories directory: %s\n' "$inventory_root" >&2
        exit 1
    fi
    if [[ "$force_clear" != true ]]; then
        printf '%s\n' 'This permanently deletes every private server directory and file in inventories/.' >&2
        printf '%s\n' 'The tracked inventories/example-server/ template is kept.' >&2
        printf 'Continue? [y/N] ' >&2
        if ! read -r answer || [[ "$answer" != [yY] ]]; then
            printf '%s\n' 'Canceled. No files were removed.' >&2
            exit 1
        fi
    fi
fi

# These directories are generated from requirements-dev.txt and requirements.yml.
for name in .venv collections galaxy_roles .ansible .pytest_cache .mypy_cache .ruff_cache .tox .nox; do
    path="$repo_root/$name"
    if [[ -e "$path" || -L "$path" ]]; then
        rm -rf -- "$path"
        printf 'Removed %s/\n' "$name"
    fi
done

# Do not inspect Git, inventories, secrets, or stored test results for cache files.
# find does not follow symlinks, so it cannot enter linked external directories.
find "$repo_root" \
    \( -name .git -o -name inventories -o -name secrets -o -name test-results \
       -o -name .agents -o -name .aws -o -name .codex \) -prune -o \
    -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \
              -o -name .ruff_cache \) -prune -exec rm -rf -- {} +
find "$repo_root" \
    \( -name .git -o -name inventories -o -name secrets -o -name test-results \
       -o -name .agents -o -name .aws -o -name .codex \) -prune -o \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) -exec rm -f -- {} +
printf '%s\n' 'Removed local Python and Ansible caches and installed dependencies.'

if [[ "$clear_inventories" == true ]]; then
    # Keep the public example. Every other direct child is private local data.
    find "$inventory_root" -mindepth 1 -maxdepth 1 ! -name example-server \
        -exec rm -rf -- {} +
    # A user might have put real credentials beside the example secret templates.
    example_secrets="$inventory_root/example-server/secrets"
    if [[ -d "$example_secrets" && ! -L "$example_secrets" ]]; then
        find "$example_secrets" -type f ! -name '*.example' -exec rm -f -- {} +
        find "$example_secrets" -type l -exec rm -f -- {} +
    fi
    printf '%s\n' 'Removed private inventories. The example-server template remains.'
fi
