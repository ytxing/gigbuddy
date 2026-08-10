#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${GIGBUDDY_REPO_URL:-https://github.com/ytxing/gigbuddy.git}"
REPO_REF="${GIGBUDDY_REF:-v1.1.0}"
USER_HOME="${HOME:-}"
INSTALL_ROOT="${GIGBUDDY_HOME:-${USER_HOME}/.local/share/gigbuddy}"
BIN_DIR="${GIGBUDDY_BIN_DIR:-${USER_HOME}/.local/bin}"
PYTHON_BIN="${GIGBUDDY_PYTHON:-python3}"

ROLLBACK_FILE="$(mktemp -t gigbuddy-rollback.XXXXXX)"

die() {
  stop_banner
  rollback_install
  printf 'GigBuddy install failed: %s\n' "$*" >&2
  exit 1
}

rollback_install() {
  # 安装失败时撤销本次安装做的改动，恢复到安装前状态：
  # - 本次新建的安装目录 → 整个删除
  # - 更新已有安装 → checkout 回原 HEAD，删除本次新建的 .venv/data/third_party
  #   （安装前已存在的保留，幂等重跑不受影响）
  # - 本次新建的 ~/.local/bin wrapper 链接 → 删除
  [[ -f "$ROLLBACK_FILE" ]] || return 0
  . "$ROLLBACK_FILE"
  if [[ "${WAS_NEW_CLONE:-0}" == "1" ]]; then
    rm -rf -- "$INSTALL_ROOT"
  elif [[ -n "${PREV_HEAD:-}" ]]; then
    git -C "$INSTALL_ROOT" checkout --quiet --detach "$PREV_HEAD" 2>/dev/null || true
    for dir_name in .venv data third_party; do
      case "$dir_name" in
        .venv) had=HAD_VENV ;;
        data) had=HAD_DATA ;;
        third_party) had=HAD_THIRDPARTY ;;
      esac
      if [[ "${!had:-0}" != "1" && -d "$INSTALL_ROOT/$dir_name" ]]; then
        rm -rf -- "$INSTALL_ROOT/$dir_name"
      fi
    done
  fi
  for command_name in gigbuddy gigbuddy-tui; do
    link="$BIN_DIR/$command_name"
    if [[ -f "$link" ]] && grep -q "$INSTALL_ROOT" "$link" 2>/dev/null; then
      rm -f -- "$link"
    fi
  done
  rm -f "$ROLLBACK_FILE"
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
  if [[ -t 1 && "${GIGBUDDY_VERBOSE:-0}" != "1" && -n "${TERM:-}" && "$TERM" != "dumb" ]] \
     && command -v python3 >/dev/null 2>&1; then
    # 动画进程独占终端；安装主流程只通过状态文件更新步骤文字。
    printf '\033[2J\033[H'
    local lines cols tty_size
    tty_size=$(stty size </dev/tty 2>/dev/null || true)
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
        fi
        return
      fi
    fi
  fi
  # 非交互终端、无 python3、不支持 truecolor、或终端太矮：静态金色版
  local s_lines s_cols s_size
  s_size=$(stty size </dev/tty 2>/dev/null || true)
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

trap cleanup EXIT
# 交互安装时询问安装位置（Agent 场景：装到自己的项目文件夹，让 .claude/skills
# 在项目内可用）。已显式设置 GIGBUDDY_HOME 或非交互（curl | bash）时跳过。
# 注意：必须在 start_banner 之前询问——banner 动画每 0.1s 重绘终端，会覆盖
# 提示文字，read 将无提示地阻塞（表现为“卡在依赖确认”）。
if [[ -t 0 && -z "${GIGBUDDY_HOME:-}" ]]; then
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

# 记录安装前状态，供失败回滚使用（rollback_install）
if [[ -d "$INSTALL_ROOT/.git" ]]; then
  printf 'WAS_NEW_CLONE=0\nPREV_HEAD=%s\n' \
    "$(git -C "$INSTALL_ROOT" rev-parse HEAD 2>/dev/null || true)" \
    > "$ROLLBACK_FILE"
else
  printf 'WAS_NEW_CLONE=1\n' > "$ROLLBACK_FILE"
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

