"""Playback protocol tests: input-source chain key, dry-download helpers.

Network-free: no TONE3000 calls; chain/level files point at tmp dirs.
Run: .venv/bin/python -m pytest tests/ -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import library  # noqa: E402
import tone3000  # noqa: E402
from tui import live  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point DB + chain/level files at a tmp dir for every test."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", tmp_path / "tones")
    monkeypatch.setattr(live, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(live, "LEVEL_FILE", tmp_path / "level.json")
    yield


def test_chain_input_defaults_to_instrument():
    assert live.chain_input({}) == {"source": "instrument"}
    assert live.chain_input({"model": "m.nam"})["source"] == "instrument"
    inp = live.chain_input({"input": {"source": "file", "file": "x.wav"}})
    assert inp["source"] == "file"


def test_write_playback_toggles_state_preserving_file_and_loop():
    live.write_chain({"model": "m.nam",
                      "input": {"source": "file", "file": "data/dry_inputs/a.wav",
                                "state": "playing", "loop": True}})
    cfg = live.write_playback(live.PLAY_PAUSED)
    assert cfg["input"]["state"] == "paused"
    assert cfg["input"]["file"] == "data/dry_inputs/a.wav"
    assert cfg["input"]["loop"] is True
    # REQ-035 portable：链里相对路径读取时解析为项目根下绝对
    assert cfg["model"] == str(live.ROOT / "m.nam")
    cfg = live.write_playback(live.PLAY_PLAYING, loop=False)
    assert cfg["input"]["state"] == "playing"
    assert cfg["input"]["loop"] is False


def test_write_playback_no_chain_returns_none():
    assert live.write_playback(live.PLAY_PLAYING) is None


def test_write_playback_instrument_chain_gains_source_key():
    live.write_chain({"model": "m.nam"})
    cfg = live.write_playback(live.PLAY_PLAYING)
    assert cfg["input"] == {"source": "instrument", "state": "playing"}


def test_read_levels_extended_returns_playback_state():
    live.LEVEL_FILE.write_text(json.dumps(
        {"in": 0.1, "out": 0.2, "play_state": "playing", "play_pos": 12.5}))
    assert live.read_levels() == (0.1, 0.2, "playing", 12.5)
    live.LEVEL_FILE.write_text("{}")
    assert live.read_levels() == (0.0, 0.0, "stopped", 0.0)
    live.LEVEL_FILE.unlink()
    assert live.read_levels() == (0.0, 0.0, "stopped", 0.0)


def test_preset_load_keeps_current_input_source(tmp_path):
    """preset 只存音色链：加载 preset 保留当前输入源（干声试听不被打断）"""
    tone = {"id": 19, "title": "Fender Super Reverb 1977", "gear": "amp-cab",
            "platform": "nam", "username": "u", "avatar_url": "", "user_id": "x",
            "description": "", "tags": [], "makes": [], "images": [],
            "downloads_count": 1, "favorites_count": 0, "a1_models_count": 0,
            "a2_models_count": 1, "custom_models_count": 0, "models_count": 1,
            "irs_count": 0, "has_model_with_url": 1, "model_name": "EQ Flat",
            "created_at": "2025-01-01", "updated_at": "2025-01-01",
            "published_at": "2025-01-01"}
    with library.connect() as conn:
        library.upsert_tone(conn, tone, commit=False)
        library.upsert_model(conn, {"id": 1001, "tone_id": 19, "name": "EQ Flat.nam",
                                    "model_url": "u", "architecture": "SlimmableContainer",
                                    "local_path": str(tmp_path / "m.nam")},
                             commit=False)
        conn.commit()
    (tmp_path / "m.nam").write_bytes(b"x")
    live.write_chain({"model": str(tmp_path / "m.nam"), "gain": 0.8,
                      "input": {"source": "file", "file": "data/dry_inputs/mayer.wav",
                                "state": "playing", "loop": True}})
    library.preset_save("t", note=None)
    cfg = library.preset_load("t")
    assert cfg["model"] == str(tmp_path / "m.nam")
    assert cfg["input"]["source"] == "file"
    assert cfg["input"]["file"] == "data/dry_inputs/mayer.wav"
    assert cfg["input"]["state"] == "playing"


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


def test_fetch_dry_inputs_missing_and_progress(tmp_path, monkeypatch):
    (tmp_path / "Mayer - Guitar.wav").write_bytes(b"x")
    monkeypatch.setattr(tone3000, "DRY_INPUTS",
                        {"mayer": "Mayer - Guitar.wav", "brit": "Brit - Guitar.wav"})
    assert tone3000.fetch_dry_inputs_missing(tmp_path) == ["brit"]
    assert tone3000.fetch_dry_inputs_missing(tmp_path, names=["mayer"]) == []

    calls = []
    monkeypatch.setattr(
        tone3000.urllib.request, "urlopen",
        lambda req, timeout=60: _FakeResp(b"RIFF...."))
    n = tone3000.fetch_dry_inputs(
        tmp_path, ["brit"], progress=lambda done, total, fname: calls.append((done, total, fname)))
    assert n == 1
    assert (tmp_path / "Brit - Guitar.wav").read_bytes() == b"RIFF...."
    assert calls[-1] == (1, 1, None)
