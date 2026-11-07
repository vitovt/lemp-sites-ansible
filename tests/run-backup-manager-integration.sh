#!/usr/bin/env bash
# Build from an allowlist. Never send inventories, keys or the working tree to Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
context=$(mktemp -d /tmp/wbm-build-XXXXXXXX)
trap 'rm -rf "$context"' EXIT
mkdir -p "$context/server-tools" "$context/roles" "$context/tests"
cp -R server-tools/bin server-tools/lib "$context/server-tools/"
find "$context/server-tools" -type d -name __pycache__ -prune -exec rm -rf {} +
cp -R roles/server_tools "$context/roles/"
cp tests/integration_backup_manager.py tests/backup-manager-install.yml "$context/tests/"
cp tests/backup-manager.Dockerfile "$context/Dockerfile"
docker build -t wp-backup-manager-test "$context"
# No host mounts, host secrets or network access during tests.
docker run --rm --network none --cap-drop ALL --cap-add DAC_OVERRIDE --cap-add CHOWN \
    --cap-add SETUID --cap-add SETGID --cap-add KILL wp-backup-manager-test
