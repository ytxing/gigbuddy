"""Regression coverage for the TONE3000 login state in LibraryPanel."""

import asyncio
import threading

from textual.widgets import Button, DataTable, Select

from tui.app import GigBuddyApp
from tui.library_panel import LibraryPanel

import library
import tone3000


def run(coro):
    return asyncio.run(coro)


def test_auth_failure_is_left_aligned_and_offers_login(monkeypatch):
    def requires_login(*_args, **_kwargs):
        raise tone3000.AuthenticationRequiredError("login required")

    monkeypatch.setattr(library.tone3000, "search", requires_login)
    monkeypatch.setattr(library.tone3000, "top_creators",
                        lambda **_kwargs: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(0.8)
            table = app.query_one("#lib-table-tone", DataTable)
            button = app.query_one("#tone-login-button", Button)
            assert table.ordered_rows[0].key.value == "__status__"
            assert "login required" in str(
                table.get_cell("__status__", "title")).lower()
            assert button.display
            assert any(
                "Log in to TONE3000" in segment.text
                for row in range(button.region.height)
                for segment in button.render_line(row)
            )

    run(scenario())


def test_creator_auth_failure_offers_login_and_reloads_creators(monkeypatch):
    calls = {"creators": 0, "login": 0}

    def top_creators(**_kwargs):
        calls["creators"] += 1
        if calls["login"] == 0:
            raise tone3000.AuthenticationRequiredError("login required")
        return [{
            "id": "user:tester", "username": "tester",
            "public_tones_count": 8, "downloads_count": 12,
            "favorites_count": 3, "public_models_count": 5,
        }]

    monkeypatch.setattr(library.tone3000, "search", lambda *_a, **_k: [])
    monkeypatch.setattr(library.tone3000, "top_creators", top_creators)
    monkeypatch.setattr(library.tone3000, "login",
                        lambda: calls.__setitem__("login", 1) or {
                            "access_token": "access"})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(0.8)
            panel = app.query_one(LibraryPanel)
            table = app.query_one("#lib-table-creators", DataTable)
            button = app.query_one("#creators-login-button", Button)
            assert table.ordered_rows[0].key.value == "__status__"
            assert "login required" in str(
                table.get_cell("__status__", "creator")).lower()
            assert button.display
            assert any(token == "log in" for token, _action in
                       panel._border_hint_actions())
            await pilot.click("#creators-login-button")
            await pilot.pause(0.8)
            assert calls["login"] == 1
            assert table.ordered_rows[0].key.value == "creator:tester"
            assert not button.display

    run(scenario())


def test_login_button_reloads_current_tone_view(monkeypatch):
    calls = {"search": 0, "login": 0}

    def search(*_args, **_kwargs):
        calls["search"] += 1
        if calls["search"] < 3:
            raise tone3000.AuthenticationRequiredError("login required")
        return [{"id": 7, "title": "Logged-in tone", "gear": "amp",
                 "username": "tester", "downloads_count": 1,
                 "favorites_count": 0, "a2_models_count": 1}]

    monkeypatch.setattr(library.tone3000, "search", search)
    monkeypatch.setattr(library.tone3000, "top_creators",
                        lambda **_kwargs: [])
    monkeypatch.setattr(library.tone3000, "login",
                        lambda: calls.__setitem__("login", calls["login"] + 1)
                        or {"access_token": "access"})
    def current_user():
        if calls["login"] == 0:
            raise tone3000.AuthenticationRequiredError("login required")
        return {"id": 1, "username": "tester"}

    monkeypatch.setattr(library.tone3000, "current_user", current_user)
    monkeypatch.setattr(library, "mark_download_state", lambda rows: rows)
    monkeypatch.setattr(library.tone3000, "verify_username", lambda _name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(0.8)
            button = app.query_one("#tone-login-button", Button)
            assert button.display
            await pilot.click("#tone-login-button")
            await pilot.pause(0.8)
            table = app.query_one("#lib-table-tone", DataTable)
            assert calls["login"] == 1
            assert not button.display
            assert table.ordered_rows[0].key.value == "remote:7"
            assert app.sub_title == (
                "tester's one-stop NAM tone manager")

    run(scenario())


def test_header_auth_button_logs_in_and_out(monkeypatch):
    calls = {"login": 0, "logout": 0}

    def current_user():
        if calls["login"] == 0 or calls["logout"]:
            raise tone3000.AuthenticationRequiredError("login required")
        return {"id": 1, "username": "tester"}

    monkeypatch.setattr(library.tone3000, "current_user", current_user)
    monkeypatch.setattr(
        library.tone3000, "login",
        lambda: calls.__setitem__("login", calls["login"] + 1)
        or {"access_token": "access"},
    )
    monkeypatch.setattr(
        library.tone3000, "logout",
        lambda: calls.__setitem__("logout", calls["logout"] + 1) or True,
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            button = app.query_one("#header-auth", Button)
            clock = app.query_one("HeaderClock")
            assert button.region.width == 12
            assert button.region.right == clock.region.x
            assert button.label.plain == "log in"
            assert "log in" in button.render_line(0).text

            await pilot.click(button)
            await pilot.pause(0.8)
            assert calls["login"] == 1
            assert button.label.plain == "log out"
            assert "log out" in button.render_line(0).text
            assert app.sub_title == (
                "tester's one-stop NAM tone manager")

            await pilot.click(button)
            await pilot.pause(0.3)
            assert calls["logout"] == 1
            assert button.label.plain == "log in"
            assert "log in" in button.render_line(0).text
            assert app.sub_title == GigBuddyApp.DEFAULT_SUB_TITLE

    run(scenario())


def test_tone_rows_render_before_download_state_probe(monkeypatch, tmp_path):
    """The remote page must remain usable while local status is enriched."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    release_probe = threading.Event()
    probe_started = threading.Event()

    def search(*_args, **_kwargs):
        return [{"id": 7, "title": "Visible tone", "gear": "amp",
                 "username": "tester", "downloads_count": 1,
                 "favorites_count": 0, "a2_models_count": 1}]

    def slow_mark(rows):
        if rows:
            probe_started.set()
        release_probe.wait(2)
        return [{**row, "download_state": "all", "downloaded": 1}
                for row in rows]

    monkeypatch.setattr(library.tone3000, "search", search)
    monkeypatch.setattr(library.tone3000, "top_creators",
                        lambda **_kwargs: [])
    monkeypatch.setattr(library, "mark_download_state", slow_mark)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(0.3)
            table = app.query_one("#lib-table-tone", DataTable)
            assert probe_started.is_set()
            assert table.ordered_rows[0].key.value == "remote:7"
            assert not app.query_one(LibraryPanel)._tone_loading
            release_probe.set()
            await pilot.pause(0.3)
            assert "✓" in str(table.get_cell("remote:7", "title"))

    run(scenario())


def test_favorite_rows_render_before_download_state_probe(monkeypatch, tmp_path):
    """The favorites view uses the same non-blocking enrichment boundary."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    release_probe = threading.Event()
    probe_started = threading.Event()

    tone = {"id": 8, "title": "Visible favorite", "gear": "amp",
            "username": "tester", "downloads_count": 2,
            "favorites_count": 3, "a2_models_count": 1}

    def slow_mark(rows):
        if rows:
            probe_started.set()
        release_probe.wait(2)
        return [{**row, "download_state": "all", "downloaded": 1}
                for row in rows]

    monkeypatch.setattr(library.tone3000, "search", lambda *_a, **_k: [])
    monkeypatch.setattr(library.tone3000, "top_favorites",
                        lambda *_a, **_k: [dict(tone)])
    monkeypatch.setattr(library.tone3000, "top_creators",
                        lambda **_kwargs: [])
    monkeypatch.setattr(library, "mark_download_state", slow_mark)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(LibraryPanel)
            panel.activate_view_tab("pane-tone")
            await pilot.pause(0.1)
            app.query_one("#sort-filter", Select).value = "favorites"
            await pilot.pause(0.3)
            table = app.query_one("#lib-table-tone", DataTable)
            assert probe_started.is_set()
            assert table.ordered_rows[0].key.value == "remote:8"
            assert not panel._tone_loading
            release_probe.set()
            await pilot.pause(0.3)
            assert "✓" in str(table.get_cell("remote:8", "title"))

    run(scenario())


def test_creator_initial_load_uses_one_official_page(monkeypatch, tmp_path):
    """The TUI must not compose ten official pages before first render."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    calls = []
    rows = [{"id": f"user:{i}", "username": f"creator{i}",
             "public_tones_count": 10 - i, "downloads_count": i,
             "favorites_count": 0, "public_models_count": 1}
            for i in range(10)]

    def top_creators(**kwargs):
        calls.append(kwargs)
        return [dict(row) for row in rows]

    monkeypatch.setattr(library.tone3000, "top_creators", top_creators)
    monkeypatch.setattr(library.tone3000, "search", lambda *_a, **_k: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(0.4)
            panel = app.query_one(LibraryPanel)
            table = app.query_one("#lib-table-creators", DataTable)
            assert calls
            assert all(call["page_size"] == 10 for call in calls)
            assert table.row_count == 10
            assert panel._creator_has_more

    run(scenario())
