"""Regression: a slow async load finishing after the user left the tab must
not switch TabbedContent back to it.

Root cause: load workers end with `table.focus()`, and Textual's
TabbedContent treats any focus inside a pane as a tab switch request
(TabPane.Focused → `active` = pane id). `Screen.set_focus` does not check
whether the widget is in a hidden pane, so focusing a table in a pane the
user has left yanks the UI back to the tab that started the load.
"""
import asyncio
import time

from textual.widgets import TabbedContent

import library
from tui.app import GigBuddyApp


def run(coro):
    return asyncio.run(coro)


def slow_tone() -> dict:
    return {"id": 1, "title": "Slow tone", "gear": "amp", "username": "alice",
            "downloads_count": 1, "favorites_count": 0, "a2_models_count": 1,
            "published_at": "2026-01-01T00:00:00Z", "total_count": 1,
            "download_state": "none"}


def test_slow_tone_load_does_not_yank_back(monkeypatch, tmp_path):
    """Leave TONE3000 while its first page is still loading: when the load
    finishes the app must stay on LOCAL."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    def slow_search(*args, **kwargs):
        # Runs inside asyncio.to_thread: sleep blocks the worker thread, not
        # the event loop, so the app stays interactive while it is in flight.
        # 1.5s > the click-pause windows below: the load is guaranteed to be
        # still in flight when we leave the tab.
        time.sleep(1.5)
        return [slow_tone()]

    monkeypatch.setattr(library.tone3000, "search", slow_search)
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            tabs = app.query_one(TabbedContent)
            # 1. Go to TONE3000; the 0.1s tick sees the switch and starts the
            #    slow reload worker.
            await pilot.click(app.query_one("#--content-tab-pane-tone"))
            await pilot.pause(0.4)
            assert tabs.active == "pane-tone"
            # 2. Leave for LOCAL while the load is still in flight.
            await pilot.click(app.query_one("#--content-tab-pane-local"))
            await pilot.pause(0.3)
            # 3. Let the slow search finish. It must not pull the UI back.
            await pilot.pause(1.8)
            assert tabs.active == "pane-local"
            assert app.screen.focused is None or (
                app.screen.focused.id in ("local-search", "lib-table-local"))
    run(scenario())


def test_slow_load_stays_when_still_on_tone_tab(monkeypatch, tmp_path):
    """Control: while staying on TONE3000, a completing load keeps focus in
    the tone table (the guard must not break the normal path)."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    def slow_search(*args, **kwargs):
        # Runs inside asyncio.to_thread: sleep blocks the worker thread, not
        # the event loop, so the app stays interactive while it is in flight.
        # 1.5s > the click-pause windows below: the load is guaranteed to be
        # still in flight when we leave the tab.
        time.sleep(1.5)
        return [slow_tone()]

    monkeypatch.setattr(library.tone3000, "search", slow_search)
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await pilot.click(app.query_one("#--content-tab-pane-tone"))
            await pilot.pause(0.4)
            await pilot.pause(1.8)  # let the load finish in place
            assert app.query_one(TabbedContent).active == "pane-tone"
            assert app.screen.focused.id == "lib-table-tone"
    run(scenario())


def _creator_hits(offset: int, count: int, total: int) -> list[dict]:
    """A page from TONE3000's official creator leaderboard."""
    return [
        {"id": str(1000 + offset + i), "username": f"creator{offset + i}",
         "public_tones_count": total - offset - i,
         "downloads_count": 10, "favorites_count": 0,
         "public_models_count": 1}
        for i in range(count)
    ]


def test_creator_load_more_keeps_cursor_and_viewport(monkeypatch, tmp_path):
    """Appending a TOP CREATORS page rebuilds the aggregate table
    (clear + refill); the restored cursor must pull the viewport back to it
    instead of leaving the view stuck at the top."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    calls = {"n": 0}

    def paged_creators(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _creator_hits(0, 100, 200)
        return _creator_hits(100, 100, 200)

    monkeypatch.setattr(library.tone3000, "top_creators", paged_creators)
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await pilot.click(app.query_one("#--content-tab-pane-creators"))
            await pilot.pause(0.4)   # tick starts the first page load
            await pilot.pause(0.6)   # first page lands
            table = app.query_one("#lib-table-creators")
            assert table.row_count == 100
            # Move the cursor near the tail to request the next page.
            table.move_cursor(row=95)
            await pilot.pause(0.3)
            key_before = table.ordered_rows[table.cursor_row].key.value
            await pilot.pause(1.0)   # append page lands
            assert table.row_count == 200
            # Cursor restored to the same row AND the viewport follows it
            # (scroll_y > 0 — before the fix it snapped back to the top).
            assert table.ordered_rows[table.cursor_row].key.value == key_before
            assert table.scroll_y > 0, "viewport jumped back to the top"
    run(scenario())


def test_creator_scroll_bottom_preserves_viewport_anchor(monkeypatch, tmp_path):
    """Appending a page keeps the existing viewport anchor.

    The next page belongs below the rows already on screen. Re-pinning to the
    new max would immediately retrigger load-more while the user is still at
    the old bottom, causing repeated requests and visible number changes.
    """
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    calls = {"n": 0}

    def paged_creators(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _creator_hits(0, 100, 200)
        return _creator_hits(100, 100, 200)

    monkeypatch.setattr(library.tone3000, "top_creators", paged_creators)
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await pilot.click(app.query_one("#--content-tab-pane-creators"))
            await pilot.pause(0.4)
            await pilot.pause(0.6)   # first page lands
            table = app.query_one("#lib-table-creators")
            assert table.row_count == 100
            # Wheel to the bottom without moving the cursor.
            table.scroll_to(y=table.max_scroll_y, animate=False)
            await pilot.pause(0.3)
            assert table.scroll_y > 0
            old_bottom = table.scroll_y
            await pilot.pause(1.0)   # append lands
            assert table.row_count == 200
            assert table.scroll_y == old_bottom, "viewport moved during append"
            assert calls["n"] == 2, "append must not recursively load another page"
    run(scenario())