# 记录 install.sh 将创建的目录是否存在（失败时只删本次新建的）
{
  printf 'HAD_VENV=%s\n' "$([[ -d "$INSTALL_ROOT/.venv" ]] && printf 1 || printf 0)"
  printf 'HAD_DATA=%s\n' "$([[ -d "$INSTALL_ROOT/data" ]] && printf 1 || printf 0)"
  printf 'HAD_THIRDPARTY=%s\n' "$([[ -d "$INSTALL_ROOT/third_party" ]] && printf 1 || printf 0)"
} >> "$ROLLBACK_FILE"

step "Creating Python environment (uv)"
UV_BIN="${GIGBUDDY_UV:-uv}"
if ! command -v "$UV_BIN" >/dev/null 2>&1; then
  step "Installing uv (Python manager)"
  run_quiet bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  export PATH="$HOME/.local/bin:$PATH"
  UV_BIN="uv"
fi
if [[ ! -x "$INSTALL_ROOT/.venv/bin/python" ]]; then
  run_quiet "$UV_BIN" venv --python 3.12 "$INSTALL_ROOT/.venv"
fi

step "Installing Python dependencies"
run_quiet "$UV_BIN" pip install --python "$INSTALL_ROOT/.venv/bin/python" \
  -r "$INSTALL_ROOT/requirements.txt"

step "Downloading starter presets and dry inputs (this can take a while; please be patient)"
run_quiet env PYTHONPATH="$INSTALL_ROOT/src" \
  "$INSTALL_ROOT/.venv/bin/python" "$INSTALL_ROOT/scripts/bootstrap.py"

# 引擎依赖与编译（逻辑与根 install.sh 同步维护）
if [[ -d "$INSTALL_ROOT/cpp" ]]; then
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
      rm -rf "$EIGEN_DIR"
      run_quiet bash -c "curl -sSL https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz | tar xz -C '$INSTALL_ROOT/third_party/NeuralAudio/deps/RTNeural/modules' && mv '$INSTALL_ROOT/third_party/NeuralAudio/deps/RTNeural/modules/eigen-3.4.0' '$EIGEN_DIR'"
      for header in LSTM.h LSTMDynamic.h; do
        sed -i '' 's/Eigen::placeholders::lastN/Eigen::lastN/g' \
          "$INSTALL_ROOT/third_party/NeuralAudio/NeuralAudio/$header"
      done
    fi
  fi

  # PortAudio：固定官方稳定版 v19.7.0 源码编译到本地（不依赖 Homebrew）
  PA_DIR="$INSTALL_ROOT/.local"
  if [[ ! -f "$PA_DIR/lib/libportaudio.2.dylib" ]]; then
    step "Building PortAudio 19.7.0 from source"
    PA_TARBALL="pa_stable_v190700_20210406.tgz"
    run_quiet bash -c "curl -L -o /tmp/$PA_TARBALL https://codeload.github.com/PortAudio/portaudio/tar.gz/refs/tags/v19.7.0 && rm -rf /tmp/portaudio-19.7.0
    tar xzf /tmp/$PA_TARBALL -C /tmp"
    run_quiet bash -c "cd /tmp/portaudio-19.7.0 && ./configure CFLAGS='-Wno-implicit-const-int-float-conversion' --prefix='$PA_DIR' --disable-mac-universal --disable-silent-rules && make -j\$(sysctl -n hw.ncpu 2>/dev/null || echo 4) && make install"
    rm -f /tmp/"$PA_TARBALL"
  fi

  step "Building the NAM engine (this can take a while; please be patient)"
  run_quiet "$INSTALL_ROOT/cpp/build.sh"
fi

step "Linking commands"
mkdir -p "$BIN_DIR"
# bin/gigbuddy 是相对仓库根的 bash 包装脚本，不能直接 symlink（BASH_SOURCE
# 会解析到链接位置）；生成指向安装目录的 wrapper。
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

# 安装成功：不再需要回滚状态
rm -f "$ROLLBACK_FILE"
stop_banner
printf '\nGigBuddy ready\n'
printf '  %s/gigbuddy\n' "$BIN_DIR"
printf '  install: %s\n' "$INSTALL_ROOT"
printf 'Tip: use a truecolor terminal (iTerm2 / Kitty / WezTerm / Alacritty /\n'
printf 'Warp / Ghostty) for the full guitar-amp theme; macOS Terminal.app\n'
printf 'is 256-color and falls back to the compatible theme automatically.\n'
printf 'Force the full theme with: TEXTUAL_COLOR_SYSTEM=truecolor gigbuddy\n'
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  printf 'Add to PATH if needed: %s\n' "$BIN_DIR"
fi
