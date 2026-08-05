"""Narrow T06 coverage for canonical Slot-aware DetailPane behavior."""

import asyncio

from tui.app import GigBuddyApp
from tui.library_panel import ToneSelected
from tui.panels import ChainPanel, DetailPane
from tui.chain_state import SlotStatus


def run(coro):
    return asyncio.run(coro)


def _chain(paths, revision=1):
    return {
        "input": {
            "source": "instrument",
            "file": None,
            "state": "stopped",
            "loop": False,
        },
        "slots": [{"path": path} for path in paths],
        "gain": 1.0,
        "master": 1.0,
        "quality": 1.0,
        "mute": False,
        "revision": revision,
    }


def _slot_models(tmp_path):
    first = str(tmp_path / "first.nam")
    second = str(tmp_path / "second.nam")
    models = [
        {"id": 101, "tone_id": 10, "name": "first.nam",
         "local_path": first, "architecture": "SlimmableContainer"},
        {"id": 102, "tone_id": 10, "name": "second.nam",
         "local_path": second, "architecture": "SlimmableContainer"},
    ]
    tone = {"id": 10, "title": "Slot Tone", "gear": "pedal",
            "username": "creator", "models": models}
    return first, second, models, tone


def _patch_canonical_chain(monkeypatch, tmp_path):
    first, second, models, tone = _slot_models(tmp_path)
    current = {"chain": _chain([first])}

    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda _path: [dict(model) for model in models])
    monkeypatch.setattr("tui.app.library.get_tone", lambda _tone_id: tone)
    monkeypatch.setattr("tui.panels.library.local_models_by_tone",
                        lambda _path: [dict(model) for model in models])
    monkeypatch.setattr("tui.panels.library.get_tone", lambda _tone_id: tone)

    def write_chain(chain):
        saved = dict(chain)
        saved["revision"] = int(saved.get("revision", 0)) + 1
        current["chain"] = saved

    monkeypatch.setattr("tui.app.live.write_chain", write_chain)
    return current, first, second, models, tone


def test_slot_focus_opens_slot_pack_and_tracks_three_states(monkeypatch, tmp_path):
    current, first, _second, models, _tone = _patch_canonical_chain(
        monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            panel.slot_widgets[0].focus()
            await pilot.pause()

            assert panel.state.target_index == 0
            assert detail._pack_mode
            assert detail._pack_slot_index == 0
            assert detail._pack_table.get_cell("m101", "sel") == \
                "[bold $success]▶[/]"

            await pilot.press("enter")
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.BYPASS
            assert detail._pack_table.get_cell("m101", "sel") == \
                "[bold $error]▷[/]"

            await pilot.press("enter")
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.ACTIVE
            assert panel.state.slot(0).path == first
            assert current["chain"]["slots"][0]["path"] == first

    run(scenario())


def test_slot_pack_loads_by_index_and_esc_restores_slot_focus(
        monkeypatch, tmp_path):
    current, _first, second, _models, _tone = _patch_canonical_chain(
        monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            panel.slot_widgets[0].focus()
            await pilot.pause()
            table = detail._pack_table
            table.focus()
            table.move_cursor(row=1, animate=False)
            await pilot.press("enter")
            await pilot.pause()

            assert panel.state.slot(0).path == second
            assert current["chain"]["slots"][0]["path"] == second
            assert detail._pack_slot_index == 0
            assert table.get_cell("m102", "sel") == "[bold $success]▶[/]"

            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is panel.slot_widgets[0]
            assert panel.state.target_index == 0

    run(scenario())


def test_library_pack_uses_existing_target_without_changing_source(
        monkeypatch, tmp_path):
    _current, first, second, models, tone = _patch_canonical_chain(
        monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            panel.slot_widgets[0].focus()
            await pilot.pause()

            # Simulate switching the same Detail context from Library
            # Description to Pack while preserving the current target.
            detail.show(tone)
            await pilot.pause()
            detail._view_tabs.focus()
            await pilot.press("]")
            await pilot.pause()
            assert detail._pack_origin == "description"
            assert detail._pack_slot_index == 0

            detail._pack_table.focus()
            detail._pack_table.move_cursor(row=1, animate=False)
            await pilot.press("enter")
            await pilot.pause()
            assert panel.state.slot(0).path == second
            assert detail._pack_mode
            assert detail._pack_origin == "description"
            assert detail._pack_table.get_cell("m102", "sel") == \
                "[bold $success]▶[/]"
            assert panel.state.target_index == 0
            assert first != second

    run(scenario())


def test_canonical_local_enter_stays_in_detail_context(
        monkeypatch, tmp_path):
    _current, _first, _second, _models, tone = _patch_canonical_chain(
        monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            detail = app.query_one(DetailPane)
            app.on_tone_selected(ToneSelected(tone["id"]))
            await pilot.pause()
            assert detail._view_mode == "description"
            assert not detail._pack_mode
            assert detail._current_tone["id"] == tone["id"]

    run(scenario())


def test_empty_slot_clears_old_pack_and_brackets_switch_views(
        monkeypatch, tmp_path):
    _current, _first, _second, _models, _tone = _patch_canonical_chain(
        monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            panel.slot_widgets[0].focus()
            await pilot.pause()
            tabs = app.query_one("#detail-view-tabs")
            tabs.focus()
            await pilot.press("]")
            await pilot.pause()
            assert detail._view_mode == "selection"
            await pilot.press("[")
            await pilot.pause()
            assert detail._view_mode == "description"

            await pilot.click(panel.add_slot)
            await pilot.pause()
            assert panel.state.slot(1).status is SlotStatus.EMPTY
            assert detail._pack_mode is False
            assert detail._view_mode == "empty"
            assert "NONE" in str(detail._summary.content)
            assert "m101" not in {
                row.key.value for row in detail._pack_table.ordered_rows
            }

    run(scenario())
