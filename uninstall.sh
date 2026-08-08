#!/usr/bin/env bash
# 一行命令卸载：删除 GigBuddy 的全部本地数据与构建产物。
# 用法：./uninstall.sh
# 保留：源码、data.bak-* 备份目录（如需一并删除请手动清理）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGETS=(
  "$ROOT/.venv"                  # Python 虚拟环境
  "$ROOT/data"                   # 本地库 / 链文件 / 音色文件 / 干音素材
  "$ROOT/bin/realtime_cli"       # 实时引擎
  "$ROOT/bin/nam_cli"            # 离线渲染引擎
  "$ROOT/third_party"            # NeuralAudio 依赖（下次 install 重新拉取）
)

echo "Removing: ${TARGETS[*]}"
for target in "${TARGETS[@]}"; do
  if [ -e "$target" ]; then
    rm -rf "$target"
    echo "  removed $target"
  fi
done
echo "Done. Reinstall with ./install.sh"
