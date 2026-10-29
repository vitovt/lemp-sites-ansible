#!/usr/bin/env bash
# Compatibility entry point. Require the disposable VM marker and its token.
set -euo pipefail
: "${WP_HOSTING_TEST_ID:?Use tests/run-integration.py, or set the disposable VM test ID}"
exec python3 "$(dirname "$0")/integration/scenarios.py" "$WP_HOSTING_TEST_ID"
