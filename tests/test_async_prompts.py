"""REQ-008: loading prompts stay as complete words in the TUI."""
import asyncio
import re

from rich.style import Style
from textual._border import render_border_label
from textual.content import Content

from tui.app import GigBuddyApp
from tui.library_panel import LibraryPanel


def run(coro):
    return asyncio.run(coro)


def test_tone_subtitle_loading_shows_complete_word():
    """`_update_tone_subtitle(loading=True)` keeps the lowercase loading token
    as its own complete line, as required by the interaction spec."""
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(LibraryPanel)
            await pilot.click(app.query_one("#--content-tab-pane-tone"))
            await pilot.pause(0.2)
            panel._remote_tones = {}
            panel._tone_total = None
            panel._tone_has_more = False
            panel._update_tone_subtitle(loading=True)
            assert panel.border_subtitle.startswith("loading…")
            assert panel.border_subtitle.rstrip().endswith("enter detail")
            # The non-loading state keeps the informative form.
            # REQ-038：enter 打开二级菜单详情页，提示使用 enter detail。
            panel._update_tone_subtitle()
            assert " ".join(panel.border_subtitle.split()) == (
                "0 · all loaded · enter detail")
            panel._tone_has_more = True
            panel._update_tone_subtitle()
            assert " ".join(panel.border_subtitle.split()) == (
                "0 · ↓ more · enter detail")
    run(scenario())


def test_creator_subtitle_loading_shows_complete_word():
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(LibraryPanel)
            await pilot.click(app.query_one("#--content-tab-pane-creators"))
            await pilot.pause(0.2)
            panel._creator_tones = {}
            panel._update_creator_subtitle(loading=True)
            assert panel.border_subtitle.startswith("loading…")
            assert panel.border_subtitle.rstrip().endswith("enter search")
            panel._creator_has_more = True
            panel._update_creator_subtitle()
            assert " ".join(panel.border_subtitle.split()) == (
                "0 · ↓ more · enter search")
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
