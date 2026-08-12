"""Download completion remains visible after the originating view is gone."""

import asyncio
import threading

from tui.app import GigBuddyApp
from tui.install_screen import PackInstallScreen
from tui.panels import DetailPane

import library


def run(coro):
    return asyncio.run(coro)


async def _wait_for_notice(notices: list[str], expected: str, pilot) -> None:
    for _ in range(30):
        await pilot.pause(0.1)
        if expected in notices:
            return
    raise AssertionError(f"missing notification {expected!r}: {notices!r}")


def test_install_screen_download_survives_dismiss_and_notifies(
        monkeypatch, tmp_path):
    """Closing PackInstallScreen does not cancel its App-owned download."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    local = {77: set()}
    started = threading.Event()
    release = threading.Event()

    monkeypatch.setattr(
        "tui.install_screen.tone3000.models",
        lambda _tone_id, a2_only=False: [
            {"id": 1, "name": "one.nam",
             "architecture": "SlimmableContainer"}])
    monkeypatch.setattr(
        "tui.install_screen.library.downloaded_model_ids_by_tone",
        lambda: local)

    def fake_import(tone_id, _progress, **_kwargs):
        started.set()
        assert release.wait(3)
        local[tone_id] = {1}
        return {"id": tone_id, "revision": "r"}

    monkeypatch.setattr("tui.install_screen.library.import_tone", fake_import)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        notices = []
        worker_calls = []
        real_run_worker = app.run_worker

        def record_run_worker(*args, **kwargs):
            worker_calls.append(dict(kwargs))
            return real_run_worker(*args, **kwargs)

        monkeypatch.setattr(app, "run_worker", record_run_worker)
        monkeypatch.setattr(
            app, "notify",
            lambda message, **_kwargs: notices.append(str(message)))

        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(PackInstallScreen({
                "id": 77, "title": "Remote Pack", "gear": "amp",
                "username": "tester", "downloads_count": 2}))
            await pilot.pause(0.4)
            screen = app.screen
            assert screen._selected == {1}

            screen._confirm()
            await pilot.pause(0.2)
            assert started.is_set()
            assert any(
                call.get("name") == "tone-download"
                and call.get("exclusive") is False
                for call in worker_calls)

            # Leave before the worker has a chance to enter _install. The
            # download must still start after the screen lifecycle ends.
            await pilot.press("escape")
            assert not isinstance(app.screen, PackInstallScreen)
            release.set()
            await _wait_for_notice(notices, "Downloaded 1 model", pilot)
            assert started.is_set()
            assert notices.count("Downloaded 1 model") == 1

    run(scenario())


def test_detail_download_survives_leaving_tone_and_notifies(
        monkeypatch, tmp_path):
    """Leaving an inline PACK view does not cancel its App-owned download."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.panels.tone3000.verify_username",
                        lambda _username: None)

    local = {77: set()}
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(
        "tui.panels.library.downloaded_model_ids_by_tone",
        lambda: local)

    def fake_import(tone_id, _progress, **_kwargs):
        started.set()
        assert release.wait(3)
        local[tone_id] = {1, 2}
        return {"id": tone_id, "revision": "r"}

    monkeypatch.setattr("tui.panels.library.import_tone", fake_import)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        notices = []
        worker_calls = []
        real_run_worker = app.run_worker

        def record_run_worker(*args, **kwargs):
            worker_calls.append(dict(kwargs))
            return real_run_worker(*args, **kwargs)

        monkeypatch.setattr(app, "run_worker", record_run_worker)
        monkeypatch.setattr(
            app, "notify",
            lambda message, **_kwargs: notices.append(str(message)))

        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane._enter_selection(
                tone={"id": 77, "title": "Remote Pack", "gear": "amp",
                      "username": "tester"},
                models=[
                    {"id": 1, "name": "one.nam",
                     "architecture": "SlimmableContainer"},
                    {"id": 2, "name": "two.nam",
                     "architecture": "SlimmableContainer"},
                ],
                origin="description", remote=True)
            pane._pack_picked = {"m1", "m2"}
            pane._pack_install_selected()
            await pilot.pause(0.2)
            assert started.is_set()
            assert any(
                call.get("name") == "tone-download"
                and call.get("exclusive") is False
                for call in worker_calls)

            pane._exit_pack_mode()
            release.set()
            await _wait_for_notice(notices, "Downloaded 2 models", pilot)
            assert notices.count("Downloaded 2 models") == 1

    run(scenario())
