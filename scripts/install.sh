#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${GIGBUDDY_REPO_URL:-https://github.com/ytxing/gigbuddy.git}"
REPO_REF="${GIGBUDDY_REF:-v0.1.0-alpha.7}"
USER_HOME="${HOME:-}"
INSTALL_ROOT="${GIGBUDDY_HOME:-${USER_HOME}/.local/share/gigbuddy}"
BIN_DIR="${GIGBUDDY_BIN_DIR:-${USER_HOME}/.local/bin}"
PYTHON_BIN="${GIGBUDDY_PYTHON:-python3}"

die() {
  printf 'GigBuddy install failed: %s\n' "$*" >&2
  exit 1
}

step() {
  printf '==> %s\n' "$1"
}

INSTALL_LOG="$(mktemp -t gigbuddy-install.XXXXXX)"
trap 'rm -f "$INSTALL_LOG"' EXIT

run_quiet() {
  if [[ "${GIGBUDDY_VERBOSE:-0}" == "1" ]]; then
    "$@"
    return
  fi
  : >"$INSTALL_LOG"
  if "$@" >"$INSTALL_LOG" 2>&1; then
    return
  fi
  printf 'GigBuddy install failed while running:' >&2
  printf ' %q' "$@" >&2
  printf '\n' >&2
  tail -n 40 "$INSTALL_LOG" >&2
  return 1
}

is_gigbuddy_checkout() {
  if [[ -f "$INSTALL_ROOT/.gigbuddy-install" ]] &&
     grep -qx 'GigBuddy' "$INSTALL_ROOT/.gigbuddy-install"; then
    return 0
  fi
  if [[ -f "$INSTALL_ROOT/pyproject.toml" ]] &&
     grep -Eq '^[[:space:]]*name[[:space:]]*=[[:space:]]*"gigbuddy"[[:space:]]*$' \
       "$INSTALL_ROOT/pyproject.toml"; then
    return 0
  fi
  return 1
}

step "Checking prerequisites"
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
  step "Installing PortAudio"
  run_quiet brew install --quiet portaudio
fi

if [[ -e "$INSTALL_ROOT" && ! -d "$INSTALL_ROOT/.git" ]]; then
  die "install path exists but is not a GigBuddy checkout: $INSTALL_ROOT"
fi

if [[ -d "$INSTALL_ROOT/.git" ]]; then
  if ! is_gigbuddy_checkout; then
    die "install path is not a GigBuddy checkout: $INSTALL_ROOT"
  fi
  step "Updating GigBuddy to $REPO_REF"
  run_quiet git -C "$INSTALL_ROOT" fetch --quiet --tags origin
  run_quiet git -C "$INSTALL_ROOT" checkout --quiet --detach "$REPO_REF"
else
  step "Downloading GigBuddy $REPO_REF"
  mkdir -p "$(dirname "$INSTALL_ROOT")"
  run_quiet git clone --quiet --depth 1 --branch "$REPO_REF" "$REPO_URL" "$INSTALL_ROOT"
fi

printf 'GigBuddy\n' > "$INSTALL_ROOT/.gigbuddy-install"

step "Preparing Python environment"
if [[ ! -x "$INSTALL_ROOT/.venv/bin/python" ]]; then
  run_quiet "$PYTHON_BIN" -m venv "$INSTALL_ROOT/.venv"
fi

step "Installing Python dependencies"
run_quiet "$INSTALL_ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip
run_quiet "$INSTALL_ROOT/.venv/bin/python" -m pip install --quiet --editable "$INSTALL_ROOT"

step "Fetching pinned C++ dependencies"
run_quiet "$INSTALL_ROOT/scripts/bootstrap_third_party.sh"

step "Building audio engines"
run_quiet "$INSTALL_ROOT/cpp/build.sh"

step "Linking commands"
mkdir -p "$INSTALL_ROOT/data" "$BIN_DIR"
for command_name in gigbuddy gigbuddy-tui; do
  link="$BIN_DIR/$command_name"
  if [[ -e "$link" && ! -L "$link" ]]; then
    die "refusing to replace an existing non-symlink: $link"
  fi
  ln -sfn "$INSTALL_ROOT/.venv/bin/$command_name" "$link"
done

printf '\nGigBuddy ready\n'
printf '  %s/gigbuddy\n' "$BIN_DIR"
printf '  %s/gigbuddy-tui\n' "$BIN_DIR"
printf '  install: %s\n' "$INSTALL_ROOT"
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  printf 'Add to PATH if needed: %s\n' "$BIN_DIR"
fi
