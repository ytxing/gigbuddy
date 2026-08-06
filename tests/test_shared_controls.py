"""Focused acceptance checks for the v0.2 shared Library controls."""

import asyncio

from rich.text import Text
from textual.widgets import DataTable
from textual.widgets._data_table import ColumnKey

import library
from tui.app import GigBuddyApp
from tui.view_controls import SearchBar, TypeFilterMenu, ViewTabStrip


def run(coro):
    return asyncio.run(coro)


def _patch_remote(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    hits = [
        {"id": 101, "title": "Amp tone", "gear": "amp",
         "username": "alice", "downloads_count": 1,
         "favorites_count": 0, "a2_models_count": 1,
         "published_at": "2026-01-01", "total_count": 2,
         "download_state": "none"},
        {"id": 102, "title": "Outboard tone", "gear": "outboard",
         "username": "bob", "downloads_count": 2,
         "favorites_count": 0, "a2_models_count": 1,
         "published_at": "2026-01-02", "total_count": 2,
         "download_state": "none"},
    ]
    monkeypatch.setattr(library.tone3000, "search",
                        lambda *_args, **_kwargs: [dict(hit) for hit in hits])
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda rows: rows)


def test_view_tabs_are_one_focus_stop_and_brackets_switch(monkeypatch, tmp_path):
    _patch_remote(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(180, 40)) as pilot:
            await pilot.pause(0.4)
            strip = app.query_one("#library-view-tabs", ViewTabStrip)
            content_tabs = app.query_one("LibraryContentTabs").query_one(
                "ContentTabs")
            app.set_focus(strip)
            await pilot.pause()
            assert app.focused is strip
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "local-search"
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.focused is strip
            assert not content_tabs.can_focus
            assert not content_tabs.can_focus_children

            await pilot.press("]")
            await pilot.pause()
            assert app.query_one("TabbedContent").active == "pane-tone"
            assert app.focused is strip
            await pilot.press("]")
            await pilot.pause()
            assert app.query_one("TabbedContent").active == "pane-favorites"
            await pilot.press("]")
            await pilot.pause()
            assert app.query_one("TabbedContent").active == "pane-creators"

            await pilot.press("[")
            await pilot.pause()
            assert app.query_one("TabbedContent").active == "pane-favorites"
            await pilot.press("[")
            await pilot.pause()
            assert app.query_one("TabbedContent").active == "pane-tone"

    run(scenario())


def test_searchbar_sort_track_does_not_move_for_long_query(monkeypatch, tmp_path):
    _patch_remote(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(180, 40)) as pilot:
            await pilot.pause(0.3)
            strip = app.query_one("#library-view-tabs", ViewTabStrip)
            app.set_focus(strip)
            await pilot.pause()
            await pilot.press("]")
            await pilot.pause(0.4)
            bar = app.query_one("#tone-search-bar", SearchBar)
            query = app.query_one("#tone-search")
            sort = app.query_one("#sort-filter")
            before = (bar.region.x, bar.region.y, bar.region.width,
                      bar.region.height, sort.region.x, sort.region.width)
            query.value = "a" * 240
            await pilot.pause()
            after = (bar.region.x, bar.region.y, bar.region.width,
                     bar.region.height, sort.region.x, sort.region.width)
            assert after == before
            assert bar.region.height == 1
            assert sort.region.x + sort.region.width == \
                bar.region.x + bar.region.width

    run(scenario())


def test_type_filter_is_dynamic_and_author_header_is_not_filterable(
        monkeypatch, tmp_path):
    _patch_remote(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(180, 40)) as pilot:
            await pilot.pause(0.6)
            panel = app.query_one("LibraryPanel")
            panel.activate_view_tab("pane-tone")
            await pilot.pause(0.3)
            table = app.query_one("#lib-table-tone", DataTable)
            menu = app.query_one(TypeFilterMenu)

            panel.on_data_table_header_selected(
                DataTable.HeaderSelected(
                    table, ColumnKey("author"), 8, Text("Author")))
            await pilot.pause()
            assert not menu.display

            panel.on_data_table_header_selected(
                DataTable.HeaderSelected(
                    table, ColumnKey("type"), 3, Text("Type")))
            await pilot.pause()
            assert menu.display
            assert menu.target_table_id == "lib-table-tone"
            assert menu._options == [
                ("ALL", "all"), ("AMP", "amp"),
                ("OUTBOARD", "outboard")]

    run(scenario())


def test_view_tab_restores_query_and_sort(monkeypatch, tmp_path):
    _patch_remote(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(180, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one("LibraryPanel")
            strip = app.query_one("#library-view-tabs", ViewTabStrip)
            app.set_focus(strip)
            await pilot.pause()
            await pilot.press("]")
            await pilot.pause(0.3)
            query = app.query_one("#tone-search")
            query.focus()
            await pilot.pause()
            query.value = "foo"
            await pilot.press("enter")
            await pilot.pause(0.3)
            app.query_one("#sort-filter").value = "downloads"
            await pilot.pause(0.3)

            app.set_focus(strip)
            await pilot.pause()
            await pilot.press("[")
            await pilot.pause()
            assert panel._active_pane == "pane-local"
            await pilot.press("]")
            await pilot.pause(0.4)
            assert panel._active_pane == "pane-tone"
            assert app.query_one("#tone-search").value == "foo"
            assert app.query_one("#sort-filter").value == "downloads"

    run(scenario())
