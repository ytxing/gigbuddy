#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${GIGBUDDY_REPO_URL:-https://github.com/ytxing/gigbuddy.git}"
REPO_REF="${GIGBUDDY_REF:-v1.0.0}"
USER_HOME="${HOME:-}"
INSTALL_ROOT="${GIGBUDDY_HOME:-${USER_HOME}/.local/share/gigbuddy}"
BIN_DIR="${GIGBUDDY_BIN_DIR:-${USER_HOME}/.local/bin}"
PYTHON_BIN="${GIGBUDDY_PYTHON:-python3}"

die() {
  stop_banner
  printf 'GigBuddy install failed: %s\n' "$*" >&2
  exit 1
}

step() {
  printf '==> %s\n' "$1" >> "${INSTALL_LOG:?}"
  if [[ -n "${BANNER_PID:-}" ]]; then
    printf '==> %s\n' "$1" >>"${STATUS_FILE:?}"
  else
    printf '==> %s\n' "$1"
  fi
}

BANNER_PID=""

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

start_banner() {
  if [[ -t 1 && "${GIGBUDDY_VERBOSE:-0}" != "1" ]] && command -v python3 >/dev/null 2>&1; then
    # 动画进程独占终端；安装主流程只通过状态文件更新步骤文字。
    printf '\033[2J\033[H'
    local lines cols tty_size
    tty_size=$(stty size </dev/tty 2>/dev/null || true)
    read -r lines cols <<<"$tty_size"
    if [[ ! "$lines" =~ ^[1-9][0-9]*$ || ! "$cols" =~ ^[1-9][0-9]*$ ]]; then
      lines=$(tput lines 2>/dev/null || printf '24')
      cols=$(tput cols 2>/dev/null || printf '80')
    fi
    STATUS_ROW=9
    # 动画 8 行 + 状态区至少 8 行；横幅 96 列，终端须更宽避免行尾折行。
    if (( lines >= STATUS_ROW + 8 && cols > 96 )); then
      export GB_BANNER_ROW="$STATUS_ROW"
      export GB_STATUS_FILE="$STATUS_FILE"
    # 霓虹灯呼吸：字符不动，横幅上均匀分布 3 个明暗浪，金色系内流动，
    # 后台持续播放，直到安装流程调用 stop_banner。
    python3 - <<'PY' &
import math, os, signal, sys, time

LINES = (
  '   █████████  █████   █████████  ███████████  █████  █████ ██████████   ██████████   █████ █████',
  '  ███▒▒▒▒▒███▒▒███   ███▒▒▒▒▒███▒▒███▒▒▒▒▒███▒▒███  ▒▒███ ▒▒███▒▒▒▒███ ▒▒███▒▒▒▒███ ▒▒███ ▒▒███',
  ' ███     ▒▒▒  ▒███  ███     ▒▒▒  ▒███    ▒███ ▒███   ▒███  ▒███   ▒▒███ ▒███   ▒▒███ ▒▒███ ███',
  '▒███          ▒███ ▒███          ▒██████████  ▒███   ▒███  ▒███    ▒███ ▒███    ▒███  ▒▒█████',
  '▒███    █████ ▒███ ▒███    █████ ▒███▒▒▒▒▒███ ▒███   ▒███  ▒███    ▒███ ▒███    ▒███   ▒▒███',
  '▒▒███  ▒▒███  ▒███ ▒▒███  ▒▒███  ▒███    ▒███ ▒███   ▒███  ▒███    ███  ▒███    ███     ▒███',
  ' ▒▒█████████  █████ ▒▒█████████  ███████████  ▒▒████████   ██████████   ██████████      █████',
  '  ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒▒▒▒▒▒▒    ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒      ▒▒▒▒▒',
)
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
    return
  fi
  fi
  # 非交互终端、没有 python3、或终端太矮：静态金色版
    printf '\033[38;2;184;134;11m%s\033[0m\n' \
      '   █████████  █████   █████████  ███████████  █████  █████ ██████████   ██████████   █████ █████'
    printf '\033[38;2;184;134;11m%s\033[0m\n' \
      '  ███▒▒▒▒▒███▒▒███   ███▒▒▒▒▒███▒▒███▒▒▒▒▒███▒▒███  ▒▒███ ▒▒███▒▒▒▒███ ▒▒███▒▒▒▒███ ▒▒███ ▒▒███'
    printf '\033[38;2;184;134;11m%s\033[0m\n' \
      ' ███     ▒▒▒  ▒███  ███     ▒▒▒  ▒███    ▒███ ▒███   ▒███  ▒███   ▒▒███ ▒███   ▒▒███ ▒▒███ ███'
    printf '\033[38;2;184;134;11m%s\033[0m\n' \
      '▒███          ▒███ ▒███          ▒██████████  ▒███   ▒███  ▒███    ▒███ ▒███    ▒███  ▒▒█████'
    printf '\033[38;2;184;134;11m%s\033[0m\n' \
      '▒███    █████ ▒███ ▒███    █████ ▒███▒▒▒▒▒███ ▒███   ▒███  ▒███    ▒███ ▒███    ▒███   ▒▒███'
    printf '\033[38;2;184;134;11m%s\033[0m\n' \
      '▒▒███  ▒▒███  ▒███ ▒▒███  ▒▒███  ▒███    ▒███ ▒███   ▒███  ▒███    ███  ▒███    ███     ▒███'
    printf '\033[38;2;184;134;11m%s\033[0m\n' \
      ' ▒▒█████████  █████ ▒▒█████████  ███████████  ▒▒████████   ██████████   ██████████      █████'
    printf '\033[38;2;184;134;11m%s\033[0m\n' \
      '  ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒▒▒▒▒▒▒    ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒      ▒▒▒▒▒'
    printf '\n\n\n'
}

