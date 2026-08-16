#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HOME:-}"
INSTALL_ROOT_RAW="${GIGBUDDY_HOME:-${USER_HOME}/.local/share/gigbuddy}"
BIN_DIR_RAW="${GIGBUDDY_BIN_DIR:-${USER_HOME}/.local/bin}"
DATA_ROOT_RAW="${GIGBUDDY_DATA_HOME:-}"
TOKEN_FILE="${USER_HOME}/.config/gigbuddy/tone3000_tokens.json"
ASSUME_YES=0
KEEP_DATA=0

die() {
  printf 'GigBuddy uninstall failed: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf '%s\n' \
    'Usage: uninstall.sh [OPTIONS]' \
    '' \
    'Options:' \
    '  -y, --yes       Confirm uninstall without prompting' \
    '      --keep-data Remove the runtime but keep downloaded tones and data' \
    '  -h, --help      Show this help and exit'
}

for arg in "$@"; do
  case "$arg" in
    -y|--yes)
      ASSUME_YES=1
      ;;
    --keep-data)
      KEEP_DATA=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $arg (run --help for usage)"
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

expand_user_path() {
  local path="$1"
  case "$path" in
    "~") printf '%s\n' "$USER_HOME" ;;
    "~"/*) printf '%s%s\n' "$USER_HOME" "${path#\~}" ;;
    *) printf '%s\n' "$path" ;;
  esac
}

canonicalize_data_path() {
  local path
  path="$(expand_user_path "$1")"
  if [[ -d "$path" ]]; then
    (cd -P -- "$path" 2>/dev/null && pwd -P)
    return
  fi
  canonicalize_path "$path"
}

resolve_data_link() {
  local link="$1"
  local target
  target="$(readlink "$link")" || return 1
  case "$target" in
    /*) ;;
    *) target="$(dirname -- "$link")/$target" ;;
  esac
  canonicalize_data_path "$target"
}

validate_external_data_root() {
  local path="$1"
  [[ -n "$path" && "$path" != "/" && "$path" != "$USER_HOME" &&
     "$path" != "$USER_HOME/.local" &&
     "$path" != "$USER_HOME/.local/share" ]] ||
    die "refusing to remove a broad data path: $path"
  [[ "$path" != "$INSTALL_ROOT" ]] ||
    die "data path must be separate from the install path"
  case "$path/" in
    "$INSTALL_ROOT"/*) die "data path must be outside the install path" ;;
  esac
  case "$INSTALL_ROOT/" in
    "$path"/*) die "install path must be outside the data path" ;;
  esac
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

resolve_physical_path() {
  local path="$1"
  local target parent name base suffix=""
  local attempts=0
  case "$path" in
    /*) ;;
    *) path="$(pwd -P)/$path" ;;
  esac
  while (( attempts < 80 )); do
    attempts=$((attempts + 1))
    if [[ -L "$path" ]]; then
      parent="$(dirname -- "$path")"
      parent="$(cd -P -- "$parent" 2>/dev/null && pwd -P)" || return 1
      target="$(readlink "$path")" || return 1
      case "$target" in
        /*) path="$target" ;;
        *) path="$parent/$target" ;;
      esac
      continue
    fi
    if [[ -d "$path" ]]; then
      base="$(cd -P -- "$path" 2>/dev/null && pwd -P)" || return 1
      if [[ "$base" == "/" ]]; then
        printf '/%s\n' "${suffix#/}"
      else
        printf '%s%s\n' "$base" "$suffix"
      fi
      return 0
    fi
    if [[ -e "$path" ]]; then
      parent="$(dirname -- "$path")"
      name="$(basename -- "$path")"
      parent="$(cd -P -- "$parent" 2>/dev/null && pwd -P)" || return 1
      printf '%s/%s%s\n' "$parent" "$name" "$suffix"
      return 0
    fi
    name="$(basename -- "$path")"
    case "$name" in
      ""|.|..) return 1 ;;
    esac
    suffix="/$name$suffix"
    path="$(dirname -- "$path")"
  done
  return 1
}

wrapper_command_target() {
  local wrapper="$1" line_count
  [[ -f "$wrapper" && ! -L "$wrapper" ]] || return 1
  [[ "$(sed -n '1p' "$wrapper")" == '#!/usr/bin/env bash' ]] || return 1
  line_count="$(awk 'END { print NR }' "$wrapper")" || return 1
  [[ "$line_count" == 2 ]] || return 1
  sed -n \
    -e '2s/^exec "\([^"]*\)" "\$@"$/\1/p' \
    -e '2s/^exec \([^[:space:]]*\) "\$@"$/\1/p' "$wrapper"
}

link_points_into_install() {
  local link="$1"
  local target
  if [[ -L "$link" ]]; then
    target="$(resolve_physical_path "$link")" || return 1
    case "$target/" in
      "$INSTALL_ROOT/"*) return 0 ;;
      *) return 1 ;;
    esac
  fi
  # v1.0.0 起 bin 命令是内容指向安装目录的 wrapper 脚本（bash 包装，
  # 非 symlink）。只解析安装器生成的固定 exec 形状，并比较物理目标。
  if [[ -f "$link" ]]; then
    target="$(wrapper_command_target "$link")"
    [[ -n "$target" ]] || return 1
    target="$(resolve_physical_path "$target")" || return 1
    case "$target/" in
      "$INSTALL_ROOT/"*) return 0 ;;
      *) return 1 ;;
    esac
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

DATA_LINK="$INSTALL_ROOT/data"
DATA_LAYOUT="none"
DATA_ROOT=""
if [[ -L "$DATA_LINK" ]]; then
  DATA_ROOT="$(resolve_data_link "$DATA_LINK")" ||
    die "cannot resolve installed data link: $DATA_LINK"
  DATA_LAYOUT="external"
elif [[ -d "$DATA_LINK" ]]; then
  DATA_ROOT="$DATA_LINK"
  DATA_LAYOUT="embedded"
elif [[ -n "$DATA_ROOT_RAW" ]]; then
  DATA_ROOT="$(canonicalize_data_path "$DATA_ROOT_RAW")" ||
    die "invalid data path: $DATA_ROOT_RAW"
  DATA_LAYOUT="external"
elif [[ -d "${INSTALL_ROOT}-data" ]]; then
  DATA_ROOT="$(canonicalize_data_path "${INSTALL_ROOT}-data")" ||
    die "invalid data path: ${INSTALL_ROOT}-data"
  DATA_LAYOUT="external"
fi

if [[ "$DATA_LAYOUT" == "external" ]]; then
  validate_external_data_root "$DATA_ROOT"
  if [[ -n "$DATA_ROOT_RAW" && -L "$DATA_LINK" ]]; then
    CONFIGURED_DATA_ROOT="$(canonicalize_data_path "$DATA_ROOT_RAW")" ||
      die "invalid data path: $DATA_ROOT_RAW"
    [[ "$CONFIGURED_DATA_ROOT" == "$DATA_ROOT" ]] ||
      die "configured data path does not match installed data link"
  fi
fi

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
if [[ "$DATA_LAYOUT" != "none" ]]; then
  printf '  local data: %s\n' "$DATA_ROOT"
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
if [[ "$DATA_LAYOUT" != "none" ]]; then
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

PRESERVED_DATA_ROOT=""
if [[ "$DATA_LAYOUT" != "none" && "$DELETE_DATA" != 1 ]]; then
  if [[ "$DATA_LAYOUT" == "embedded" ]]; then
    migration_target_raw="${DATA_ROOT_RAW:-${INSTALL_ROOT}-data}"
    migration_target="$(canonicalize_data_path "$migration_target_raw")" ||
      die "invalid data migration path: $migration_target_raw"
    validate_external_data_root "$migration_target"
    if [[ -e "$migration_target" || -L "$migration_target" ]]; then
      [[ -d "$migration_target" && ! -L "$migration_target" ]] ||
        die "data migration path is not a directory: $migration_target"
      rmdir "$migration_target" 2>/dev/null ||
        die "both embedded and external data directories contain files; refusing to merge"
    fi
    mkdir -p "$(dirname -- "$migration_target")"
    mv -- "$DATA_ROOT" "$migration_target" ||
      die "could not preserve legacy data at $migration_target"
    DATA_ROOT="$migration_target"
    DATA_LAYOUT="external"
  fi
  PRESERVED_DATA_ROOT="$DATA_ROOT"
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

if [[ -d "$INSTALL_ROOT" ]]; then
  rm -rf -- "$INSTALL_ROOT"
fi
if [[ "$DELETE_DATA" == 1 && "$DATA_LAYOUT" == "external" &&
      ( -e "$DATA_ROOT" || -L "$DATA_ROOT" ) ]]; then
  rm -rf -- "$DATA_ROOT"
fi

if [[ "$REMOVE_SESSION" == 1 ]]; then
  rm -f -- "$TOKEN_FILE"
  printf 'TONE3000 login session removed.\n'
fi

if [[ "$DELETE_DATA" == 1 ]]; then
  printf 'GigBuddy removed, including local data.\n'
elif [[ -n "$PRESERVED_DATA_ROOT" ]]; then
  printf 'GigBuddy removed; local data kept at %s.\n' "$PRESERVED_DATA_ROOT"
else
  printf 'GigBuddy removed.\n'
fi
