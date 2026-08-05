"""The GigBuddy header title is always horizontally centered in the header.

REQ-006b/REQ-015/REQ-018: the "GigBuddy — Your one-stop NAM tone manager"
title must read centered, and a notification must never push it aside or cover
it. The header
layout is [1fr title, content padded 10] [clock 10]; the left padding mirrors
the clock so the title's content region is symmetric about the header center.
Notifications are an overlay strip in the header's top-left corner (out of the
flow, so the title's center never moves); their width is capped below the
title's left edge, so they never cover the title and stay within the single
header row (never touching the library panel below).
"""
import asyncio

from rich.cells import cell_len
from textual.widgets._header import HeaderTitle

from tui.app import GigBuddyApp, HeaderStatus


def run(coro):
    return asyncio.run(coro)


def title_center(title) -> int:
    return title.content_region.x + title.content_region.width // 2


def header_center(header) -> int:
    return header.region.x + header.region.width // 2


def title_left_edge(title) -> int:
    """Left edge of the rendered title text inside its content region."""
    width = cell_len(str(title.render()))
    return title.content_region.x + (title.content_region.width - width) // 2


def test_header_title_is_horizontally_centered(monkeypatch):
    """The title's content region is symmetric about the header center."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            header = app.query_one("GigBuddyHeader")
            title = app.query_one(HeaderTitle)
            assert title_center(title) == header_center(header)
            # the title keeps full width minus the clock on the right
            assert title.region.width == header.region.width - 10

    run(scenario())


def test_notification_strip_in_header_keeps_title_centered(monkeypatch):
    """The notification is an overlay strip in the header's top-left corner:
    the title stays perfectly centered while it is visible and after it
    clears (REQ-015/REQ-018)."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            header = app.query_one("GigBuddyHeader")
            title = app.query_one(HeaderTitle)
            status = app.query_one(HeaderStatus)
            title_region = title.region
            assert title_center(title) == header_center(header)
            app.notify("short notice", timeout=0.5)
            await pilot.pause(0.1)
            assert status.has_class("header-status--visible")
            # the strip stays inside the single header row, top-left corner
            assert status.region.y == 0
            assert status.region.x == 0
            # and never reaches the centered title's text
            assert status.region.right <= title_left_edge(title)
            assert title.region == title_region
            assert title_center(title) == header_center(header)
            await pilot.pause(0.6)
            assert not status.has_class("header-status--visible")
            # still perfectly centered after the strip clears
            assert title_center(title) == header_center(header)
            assert title.region == title_region

    run(scenario())


def test_notification_cap_scales_with_terminal_width(monkeypatch):
    """The notification strip's width is capped below the title's left edge
    on any terminal width (REQ-018: single row, never covers the title)."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(80, 40)) as pilot:
            header = app.query_one("GigBuddyHeader")
            title = app.query_one(HeaderTitle)
            status = app.query_one(HeaderStatus)
            status.show_status("Engine restarted · IN default · OUT default",
                               "warning")
            await pilot.pause()
            assert status.has_class("header-status--visible")
            # still on the header row, never on the library panel row
            assert status.region.y == 0
            assert status.region.height == 1
            # capped below the title's left edge: no overlap
            assert status.region.right <= title_left_edge(title)
            # still centered while the notification is shown
            assert title_center(title) == header_center(header)

    run(scenario())


def test_header_title_centered_on_narrow_terminal(monkeypatch):
    """Even at 40 columns the title stays centered without overflowing."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(40, 40)) as pilot:
            header = app.query_one("GigBuddyHeader")
            title = app.query_one(HeaderTitle)
            assert header.region.height == 1
            assert title_center(title) == header_center(header)
            # content region never leaves the header
            assert title.content_region.x >= 0
            assert title.content_region.right <= header.region.width

    run(scenario())
