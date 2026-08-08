#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
NO_ENGINE=0
SKIP_PRESETS=0
SKIP_DRY_INPUTS=0
DRY_INPUTS="all"

# 阶段提示：每一步安装内容打印一行，慢步骤自带提示。
stage() {
    echo "$1"
}

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Creates .venv, installs Python dependencies, prepares starter presets and dry
inputs, and builds the realtime engine when its NeuralAudio sources are absent.

Options:
  --no-engine         skip NeuralAudio/PortAudio download and C++ build
  --skip-presets      skip starter model downloads and preset creation
  --skip-dry-inputs   skip official dry-input downloads
  --starter-dry       download ten common dry inputs instead of all inputs
  -h, --help          show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-engine) NO_ENGINE=1 ;;
        --skip-presets) SKIP_PRESETS=1 ;;
        --skip-dry-inputs) SKIP_DRY_INPUTS=1 ;;
        --starter-dry) DRY_INPUTS="starter" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# 统一用 uv 管理 Python 环境：系统有 Python 3.12 用它，没有则自动下载；
# 系统 Python 有无都不影响——uv 全权托管（新手零配置）。
UV_BIN="${GIGBUDDY_UV:-uv}"
if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    stage "Installing uv (Python manager)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    UV_BIN="uv"
fi

VENV_PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
    stage "Creating Python environment (uv)"
    "$UV_BIN" venv --python 3.12 "$ROOT/.venv"
fi

stage "Installing Python dependencies"
"$UV_BIN" pip install --python "$VENV_PYTHON" -r "$ROOT/requirements.txt"

bootstrap_args=()
if [[ "$SKIP_PRESETS" -eq 1 ]]; then
    bootstrap_args+=(--skip-presets)
fi
if [[ "$SKIP_DRY_INPUTS" -eq 1 ]]; then
    bootstrap_args+=(--skip-dry-inputs)
else
    bootstrap_args+=(--dry-inputs "$DRY_INPUTS")
fi

stage "Preparing local database, starter presets, and dry inputs"
stage "  (downloading 30 starter models — this can be a bit slow; please be patient)"
PYTHONPATH="$ROOT/src" "$VENV_PYTHON" "$ROOT/scripts/bootstrap.py" \
    "${bootstrap_args[@]}"

if [[ "$NO_ENGINE" -eq 0 ]]; then
    if [[ ! -d "$ROOT/third_party/NeuralAudio" ]]; then
        command -v git >/dev/null 2>&1 || {
            echo "git is required to fetch NeuralAudio." >&2
            exit 1
        }
        stage "Fetching NeuralAudio and its submodules"
        git clone --recurse-submodules \
            https://github.com/mikeoliphant/NeuralAudio \
            "$ROOT/third_party/NeuralAudio"
        # 固定引擎依赖版本（与 README 依赖清单一致，避免上游漂移）
        git -C "$ROOT/third_party/NeuralAudio" checkout --quiet 49100f9
        # 依赖版本坑（自动化修补，替代人工操作）：RTNeural checkout 里
        # vendored 的 Eigen 目录默认指向 master（3.4.90 = 3.5 预发布版），
        # 与 NAM Core 不兼容——① 移除 unsupported/Eigen/FFT（NAM linear.cpp
        # 依赖它）；② 移除 Eigen::placeholders::lastN（LSTM 头文件使用）。
        # 检测 FFT 缺失即替换为 Eigen 3.4.0 稳定版，并修补 lastN 调用。
        EIGEN_DIR="$ROOT/third_party/NeuralAudio/deps/RTNeural/modules/Eigen"
        if [[ ! -f "$EIGEN_DIR/unsupported/Eigen/FFT" ]]; then
            echo "Patching Eigen: RTNeural vendors 3.5-pre (incompatible); using 3.4.0"
            rm -rf "$EIGEN_DIR"
            curl -sSL https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz \
                | tar xz -C "$ROOT/third_party/NeuralAudio/deps/RTNeural/modules"
            mv "$ROOT/third_party/NeuralAudio/deps/RTNeural/modules/eigen-3.4.0" "$EIGEN_DIR"
            for header in LSTM.h LSTMDynamic.h; do
                sed -i '' 's/Eigen::placeholders::lastN/Eigen::lastN/g' \
                    "$ROOT/third_party/NeuralAudio/NeuralAudio/$header"
            done
        fi
    fi

    # PortAudio：固定官方稳定版 v19.7.0，源码编译到本地（不依赖 Homebrew）。
    # macOS 上 PortAudio 用系统自带的 CoreAudio 框架，零额外依赖。
    PA_DIR="$ROOT/.local"
    if [[ ! -f "$PA_DIR/lib/libportaudio.2.dylib" ]]; then
        stage "Building PortAudio 19.7.0 from source"
        PA_TARBALL="pa_stable_v190700_20210406.tgz"
        curl -L -o /tmp/"$PA_TARBALL" \
            "https://codeload.github.com/PortAudio/portaudio/tar.gz/refs/tags/v19.7.0"
        rm -rf /tmp/portaudio-19.7.0
        tar xzf /tmp/"$PA_TARBALL" -C /tmp
        (cd /tmp/portaudio-19.7.0 && \
         ./configure CFLAGS="-Wno-implicit-const-int-float-conversion" \
             --prefix="$PA_DIR" --disable-mac-universal --disable-silent-rules && \
         make -j"$(sysctl -n hw.ncpu 2>/dev/null || printf 4)" && \
         make install)
        rm -f /tmp/"$PA_TARBALL"
    fi

    stage "Building NAM and realtime engine"
    stage "  (compiling NeuralAudio + NAM with clang++ -O3 — this can be a bit slow; please be patient)"
    "$ROOT/cpp/build.sh"
else
    stage "Skipping engine build; launch the TUI with --no-engine"
fi

echo
echo "GigBuddy is installed. Try:"
echo "  $ROOT/.venv/bin/python -m tui --no-engine"
if [[ -x "$ROOT/bin/realtime_cli" ]]; then
    echo "  $ROOT/.venv/bin/python -m tui"
fi
echo
echo "Tip: use a truecolor terminal (iTerm2 / Kitty / WezTerm / Alacritty /"
echo "Warp / Ghostty) for the full guitar-amp theme; macOS Terminal.app"
echo "is 256-color and falls back to the compatible theme automatically."
echo "Force the full theme with: TEXTUAL_COLOR_SYSTEM=truecolor gigbuddy"
