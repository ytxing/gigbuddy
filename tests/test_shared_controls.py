"""Focused acceptance checks for the v0.2 shared Library controls."""

import asyncio
import json

from rich.text import Text
from textual.events import Click
from textual.widgets import DataTable, Select
from textual.widgets._data_table import ColumnKey

import library
from tui.app import GigBuddyApp
from tui.library_panel import LibraryPanel
from tui.modals import set_border_hint_hover
from tui.panels import ChainPanel
from tui.view_controls import SearchBar, ViewTabStrip


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
            assert app.query_one("TabbedContent").active == "pane-creators"

            await pilot.press("[")
            await pilot.pause()
            assert app.query_one("TabbedContent").active == "pane-tone"

    run(scenario())


def test_clicking_library_search_stays_in_library(monkeypatch, tmp_path):
    _patch_remote(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            search = app.query_one("#local-search")
            chain = app.query_one(ChainPanel)

            await pilot.click(search)
            await pilot.pause()

            assert app.focused is search
            assert not any(ancestor is chain
                           for ancestor in search.ancestors_with_self)

    run(scenario())


def test_app_chain_router_ignores_bubbled_click_from_library(
        monkeypatch, tmp_path):
    _patch_remote(monkeypatch, tmp_path)
    chain_file = tmp_path / "live_chain.json"
    chain_file.write_text(json.dumps({
        "slots": [{"path": None}],
        "gain": 1.0,
        "master": 1.0,
        "quality": 1.0,
    }), encoding="utf-8")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", chain_file)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            chain = app.query_one(ChainPanel)
            library_panel = app.query_one(LibraryPanel)
            search = app.query_one("#local-search")
            assert chain.slot_widgets

            search.focus()
            await pilot.pause()
            x = library_panel.region.x + 1
            y = library_panel.region.y + 1
            event = Click(
                library_panel, 1, 1, 0, 0, 1, False, False, False, x, y)
            app.on_click(event)
            await pilot.pause()

            assert app.focused is search
            assert not chain.slot_widgets[0].has_focus

    run(scenario())


def test_view_tab_strip_uses_border_separator_and_underlined_active_tab():
    strip = ViewTabStrip(
        "TONE DETAIL", [("description", "DESCRIPTION"), ("selection", "PACK")]
    )

    assert strip.render().plain == "TONE DETAIL  ──  DESCRIPTION / PACK"


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
            type_select = app.query_one("#type-filter-tone-search", Select)
            assert type_select.region.x + type_select.region.width == \
                bar.region.x + bar.region.width

    run(scenario())


def test_border_hint_hover_resolves_textual_ansi_theme_colors():
    """Rich hover spans must not receive Textual-only ansi_* color names."""
    class Widget:
        border_subtitle = "enter detail · esc back"
        app = type("App", (), {
            "theme_variables": {
                "accent": "ansi_green",
                "background": "ansi_black",
            }
        })()

    widget = Widget()
    set_border_hint_hover(widget, "enter detail")

    assert widget.border_subtitle.spans
    assert all("ansi_" not in str(span.style)
               for span in widget.border_subtitle.spans)


def test_type_filter_is_inline_and_table_headers_are_not_filterable(
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
            type_select = app.query_one("#type-filter-tone-search", Select)

            assert type_select._options == [
                ("ALL", "all"), ("AMP", "amp"),
                ("OUTBOARD", "outboard")]

            panel.on_data_table_header_selected(
                DataTable.HeaderSelected(
                    table, ColumnKey("author"), 8, Text("Author")))
            await pilot.pause()
            assert type_select.value == "all"

            panel.on_data_table_header_selected(
                DataTable.HeaderSelected(
                    table, ColumnKey("type"), 3, Text("Type")))
            await pilot.pause()
            assert type_select.value == "all"

            type_select.value = "amp"
            await pilot.pause(0.4)
            assert panel._type_filters["pane-tone"] == "amp"

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
