#!/usr/bin/env bash
# Run controller checks. Use the explicit disposable VM runner for integration tests.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-check}" in
  check) exec tests/check.sh ;;
  *) echo 'Use tests/run-integration.py --help for isolated VM integration tests.' >&2; exit 2 ;;
esac
