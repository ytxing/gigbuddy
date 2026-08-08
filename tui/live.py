"""File channel between the GigBuddy TUI and the realtime engine (realtime_cli --live/--level-file)"""
import json
import hashlib
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402  （_to_rel_path/_to_abs_path 复用，REQ-035）

ROOT = Path(__file__).resolve().parent.parent
CHAIN_FILE = ROOT / "data" / "live_chain.json"
LEVEL_FILE = ROOT / "data" / "level.json"
TONES_DIR = ROOT / "data" / "tones"
DRY_INPUTS_DIR = ROOT / "data" / "dry_inputs"

# 干声输入源（live_chain.json 的 input 键）的播放状态常量（与引擎协议一致）
PLAY_STOPPED, PLAY_PLAYING, PLAY_PAUSED = "stopped", "playing", "paused"

# Chain parameter defaults are part of the live protocol.  UI reset actions,
# missing-key fallbacks, and preset-facing views must all use the same values.
CHAIN_PARAMETER_DEFAULTS = {
    "gain": 1.0,
    "master": 1.0,
    "quality": 1.0,
}

# The TUI keeps bypass recovery candidates in process memory.  This marker lets
# the UI distinguish its own atomic writes from a direct edit by another
# process, even when both writes leave a slot set to null.
_last_chain_write_fingerprint: str | None = None
_last_chain_write_path: Path | None = None

# Tone-chain node definitions (amp/ir live nodes + placeholder effects)
CHAIN_ORDER = [
    ("amp", "AMP", "guitar → amp model"),
    ("ir", "CAB", "cab simulation"),
    ("comp", "COMP", "compressor (phase 2)"),
    ("od", "OD", "overdrive (phase 2)"),
    ("delay", "DELAY", "delay (phase 2)"),
    ("reverb", "REVERB", "reverb (phase 2)"),
]


def read_chain() -> dict:
    """Read current chain config (empty dict if missing/broken).

    REQ-035 portable：chain 文件存相对路径，读取还原为项目根下绝对
    （TUI 内部与 DB 返回的绝对路径一致比较）。
    """
    try:
        cfg = json.loads(CHAIN_FILE.read_text())
    except Exception:
        return {}
    for key in ("model", "ir"):
        if cfg.get(key):
            cfg[key] = library._to_abs_path(cfg[key])
    return cfg


def write_chain(cfg: dict) -> None:
    """Write chain config (tmp+rename atomic; engine hot-swaps within 0.3s).

    REQ-035 portable：model/ir 路径写入时转相对项目根。
    """
    cfg = dict(cfg)
    for key in ("model", "ir"):
        if cfg.get(key):
            cfg[key] = library._to_rel_path(cfg[key])
    tmp = CHAIN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    tmp.rename(CHAIN_FILE)
    global _last_chain_write_fingerprint, _last_chain_write_path
    _last_chain_write_fingerprint = chain_file_fingerprint()
    _last_chain_write_path = CHAIN_FILE


def chain_file_fingerprint() -> str | None:
    """Return the current chain bytes' fingerprint, or ``None`` if unreadable."""
    try:
        return hashlib.sha256(CHAIN_FILE.read_bytes()).hexdigest()
    except OSError:
        return None


def last_chain_write_fingerprint() -> str | None:
    """Return the last fingerprint written through this Python process."""
    if _last_chain_write_path != CHAIN_FILE:
        return None
    return _last_chain_write_fingerprint


def read_levels() -> tuple[float, float, str, float]:
    """Read engine levels (in, out, play_state, play_pos_sec); 0/stopped on missing/broken file"""
    try:
        d = json.loads(LEVEL_FILE.read_text())
        return (float(d.get("in", 0.0)), float(d.get("out", 0.0)),
                d.get("play_state", PLAY_STOPPED), float(d.get("play_pos", 0.0)))
    except Exception:
        return 0.0, 0.0, PLAY_STOPPED, 0.0


def chain_input(chain: dict) -> dict:
    """当前链的 input 键（缺省 = 乐器输入）"""
    inp = chain.get("input")
    return inp if isinstance(inp, dict) else {"source": "instrument"}


def write_playback(state: str, loop: bool | None = None) -> dict | None:
    """播放控制：读链 → 改 input.state（loop 可选）→ 写回。返回新链或 None（链缺失）"""
    chain = read_chain()
    if not chain:
        return None
    inp = chain_input(chain)
    inp["source"] = inp.get("source", "instrument")
    inp["state"] = state
    if loop is not None:
        inp["loop"] = loop
    chain["input"] = inp
    write_chain(chain)
    return chain


def short_name(path: str) -> str:
    """Path → display name (basename without extension)"""
    return os.path.basename(path)
