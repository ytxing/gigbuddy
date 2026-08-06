#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${GIGBUDDY_REPO_URL:-https://github.com/ytxing/gigbuddy.git}"
REPO_REF="${GIGBUDDY_REF:-v0.1.0-alpha.8}"
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
  if [[ -t 1 && -n "$BANNER_PID" ]]; then
    # 动画模式下：步骤只覆盖显示在动画下方的状态行，完整日志进文件
    printf '\033[%d;1H\033[2K==> %s' "${STATUS_ROW:-9}" "$1"
  else
    printf '==> %s\n' "$1"
  fi
}

BANNER_PID=""

stop_banner() {
  if [[ -n "$BANNER_PID" ]]; then
    kill "$BANNER_PID" 2>/dev/null || true
    sleep 0.05
    kill -9 "$BANNER_PID" 2>/dev/null || true
    BANNER_PID=""
  fi
  printf '\033[r'   # 恢复全屏滚动区域
}

start_banner() {
  if [[ -t 1 ]] && command -v python3 >/dev/null 2>&1; then
    # 清屏后开始：动画固定占顶部，安装日志只在动画下方滚动。
    # 终端够宽（>=112 列）时 version 与 GIGBUDDY 并排（8 行）；
    # 否则 version 移到 GIGBUDDY 下方（11 行），避免折行错位。
    printf '\033[2J\033[H'
    local lines cols
    lines=$(tput lines 2>/dev/null || printf '24')
    cols=$(tput cols 2>/dev/null || printf '80')
    STATUS_ROW=9
    if (( cols < 112 )); then
      STATUS_ROW=12
    fi
    # 终端高度够放动画 + 状态行 + 日志才开动画；否则退回静态 banner
    if (( lines >= STATUS_ROW + 3 )); then
      printf '\033[%d;%sr\033[%d;1H' "$STATUS_ROW" "$lines" "$STATUS_ROW"
      export GB_BANNER_ROW="$STATUS_ROW"
    # 霓虹灯呼吸：字符不动，横幅上均匀分布 3 个明暗浪，金色系内流动，
    # 后台循环直到安装结束（stop_banner 停止）
    python3 - <<'PY' &
import math, sys, time, os

# 布局一（宽终端，>=112 列）：GIGBUDDY 与 version 并排，8 行
SIDE_LINES = (
  '   █████████  █████   █████████  ███████████  █████  █████ ██████████   ██████████   █████ █████',
  '  ███▒▒▒▒▒███▒▒███   ███▒▒▒▒▒███▒▒███▒▒▒▒▒███▒▒███  ▒▒███ ▒▒███▒▒▒▒███ ▒▒███▒▒▒▒███ ▒▒███ ▒▒███',
  ' ███     ▒▒▒  ▒███  ███     ▒▒▒  ▒███    ▒███ ▒███   ▒███  ▒███   ▒▒███ ▒███   ▒▒███ ▒▒███ ███',
  '▒███          ▒███ ▒███          ▒██████████  ▒███   ▒███  ▒███    ▒███ ▒███    ▒███  ▒▒█████',
  '▒███    █████ ▒███ ▒███    █████ ▒███▒▒▒▒▒███ ▒███   ▒███  ▒███    ▒███ ▒███    ▒███   ▒▒███',
  '▒▒███  ▒▒███  ▒███ ▒▒███  ▒▒███  ▒███    ▒███ ▒███   ▒███  ▒███    ███  ▒███    ███     ▒███        ▄▖  ▗   ▄▖',
  ' ▒▒█████████  █████ ▒▒█████████  ███████████  ▒▒████████   ██████████   ██████████      █████      ▌▌▛▌  ▜   ▛▌',
  '  ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒▒▒▒▒▒▒    ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒      ▒▒▒▒▒      ▚▘█▌▗ ▟▖▗ █▌',
)
# 布局二（窄终端）：version 居中放在 GIGBUDDY 下方，11 行
BELOW_LINES = (
  '   █████████  █████   █████████  ███████████  █████  █████ ██████████   ██████████   █████ █████',
  '  ███▒▒▒▒▒███▒▒███   ███▒▒▒▒▒███▒▒███▒▒▒▒▒███▒▒███  ▒▒███ ▒▒███▒▒▒▒███ ▒▒███▒▒▒▒███ ▒▒███ ▒▒███',
  ' ███     ▒▒▒  ▒███  ███     ▒▒▒  ▒███    ▒███ ▒███   ▒███  ▒███   ▒▒███ ▒███   ▒▒███ ▒▒███ ███',
  '▒███          ▒███ ▒███          ▒██████████  ▒███   ▒███  ▒███    ▒███ ▒███    ▒███  ▒▒█████',
  '▒███    █████ ▒███ ▒███    █████ ▒███▒▒▒▒▒███ ▒███   ▒███  ▒███    ▒███ ▒███    ▒███   ▒▒███',
  '▒▒███  ▒▒███  ▒███ ▒▒███  ▒▒███  ▒███    ▒███ ▒███   ▒███  ▒███    ███  ▒███    ███     ▒███',
  ' ▒▒█████████  █████ ▒▒█████████  ███████████  ▒▒████████   ██████████   ██████████      █████',
  '  ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒▒▒▒▒▒▒    ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒      ▒▒▒▒▒',
  '                                            ▄▖  ▗   ▄▖',
  '                                          ▌▌▛▌  ▜   ▛▌',
  '                                          ▚▘█▌▗ ▟▖▗ █▌',
)
ROW_N = int(os.environ.get('GB_BANNER_ROW', '9'))
if ROW_N == 9:
    LINES = SIDE_LINES            # 并排：version 在第 96 列之后
    MAIN_W = 96
    SILVER_ROW = 999              # 银色按列判断
else:
    LINES = BELOW_LINES           # 下方：version 独占底部 3 行
    MAIN_W = 96                   # 行宽 = 96：银色只按行号（SILVER_ROW）判断
    SILVER_ROW = 8
W = max(len(l) for l in LINES)
R = len(LINES)
WAVES = 3                       # 均匀分布的明暗浪数量
DARK = (110, 72, 8)             # 浪谷：暗金（GIGBUDDY）
BRIGHT = (250, 195, 90)         # 浪峰：亮金（GIGBUDDY）
VDARK = (85, 85, 95)            # 浪谷：暗银（version）
VBRIGHT = (240, 240, 245)       # 浪峰：亮银（version）

def esc(c):
    return '\033[38;2;%d;%d;%dm' % c

def render(phase):
    out = []
    for r_i, row in enumerate(LINES):
        row = row.ljust(W)
        seg = []
        for col, ch in enumerate(row):
            b = 0.5 + 0.5 * math.sin(2 * math.pi * WAVES * col / W - phase)
            if r_i >= SILVER_ROW or col >= MAIN_W:
                d, br = VDARK, VBRIGHT    # version：银色波浪
            else:
                d, br = DARK, BRIGHT      # GIGBUDDY：金色波浪
            c = tuple(int(d[i] + (br[i] - d[i]) * b) for i in range(3))
            seg.append(esc(c) + ch)
        out.append(''.join(seg) + '\033[0m')
    return '\n'.join(out)

import os, signal
_parent = os.getppid()
signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
f = 0
try:
    # 父进程退出（ppid 变 1）或 stdout 断开时自行结束，避免动画停不下来
    while os.getppid() == _parent:
        sys.stdout.write('\033[s\033[1;1H' + render(2 * math.pi * f / 32) + '\033[u')
        sys.stdout.flush()
        f += 1
        time.sleep(0.1)
except (BrokenPipeError, OSError, KeyboardInterrupt, SystemExit):
    pass
PY
    BANNER_PID=$!
    trap 'stop_banner' EXIT
    return
  fi
  fi
  # 非交互终端、没有 python3、或终端太矮：静态金+银版
    printf '\033[38;2;184;134;11m%s\033[38;2;200;200;210m%s\033[0m\n' \
      '   █████████  █████   █████████  ███████████  █████  █████ ██████████   ██████████   █████ █████' ''
    printf '\033[38;2;184;134;11m%s\033[38;2;200;200;210m%s\033[0m\n' \
      '  ███▒▒▒▒▒███▒▒███   ███▒▒▒▒▒███▒▒███▒▒▒▒▒███▒▒███  ▒▒███ ▒▒███▒▒▒▒███ ▒▒███▒▒▒▒███ ▒▒███ ▒▒███' ''
    printf '\033[38;2;184;134;11m%s\033[38;2;200;200;210m%s\033[0m\n' \
      ' ███     ▒▒▒  ▒███  ███     ▒▒▒  ▒███    ▒███ ▒███   ▒███  ▒███   ▒▒███ ▒███   ▒▒███ ▒▒███ ███' ''
    printf '\033[38;2;184;134;11m%s\033[38;2;200;200;210m%s\033[0m\n' \
      '▒███          ▒███ ▒███          ▒██████████  ▒███   ▒███  ▒███    ▒███ ▒███    ▒███  ▒▒█████' ''
    printf '\033[38;2;184;134;11m%s\033[38;2;200;200;210m%s\033[0m\n' \
      '▒███    █████ ▒███ ▒███    █████ ▒███▒▒▒▒▒███ ▒███   ▒███  ▒███    ▒███ ▒███    ▒███   ▒▒███' ''
    printf '\033[38;2;184;134;11m%s\033[38;2;200;200;210m%s\033[0m\n' \
      '▒▒███  ▒▒███  ▒███ ▒▒███  ▒▒███  ▒███    ▒███ ▒███   ▒███  ▒███    ███  ▒███    ███     ▒███    ' '    ▄▖  ▗   ▄▖'
    printf '\033[38;2;184;134;11m%s\033[38;2;200;200;210m%s\033[0m\n' \
      ' ▒▒█████████  █████ ▒▒█████████  ███████████  ▒▒████████   ██████████   ██████████      █████   ' '  ▌▌▛▌  ▜   ▛▌'
    printf '\033[38;2;184;134;11m%s\033[38;2;200;200;210m%s\033[0m\n' \
      '  ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒▒▒▒▒▒▒    ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒      ▒▒▒▒▒    ' '  ▚▘█▌▗ ▟▖▗ █▌'
    printf '\n\n\n'
}
start_banner

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

stop_banner
printf '\033[%d;1H\033[2K' "${STATUS_ROW:-9}"   # 清掉最后的状态行
printf '\nGigBuddy ready\n'
printf '  %s/gigbuddy\n' "$BIN_DIR"
printf '  %s/gigbuddy-tui\n' "$BIN_DIR"
printf '  install: %s\n' "$INSTALL_ROOT"
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  printf 'Add to PATH if needed: %s\n' "$BIN_DIR"
fi
printf 'Steps completed:\n'
sed -n 's/^==> /  - /p' "$INSTALL_LOG"
