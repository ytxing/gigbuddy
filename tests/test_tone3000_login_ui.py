"""Regression coverage for the TONE3000 login state in LibraryPanel."""

import asyncio

from textual.widgets import Button, DataTable

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

    run(scenario())
