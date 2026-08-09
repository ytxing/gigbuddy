"""Playback protocol tests: input-source chain key, dry-download helpers.

Network-free: no TONE3000 calls; chain/level files point at tmp dirs.
Run: .venv/bin/python -m pytest tests/ -q
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import library  # noqa: E402
import tone3000  # noqa: E402
from tui import live  # noqa: E402
from tui.app import GigBuddyApp  # noqa: E402
from tui.input_screen import InputSourceScreen  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point DB + chain/level files at a tmp dir for every test."""
    monkeypatch.setattr(library, "ROOT", tmp_path)
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", tmp_path / "data" / "tones")
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(live, "LEVEL_FILE", tmp_path / "level.json")
    monkeypatch.setattr(live, "TONES_DIR", tmp_path / "data" / "tones")
    monkeypatch.setattr(live, "DRY_INPUTS_DIR", tmp_path / "data" / "dry_inputs")
    yield


def test_chain_input_defaults_to_instrument():
    assert live.chain_input({}) == {"source": "instrument"}
    assert live.chain_input({"model": "m.nam"})["source"] == "instrument"
    inp = live.chain_input({"input": {"source": "file", "file": "x.wav"}})
    assert inp["source"] == "file"


def test_input_source_playback_commit_does_not_publish_mutation():
    events = []

    class App:
        @staticmethod
        def _commit_external_chain(cfg):
            return {**cfg, "revision": 3}

        def _publish_mutation(self, *args):
            events.append(args)

    screen = SimpleNamespace(app=App())
    result = InputSourceScreen._commit_input(screen, {"input": {"source": "file"}})

    assert result["revision"] == 3
    assert events == []


def test_read_levels_extended_returns_playback_state():
    live.LEVEL_FILE.write_text(json.dumps(
        {"in": 0.1, "out": 0.2, "play_state": "playing", "play_pos": 12.5}))
    assert live.read_levels() == (0.1, 0.2, "playing", 12.5)
    live.LEVEL_FILE.write_text("{}")
    assert live.read_levels() == (0.0, 0.0, "stopped", 0.0)
    live.LEVEL_FILE.unlink()
    assert live.read_levels() == (0.0, 0.0, "stopped", 0.0)


def test_managed_playback_does_not_write_when_engine_is_down(monkeypatch):
    original = {
        "slots": [],
        "input": {
            "source": "file",
            "file": "data/dry_inputs/mayer.wav",
            "state": "stopped",
            "loop": True,
        },
    }
    writes = []
    notices = []
    monkeypatch.setattr(live, "read_chain", lambda: json.loads(json.dumps(original)))
    app = SimpleNamespace(
        _spawn_engine=True,
        _managed_engine_active=lambda: False,
        notify=lambda message, **kwargs: notices.append((message, kwargs)),
        _commit_external_chain=lambda cfg: writes.append(cfg),
    )

    GigBuddyApp._playback_edit(
        app, lambda inp: inp.__setitem__("state", live.PLAY_PLAYING))

    assert writes == []
    assert any("engine" in message.lower() for message, _ in notices)


def test_managed_levels_ignore_stale_telemetry_when_engine_is_down(monkeypatch):
    monkeypatch.setattr(
        live, "read_levels",
        lambda: (0.25, 0.4, live.PLAY_PLAYING, 8.0),
    )
    app = SimpleNamespace(
        _spawn_engine=True,
        _managed_engine_active=lambda: False,
    )

    assert GigBuddyApp._audio_levels(app) == (
        0.0, 0.0, live.PLAY_STOPPED, 0.0)


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
                                    "local_path": str(tmp_path / "data" / "tones" / "m.nam")},
                                 commit=False)
        conn.commit()
    model = tmp_path / "data" / "tones" / "m.nam"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"x")
    live.write_chain({"slots": [{"path": str(model)}], "gain": 0.8,
                      "input": {"source": "file", "file": "data/dry_inputs/mayer.wav",
                                "state": "playing", "loop": True}})
    library.preset_save("t", note=None)
    cfg = library.preset_load("t")
    assert cfg["slots"] == [{"path": str(model)}]
    assert cfg["input"]["source"] == "file"
    assert cfg["input"]["file"] == str(
        tmp_path / "data" / "dry_inputs" / "mayer.wav"
    )
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
