"""ChainParams single-focus-stop interaction contract (v0.1.1 port).

The v0.2 split-focus overlay mechanism (ChainParamFocusStop children over
each parameter column) is gone: ChainParams itself is the one focus stop for
the parameter row, the App owns the g/G/m/M/q/Q step bindings, and the
widget's edit_guard shadows them while focused.
"""

import asyncio

from tui import live
from tui.app import GigBuddyApp
from tui.panels import ChainPanel


def run(coro):
    return asyncio.run(coro)


def _patch_chain(monkeypatch, tmp_path):
    """Isolate the live chain file so stepping tests never touch repo data."""
    chain_file = tmp_path / "live_chain.json"
    monkeypatch.setattr(live, "CHAIN_FILE", chain_file)


def test_chain_params_is_single_focus_stop_without_overlays(
        monkeypatch, tmp_path):
    """ChainParams takes focus itself; no overlay children; row unchanged."""

    _patch_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(220, 55)) as pilot:
            await pilot.pause(0.3)
            params = app.query_one(ChainPanel).params

            # split-focus mechanism is gone: no overlay children, and the
            # row is itself the focus stop (v0.1.1 contract).
            assert params.can_focus
            assert not hasattr(params, "split_focus")
            assert list(params.query("*")) == []
            assert not app.query(  # nothing on screen still has the class
                "ChainParamFocusStop")

            params.focus()
            await pilot.pause()
            assert app.focused is params

            # Inspect the composed screen row: bracketed key buttons, fixed
            # signed values, and no overlay duplicate.
            screen_row = app.screen._compositor.render_strips()[
                params.region.y].text
            visible = screen_row[
                params.content_region.x:params.content_region.right]
            assert visible.startswith(
                "gain  [g] 1.00 [G]   master  [m] 1.00 [M]   "
                "quality  [q] 1.00 [Q]")
            assert all(
                duplicate not in visible
                for duplicate in ("GGAIN", "MMASTER", "QQUALITY"))

    run(scenario())


def test_global_step_keys_fire_off_params_row_and_guard_shadows_them(
        monkeypatch, tmp_path):
    """g/G/m/M/q/Q step from anywhere (App bindings); ChainParams focus
    swallows them via edit_guard; space/s/l playback keys are also shadowed
    while the row is focused (they live on ChainPanel as its parent)."""

    _patch_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(220, 55)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(ChainPanel)
            params = panel.params
            app.set_focus(panel)
            await pilot.pause()
            assert app.focused is panel

            # Global App bindings fire while focus is outside ChainParams.
            await pilot.press("g")
            await pilot.pause(0.2)
            assert live.read_chain().get("gain") == 0.95
            await pilot.press("G")
            await pilot.pause(0.2)
            assert live.read_chain().get("gain") == 1.0
            await pilot.press("m")
            await pilot.pause(0.2)
            assert live.read_chain().get("master") == 0.95
            await pilot.press("M")
            await pilot.pause(0.2)
            assert live.read_chain().get("master") == 1.0
            await pilot.press("q")
            await pilot.pause(0.2)
            assert live.read_chain().get("quality") == 0.95
            await pilot.press("Q")
            await pilot.pause(0.2)
            assert live.read_chain().get("quality") == 1.0

            # Playback key reaches ChainPanel while the panel is focused.
            calls = []
            app.action_playback_toggle = lambda: calls.append("toggle")
            await pilot.press("space")
            await pilot.pause(0.2)
            assert calls == ["toggle"]

            # ChainParams focused: edit_guard swallows step and playback keys.
            params.focus()
            await pilot.pause()
            assert app.focused is params
            calls.clear()
            await pilot.press("g")
            await pilot.press("M")
            await pilot.press("q")
            await pilot.press("space")
            await pilot.pause(0.2)
            assert live.read_chain().get("gain") == 1.0
            assert live.read_chain().get("master") == 1.0
            assert live.read_chain().get("quality") == 1.0
            assert calls == []

    run(scenario())


def test_value_edit_commits_and_escape_cancels(monkeypatch, tmp_path):
    """Keep the two essential value-edit outcomes: apply and cancel."""

    _patch_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(220, 55)) as pilot:
            await pilot.pause(0.3)
            params = app.query_one(ChainPanel).params
            _index, start, end = params._value_spans[0]
            value_x = params.content_region.x + (start + end) // 2
            markup = params._parameter_markup(0, 10)
            assert "$text on $surface-lighten-1" in markup
            assert "[b $text" not in markup

            await pilot.click(offset=(value_x, params.region.y))
            await pilot.pause()
            assert params._editing == 0
            assert "▌" in params._parameter_markup(0, None)
            assert "not bold $text on $surface-lighten-1" in (
                params._parameter_markup(0, None))
            params._cursor_visible = False
            assert "▌" not in params._parameter_markup(0, None)
            params._cursor_visible = True
            await pilot.press("backspace", "backspace", "7")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert params._editing is None
            assert live.read_chain().get("gain") == 1.7

            await pilot.click(offset=(value_x, params.region.y))
            await pilot.pause()
            assert params._editing == 0
            await pilot.press("escape")
            await pilot.pause()
            assert params._editing is None
            assert live.read_chain().get("gain") == 1.7

            # A direct double-click from the normal display resets the value
            # instead of leaving the first click in edit mode.
            await pilot.double_click(offset=(value_x, params.region.y))
            await pilot.pause(0.2)
            assert params._editing is None
            assert live.read_chain().get("gain") == 1.0

    run(scenario())


def test_click_tokens_and_blank_do_not_move_focus(monkeypatch, tmp_path):
    """Key buttons step without stealing focus; blank space is inert."""

    _patch_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(220, 55)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(ChainPanel)
            params = panel.params
            app.set_focus(panel)
            await pilot.pause()

            # Bump gain with the keyboard so the mouse step is observable.
            await pilot.press("G")
            await pilot.pause(0.2)
            assert live.read_chain().get("gain") == 1.05

            # [g] button: mouse step down (0.05), no focus move.
            token_x = params.content_region.x + params._spans[0][1]
            await pilot.click(offset=(token_x, params.region.y))
            await pilot.pause()
            assert app.focused is panel
            assert params._editing is None
            assert live.read_chain().get("gain") == 1.0

            # blank right edge: no focus move, no state change.
            blank_x = params.content_region.right - 2
            await pilot.click(offset=(blank_x, params.region.y))
            await pilot.pause()
            assert app.focused is panel
            assert live.read_chain().get("gain") == 1.0

    run(scenario())
