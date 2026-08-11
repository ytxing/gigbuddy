"""Playback protocol tests: input-source chain key, dry-download helpers.

Network-free: no TONE3000 calls; chain/level files point at tmp dirs.
Run: .venv/bin/python -m pytest tests/ -q
"""
import asyncio
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
from tui.panels import InputNodeWidget, InterfaceBar, MeterBar  # noqa: E402


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


def test_input_source_tree_marks_current_file(monkeypatch, tmp_path):
    dry_dir = tmp_path / "data" / "dry_inputs"
    dry_dir.mkdir(parents=True)
    selected = dry_dir / "selected - Guitar.wav"
    other = dry_dir / "other - Guitar.wav"
    selected.write_bytes(b"wav")
    other.write_bytes(b"wav")
    live.write_chain({
        "slots": [],
        "input": {
            "source": "file",
            "file": "data/dry_inputs/selected - Guitar.wav",
            "state": "playing",
            "loop": True,
        },
    })

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_open_input_source()
            await pilot.pause()
            tree = app.screen.query_one("#input-tree")

            def labels(node):
                yield str(node.label)
                for child in node.children:
                    yield from labels(child)

            assert "✓ selected - Guitar.wav" in set(labels(tree.root))
            assert "  other - Guitar.wav" in set(labels(tree.root))

    asyncio.run(scenario())


def test_read_levels_extended_returns_playback_state():
    live.LEVEL_FILE.write_text(json.dumps(
        {"in": 0.1, "out": 0.2, "play_state": "playing", "play_pos": 12.5}))
    assert live.read_levels() == (0.1, 0.2, "playing", 12.5)
    live.LEVEL_FILE.write_text("{}")
    assert live.read_levels() == (0.0, 0.0, "stopped", 0.0)
    live.LEVEL_FILE.unlink()
    assert live.read_levels() == (0.0, 0.0, "stopped", 0.0)


def test_read_level_snapshot_combines_levels_and_runtime_status():
    live.LEVEL_FILE.write_text(json.dumps({
        "in": 0.1,
        "out": 0.2,
        "play_state": "playing",
        "play_pos": 12.5,
        "runtime_revision": 7,
        "runtime_status": "applied",
    }))

    assert live.read_level_snapshot() == (
        (0.1, 0.2, "playing", 12.5),
        (7, "applied"),
    )


def test_input_playback_skips_refresh_when_visible_state_is_unchanged(monkeypatch):
    widget = InputNodeWidget()
    refreshes = []
    monkeypatch.setattr(widget, "refresh", lambda: refreshes.append(True))

    widget.set_playback(live.PLAY_PLAYING, 0.1, False)
    widget.set_playback(live.PLAY_PLAYING, 0.2, False)
    widget.set_playback(live.PLAY_PLAYING, 1.0, False)

    assert len(refreshes) == 2
    assert widget.play_pos == 1.0


def test_interface_bar_skips_repeated_status_updates():
    class FakeWidget:
        def __init__(self):
            self.classes = []
            self.labels = []

        def set_classes(self, value):
            self.classes.append(value)

        def update(self, value):
            self.labels.append(value)

    mute = FakeWidget()
    cpu = FakeWidget()
    runtime = FakeWidget()
    bar = SimpleNamespace(
        _muted=None,
        _cpu_text=None,
        _runtime_text=None,
        mute=mute,
        cpu=cpu,
        runtime=runtime,
    )

    InterfaceBar.set_muted(bar, False)
    InterfaceBar.set_muted(bar, False)
    InterfaceBar.set_cpu_usage(bar, 8.9)
    InterfaceBar.set_cpu_usage(bar, 8.9)
    InterfaceBar.set_cpu_usage(bar, 12.3)
    InterfaceBar.set_runtime_status(bar, 7, 7, "applied")
    InterfaceBar.set_runtime_status(bar, 7, 7, "applied")

    assert mute.labels == ["MUTE"]
    assert cpu.labels == ["CPU  8.9%", "CPU 12.3%"]
    assert len(cpu.labels[0]) == len(cpu.labels[1])
    assert "08.9%" not in cpu.labels[0]
    assert runtime.labels == ["APPLIED"]


def test_meter_skips_repaint_when_visible_output_is_unchanged(monkeypatch):
    meter = MeterBar()
    refreshes = []
    monkeypatch.setattr(meter, "refresh", lambda: refreshes.append(True))

    meter.watch_levels((0.1, 0.2))
    meter.watch_levels((0.10001, 0.20001))
    meter.watch_levels((0.2, 0.2))

    assert len(refreshes) == 2


def test_meter_repaints_when_peak_hold_expires(monkeypatch):
    meter = MeterBar()
    refreshes = []
    monkeypatch.setattr(meter, "refresh", lambda: refreshes.append(True))

    meter.watch_levels((1.0, 0.2))
    meter._pk_in_at = 0.0
    meter.watch_levels((0.1, 0.2))

    assert len(refreshes) == 2


