#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HOME:-}"
INSTALL_ROOT_RAW="${GIGBUDDY_HOME:-${USER_HOME}/.local/share/gigbuddy}"
BIN_DIR_RAW="${GIGBUDDY_BIN_DIR:-${USER_HOME}/.local/bin}"
TOKEN_FILE="${USER_HOME}/.config/gigbuddy/tone3000_tokens.json"
ASSUME_YES=0
KEEP_DATA=0

die() {
  printf 'GigBuddy uninstall failed: %s\n' "$*" >&2
  exit 1
}

for arg in "$@"; do
  case "$arg" in
    -y|--yes)
      ASSUME_YES=1
      ;;
    --keep-data)
      KEEP_DATA=1
      ;;
    *)
      die "unknown option: $arg (use --yes or --keep-data)"
      ;;
  esac
done

[[ -n "$USER_HOME" ]] || die "HOME is not set"
canonicalize_path() {
  local path="$1"
  local parent name
  parent="$(dirname -- "$path")"
  name="$(basename -- "$path")"
  [[ "$name" != "." && "$name" != ".." ]] || return 1
  parent="$(cd -P -- "$parent" 2>/dev/null && pwd -P)" || return 1
  printf '%s/%s\n' "$parent" "$name"
}

INSTALL_ROOT="$(canonicalize_path "$INSTALL_ROOT_RAW")" ||
  die "invalid install path: $INSTALL_ROOT_RAW"
BIN_DIR="$(canonicalize_path "$BIN_DIR_RAW")" ||
  die "invalid bin path: $BIN_DIR_RAW"
[[ "$INSTALL_ROOT" != "/" && "$INSTALL_ROOT" != "$USER_HOME" &&
   "$INSTALL_ROOT" != "$USER_HOME/.local" &&
   "$INSTALL_ROOT" != "$USER_HOME/.local/share" ]] ||
  die "refusing to remove a broad path: $INSTALL_ROOT"
[[ ! -L "$INSTALL_ROOT" ]] || die "refusing to remove a symlink install path: $INSTALL_ROOT"

