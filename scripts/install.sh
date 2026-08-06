#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${GIGBUDDY_REPO_URL:-https://github.com/ytxing/gigbuddy.git}"
REPO_REF="${GIGBUDDY_REF:-v0.1.0-alpha.3}"
USER_HOME="${HOME:-}"
INSTALL_ROOT="${GIGBUDDY_HOME:-${USER_HOME}/.local/share/gigbuddy}"
BIN_DIR="${GIGBUDDY_BIN_DIR:-${USER_HOME}/.local/bin}"
PYTHON_BIN="${GIGBUDDY_PYTHON:-python3}"

die() {
  printf 'GigBuddy install failed: %s\n' "$*" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || die "git is required"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python 3.11+ is required: $PYTHON_BIN"

[[ -n "$USER_HOME" ]] || die "HOME is not set"
[[ "$(uname -s)" == "Darwin" ]] || die "the full installer currently supports macOS only"

"$PYTHON_BIN" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required")
PY

command -v brew >/dev/null 2>&1 || die "Homebrew is required: https://brew.sh"
if ! brew --prefix portaudio >/dev/null 2>&1; then
  printf '%s\n' 'Installing PortAudio with Homebrew...'
  brew install portaudio
fi

if [[ -e "$INSTALL_ROOT" && ! -d "$INSTALL_ROOT/.git" ]]; then
  die "install path exists but is not a GigBuddy checkout: $INSTALL_ROOT"
fi

if [[ -d "$INSTALL_ROOT/.git" ]]; then
  printf 'Updating %s to %s...\n' "$INSTALL_ROOT" "$REPO_REF"
  git -C "$INSTALL_ROOT" fetch --quiet --tags origin
  git -C "$INSTALL_ROOT" checkout --quiet --detach "$REPO_REF"
else
  printf 'Cloning GigBuddy %s...\n' "$REPO_REF"
  mkdir -p "$(dirname "$INSTALL_ROOT")"
  git clone --quiet --depth 1 --branch "$REPO_REF" "$REPO_URL" "$INSTALL_ROOT"
fi

if [[ ! -x "$INSTALL_ROOT/.venv/bin/python" ]]; then
  printf '%s\n' 'Creating the Python environment...'
  "$PYTHON_BIN" -m venv "$INSTALL_ROOT/.venv"
fi

printf '%s\n' 'Installing Python dependencies...'
"$INSTALL_ROOT/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_ROOT/.venv/bin/python" -m pip install --editable "$INSTALL_ROOT"

printf '%s\n' 'Fetching pinned C++ dependencies...'
"$INSTALL_ROOT/scripts/bootstrap_third_party.sh"

printf '%s\n' 'Building the realtime and offline engines...'
"$INSTALL_ROOT/cpp/build.sh"

mkdir -p "$INSTALL_ROOT/data" "$BIN_DIR"
for command_name in gigbuddy gigbuddy-tui; do
  link="$BIN_DIR/$command_name"
  if [[ -e "$link" && ! -L "$link" ]]; then
    die "refusing to replace an existing non-symlink: $link"
  fi
  ln -sfn "$INSTALL_ROOT/.venv/bin/$command_name" "$link"
done

printf '\nGigBuddy installed at %s\n' "$INSTALL_ROOT"
printf 'Commands: %s/gigbuddy and %s/gigbuddy-tui\n' "$BIN_DIR" "$BIN_DIR"
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  printf 'Add this directory to PATH if needed: %s\n' "$BIN_DIR"
fi
