#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
NO_ENGINE=0
SKIP_PRESETS=0
SKIP_DRY_INPUTS=0
DRY_INPUTS="all"

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

if [[ -z "$PYTHON_BIN" ]]; then
    if command -v python3.12 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3.12)"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
    else
        echo "Python 3 is required; install Python 3.12 or set PYTHON_BIN." >&2
        exit 1
    fi
fi

VENV_PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Creating $ROOT/.venv"
    "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi

echo "Installing Python dependencies"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r "$ROOT/requirements.txt"

bootstrap_args=()
if [[ "$SKIP_PRESETS" -eq 1 ]]; then
    bootstrap_args+=(--skip-presets)
fi
if [[ "$SKIP_DRY_INPUTS" -eq 1 ]]; then
    bootstrap_args+=(--skip-dry-inputs)
else
    bootstrap_args+=(--dry-inputs "$DRY_INPUTS")
fi

echo "Preparing local database, starter presets, and dry inputs"
PYTHONPATH="$ROOT/src" "$VENV_PYTHON" "$ROOT/scripts/bootstrap.py" \
    "${bootstrap_args[@]}"

if [[ "$NO_ENGINE" -eq 0 ]]; then
    if [[ ! -d "$ROOT/third_party/NeuralAudio" ]]; then
        command -v git >/dev/null 2>&1 || {
            echo "git is required to fetch NeuralAudio." >&2
            exit 1
        }
        echo "Fetching NeuralAudio and its submodules"
        git clone --recurse-submodules \
            https://github.com/mikeoliphant/NeuralAudio \
            "$ROOT/third_party/NeuralAudio"
        # 依赖版本坑：RTNeural checkout 里 vendored 的 Eigen 目录默认指向
        # master（3.4.90 = 3.5 预发布版），与 NAM Core 不兼容——① 移除
        # unsupported/Eigen/FFT（NAM linear.cpp 依赖它）；② 移除
        # Eigen::placeholders::lastN（NeuralAudio LSTM.h/LSTMDynamic.h 使用）。
        # 若 cpp/build.sh 报这两个错误：用 Eigen 3.4.0 稳定版源码 tarball
        # （gitlab.com/libeigen/eigen/-/archive/3.4.0）替换
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

    if command -v brew >/dev/null 2>&1; then
        if ! brew --prefix portaudio >/dev/null 2>&1; then
            echo "Installing PortAudio"
            brew install portaudio
        fi
    else
        echo "Homebrew is required to build the realtime engine (PortAudio)." >&2
        echo "Re-run with --no-engine to use the TUI without a local engine." >&2
        exit 1
    fi

    echo "Building NAM and realtime engine"
    "$ROOT/cpp/build.sh"
else
    echo "Skipping engine build; launch the TUI with --no-engine"
fi

echo
echo "GigBuddy is installed. Try:"
echo "  $ROOT/.venv/bin/python -m tui --no-engine"
if [[ -x "$ROOT/bin/realtime_cli" ]]; then
    echo "  $ROOT/.venv/bin/python -m tui"
fi
