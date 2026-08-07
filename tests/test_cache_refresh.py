"""REQ-010: TONE3000 search results are cached per (query, TYPE, SORT,
author) combination; switching filters or tabs hits the cache without a
network request; `r` manually refreshes; the app prefetches the default
TONE3000 trending and TOP CREATORS views at startup.

Tone search and the official creator leaderboard use separate API calls.
"""
import asyncio

from textual.widgets import DataTable, Input, Select

import library
from tui.app import GigBuddyApp

REMOTE_PAGE_SIZE = 40


def run(coro):
    return asyncio.run(coro)


def _hits(offset: int, count: int, total: int) -> list[dict]:
    return [
        {"id": 1000 + offset + i, "title": f"Tone {offset + i}",
         "gear": "amp", "username": "alice", "downloads_count": 10,
         "favorites_count": 0, "a2_models_count": 1,
         "published_at": "2026-01-01", "total_count": total,
         "download_state": "none"}
        for i in range(count)
    ]


def _patch(monkeypatch, tmp_path, counts: dict, search_fn=None):
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    def counting_search(*args, **kwargs):
        counts["tone"] += 1
        if search_fn is not None:
            return search_fn()
        return _hits(0, REMOTE_PAGE_SIZE, 100)

    def counting_creators(**_kwargs):
        counts["creators"] += 1
        return []

    monkeypatch.setattr(library.tone3000, "search", counting_search)
    monkeypatch.setattr(library.tone3000, "top_creators", counting_creators)
    monkeypatch.setattr(library.tone3000, "top_favorites", lambda n: [])
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)


def test_startup_prefetches_default_views(monkeypatch, tmp_path):
    """Opening the app fetches the default TONE3000 trending search and the
    TOP CREATORS aggregate exactly once, with no user action."""
    counts = {"tone": 0, "creators": 0}
    _patch(monkeypatch, tmp_path, counts)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)  # prefetch workers finish (mocks are fast)
            assert counts == {"tone": 1, "creators": 1}
            # Both views are cached: entering either tab issues no request.
            app.query_one("LibraryPanel").activate_view_tab("pane-tone")
            await pilot.pause(0.4)
            app.query_one("LibraryPanel").activate_view_tab("pane-creators")
            await pilot.pause(0.4)
            assert counts == {"tone": 1, "creators": 1}
            table = app.query_one("#lib-table-tone", DataTable)
            assert table.row_count == REMOTE_PAGE_SIZE
    run(scenario())


def test_cache_hit_skips_network_across_tabs_and_sorts(monkeypatch, tmp_path):
    """Re-entering TONE3000 or switching SORT back to a cached combination
    renders the cached page set without a network request; a new SORT
    fetches once and is then cached."""
    counts = {"tone": 0, "creators": 0}
    _patch(monkeypatch, tmp_path, counts)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)  # startup prefetch (1 tone + 1 creators)
            assert counts["tone"] == 1
            # First visit: cached page set shows, no new request.
            app.query_one("LibraryPanel").activate_view_tab("pane-tone")
            await pilot.pause(0.4)
            assert counts["tone"] == 1
            assert app.query_one("#lib-table-tone", DataTable).row_count == 40
            # Leave and come back: still cached.
            app.query_one("LibraryPanel").activate_view_tab("pane-local")
            await pilot.pause(0.3)
            app.query_one("LibraryPanel").activate_view_tab("pane-tone")
            await pilot.pause(0.4)
            assert counts["tone"] == 1
            # New SORT combination: one fetch.
            app.query_one("#sort-filter", Select).value = "downloads"
            await pilot.pause(0.5)
            assert counts["tone"] == 2
            # Back to the cached trending combination: no fetch.
            app.query_one("#sort-filter", Select).value = "trending"
            await pilot.pause(0.4)
            assert counts["tone"] == 2
            assert app.query_one("#lib-table-tone", DataTable).row_count == 40
    run(scenario())


def test_type_filter_switch_hits_cache(monkeypatch, tmp_path):
    """Switching the TYPE filter uses a per-combination cache slot: the new
    TYPE fetches once, returning to the previous TYPE is a cache hit."""
    counts = {"tone": 0, "creators": 0}
    _patch(monkeypatch, tmp_path, counts)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            app.query_one("LibraryPanel").activate_view_tab("pane-tone")
            await pilot.pause(0.4)
            assert counts["tone"] == 1
            app.query_one("#type-filter-tone-search", Select).value = "amp"
            await pilot.pause(0.5)
            assert counts["tone"] == 2
            app.query_one("#type-filter-tone-search", Select).value = "all"
            await pilot.pause(0.4)
            assert counts["tone"] == 2
    run(scenario())


def test_manual_refresh_reloads_current_view(monkeypatch, tmp_path):
    """`r` drops the current cache entry and re-fetches, updating the rows
    (fresh data with different ids replace the cached ones)."""
    counts = {"tone": 0, "creators": 0}

    def paged():
        if counts["tone"] == 1:  # first fetch (startup prefetch)
            return _hits(0, REMOTE_PAGE_SIZE, 100)
        return _hits(5000, REMOTE_PAGE_SIZE, 100)  # refresh returns new ids

    _patch(monkeypatch, tmp_path, counts, search_fn=paged)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            app.query_one("LibraryPanel").activate_view_tab("pane-tone")
            await pilot.pause(0.4)
            assert counts["tone"] == 1
            await pilot.press("r")  # LibraryTable binding → refresh active view
            await pilot.pause(0.5)
            assert counts["tone"] == 2
            keys = [row.key.value for row in
                    app.query_one("#lib-table-tone", DataTable).ordered_rows]
            assert "remote:6001" in keys, "refresh must show the new page set"
            # The refreshed set is now cached: leaving and returning fetches
            # nothing.
            app.query_one("LibraryPanel").activate_view_tab("pane-local")
            await pilot.pause(0.3)
            app.query_one("LibraryPanel").activate_view_tab("pane-tone")
            await pilot.pause(0.4)
            assert counts["tone"] == 2
    run(scenario())


def test_new_query_gets_own_cache_slot(monkeypatch, tmp_path):
    """Typing a new search term fetches once; clearing back to the default
    query is a cache hit."""
    counts = {"tone": 0, "creators": 0}
    _patch(monkeypatch, tmp_path, counts)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            app.query_one("LibraryPanel").activate_view_tab("pane-tone")
            await pilot.pause(0.4)
            assert counts["tone"] == 1
            search = app.query_one("#tone-search", Input)
            search.focus()
            await pilot.pause(0.1)
            search.value = "foo"
            await pilot.press("enter")
            await pilot.pause(0.5)
            assert counts["tone"] == 2
            # Back to the default (empty) query: cached trending, no fetch.
            search.value = ""
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert counts["tone"] == 2
    run(scenario())
