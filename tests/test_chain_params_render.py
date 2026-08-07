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

            # Inspect the composed screen row: 3-space separators, three
            # parameters, no overlay duplicate (GGAIN/MMASTER/QQUALITY were
            # the old overlay misalignment).
            screen_row = app.screen._compositor.render_strips()[
                params.region.y].text
            visible = screen_row[
                params.content_region.x:params.content_region.right]
            assert visible.startswith(
                "GAIN   1.00 g · G   MASTER   1.00 m · M   "
                "QUALITY   1.00 q · Q")
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


def test_click_token_dot_and_blank_do_not_move_focus(monkeypatch, tmp_path):
    """Clicking any non-value part of the row never steals focus; the dot
    restores the default value (REQ-027)."""

    _patch_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(220, 55)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(ChainPanel)
            params = panel.params
            app.set_focus(panel)
            await pilot.pause()

            # Bump gain with the keyboard so the dot reset is observable.
            await pilot.press("G")
            await pilot.pause(0.2)
            assert live.read_chain().get("gain") == 1.05

            # dot (content x 14): resets gain to its default (1.0),
            # focus stays on the panel.
            dot_x = params.content_region.x + 14
            await pilot.click(offset=(dot_x, params.region.y))
            await pilot.pause(0.2)
            assert app.focused is panel
            assert live.read_chain().get("gain") == 1.0

            # g token (content x 12): mouse step down (0.05), no focus move.
            token_x = params.content_region.x + 12
            await pilot.click(offset=(token_x, params.region.y))
            await pilot.pause()
            assert app.focused is panel
            assert params._editing is None
            assert live.read_chain().get("gain") == 0.95

            # blank right edge: no focus move, no state change.
            blank_x = params.content_region.right - 2
            await pilot.click(offset=(blank_x, params.region.y))
            await pilot.pause()
            assert app.focused is panel
            assert live.read_chain().get("gain") == 0.95

    run(scenario())
