#!/usr/bin/env bash
# Build from public tool/test files only. No inventory, SSH keys or host mounts.
set -euo pipefail
cd "$(dirname "$0")/.."
context=$(mktemp -d /tmp/site-shell-build-XXXXXXXX)
trap 'rm -rf "$context"' EXIT
mkdir -p "$context/server-tools" "$context/roles/site/tasks" "$context/tests"
cp -R server-tools/bin server-tools/lib "$context/server-tools/"
find "$context/server-tools" -type d -name __pycache__ -prune -exec rm -rf {} +
cp -R roles/server_tools "$context/roles/"
cp roles/site/tasks/one-site.yml "$context/roles/site/tasks/"
cp tests/integration_site_shell.py "$context/tests/"
cp tests/site-shell.Dockerfile "$context/Dockerfile"
docker build -t wp-site-shell-test "$context"
docker run --rm --network none --cap-drop ALL --cap-add DAC_OVERRIDE --cap-add CHOWN --cap-add FOWNER \
    --cap-add SETUID --cap-add SETGID --cap-add KILL wp-site-shell-test
