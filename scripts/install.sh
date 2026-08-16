#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${GIGBUDDY_REPO_URL:-https://github.com/ytxing/gigbuddy.git}"
# This bootstrap locator must exist before a checkout is available. A test
# keeps it aligned with the runtime version in pyproject.toml.
REPO_REF="${GIGBUDDY_REF:-v1.2.4}"
USER_HOME="${HOME:-}"
INSTALL_ROOT="${GIGBUDDY_HOME:-${USER_HOME}/.local/share/gigbuddy}"
BIN_DIR="${GIGBUDDY_BIN_DIR:-${USER_HOME}/.local/bin}"
DATA_ROOT="${GIGBUDDY_DATA_HOME:-}"
PYTHON_BIN="${GIGBUDDY_PYTHON:-python3}"
LOGIN_PYTHON="${GIGBUDDY_LOGIN_PYTHON:-$PYTHON_BIN}"
SOURCE_CHECKOUT="${GIGBUDDY_SOURCE_CHECKOUT:-0}"
USAGE_NAME="scripts/install.sh"
if [[ "$SOURCE_CHECKOUT" == 1 ]]; then
  USAGE_NAME="./install.sh"
fi
NO_ENGINE=0
SKIP_PRESETS=0
SKIP_PRESETS_EXPLICIT=0
SKIP_DRY_INPUTS=0
DRY_INPUTS="all"
CONFIRM_DECLINED=1
CONFIRM_READ_FAILED=2
CONFIRM_NO_TTY=3

usage() {
  printf 'Usage: %s [options]\n\n' "$USAGE_NAME"
  cat <<'EOF'
Options:
  --no-engine         skip NeuralAudio/PortAudio download and C++ build
  --skip-presets      skip install-time Preset registration and login check
  --skip-dry-inputs   skip official dry-input downloads
  --starter-dry       download only the ten common starter dry inputs
  -h, --help          show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-engine) NO_ENGINE=1 ;;
    --skip-presets)
      SKIP_PRESETS=1
      SKIP_PRESETS_EXPLICIT=1
      ;;
    --skip-dry-inputs) SKIP_DRY_INPUTS=1 ;;
    --starter-dry) DRY_INPUTS="starter" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

ROLLBACK_FILE="$(mktemp -t gigbuddy-rollback.XXXXXX)"
DB_BACKUP_FILE=""
DB_BACKUP_PYTHON=""
DB_ROLLBACK_PATH=""
DATABASE_ROLLBACK_PREPARED=0
DATA_ROOT_CREATED=0
DATA_ROOT_MIGRATED=0
DATA_LINK_CREATED=0
EIGEN_BACKUP_DIR=""
EIGEN_ROLLBACK_TARGET=""
EIGEN_ORIGINAL_PRESENT=0
ROLLBACK_ARMED=0

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

generated_wrapper_points_into_install() {
  local wrapper="$1" command_name="$2"
  local target install_root_real
  target="$(wrapper_command_target "$wrapper")" || return 1
  [[ -n "$target" ]] || return 1
  target="$(resolve_physical_path "$target")" || return 1
  [[ "$(basename -- "$target")" == "$command_name" ]] || return 1
  install_root_real="$(resolve_physical_path "$INSTALL_ROOT")" || return 1
  case "$target/" in
    "$install_root_real/"*) return 0 ;;
    *) return 1 ;;
  esac
}

die() {
  stop_banner
  if ! rollback_once; then
    printf 'GigBuddy rollback was incomplete; review the recovery error above.\n' >&2
  fi
  printf 'GigBuddy install failed: %s\n' "$*" >&2
  exit 1
}

rollback_install() {
  # 安装失败时撤销本次安装做的改动，恢复到安装前状态：
  # - 本次新建的安装目录 → 整个删除
  # - 更新已有安装 → checkout 回原 HEAD，删除本次新建的 .venv/data/third_party
  #   （安装前已存在的保留，幂等重跑不受影响）
  # - 本次新建的 ~/.local/bin wrapper 链接 → 删除
  local database rollback_failed=0
  local dir_name had command_name link
  [[ -f "$ROLLBACK_FILE" ]] || return 0
  . "$ROLLBACK_FILE"
  if ! restore_eigen_backup; then
    rollback_failed=1
  fi
  if [[ "$SOURCE_CHECKOUT" == 1 ]]; then
    if [[ "$rollback_failed" == 0 ]]; then
      rm -f "$ROLLBACK_FILE"
    fi
    return "$rollback_failed"
  fi
  database="${DB_ROLLBACK_PATH:-$INSTALL_ROOT/data/gigbuddy.db}"
  if [[ "${ORIGINAL_DB_PRESENT:-}" == "1" ]]; then
    if [[ -z "${DB_BACKUP_FILE:-}" || ! -f "$DB_BACKUP_FILE" \
          || -z "${DB_BACKUP_PYTHON:-}" ]]; then
      printf 'Database recovery snapshot is unavailable.\n' >&2
      rollback_failed=1
    elif restore_database_backup \
        "$DB_BACKUP_PYTHON" "$DB_BACKUP_FILE" "$database"; then
      rm -f -- "$DB_BACKUP_FILE"
    else
      printf 'Could not restore the previous GigBuddy database.\n' >&2
      printf 'Recovery snapshot retained at %s\n' "$DB_BACKUP_FILE" >&2
      rollback_failed=1
    fi
  elif [[ "${ORIGINAL_DB_PRESENT:-}" == "0" ]]; then
    rm -f -- "$database" "$database-wal" "$database-shm" "$database-journal"
  fi
  if [[ "${WAS_NEW_CLONE:-0}" == "1" ]]; then
    rm -rf -- "$INSTALL_ROOT"
  elif [[ -n "${PREV_HEAD:-}" ]]; then
    if ! git -C "$INSTALL_ROOT" checkout --quiet --detach "$PREV_HEAD"; then
      printf 'Could not restore GigBuddy checkout to %s\n' "$PREV_HEAD" >&2
      rollback_failed=1
    fi
    for dir_name in .venv data third_party; do
      case "$dir_name" in
        .venv) had=HAD_VENV ;;
        data) had=HAD_DATA ;;
        third_party) had=HAD_THIRDPARTY ;;
      esac
      # Missing rollback metadata is not proof that a directory was created by
      # this run. Preserve unknown state instead of deleting preexisting data.
      if [[ "${!had:-1}" != "1" && -d "$INSTALL_ROOT/$dir_name" ]]; then
        rm -rf -- "$INSTALL_ROOT/$dir_name"
      fi
    done
  fi
  if [[ "${DATA_LINK_CREATED:-0}" == "1" \
        && "${DATA_ROOT_MIGRATED:-0}" != "1" \
        && -L "$INSTALL_ROOT/data" ]]; then
    rm -f -- "$INSTALL_ROOT/data"
  fi
  if [[ "${DATA_ROOT_CREATED:-0}" == "1" \
        && -n "${DATA_ROOT:-}" && -d "$DATA_ROOT" ]]; then
    rm -rf -- "$DATA_ROOT"
  fi
  for command_name in gigbuddy gigbuddy-tui; do
    link="$BIN_DIR/$command_name"
    if generated_wrapper_points_into_install "$link" "$command_name"; then
      rm -f -- "$link"
    fi
  done
  if [[ "$rollback_failed" == 0 ]]; then
    rm -f "$ROLLBACK_FILE"
  fi
  return "$rollback_failed"
}

