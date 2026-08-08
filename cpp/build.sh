#!/bin/bash
# Build nam_cli (NeuralAudio + NAM Core), MIT licensed dependencies.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$ROOT/third_party/NeuralAudio"
D="$T/deps"
OUT="$ROOT/bin"
mkdir -p "$OUT"

MACROS=(
  -DNAM_SAMPLE_FLOAT -DDSP_SAMPLE_FLOAT -DBUILD_NAMCORE -DRTNEURAL_USE_EIGEN=1
  -DNAM_ENABLE_A2_FAST -DBUILD_INTERNAL_STATIC_WAVENET -DBUILD_STATIC_INTERNAL_NAMA2
  -DWAVENET_MATH=FastMath -DLSTM_MATH=FastMath -DDEFAULT_QUALITY_SCALE=1.0
  -DDEFAULT_INPUT_DBU=12 -DWAVENET_MAX_NUM_FRAMES=64 -DLAYER_ARRAY_BUFFER_PADDING=24
)
INCS=(
  -I"$T"
  -I"$T/NeuralAudio"
  -I"$D/RTNeural"
  -I"$D/RTNeural/modules"
  -I"$D/RTNeural/modules/Eigen"
  -I"$D/NeuralAmpModelerCore"
  -I"$D/NeuralAmpModelerCore/Dependencies/nlohmann"
  -I"$D/math_approx/include"
)
NAM_CORE="$D/NeuralAmpModelerCore/NAM"
SRCS=(
  "$ROOT/cpp/nam_cli.cpp"
  "$T/NeuralAudio/NeuralModel.cpp"
  "$T/NeuralAudio/RTNeuralLoader.cpp"
  "$NAM_CORE/activations.cpp" "$NAM_CORE/container.cpp" "$NAM_CORE/conv1d.cpp"
  "$NAM_CORE/convnet.cpp" "$NAM_CORE/dsp.cpp" "$NAM_CORE/get_dsp.cpp"
  "$NAM_CORE/linear.cpp" "$NAM_CORE/lstm.cpp" "$NAM_CORE/ring_buffer.cpp" "$NAM_CORE/util.cpp"
  "$NAM_CORE/wavenet/a2_fast.cpp" "$NAM_CORE/wavenet/model.cpp" "$NAM_CORE/wavenet/slimmable.cpp"
)
clang++ -O3 -std=c++20 "${MACROS[@]}" "${INCS[@]}" "${SRCS[@]}" -o "$OUT/nam_cli"
echo "built $OUT/nam_cli"

# 实时版（PortAudio v19.7.0，install.sh 源码编译到 .local）
REALTIME_SRCS=(
  "$ROOT/cpp/realtime_cli.cpp"
  "$T/NeuralAudio/NeuralModel.cpp"
  "$T/NeuralAudio/RTNeuralLoader.cpp"
  "$NAM_CORE/activations.cpp" "$NAM_CORE/container.cpp" "$NAM_CORE/conv1d.cpp"
  "$NAM_CORE/convnet.cpp" "$NAM_CORE/dsp.cpp" "$NAM_CORE/get_dsp.cpp"
  "$NAM_CORE/linear.cpp" "$NAM_CORE/lstm.cpp" "$NAM_CORE/ring_buffer.cpp" "$NAM_CORE/util.cpp"
  "$NAM_CORE/wavenet/a2_fast.cpp" "$NAM_CORE/wavenet/model.cpp" "$NAM_CORE/wavenet/slimmable.cpp"
)
# PortAudio：install.sh 源码编译的 v19.7.0（$ROOT/.local），不依赖 Homebrew。
LOCAL_PREFIX="$ROOT/.local"
if [[ -f "$LOCAL_PREFIX/lib/libportaudio.2.dylib" ]]; then
    PA_INC="$LOCAL_PREFIX/include"
    PA_LIB="$LOCAL_PREFIX/lib"
    PA_RPATH="-Wl,-rpath,$LOCAL_PREFIX/lib"
else
    echo "PortAudio not found. Run ./install.sh (builds v19.7.0 from source)." >&2
    exit 1
fi
clang++ -O3 -std=c++20 "${MACROS[@]}" "${INCS[@]}" -I"$PA_INC" \
    "${REALTIME_SRCS[@]}" -L"$PA_LIB" $PA_RPATH -lportaudio -o "$OUT/realtime_cli"
echo "built $OUT/realtime_cli"