link_points_into_install() {
  local link="$1"
  local target
  if [[ -L "$link" ]]; then
    target="$(readlink "$link")"
    case "$target" in
      "$INSTALL_ROOT"/*|"$INSTALL_ROOT_RAW"/*) return 0 ;;
      *) return 1 ;;
    esac
  fi
  # v1.0.0 起 bin 命令是内容指向安装目录的 wrapper 脚本（bash 包装，
  # 非 symlink）——按内容识别。
  if [[ -f "$link" ]] && grep -q "$INSTALL_ROOT" "$link" 2>/dev/null; then
    return 0
  fi
  return 1
}

recognized_install=false
if [[ -e "$INSTALL_ROOT" ]]; then
  if [[ -f "$INSTALL_ROOT/.gigbuddy-install" ]] &&
     grep -qx 'GigBuddy' "$INSTALL_ROOT/.gigbuddy-install"; then
    recognized_install=true
  elif [[ -d "$INSTALL_ROOT/.git" && -f "$INSTALL_ROOT/pyproject.toml" ]] &&
       grep -Eq '^[[:space:]]*name[[:space:]]*=[[:space:]]*"gigbuddy"[[:space:]]*$' \
         "$INSTALL_ROOT/pyproject.toml"; then
    recognized_install=true
  fi
else
  for command_name in gigbuddy gigbuddy-tui; do
    link_points_into_install "$BIN_DIR/$command_name" && recognized_install=true
  done
fi

[[ "$recognized_install" == true ]] || {
  if [[ -e "$INSTALL_ROOT" ]]; then
    die "not a recognized GigBuddy install: $INSTALL_ROOT"
  fi
  printf 'GigBuddy is not installed at %s\n' "$INSTALL_ROOT"
  exit 0
}

has_install=false
[[ -e "$INSTALL_ROOT" ]] && has_install=true
for command_name in gigbuddy gigbuddy-tui; do
  link_points_into_install "$BIN_DIR/$command_name" && has_install=true
done

if [[ "$has_install" == false ]]; then
  printf 'GigBuddy is not installed at %s\n' "$INSTALL_ROOT"
  exit 0
fi

printf 'GigBuddy will remove:\n'
printf '  files: %s\n' "$INSTALL_ROOT"
printf '  links: %s/gigbuddy\n' "$BIN_DIR"
if [[ -d "$INSTALL_ROOT/data" ]]; then
  printf '  local data: %s\n' "$INSTALL_ROOT/data"
fi
printf '  local PortAudio build (.local): removed with the install\n'

if [[ "$ASSUME_YES" != 1 ]]; then
  answer=''
  if [[ -t 0 ]]; then
    read -r -p 'Continue? [y/N] ' answer
  elif [[ -r /dev/tty ]]; then
    read -r -p 'Continue? [y/N] ' answer </dev/tty
  else
    die 'non-interactive uninstall requires --yes'
  fi
  [[ "$answer" =~ ^[Yy]$ ]] || {
    printf 'Cancelled.\n'
    exit 0
  }
fi

DELETE_DATA=0
if [[ -d "$INSTALL_ROOT/data" ]]; then
  if [[ "$ASSUME_YES" == 1 && "$KEEP_DATA" != 1 ]]; then
    DELETE_DATA=1
  elif [[ "$ASSUME_YES" != 1 && "$KEEP_DATA" != 1 ]]; then
    data_answer=''
    if [[ -t 0 ]]; then
      read -r -p 'Delete downloaded tones and local data? [y/N] ' data_answer
    elif [[ -r /dev/tty ]]; then
      read -r -p 'Delete downloaded tones and local data? [y/N] ' data_answer </dev/tty
    else
      die 'non-interactive data choice requires --yes or --keep-data'
    fi
    [[ "$data_answer" =~ ^[Yy]$ ]] && DELETE_DATA=1
  fi
fi

REMOVE_SESSION=0
if [[ -f "$TOKEN_FILE" ]]; then
  REMOVE_SESSION=1
fi

if [[ -d "$INSTALL_ROOT" && ! -f "$INSTALL_ROOT/.gigbuddy-install" ]]; then
  printf 'GigBuddy\n' > "$INSTALL_ROOT/.gigbuddy-install"
fi

for command_name in gigbuddy gigbuddy-tui; do
  link="$BIN_DIR/$command_name"
  if link_points_into_install "$link"; then
    rm -f -- "$link"
  elif [[ -L "$link" ]]; then
    printf 'Keeping unrelated symlink: %s\n' "$link"
  elif [[ -e "$link" ]]; then
    printf 'Keeping non-symlink command: %s\n' "$link"
  fi
done

if [[ "$DELETE_DATA" == 1 ]]; then
  rm -rf -- "$INSTALL_ROOT"
elif [[ -d "$INSTALL_ROOT" ]]; then
  # Keep data and .git so a later installer run can restore the checkout in place.
  for entry in "$INSTALL_ROOT"/* "$INSTALL_ROOT"/.[!.]* "$INSTALL_ROOT"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="${entry##*/}"
    case "$name" in
      data|.git|.gigbuddy-install)
        continue
        ;;
    esac
    rm -rf -- "$entry"
  done
fi

if [[ "$REMOVE_SESSION" == 1 ]]; then
  rm -f -- "$TOKEN_FILE"
  printf 'TONE3000 login session removed.\n'
fi

if [[ "$DELETE_DATA" == 1 ]]; then
  printf 'GigBuddy removed, including local data.\n'
elif [[ -d "$INSTALL_ROOT/data" ]]; then
  printf 'GigBuddy removed; local data kept at %s.\n' "$INSTALL_ROOT/data"
else
  printf 'GigBuddy removed.\n'
fi