INSTALL_LOG="$(mktemp -t gigbuddy-install.XXXXXX)"
COMMAND_LOG="$(mktemp -t gigbuddy-command.XXXXXX)"
STATUS_FILE="$(mktemp -t gigbuddy-status.XXXXXX)"

cleanup() {
  stop_banner
  rm -f "$INSTALL_LOG" "$COMMAND_LOG" "$STATUS_FILE"
}

trap cleanup EXIT
start_banner

run_quiet() {
  if [[ "${GIGBUDDY_VERBOSE:-0}" == "1" ]]; then
    "$@"
    return
  fi
  : >"$COMMAND_LOG"
  if "$@" >"$COMMAND_LOG" 2>&1; then
    return
  fi
  printf 'GigBuddy install failed while running:' >&2
  printf ' %q' "$@" >&2
  printf '\n' >&2
  tail -n 40 "$COMMAND_LOG" >&2
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
  # --force: release tag 可能被前移（修复后重发），本地旧 tag 必须可被覆盖
  run_quiet git -C "$INSTALL_ROOT" fetch --quiet --tags --force origin
  run_quiet git -C "$INSTALL_ROOT" checkout --quiet --detach "$REPO_REF"
else
  step "Downloading GigBuddy $REPO_REF"
  mkdir -p "$(dirname "$INSTALL_ROOT")"
  run_quiet git clone --quiet --depth 1 --branch "$REPO_REF" "$REPO_URL" "$INSTALL_ROOT"
fi

printf 'GigBuddy\n' > "$INSTALL_ROOT/.gigbuddy-install"

step "Installing GigBuddy (venv, library, starter presets, dry inputs, engine)"
run_quiet "$INSTALL_ROOT/install.sh"

step "Linking commands"
mkdir -p "$BIN_DIR"
# bin/gigbuddy 是相对仓库根的 bash 包装脚本，不能直接 symlink（BASH_SOURCE
# 会解析到链接位置）；生成指向安装目录的 wrapper。TUI 同理。
write_wrapper() {
  local command_name="$1" exec_line="$2"
  local link="$BIN_DIR/$command_name"
  if [[ -e "$link" && ! -L "$link" ]] \
     && ! grep -q "$INSTALL_ROOT" "$link" 2>/dev/null; then
    die "refusing to replace an existing command: $link"
  fi
  printf '#!/usr/bin/env bash\nexec %s\n' "$exec_line" > "$link"
  chmod +x "$link"
}
write_wrapper gigbuddy "\"$INSTALL_ROOT/bin/gigbuddy\" \"\$@\""
write_wrapper gigbuddy-tui "\"$INSTALL_ROOT/.venv/bin/python\" -m tui \"\$@\""

stop_banner
printf '\nGigBuddy ready\n'
printf '  %s/gigbuddy\n' "$BIN_DIR"
printf '  %s/gigbuddy-tui\n' "$BIN_DIR"
printf '  install: %s\n' "$INSTALL_ROOT"
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  printf 'Add to PATH if needed: %s\n' "$BIN_DIR"
fi
