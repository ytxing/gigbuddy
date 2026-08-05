#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/third_party/NeuralAudio"
REPO="https://github.com/mikeoliphant/NeuralAudio.git"
COMMIT="49100f90603afc83d810a960faf30e8326edc4bc"

if [[ -e "$DEST" && ! -d "$DEST/.git" ]]; then
  echo "refusing to use non-git path: $DEST" >&2
  exit 1
fi

if [[ ! -d "$DEST/.git" ]]; then
  mkdir -p "$(dirname "$DEST")"
  git clone --recurse-submodules "$REPO" "$DEST"
fi

git -C "$DEST" fetch --quiet origin "$COMMIT"
git -C "$DEST" checkout --detach --quiet "$COMMIT"
git -C "$DEST" submodule sync --recursive
git -C "$DEST" submodule update --init --recursive

echo "NeuralAudio ready at $DEST"