rollback_once() {
  [[ "$ROLLBACK_ARMED" == 1 ]] || return 0
  ROLLBACK_ARMED=0
  rollback_install
}

step() {
  printf '==> %s\n' "$1" >> "${INSTALL_LOG:?}"
  printf '==> %s\n' "$1" >>"${STATUS_FILE:?}"
  if [[ -n "${BANNER_PID:-}" ]]; then
    :
  else
    printf '==> %s\n' "$1"
  fi
}

BANNER_PID=""
BANNER_STARTED=0

stop_banner() {
  if [[ -n "$BANNER_PID" ]]; then
    kill "$BANNER_PID" 2>/dev/null || true
    wait "$BANNER_PID" 2>/dev/null || true
    BANNER_PID=""
    local completed_steps
    completed_steps=$(wc -l <"${STATUS_FILE:?}")
    printf '\033[%d;1H' "$(( ${STATUS_ROW:-9} + completed_steps ))"
  fi
}

print_saved_status() {
  [[ -s "${STATUS_FILE:?}" ]] || return 0
  while IFS= read -r status_line; do
    printf '%s\n' "$status_line"
  done <"$STATUS_FILE"
}

start_banner() {
  [[ "$BANNER_STARTED" == 0 ]] || return 0
  BANNER_STARTED=1
  local screen_controlled=0
  if [[ -t 1 && "${GIGBUDDY_VERBOSE:-0}" != "1" && -n "${TERM:-}" && "$TERM" != "dumb" ]] \
     && command -v python3 >/dev/null 2>&1; then
    # 动画进程独占终端；安装主流程只通过状态文件更新步骤文字。
    screen_controlled=1
    printf '\033[2J\033[H'
    local lines cols tty_size
    tty_size=$(stty size 2>/dev/null </dev/tty || true)
    read -r lines cols <<<"$tty_size"
    if [[ ! "$lines" =~ ^[1-9][0-9]*$ || ! "$cols" =~ ^[1-9][0-9]*$ ]]; then
      lines=$(tput lines 2>/dev/null || printf '24')
      cols=$(tput cols 2>/dev/null || printf '80')
    fi
    # 字体分层：终端越大用越大的金色字体（Rebel / Small Block / miniwi）
    local banner_lines=() status_row=0
    if (( lines >= 17 && cols > 96 )); then
      banner_lines=(
        '   █████████  █████   █████████  ███████████  █████  █████ ██████████   ██████████   █████ █████'
        '  ███▒▒▒▒▒███▒▒███   ███▒▒▒▒▒███▒▒███▒▒▒▒▒███▒▒███  ▒▒███ ▒▒███▒▒▒▒███ ▒▒███▒▒▒▒███ ▒▒███ ▒▒███'
        ' ███     ▒▒▒  ▒███  ███     ▒▒▒  ▒███    ▒███ ▒███   ▒███  ▒███   ▒▒███ ▒███   ▒▒███ ▒▒███ ███'
        '▒███          ▒███ ▒███          ▒██████████  ▒███   ▒███  ▒███    ▒███ ▒███    ▒███  ▒▒█████'
        '▒███    █████ ▒███ ▒███    █████ ▒███▒▒▒▒▒███ ▒███   ▒███  ▒███    ▒███ ▒███    ▒███   ▒▒███'
        '▒▒███  ▒▒███  ▒███ ▒▒███  ▒▒███  ▒███    ▒███ ▒███   ▒███  ▒███    ███  ▒███    ███     ▒███'
        ' ▒▒█████████  █████ ▒▒█████████  ███████████  ▒▒████████   ██████████   ██████████      █████'
        '  ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒▒▒▒▒▒▒    ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒      ▒▒▒▒▒'
      )
      status_row=9
    elif (( lines >= 13 && cols > 28 )); then
      banner_lines=(
        '▞▀▖▜▘▞▀▖▛▀▖▌ ▌▛▀▖▛▀▖▌ ▌'
        '▌▄▖▐ ▌▄▖▙▄▘▌ ▌▌ ▌▌ ▌▝▞'
        '▌ ▌▐ ▌ ▌▌ ▌▌ ▌▌ ▌▌ ▌ ▌'
        '▝▀ ▀▘▝▀ ▀▀ ▝▀ ▀▀ ▀▀  ▘'
      )
      status_row=5
    elif (( lines >= 12 && cols > 22 )); then
      banner_lines=(
        '▄▖▄▖▄▖▄ ▖▖▄ ▄ ▖▖'
        '▌ ▐ ▌ ▙▘▌▌▌▌▌▌▌▌'
        '▙▌▟▖▙▌▙▘▙▌▙▘▙▘▐'
      )
      status_row=4
    fi
    if (( ${#banner_lines[@]} >= 3 )); then
      # 真彩色能力检测：金色 24bit 需要 truecolor；否则 256 色静态近似
      if [[ "${COLORTERM:-}" == "truecolor" || "${COLORTERM:-}" == "24bit" \
            || "$TERM" == *"truecolor"* || "$TERM" == *"direct"* ]]; then
        export GB_BANNER_ROW="$status_row"
        export GB_STATUS_FILE="$STATUS_FILE"
        export GB_BANNER_LINES="$(printf '%s\n' "${banner_lines[@]}")"
        # 霓虹灯呼吸：字符不动，横幅上均匀分布 3 个明暗浪，金色系内流动，
        # 后台持续播放，直到安装流程调用 stop_banner。
        python3 - <<'PY' &
import math, os, signal, sys, time

LINES = tuple(os.environ['GB_BANNER_LINES'].split('\n'))
W = max(len(l) for l in LINES)
R = len(LINES)
WAVES = 3                       # 均匀分布的明暗浪数量
DARK = (110, 72, 8)             # 浪谷：暗金
BRIGHT = (250, 195, 90)         # 浪峰：亮金

def esc(c):
    return '\033[38;2;%d;%d;%dm' % c

def render(phase):
    out = []
    for row in LINES:
        seg = []
        for col, ch in enumerate(row):
            b = 0.5 + 0.5 * math.sin(2 * math.pi * WAVES * col / W - phase)
            c = tuple(int(DARK[i] + (BRIGHT[i] - DARK[i]) * b) for i in range(3))
            seg.append(esc(c) + ch)
        out.append('\033[2K' + ''.join(seg) + '\033[0m')
    return '\n'.join(out)

STATUS_ROW = int(os.environ['GB_BANNER_ROW'])
STATUS_FILE = os.environ['GB_STATUS_FILE']
running = True

def stop(_signum, _frame):
    global running
    running = False

signal.signal(signal.SIGTERM, stop)
f = 0
while running:
    try:
        with open(STATUS_FILE, encoding='utf-8') as status_file:
            status = status_file.read().rstrip('\n')
    except OSError:
        status = ''
    status_block = ''.join(
        f'\033[{STATUS_ROW + index};1H\033[2K{line}'
        for index, line in enumerate(status.splitlines())
    )
    # 此进程是动画期间唯一写终端的进程。
    sys.stdout.write(
        '\033[H'
        + render(2 * math.pi * f / 32)
        + status_block
    )
    sys.stdout.flush()
    f += 1
    time.sleep(0.1)
PY
        BANNER_PID=$!
        # 动画子进程存活验证：启动后短暂检查，崩溃则回退静态金色
        sleep 0.3
        if ! kill -0 "$BANNER_PID" 2>/dev/null; then
          BANNER_PID=""
          print_static_banner "${banner_lines[@]}"
          print_saved_status
        fi
        return
      fi
    fi
  fi
  # 非交互终端、无 python3、不支持 truecolor、或终端太矮：静态金色版
  local s_lines s_cols s_size
  s_size=$(stty size 2>/dev/null </dev/tty || true)
  read -r s_lines s_cols <<<"$s_size"
  if [[ ! "$s_lines" =~ ^[1-9][0-9]*$ || ! "$s_cols" =~ ^[1-9][0-9]*$ ]]; then
    s_lines=$(tput lines 2>/dev/null || printf '24')
    s_cols=$(tput cols 2>/dev/null || printf '80')
  fi
  local static_lines=()
  if (( s_lines >= 12 && s_cols > 96 )); then
    static_lines=(
      '   █████████  █████   █████████  ███████████  █████  █████ ██████████   ██████████   █████ █████'
      '  ███▒▒▒▒▒███▒▒███   ███▒▒▒▒▒███▒▒███▒▒▒▒▒███▒▒███  ▒▒███ ▒▒███▒▒▒▒███ ▒▒███▒▒▒▒███ ▒▒███ ▒▒███'
      ' ███     ▒▒▒  ▒███  ███     ▒▒▒  ▒███    ▒███ ▒███   ▒███  ▒███   ▒▒███ ▒███   ▒▒███ ▒▒███ ███'
      '▒███          ▒███ ▒███          ▒██████████  ▒███   ▒███  ▒███    ▒███ ▒███    ▒███  ▒▒█████'
      '▒███    █████ ▒███ ▒███    █████ ▒███▒▒▒▒▒███ ▒███   ▒███  ▒███    ▒███ ▒███    ▒███   ▒▒███'
      '▒▒███  ▒▒███  ▒███ ▒▒███  ▒▒███  ▒███    ▒███ ▒███   ▒███  ▒███    ███  ▒███    ███     ▒███'
      ' ▒▒█████████  █████ ▒▒█████████  ███████████  ▒▒████████   ██████████   ██████████      █████'
      '  ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒▒▒▒▒▒▒    ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒      ▒▒▒▒▒'
    )
  elif (( s_lines >= 10 && s_cols > 28 )); then
    static_lines=(
      '▞▀▖▜▘▞▀▖▛▀▖▌ ▌▛▀▖▛▀▖▌ ▌'
      '▌▄▖▐ ▌▄▖▙▄▘▌ ▌▌ ▌▌ ▌▝▞'
      '▌ ▌▐ ▌ ▌▌ ▌▌ ▌▌ ▌▌ ▌ ▌'
      '▝▀ ▀▘▝▀ ▀▀ ▝▀ ▀▀ ▀▀  ▘'
    )
  elif (( s_lines >= 8 && s_cols > 22 )); then
    static_lines=(
      '▄▖▄▖▄▖▄ ▖▖▄ ▄ ▖▖'
      '▌ ▐ ▌ ▙▘▌▌▌▌▌▌▌▌'
      '▙▌▟▖▙▌▙▘▙▌▙▘▙▘▐'
    )
  fi
  if (( ${#static_lines[@]} >= 3 )); then
    print_static_banner "${static_lines[@]}"
    print_saved_status
  elif [[ "$screen_controlled" == 1 ]]; then
    # The terminal was cleared, but its dimensions cannot fit a banner.
    # Restore the pre-banner steps instead of leaving a blank screen.
    print_saved_status
  fi
}

# 静态金色 banner：256 色金色（不支持 truecolor 的终端也正确显示）
print_static_banner() {
  # 静态色取动画波浪亮金的略深一档（220,170,80）：
  # truecolor 终端用 24bit，否则 256 色最接近（178 ≈ 215,175,95）。
  local line color
  if [[ "${COLORTERM:-}" == "truecolor" || "${COLORTERM:-}" == "24bit" \
        || "$TERM" == *"truecolor"* || "$TERM" == *"direct"* ]]; then
    color='38;2;220;170;80'
  else
    color='38;5;178'
  fi
  for line in "$@"; do
    printf '\033[%sm%s\033[0m\n' "$color" "$line"
  done
  printf '\n\n\n'
}

INSTALL_LOG="$(mktemp -t gigbuddy-install.XXXXXX)"
COMMAND_LOG="$(mktemp -t gigbuddy-command.XXXXXX)"
STATUS_FILE="$(mktemp -t gigbuddy-status.XXXXXX)"

cleanup() {
  stop_banner
  rm -f "$INSTALL_LOG" "$COMMAND_LOG" "$STATUS_FILE"
}

on_exit() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "$status" != 0 && "$ROLLBACK_ARMED" == 1 ]]; then
    if ! rollback_once; then
      printf 'GigBuddy rollback was incomplete; review the recovery error above.\n' >&2
    fi
  fi
  cleanup
  exit "$status"
}

trap on_exit EXIT
# 交互安装时询问安装位置（Agent 场景：装到自己的项目文件夹，让 .claude/skills
# 在项目内可用）。已显式设置 GIGBUDDY_HOME 或非交互（curl | bash）时跳过。
# 注意：必须在 start_banner 之前询问——banner 动画每 0.1s 重绘终端，会覆盖
# 提示文字，read 将无提示地阻塞（表现为“卡在依赖确认”）。
if [[ "$SOURCE_CHECKOUT" != 1 && -t 0 && -z "${GIGBUDDY_HOME:-}" ]]; then
  printf 'Install location [Enter = %s]\n' "$INSTALL_ROOT"
  printf '  "." = current directory, or type any path: '
  read -r -p '' install_answer || true
  case "${install_answer:-}" in
    ""|"") ;;
    ".") INSTALL_ROOT="$(pwd)" ;;
    *) INSTALL_ROOT="$install_answer" ;;
  esac
  # 展开 ~ 与相对路径
  case "$INSTALL_ROOT" in
    "~"/*) INSTALL_ROOT="${HOME}${INSTALL_ROOT#\~}" ;;
    /*) ;;
    *) INSTALL_ROOT="$(cd "$(dirname "$INSTALL_ROOT")" 2>/dev/null && pwd)/$(basename "$INSTALL_ROOT")" ;;
  esac
fi

canonicalize_future_directory() {
  local candidate="${1%/}" probe parent component suffix="" base
  [[ -n "$candidate" ]] || candidate="/"
  if [[ -d "$candidate" ]]; then
    (cd -P -- "$candidate" 2>/dev/null && pwd -P)
    return
  fi
  [[ ! -e "$candidate" && ! -L "$candidate" ]] || return 1
  probe="$candidate"
  while [[ ! -d "$probe" ]]; do
    [[ ! -e "$probe" && ! -L "$probe" ]] || return 1
    component="$(basename -- "$probe")"
    case "$component" in
      ""|.|..) return 1 ;;
    esac
    suffix="/$component$suffix"
    parent="$(dirname -- "$probe")"
    [[ "$parent" != "$probe" ]] || return 1
    probe="$parent"
  done
  base="$(cd -P -- "$probe" 2>/dev/null && pwd -P)" || return 1
  printf '%s%s\n' "$base" "$suffix"
}

configure_external_data_root() {
  local link="$INSTALL_ROOT/data"
  local target requested_data_root install_root_real
  if [[ -z "$DATA_ROOT" && "$SOURCE_CHECKOUT" != 1 ]]; then
    if [[ -L "$link" ]]; then
      target="$(readlink "$link")" || \
        die "cannot read existing data link: $link"
      case "$target" in
        /*) ;;
        *) target="$(dirname -- "$link")/$target" ;;
      esac
      [[ -d "$target" ]] || \
        die "existing data link target is not a directory: $target"
      DATA_ROOT="$(cd -P -- "$target" && pwd -P)" || \
        die "cannot resolve existing data link: $link"
    else
      DATA_ROOT="${INSTALL_ROOT}-data"
    fi
  fi
  [[ -n "$DATA_ROOT" ]] || return 0
  case "$DATA_ROOT" in
    "~") DATA_ROOT="$USER_HOME" ;;
    "~"/*) DATA_ROOT="${USER_HOME}${DATA_ROOT#\~}" ;;
    /*) ;;
    *) DATA_ROOT="$(pwd)/$DATA_ROOT" ;;
  esac
  requested_data_root="$DATA_ROOT"
  DATA_ROOT="$(canonicalize_future_directory "$requested_data_root")" || \
    die "cannot safely resolve data path: $requested_data_root"
  install_root_real="$(cd -P -- "$INSTALL_ROOT" && pwd -P)" || \
    die "cannot resolve the GigBuddy install path: $INSTALL_ROOT"
  [[ -n "$DATA_ROOT" && "$DATA_ROOT" != "$install_root_real" \
     && "$DATA_ROOT" != "/" && "$DATA_ROOT" != "$USER_HOME" \
     && "$DATA_ROOT" != "$USER_HOME/.local" \
     && "$DATA_ROOT" != "$USER_HOME/.local/share" ]] || \
    die "data path must be separate from the install path"
  case "$DATA_ROOT/" in
    "$install_root_real"/*) die "data path must be outside the install path" ;;
  esac
  case "$install_root_real/" in
    "$DATA_ROOT"/*) die "install path must be outside the data path" ;;
  esac
  if [[ -L "$link" ]]; then
    target="$(readlink "$link")"
    case "$target" in
      /*) ;;
      *) target="$(dirname -- "$link")/$target" ;;
    esac
    if [[ -d "$target" ]]; then
      target="$(cd -P -- "$target" && pwd -P)" || \
        die "cannot resolve existing data link: $link"
    fi
    [[ "$target" == "$DATA_ROOT" ]] || \
      die "existing data link points to a different path: $target"
    return 0
  fi
  if [[ -e "$DATA_ROOT" && ! -d "$DATA_ROOT" ]]; then
    die "data path exists but is not a directory: $DATA_ROOT"
  fi
  if [[ -d "$link" ]]; then
    mkdir -p "$(dirname "$DATA_ROOT")"
    if [[ -d "$DATA_ROOT" ]] && ! rmdir "$DATA_ROOT" 2>/dev/null; then
      die "both install and external data directories contain files; refusing to merge"
    fi
    if ! mv "$link" "$DATA_ROOT"; then
      die "could not migrate existing data to $DATA_ROOT"
    fi
    DATA_ROOT_MIGRATED=1
    DATA_LINK_CREATED=1
    {
      printf 'DATA_ROOT=%q\n' "$DATA_ROOT"
      printf 'DATA_ROOT_MIGRATED=1\n'
      printf 'DATA_LINK_CREATED=1\n'
    } >> "$ROLLBACK_FILE"
    if ! ln -s "$DATA_ROOT" "$link"; then
      if ! mv "$DATA_ROOT" "$link"; then
        printf 'Could not restore data directory to %s\n' "$link" >&2
      fi
      DATA_ROOT_MIGRATED=0
      DATA_LINK_CREATED=0
      {
        printf 'DATA_ROOT_MIGRATED=0\n'
        printf 'DATA_LINK_CREATED=0\n'
      } >> "$ROLLBACK_FILE"
      die "could not link the migrated data directory"
    fi
    return 0
  fi
  if [[ -e "$link" ]]; then
    die "install data path exists but is not a directory"
  fi
  if [[ ! -d "$DATA_ROOT" ]]; then
    mkdir -p "$DATA_ROOT"
    DATA_ROOT_CREATED=1
    {
      printf 'DATA_ROOT=%q\n' "$DATA_ROOT"
      printf 'DATA_ROOT_CREATED=1\n'
    } >> "$ROLLBACK_FILE"
  fi
  if ! ln -s "$DATA_ROOT" "$link"; then
    die "could not link data directory $DATA_ROOT"
  fi
  DATA_LINK_CREATED=1
  printf 'DATA_LINK_CREATED=1\n' >> "$ROLLBACK_FILE"
}

run_quiet() {
  local status
  if [[ "${GIGBUDDY_VERBOSE:-0}" == "1" ]]; then
    if "$@"; then
      return
    else
      status=$?
    fi
    printf 'GigBuddy install failed while running (exit %d):' "$status" >&2
    printf ' %q' "$@" >&2
    printf '\n' >&2
    die "command exited with status $status"
  fi
  : >"$COMMAND_LOG"
  if "$@" >"$COMMAND_LOG" 2>&1; then
    return
  else
    status=$?
  fi
  printf 'GigBuddy install failed while running (exit %d):' "$status" >&2
  printf ' %q' "$@" >&2
  printf '\n' >&2
  printf '%s\n' '----- command output -----' >&2
  if [[ -s "$COMMAND_LOG" ]]; then
    cat "$COMMAND_LOG" >&2
  else
    printf '%s\n' '(command produced no output)' >&2
  fi
  printf '%s\n' '----- end command output -----' >&2
  die "command exited with status $status"
}

database_backup_python() {
  local candidate
  for candidate in \
      "${GIGBUDDY_DB_BACKUP_PYTHON:-}" \
      "$INSTALL_ROOT/.venv/bin/python" \
      "$LOGIN_PYTHON" \
      "$PYTHON_BIN" \
      python3; do
    [[ -n "$candidate" ]] || continue
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

create_database_backup() {
  local python="$1" database="$2" backup="$3"
  "$python" - "$database" "$backup" <<'PY'
import os
import sqlite3
import sys
from pathlib import Path

source_path = Path(sys.argv[1]).resolve()
backup_path = Path(sys.argv[2]).resolve()
source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
destination = sqlite3.connect(backup_path)
try:
    source.backup(destination)
    destination.commit()
finally:
    destination.close()
    source.close()
with backup_path.open("rb") as snapshot:
    os.fsync(snapshot.fileno())
PY
}

restore_database_backup() {
  local python="$1" backup="$2" database="$3"
  "$python" - "$backup" "$database" <<'PY'
import os
import shutil
import sys
from pathlib import Path

backup = Path(sys.argv[1]).resolve()
database = Path(sys.argv[2]).resolve()
database.parent.mkdir(parents=True, exist_ok=True)
temporary = database.with_name(f".{database.name}.restore-{os.getpid()}")
try:
    with backup.open("rb") as source, temporary.open("xb") as destination:
        shutil.copyfileobj(source, destination)
        destination.flush()
        os.fsync(destination.fileno())
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    os.replace(temporary, database)
    directory_fd = os.open(database.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    temporary.unlink(missing_ok=True)
PY
}

prepare_database_rollback() {
  local database="$INSTALL_ROOT/data/gigbuddy.db"
  [[ "$DATABASE_ROLLBACK_PREPARED" != 1 ]] || return 0
  DB_ROLLBACK_PATH="$database"
  if [[ ! -f "$database" ]]; then
    DATABASE_ROLLBACK_PREPARED=1
    {
      printf 'ORIGINAL_DB_PRESENT=0\n'
      printf 'DB_ROLLBACK_PATH=%q\n' "$DB_ROLLBACK_PATH"
      printf 'DATABASE_ROLLBACK_PREPARED=1\n'
    } >> "$ROLLBACK_FILE"
    return 0
  fi
  if ! DB_BACKUP_PYTHON="$(database_backup_python)"; then
    printf 'Python is required to snapshot the existing GigBuddy database.\n' >&2
    return 1
  fi
  DB_BACKUP_FILE="$(mktemp -t gigbuddy-database.XXXXXX)" || return
  if ! create_database_backup \
      "$DB_BACKUP_PYTHON" "$database" "$DB_BACKUP_FILE"; then
    rm -f -- "$DB_BACKUP_FILE"
    DB_BACKUP_FILE=""
    return 1
  fi
  {
    printf 'ORIGINAL_DB_PRESENT=1\n'
    printf 'DB_BACKUP_FILE=%q\n' "$DB_BACKUP_FILE"
    printf 'DB_BACKUP_PYTHON=%q\n' "$DB_BACKUP_PYTHON"
    printf 'DB_ROLLBACK_PATH=%q\n' "$DB_ROLLBACK_PATH"
    printf 'DATABASE_ROLLBACK_PREPARED=1\n'
  } >> "$ROLLBACK_FILE"
  DATABASE_ROLLBACK_PREPARED=1
}

discard_rollback_state() {
  if [[ -n "$DB_BACKUP_FILE" ]]; then
    rm -f -- "$DB_BACKUP_FILE"
  fi
  if [[ -n "$EIGEN_BACKUP_DIR" ]]; then
    rm -rf -- "$EIGEN_BACKUP_DIR"
  fi
  EIGEN_BACKUP_DIR=""
  EIGEN_ROLLBACK_TARGET=""
  EIGEN_ORIGINAL_PRESENT=0
  rm -f -- "$ROLLBACK_FILE"
}

install_uv() {
  curl -LsSf https://astral.sh/uv/install.sh | sh
}

install_eigen_340() {
  local modules_dir="$1"
  local eigen_dir="$2"
  local staging archive extracted backup_root backup status=0
  if [[ -z "$modules_dir" || -z "$eigen_dir" \
        || "$(basename "$eigen_dir")" != "Eigen" \
        || "$eigen_dir" != "$modules_dir/"* ]]; then
    printf 'Refusing unsafe Eigen destination: %s\n' "$eigen_dir" >&2
    return 2
  fi
  staging="$(mktemp -d "$modules_dir/.gigbuddy-eigen.XXXXXX")" || return
  archive="$staging/eigen-3.4.0.tar.gz"
  extracted="$staging/eigen-3.4.0"
  if ! curl -sSL -o "$archive" \
      https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz; then
    status=1
  elif ! tar xzf "$archive" -C "$staging"; then
    status=1
  elif [[ ! -d "$extracted" ]]; then
    printf 'Eigen archive did not contain eigen-3.4.0\n' >&2
    status=1
  else
    if [[ -e "$eigen_dir" ]]; then
      if ! backup_root="$(mktemp -d -t gigbuddy-eigen-backup.XXXXXX)"; then
        status=1
      else
        backup="$backup_root/Eigen"
        if ! mv -- "$eigen_dir" "$backup"; then
          rm -rf -- "$backup_root"
          backup_root=""
          status=1
        fi
      fi
    fi
    if [[ "$status" == 0 ]] && ! mv -- "$extracted" "$eigen_dir"; then
      status=1
      if [[ -n "${backup:-}" && -e "$backup" ]]; then
        mv -- "$backup" "$eigen_dir" || true
        rm -rf -- "$backup_root"
      fi
    elif [[ "$status" == 0 ]]; then
      EIGEN_ROLLBACK_TARGET="$eigen_dir"
      EIGEN_ORIGINAL_PRESENT=0
      if [[ -n "${backup_root:-}" ]]; then
        EIGEN_BACKUP_DIR="$backup_root"
        EIGEN_ORIGINAL_PRESENT=1
      fi
      if ! {
        printf 'EIGEN_ROLLBACK_TARGET=%q\n' "$EIGEN_ROLLBACK_TARGET"
        printf 'EIGEN_BACKUP_DIR=%q\n' "$EIGEN_BACKUP_DIR"
        printf 'EIGEN_ORIGINAL_PRESENT=%s\n' "$EIGEN_ORIGINAL_PRESENT"
      } >> "$ROLLBACK_FILE"; then
        if [[ "$EIGEN_ORIGINAL_PRESENT" == 1 ]]; then
          rm -rf -- "$eigen_dir"
          mv -- "$backup" "$eigen_dir" || true
          rm -rf -- "$backup_root"
        else
          rm -rf -- "$eigen_dir"
        fi
        EIGEN_BACKUP_DIR=""
        EIGEN_ROLLBACK_TARGET=""
        EIGEN_ORIGINAL_PRESENT=0
        status=1
      fi
    fi
  fi
  rm -rf -- "$staging"
  return "$status"
}

restore_eigen_backup() {
  local target="${EIGEN_ROLLBACK_TARGET:-}"
  local backup_dir="${EIGEN_BACKUP_DIR:-}"
  [[ -n "$target" ]] || return 0
  if [[ "${EIGEN_ORIGINAL_PRESENT:-0}" == 1 ]]; then
    if [[ -z "$backup_dir" || ! -d "$backup_dir/Eigen" ]]; then
      printf 'Eigen rollback backup is unavailable.\n' >&2
      return 1
    fi
    rm -rf -- "$target" || return 1
    if ! mv -- "$backup_dir/Eigen" "$target"; then
      printf 'Could not restore the previous Eigen directory.\n' >&2
      return 1
    fi
  else
    rm -rf -- "$target" || return 1
  fi
  if [[ -n "$backup_dir" ]]; then
    rm -rf -- "$backup_dir"
  fi
  EIGEN_BACKUP_DIR=""
  EIGEN_ROLLBACK_TARGET=""
  EIGEN_ORIGINAL_PRESENT=0
  return 0
}

build_portaudio_1970() {
  local prefix="$1"
  local staging archive source_dir jobs status=0
  [[ -n "$prefix" ]] || return 2
  staging="$(mktemp -d -t gigbuddy-portaudio.XXXXXX)" || return
  archive="$staging/portaudio-v19.7.0.tgz"
  source_dir="$staging/portaudio-19.7.0"
  jobs="$(sysctl -n hw.ncpu 2>/dev/null || printf '4')"
  [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || jobs=4
  if ! curl -L -o "$archive" \
      https://codeload.github.com/PortAudio/portaudio/tar.gz/refs/tags/v19.7.0; then
    status=1
  elif ! tar xzf "$archive" -C "$staging"; then
    status=1
  elif [[ ! -d "$source_dir" ]]; then
    printf 'PortAudio archive did not contain portaudio-19.7.0\n' >&2
    status=1
  elif ! (
    cd "$source_dir" &&
    env CFLAGS='-Wno-implicit-const-int-float-conversion' \
      ./configure --prefix="$prefix" --disable-mac-universal \
        --disable-silent-rules &&
    make -j"$jobs" &&
    make install
  ); then
    status=1
  fi
  rm -rf -- "$staging"
  return "$status"
}

announce_visible_step() {
  local message="$1"
  local had_banner=0
  [[ -n "${BANNER_PID:-}" ]] && had_banner=1
  step "$message"
  if [[ "$had_banner" == 1 ]]; then
    stop_banner
    printf '==> %s\n' "$message"
  fi
}

confirm_install_start() {
  local answer=""
  printf '\n'
  printf 'Login checks are complete. GigBuddy is ready to install.\n'
  printf 'Continue with the installation? [Y/n] '

  if [[ -t 0 ]]; then
    if ! IFS= read -r answer; then
      printf '\nCould not read the installation confirmation.\n' >&2
      return "$CONFIRM_READ_FAILED"
    fi
  else
    if ! { exec 3</dev/tty; } 2>/dev/null; then
      return "$CONFIRM_NO_TTY"
    fi
    if ! IFS= read -r answer <&3; then
      exec 3<&-
      printf '\nCould not read the installation confirmation.\n' >&2
      return "$CONFIRM_READ_FAILED"
    fi
    exec 3<&-
  fi

  case "${answer:-}" in
    n|N|no|NO|No|nO)
      return "$CONFIRM_DECLINED"
      ;;
  esac
  return 0
}

cancel_install() {
  stop_banner
  rollback_once
  printf 'GigBuddy installation cancelled.\n'
  exit 0
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

[[ -n "$USER_HOME" ]] || die "HOME is not set"
if [[ "$NO_ENGINE" == 0 && "$(uname -s)" != "Darwin" ]]; then
  die "the full installer currently supports macOS only"
fi

if [[ "$SKIP_PRESETS" != 1 ]]; then
  command -v "$LOGIN_PYTHON" >/dev/null 2>&1 || \
    die "Python 3.11+ is required for TONE3000 login: $LOGIN_PYTHON"
  "$LOGIN_PYTHON" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required")
PY
fi

if [[ "$SOURCE_CHECKOUT" == 1 ]]; then
  [[ -d "$INSTALL_ROOT/.git" && -f "$INSTALL_ROOT/pyproject.toml" ]] || \
    die "source install path is not a GigBuddy checkout: $INSTALL_ROOT"
  printf 'WAS_NEW_CLONE=0\n' > "$ROLLBACK_FILE"
  ROLLBACK_ARMED=1
else
  if [[ -e "$INSTALL_ROOT" && ! -d "$INSTALL_ROOT/.git" ]]; then
    die "install path exists but is not a GigBuddy checkout: $INSTALL_ROOT"
  fi
  if [[ -d "$INSTALL_ROOT/.git" ]] && ! is_gigbuddy_checkout; then
    die "install path is not a GigBuddy checkout: $INSTALL_ROOT"
  fi

  # 记录安装前状态，供失败回滚使用（rollback_install）
  if [[ -d "$INSTALL_ROOT/.git" ]]; then
    if ! PREV_HEAD="$(git -C "$INSTALL_ROOT" rev-parse HEAD 2>/dev/null)" \
        || [[ -z "$PREV_HEAD" ]]; then
      die "could not identify the existing GigBuddy revision"
    fi
    printf 'WAS_NEW_CLONE=0\nPREV_HEAD=%s\n' "$PREV_HEAD" > "$ROLLBACK_FILE"
  else
    printf 'WAS_NEW_CLONE=1\n' > "$ROLLBACK_FILE"
  fi

  # Capture ownership before the first fallible operation on an existing
  # checkout. In particular, fetch failure must not make rollback mistake old
  # runtime/data directories for artifacts created by this install attempt.
  {
    printf 'HAD_VENV=%s\n' "$([[ -d "$INSTALL_ROOT/.venv" ]] && printf 1 || printf 0)"
    printf 'HAD_DATA=%s\n' "$([[ -d "$INSTALL_ROOT/data" ]] && printf 1 || printf 0)"
    printf 'HAD_THIRDPARTY=%s\n' "$([[ -d "$INSTALL_ROOT/third_party" ]] && printf 1 || printf 0)"
  } >> "$ROLLBACK_FILE"
  ROLLBACK_ARMED=1

  if [[ -d "$INSTALL_ROOT/.git" ]]; then
    if ! prepare_database_rollback; then
      die "could not snapshot the existing GigBuddy database"
    fi
    step "Updating GigBuddy to $REPO_REF"
    # Old shallow tag clones can retain a fetch refspec for a tag no longer
    # published by the remote. An explicit wildcard refspec avoids inheriting it.
    run_quiet git -C "$INSTALL_ROOT" fetch --quiet --force origin "+refs/tags/*:refs/tags/*"
    run_quiet git -C "$INSTALL_ROOT" checkout --quiet --detach "$REPO_REF"
  else
    step "Downloading GigBuddy $REPO_REF"
    mkdir -p "$(dirname "$INSTALL_ROOT")"
    run_quiet git clone --quiet --depth 1 --branch "$REPO_REF" "$REPO_URL" "$INSTALL_ROOT"
  fi

  printf 'GigBuddy\n' > "$INSTALL_ROOT/.gigbuddy-install"
fi

configure_external_data_root
if [[ "$SOURCE_CHECKOUT" != 1 \
      && "$DATABASE_ROLLBACK_PREPARED" != 1 ]]; then
  if ! prepare_database_rollback; then
    die "could not snapshot the existing GigBuddy database"
  fi
fi

bootstrap_args=()
if [[ "$SKIP_PRESETS" == 1 ]]; then
  bootstrap_args+=(--skip-presets)
fi
if [[ "$SKIP_DRY_INPUTS" == 1 ]]; then
  bootstrap_args+=(--skip-dry-inputs)
else
  bootstrap_args+=(--dry-inputs "$DRY_INPUTS")
fi

if [[ "$SKIP_PRESETS" != 1 ]]; then
  announce_visible_step "Checking TONE3000 login"
  login_status=0
  if env PYTHONPATH="$INSTALL_ROOT/src" \
      "$LOGIN_PYTHON" \
      "$INSTALL_ROOT/scripts/ensure_tone3000_login.py"; then
    step "TONE3000 login ready"
  else
    login_status=$?
    if [[ "$login_status" == 10 ]]; then
      SKIP_PRESETS=1
      bootstrap_args+=(--skip-presets)
      step "Skipping remote model preparation"
    else
      die "TONE3000 login is required; pass --skip-presets explicitly to skip the install-time login and Preset registration"
    fi
  fi
fi

if [[ "$SOURCE_CHECKOUT" != 1 ]]; then
  confirm_status=0
  if confirm_install_start; then
    :
  else
    confirm_status=$?
    if [[ "$confirm_status" == "$CONFIRM_DECLINED" ]]; then
      cancel_install
    fi
    if [[ "$confirm_status" == "$CONFIRM_NO_TTY" \
          && "$SKIP_PRESETS_EXPLICIT" == 1 ]]; then
      printf '\nNo interactive terminal is available; continuing because --skip-presets was explicitly provided.\n'
    else
      if [[ "$confirm_status" == "$CONFIRM_NO_TTY" ]]; then
        printf '\nNo interactive terminal is available for the installation confirmation.\n' >&2
        printf 'Run the installer from a terminal and try again.\n' >&2
      fi
      die "interactive confirmation is required before installation"
    fi
  fi
fi

# All user decisions are complete before the animated banner starts. In
# particular, the prompt/browser flow must never be covered by the banner.
if [[ "$SOURCE_CHECKOUT" != 1 ]]; then
  start_banner
fi

step "Creating Python environment (uv)"
UV_BIN="${GIGBUDDY_UV:-uv}"
if ! command -v "$UV_BIN" >/dev/null 2>&1; then
  step "Installing uv (Python manager)"
  run_quiet install_uv
  export PATH="$HOME/.local/bin:$PATH"
  UV_BIN="uv"
fi
if [[ ! -x "$INSTALL_ROOT/.venv/bin/python" ]]; then
  run_quiet "$UV_BIN" venv --python 3.12 "$INSTALL_ROOT/.venv"
fi

step "Installing Python dependencies"
run_quiet "$UV_BIN" pip install --python "$INSTALL_ROOT/.venv/bin/python" \
  -r "$INSTALL_ROOT/requirements.txt"

if [[ "$SKIP_PRESETS" == 1 && "$SKIP_DRY_INPUTS" == 1 ]]; then
  step "Preparing local GigBuddy data"
elif [[ "$SKIP_PRESETS" == 1 ]]; then
  step "Downloading dry inputs (this can take a while; please be patient)"
elif [[ "$SKIP_DRY_INPUTS" == 1 ]]; then
  step "Registering built-in presets"
else
  step "Registering built-in presets and downloading dry inputs (this can take a while; please be patient)"
fi
run_quiet env PYTHONPATH="$INSTALL_ROOT/src" \
  "$INSTALL_ROOT/.venv/bin/python" "$INSTALL_ROOT/scripts/bootstrap.py" \
  "${bootstrap_args[@]}"

# 引擎依赖与编译由这个共享实现唯一维护。
if [[ "$NO_ENGINE" == 0 && -d "$INSTALL_ROOT/cpp" ]]; then
  if [[ "$SOURCE_CHECKOUT" != 1 ]]; then
    start_banner
  fi
  if [[ ! -d "$INSTALL_ROOT/third_party/NeuralAudio" ]]; then
    step "Fetching engine dependencies (NeuralAudio + NAM)"
    run_quiet git clone --recurse-submodules \
      https://github.com/mikeoliphant/NeuralAudio \
      "$INSTALL_ROOT/third_party/NeuralAudio"
    run_quiet git -C "$INSTALL_ROOT/third_party/NeuralAudio" checkout --quiet 49100f9
    # Eigen 版本坑自动化：RTNeural vendored Eigen 指向 master（3.5 pre），
    # 与 NAM Core 不兼容（缺 unsupported/Eigen/FFT + placeholders::lastN）。
    EIGEN_DIR="$INSTALL_ROOT/third_party/NeuralAudio/deps/RTNeural/modules/Eigen"
    if [[ ! -f "$EIGEN_DIR/unsupported/Eigen/FFT" ]]; then
      echo "Patching Eigen: RTNeural vendors 3.5-pre (incompatible); using 3.4.0"
      run_quiet install_eigen_340 \
        "$INSTALL_ROOT/third_party/NeuralAudio/deps/RTNeural/modules" \
        "$EIGEN_DIR"
      for header in LSTM.h LSTMDynamic.h; do
        run_quiet sed -i '' \
          's/Eigen::placeholders::lastN/Eigen::lastN/g' \
          "$INSTALL_ROOT/third_party/NeuralAudio/NeuralAudio/$header"
      done
    fi
  fi

  # PortAudio：固定官方稳定版 v19.7.0 源码编译到本地（不依赖 Homebrew）
  PA_DIR="$INSTALL_ROOT/.local"
  if [[ ! -f "$PA_DIR/lib/libportaudio.2.dylib" ]]; then
    step "Building PortAudio 19.7.0 from source"
    run_quiet build_portaudio_1970 "$PA_DIR"
  fi

  step "Building the NAM engine (this can take a while; please be patient)"
  run_quiet "$INSTALL_ROOT/cpp/build.sh"
elif [[ "$NO_ENGINE" == 1 ]]; then
  step "Skipping engine build; launch the TUI with --no-engine"
fi

if [[ "$SOURCE_CHECKOUT" != 1 ]]; then
  step "Linking commands"
  mkdir -p "$BIN_DIR"
  # bin/gigbuddy 是相对仓库根的 bash 包装脚本，不能直接 symlink（BASH_SOURCE
  # 会解析到链接位置）；生成指向安装目录的 wrapper。
  write_wrapper() {
    local command_name="$1" exec_line="$2"
    local link="$BIN_DIR/$command_name"
    local install_root_real target target_name
    if [[ -L "$link" ]]; then
      target="$(resolve_physical_path "$link")" || \
        die "refusing to replace an existing command: $link"
      target_name="$(basename -- "$target")"
      install_root_real="$(resolve_physical_path "$INSTALL_ROOT")" || \
        die "cannot resolve the GigBuddy install path: $INSTALL_ROOT"
      if [[ "$target" != "$install_root_real/"* \
            || "$target_name" != "$command_name" ]]; then
        die "refusing to replace an existing command: $link"
      fi
      rm -f -- "$link" || die "could not remove legacy command link: $link"
    elif [[ -e "$link" ]]; then
      generated_wrapper_points_into_install "$link" "$command_name" || \
        die "refusing to replace an existing command: $link"
    fi
    printf '#!/usr/bin/env bash\nexec %s\n' "$exec_line" > "$link"
    chmod +x "$link"
  }
  write_wrapper gigbuddy "\"$INSTALL_ROOT/bin/gigbuddy\" \"\$@\""
fi

# 安装成功：不再需要回滚状态
discard_rollback_state
ROLLBACK_ARMED=0
stop_banner
if [[ "$SOURCE_CHECKOUT" == 1 ]]; then
  printf '\nGigBuddy is installed. Try:\n'
  printf '  %s/.venv/bin/python -m tui --no-engine\n' "$INSTALL_ROOT"
  if [[ -x "$INSTALL_ROOT/bin/realtime_cli" ]]; then
    printf '  %s/.venv/bin/python -m tui\n' "$INSTALL_ROOT"
  fi
else
  printf '\nGigBuddy ready\n'
  printf '  %s/gigbuddy\n' "$BIN_DIR"
  printf '  install: %s\n' "$INSTALL_ROOT"
fi
printf 'Tip: use a truecolor terminal (iTerm2 / Kitty / WezTerm / Alacritty /\n'
printf 'Warp / Ghostty) for the full guitar-amp theme; macOS Terminal.app\n'
printf 'is 256-color and falls back to the compatible theme automatically.\n'
printf 'Force the full theme with: TEXTUAL_COLOR_SYSTEM=truecolor gigbuddy\n'
if [[ "$SOURCE_CHECKOUT" != 1 && ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  printf 'Add to PATH if needed: %s\n' "$BIN_DIR"
fi
