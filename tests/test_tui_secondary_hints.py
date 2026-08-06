"""Regression tests for the single-token secondary-menu hover treatment."""

import asyncio
from types import SimpleNamespace

from rich.cells import cell_len
from textual.geometry import Region, Size
from textual.events import MouseMove

from tui.app import GigBuddyApp
from tui.modals import (border_hint_label, border_hint_segments, hint_span,
                        refresh_border_hint_layout, set_border_hint_layout)
from tui.panels import ChainPanel, DetailPane


def run(coro):
    return asyncio.run(coro)


def test_hint_layout_keeps_dynamic_state_left_of_stable_actions():
    widget = SimpleNamespace(
        region=Region(0, 0, 48, 4), size=Size(44, 2), border_subtitle="")
    actions = ("enter load", "esc back")

    short = set_border_hint_layout(widget, "ready", actions)
    long = set_border_hint_layout(widget, "loading pack 2/3", actions)

    assert short.endswith("enter load · esc back")
    assert long.endswith("enter load · esc back")
    assert cell_len(long) <= widget.region.width - 6
    assert long.index("loading") < long.index("enter load")


def test_hint_layout_resize_hides_complete_tokens_only():
    widget = SimpleNamespace(
        region=Region(0, 0, 48, 4), size=Size(44, 2), border_subtitle="")
    actions = ("d delete", "space play/pause", "s stop", "l loop")
    set_border_hint_layout(widget, "", actions)

    widget.region = Region(0, 0, 20, 4)
    label = refresh_border_hint_layout(widget)
    segments = border_hint_segments(widget)

    assert label == border_hint_label(widget)
    assert cell_len(label) <= widget.region.width - 6
    assert "l l" not in label
    assert all(segment in {"d", "space", "s", "l", "d del", "s stop", "l loop"}
               for segment in segments)


def test_key_only_action_after_dynamic_state_keeps_a_clickable_span():
    widget = SimpleNamespace(
        region=Region(0, 0, 22, 4), size=Size(18, 2), border_subtitle="")
    set_border_hint_layout(widget, "busy", ("enter load", "esc back"))
    label = border_hint_label(widget)

    assert label.endswith(" · enter · esc")
    assert cell_len(label) <= widget.region.width - 6
    assert hint_span(label, "enter load") == hint_span(label, "enter")
    assert hint_span(label, "esc back") == hint_span(label, "esc")


def test_library_more_is_a_clickable_action_token():
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one("LibraryPanel")
            panel._active_pane = "pane-tone"
            panel._tone_has_more = True
            panel._tone_error = False
            panel._tone_loading = False
            panel._remote_tones = {1: {"id": 1}}
            panel._tone_total = 2
            panel._update_tone_subtitle()
            label = border_hint_label(panel)
            assert "↓ more" in border_hint_segments(panel)
            span = hint_span(label, "↓ more")
            assert span is not None

            calls = []
            panel._load_more_from_hint = lambda: calls.append(True)
            label_start = panel.region.x + max(
                1, panel.region.width - cell_len(label) - 2)
            event = SimpleNamespace(
                screen_x=label_start + (span[0] + span[1]) // 2,
                screen_y=panel.region.bottom - 1,
                stop=lambda: None,
            )
            assert panel._click_border_hint(event)
            assert calls == [True]

    run(scenario())


def test_chain_hint_hover_styles_only_the_token_under_the_pointer():
    """Moving across action tokens must never color the whole hint."""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            label = border_hint_label(panel)
            label_start = panel.region.x + max(
                1, panel.region.width - cell_len(label) - 2)

            # The first visible segment is dynamic state (for example
            # ``0/6 slots``), not a clickable action token.
            for token in border_hint_segments(panel)[1:]:
                span = hint_span(label, token)
                assert span is not None
                x = label_start + (span[0] + span[1]) // 2
                y = panel.region.bottom - 1
                await pilot._post_mouse_events(
                    [MouseMove],
                    widget=panel,
                    offset=(x - panel.region.x, y - panel.region.y),
                    button=0,
                )
                await pilot.pause()

                styled = panel._border_subtitle
                assert styled.plain == label
                assert len(styled._spans) == 1
                only_span = styled._spans[0]
                assert (only_span.start, only_span.end) == span

    run(scenario())


def test_secondary_hints_fit_the_compact_single_hint_strip():
    """Responsive labels stay readable in the right-aligned hint strip."""

    async def scenario():
        for size in ((80, 35), (120, 40), (134, 40), (140, 50)):
            app = GigBuddyApp(spawn_engine=False)
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                for selector in ("LibraryPanel", "PresetPanel", "ChainPanel", "DetailPane"):
                    widget = app.query_one(selector)
                    # Textual reserves two edge cells and two corner cells,
                    # plus the two cells passed into its label renderer.
                    assert cell_len(border_hint_label(widget)) <= widget.region.width - 6
                chain = app.query_one(ChainPanel)
                segments = border_hint_segments(chain)
                assert any(segment in {"d", "d del", "d delete"}
                           for segment in segments[1:])
                # Low-priority playback may be hidden on the narrowest
                # supported surface; core Slot mutation stays visible.

    run(scenario())


def test_chain_panel_does_not_leave_growth_gap_before_parameters():
    """The zero-slot v0.2 panel has no fixed-row growth gap."""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(134, 50)) as pilot:
            await pilot.pause()
            chain = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            params = chain.query_one(".chain-params")
            assert chain.region.height == 7
            assert detail.region.y == chain.region.bottom
            assert params.region.bottom == chain.content_region.bottom

    run(scenario())


def test_chain_hint_exposes_both_model_switch_directions():
    """Each displayed model direction must be its own clickable action."""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chain = app.query_one(ChainPanel)
            label = border_hint_label(chain)
            assert "↑/↓ model" not in label
            assert "↑ model" in label
            assert "↓ model" in label
            # 键名全部小写（d/space/s/l 才是真实绑定，D/Space 会误导）
            assert not any(c.isupper() for c in label)

    run(scenario())


def test_header_title_is_centered_and_status_slot_collapses():
    """REQ-006: 标题 "GigBuddy — Your one-stop NAM tone manager" 必须居中——
    HeaderTitle content-align center；隐藏的状态槽不再占 64 宽（否则标题被挤偏）。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            title = app.query_one("HeaderTitle")
            assert title.styles.content_align == ("center", "middle")
            status = app.query_one("#header-status")
            assert status.region.width <= 1, "隐藏状态槽仍占位，标题被挤偏"
            # 状态出现时恢复 64 宽占位
            status.show_status("engine restarted", "information")
            await pilot.pause()
            assert status.region.width >= 32

    run(scenario())
