"""Regression tests for install-time Preset preparation and first-frame startup."""

import asyncio

import library
from scripts import bootstrap
from textual.widgets import DataTable

from tui.app import GigBuddyApp


def run(coro):
    return asyncio.run(coro)


def _configure_startup_library(monkeypatch, tmp_path):
    root = tmp_path / "gigbuddy"
    data = root / "data"
    monkeypatch.setattr(library, "ROOT", root)
    monkeypatch.setattr(library, "DATA_ROOT", data)
    monkeypatch.setattr(library, "DB_FILE", data / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", data / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", data / "tones")
    monkeypatch.setattr(library, "PRESETS_DIR", data / "presets")
    monkeypatch.setattr(library, "BUNDLED_PRESETS_DIR", root / "presets" / "built-in")
    library.TONES_DIR.mkdir(parents=True)
    library.chain_set({"slots": []})


def test_initial_remote_prefetch_waits_until_local_focus_is_ready(
        monkeypatch, tmp_path):
    """The first remote request must not start before App.on_mount focus setup."""
    _configure_startup_library(monkeypatch, tmp_path)
    model = library.TONES_DIR / "local.nam"
    model.write_bytes(b"test nam")
    with library.connect() as conn:
        library.upsert_tone(conn, {
            "id": 1, "title": "Local Tone", "gear": "amp",
            "username": "tester", "models_count": 1,
        }, commit=False)
        library.upsert_model(conn, {
            "id": 11, "tone_id": 1, "model_url": None,
            "name": model.name, "architecture": "SlimmableContainer",
            "local_path": str(model),
        }, commit=False)
        conn.commit()

    app_ref = {}
    focus_at_request = []

    def search(*_args, **_kwargs):
        focused = app_ref["app"].focused
        focus_at_request.append(getattr(focused, "id", None))
        return [{
            "id": 101, "title": "Remote Tone", "gear": "amp",
            "username": "tester", "downloads_count": 1,
            "favorites_count": 0, "a1_models_count": 0,
            "a2_models_count": 1, "irs_count": 0,
        }]

    monkeypatch.setattr(library.tone3000, "search", search)
    monkeypatch.setattr(library.tone3000, "top_favorites", lambda _limit: [])
    monkeypatch.setattr(library.tone3000, "verify_username", lambda _name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app_ref["app"] = app
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            local = app.query_one("#lib-table-local", DataTable)
            remote = app.query_one("#lib-table-tone", DataTable)
            assert focus_at_request == ["lib-table-local"]
            assert app.focused is local
            assert local.row_count == 1
            assert remote.row_count == 1

    run(scenario())


def test_install_bootstrap_prepares_bundled_models_before_tui(
        monkeypatch, tmp_path):
    """The installer bootstrap requests model preparation, not registration only."""
    _configure_startup_library(monkeypatch, tmp_path)
    captured = {}

    def sync(**kwargs):
        captured.update(kwargs)
        return {
            "total": 20, "ready": 20, "preparing": 0,
            "failed": 0, "failed_presets": [],
        }

    monkeypatch.setattr(library, "sync_bundled_presets", sync)

    assert bootstrap.main(["--skip-dry-inputs"]) == 0
    assert captured == {"quiet": False, "download": True}


def test_install_bootstrap_can_finish_with_retryable_preset_failures(
        monkeypatch, tmp_path):
    """A transient remote failure must not prevent the app from being installed."""
    _configure_startup_library(monkeypatch, tmp_path)
    monkeypatch.setattr(
        library, "sync_bundled_presets",
        lambda **_kwargs: {
            "total": 2, "ready": 1, "preparing": 0,
            "failed": 1, "failed_presets": ["starter"],
        },
    )

    assert bootstrap.main([
        "--skip-dry-inputs", "--allow-preset-failures",
    ]) == 0


def test_install_bootstrap_does_not_hide_invalid_bundled_presets(
        monkeypatch, tmp_path):
    """The allow flag is only for remote failures, not broken JSON."""
    _configure_startup_library(monkeypatch, tmp_path)
    monkeypatch.setattr(
        library, "sync_bundled_presets",
        lambda **_kwargs: {
            "total": 2, "ready": 1, "preparing": 0,
            "failed": 1, "failed_presets": ["broken"],
            "invalid_presets": ["broken"],
        },
    )

    assert bootstrap.main([
        "--skip-dry-inputs", "--allow-preset-failures",
    ]) == 1