def test_catalog_refresh_is_throttled_but_runs_at_interval():
    calls = []
    library_panel = SimpleNamespace(
        refresh_rows=lambda: calls.append("library"))
    preset_panel = SimpleNamespace(
        refresh_presets=lambda **kwargs: calls.append(("preset", kwargs)))
    app = SimpleNamespace(
        _last_catalog_refresh_at=None,
        CATALOG_REFRESH_INTERVAL_S=GigBuddyApp.CATALOG_REFRESH_INTERVAL_S,
    )

    GigBuddyApp._refresh_catalog_panels(
        app, library_panel, preset_panel, now=10.0)
    GigBuddyApp._refresh_catalog_panels(
        app, library_panel, preset_panel, now=10.2)
    GigBuddyApp._refresh_catalog_panels(
        app, library_panel, preset_panel, now=10.5)

    assert calls == [
        "library",
        ("preset", {"incremental": True}),
        "library",
        ("preset", {"incremental": True}),
    ]


def test_ui_cpu_sample_is_windowed_and_uses_one_core_percent(monkeypatch):
    app = SimpleNamespace(
        _last_ui_cpu_sample_at=0.0,
        _last_ui_cpu_sample_time=0.0,
        _ui_cpu_percent=None,
        UI_CPU_SAMPLE_INTERVAL_S=0.5,
    )
    wall_times = iter((0.2, 0.6))
    cpu_times = iter((0.01, 0.03))
    monkeypatch.setattr("tui.app.time.monotonic", lambda: next(wall_times))
    monkeypatch.setattr("tui.app.time.process_time", lambda: next(cpu_times))

    assert GigBuddyApp._sample_ui_cpu(app) is None
    assert GigBuddyApp._sample_ui_cpu(app) == pytest.approx(5.0)


def test_managed_engine_cpu_reads_only_owned_process(monkeypatch):
    class FakeEngine:
        pid = 4321

        @staticmethod
        def poll():
            return None

    app = SimpleNamespace(
        _engine=FakeEngine(),
        _last_engine_cpu_pid=None,
        _last_engine_cpu_sample_at=None,
        _engine_cpu_percent=None,
        UI_CPU_SAMPLE_INTERVAL_S=0.5,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=" 3.75\n")

    monkeypatch.setattr("tui.app.subprocess.run", fake_run)

    assert GigBuddyApp._sample_engine_cpu(app) == pytest.approx(3.75)
    assert calls[0][0] == ["ps", "-p", "4321", "-o", "%cpu="]


def test_total_cpu_combines_tui_and_managed_engine(monkeypatch):
    app = GigBuddyApp(spawn_engine=False)
    app._spawn_engine = True
    app._engine = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(app, "_sample_ui_cpu", lambda: 2.3)
    monkeypatch.setattr(app, "_sample_engine_cpu", lambda: 1.7)

    assert GigBuddyApp._sample_total_cpu(app) == pytest.approx(4.0)


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


def test_managed_playback_queues_edit_against_latest_chain(monkeypatch):
    original = {
        "slots": [],
        "input": {
            "source": "file",
            "file": "data/dry_inputs/mayer.wav",
            "state": "stopped",
            "loop": True,
        },
    }
    jobs = []
    monkeypatch.setattr(live, "read_chain", lambda: json.loads(json.dumps(original)))
    app = SimpleNamespace(
        _spawn_engine=True,
        _managed_engine_active=lambda: True,
        _enqueue_managed_mutation=lambda mutation, note, **kwargs: (
            jobs.append((mutation, kwargs)) or True),
        query_one=lambda _widget: SimpleNamespace(_legacy_mode=False),
        notify=lambda *args, **kwargs: None,
    )

    GigBuddyApp._playback_edit(
        app, lambda inp: inp.__setitem__(
            "state", live.PLAY_PLAYING if inp.get("state") != live.PLAY_PLAYING
            else live.PLAY_PAUSED))

    assert len(jobs) == 1
    mutation, _ = jobs[0]

    class FakeState:
        def __init__(self):
            self.chain = json.loads(json.dumps(original))

        def to_chain(self):
            return json.loads(json.dumps(self.chain))

        def apply_candidate(self, candidate):
            self.chain = candidate

    state = FakeState()
    mutation(state)
    assert state.chain["input"]["state"] == live.PLAY_PLAYING

    # The worker must evaluate a toggle from its latest state, not the stale
    # snapshot captured when the key event was received.
    state.chain["input"]["state"] = live.PLAY_PLAYING
    mutation(state)
    assert state.chain["input"]["state"] == live.PLAY_PAUSED


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
