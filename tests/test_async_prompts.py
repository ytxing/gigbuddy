"""REQ-008: async network prompts must be complete words, never cropped
into a partial one.

Two crop paths used to produce the "loadin" the user saw on TOP CREATORS:

1. `LibraryPanel._status_row` put the banner in the first column, which on
   the TOP CREATORS table is the fixed-width Rank column (6 cells). Fixed
   DataTable columns crop their cells at column width WITHOUT an ellipsis,
   so "Loading top creators…" rendered as "Loadin" on a narrow pane.
2. The border subtitle carried "N · loading… · Enter install"; Textual's
   border label truncation (which ends in "…") then cropped through the
   middle of the word.

Both are fixed: the banner goes into the first auto-width column, and the
loading subtitle is its own short complete line ("Loading…").
"""
import asyncio
import re
import time

from rich.style import Style
from textual._border import render_border_label
from textual.content import Content
from textual.widgets import DataTable, TabbedContent

import library
from tui.app import GigBuddyApp
from tui.library_panel import LibraryPanel


def run(coro):
    return asyncio.run(coro)


def _rendered_lines(table: DataTable) -> str:
    """Concatenate every rendered line of the table for substring checks."""
    out = []
    for y in range(table.size.height):
        strip = table.render_line(y)
        if strip:
            out.append(strip.text)
    return "".join(out)


def test_status_row_fits_auto_width_column_on_narrow_pane(monkeypatch, tmp_path):
    """The status banner must land in the first auto-width column: on the
    TOP CREATORS table that is "Creator", not the fixed Rank column, so a
    narrow pane renders the whole "Loading top creators…" instead of the
    cropped "Loadin"."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    def slow_creators(**_kwargs):
        # Keep the load in flight long enough to inspect the banner row.
        time.sleep(1.5)
        return []

    monkeypatch.setattr(library.tone3000, "search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(library.tone3000, "top_creators", slow_creators)
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        # 80x32 is the smallest supported compact terminal; the left
        # library panel is narrow enough to exercise the auto-width row.
        async with app.run_test(size=(80, 32)) as pilot:
            await pilot.pause(0.3)
            # v0.2 的 tab 切换权威是 ViewTabStrip；ContentTabs 已被移出
            # 焦点循环，click 不再切换 active pane。
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(0.5)  # load still in flight
            table = app.query_one("#lib-table-creators", DataTable)
            rendered = _rendered_lines(table)
            assert "loading…" in rendered
            # The old bug rendered the banner as the bare word "Loadin"
            # (cropped in the fixed Rank column). A complete "Loading"
            # followed by "g" is fine; a "Loadin" NOT followed by "g" is the
            # cropped artifact.
            assert not re.search(r"Loadin(?!g)", rendered)
            await pilot.pause(1.6)  # let the load finish
    run(scenario())


def test_tone_subtitle_loading_shows_complete_word():
    """`_update_tone_subtitle(loading=True)` keeps the lowercase loading token
    as its own complete line, as required by the interaction spec."""
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(LibraryPanel)
            panel.activate_view_tab("pane-tone")
            await pilot.pause(0.2)
            panel._remote_tones = {}
            panel._tone_total = None
            panel._tone_has_more = False
            panel._update_tone_subtitle(loading=True)
            assert panel.border_subtitle.startswith("loading…")
            assert " ".join(panel.border_subtitle.split()) == (
                "loading… · enter detail · [ / ] select tab")
            # The non-loading state keeps the informative form.
            # REQ-038：enter 打开二级菜单详情页，提示使用 enter detail。
            panel._update_tone_subtitle()
            assert " ".join(panel.border_subtitle.split()) == (
                "0 · all loaded · enter detail · [ / ] select tab")
            panel._tone_has_more = True
            panel._update_tone_subtitle()
            assert " ".join(panel.border_subtitle.split()) == (
                "0 · ↓ more · enter detail · [ / ] select tab")
    run(scenario())


def test_creator_subtitle_loading_shows_complete_word():
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(LibraryPanel)
            panel.activate_view_tab("pane-creators")
            await pilot.pause(0.2)
            panel._creator_tones = {}
            panel._update_creator_subtitle(loading=True)
            assert panel.border_subtitle.startswith("loading…")
            assert " ".join(panel.border_subtitle.split()) == (
                "loading… · enter search · [ / ] select tab")
            panel._creator_has_more = True
            panel._update_creator_subtitle()
            assert " ".join(panel.border_subtitle.split()) == (
                "0 · ↓ more · enter search · [ / ] select tab")
    run(scenario())


def test_loading_banner_never_truncates_to_bare_word():
    """Textual crops the border subtitle at pane width. Whatever the width,
    the "Loading…" banner must never render as a bare partial word: the crop
    either keeps it whole or ends in an ellipsis."""
    label = (Content("Loading…"), Style())
    for pane_width in range(6, 40, 2):
        segments = render_border_label(label, False, "panel", pane_width - 2,
                                       Style(), Style(), Style(), True, True)
        rendered = "".join(seg.text for seg in segments)
        # "Loadin" is fine only when it is the start of the complete word
        # "Loading…"; a "Loadin" NOT followed by "g" is the cropped bare
        # word the user saw.
        assert not re.search(r"Loadin(?!g)", rendered), (
            f"pane_width={pane_width} rendered {rendered!r} (bare word)")
