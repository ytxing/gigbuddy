#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Keep the historical source-checkout environment names as a thin adapter.
if [[ -n "${PYTHON_BIN:-}" && -z "${GIGBUDDY_PYTHON:-}" ]]; then
    export GIGBUDDY_PYTHON="$PYTHON_BIN"
fi
export GIGBUDDY_SOURCE_CHECKOUT=1
export GIGBUDDY_HOME="$ROOT"
export GIGBUDDY_VERBOSE="${GIGBUDDY_VERBOSE:-1}"

exec "$ROOT/scripts/install.sh" "$@"
