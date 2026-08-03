"""File channel between the GigBuddy TUI and the realtime engine (realtime_cli --live/--level-file)"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAIN_FILE = ROOT / "data" / "live_chain.json"
LEVEL_FILE = ROOT / "data" / "level.json"
TONES_DIR = ROOT / "data" / "tones"

# Tone-chain node definitions (amp/ir live nodes + placeholder effects)
CHAIN_ORDER = [
    ("amp", "AMP", "guitar → amp model"),
    ("ir", "IR", "cab simulation"),
    ("comp", "COMP", "compressor (phase 2)"),
    ("od", "OD", "overdrive (phase 2)"),
    ("delay", "DELAY", "delay (phase 2)"),
    ("reverb", "REVERB", "reverb (phase 2)"),
]


def read_chain() -> dict:
    """Read current chain config (empty dict if missing/broken)"""
    try:
        return json.loads(CHAIN_FILE.read_text())
    except Exception:
        return {}


def write_chain(cfg: dict) -> None:
    """Write chain config (tmp+rename atomic; engine hot-swaps within 0.3s)"""
    tmp = CHAIN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    tmp.rename(CHAIN_FILE)


def read_levels() -> tuple[float, float]:
    """Read engine levels (in, out); 0 on missing/broken file"""
    try:
        d = json.loads(LEVEL_FILE.read_text())
        return float(d.get("in", 0.0)), float(d.get("out", 0.0))
    except Exception:
        return 0.0, 0.0


def short_name(path: str) -> str:
    """Path → display name (basename without extension)"""
    return os.path.basename(path)
