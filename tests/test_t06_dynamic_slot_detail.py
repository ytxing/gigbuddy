"""Narrow T06 coverage for canonical Slot-aware DetailPane behavior."""

import asyncio
import time
from pathlib import Path

from tui.app import GigBuddyApp
from tui.install_screen import PackInstallScreen
from tui.library_panel import RemoteToneSelected, ToneSelected
from tui.modals import border_hint_label
from tui.panels import ChainPanel, DetailPane
from tui.chain_state import SlotStatus


def run(coro):
    return asyncio.run(coro)


async def _wait_for_dynamic_slots(pilot, panel, count):
    for _ in range(60):
        if (len(panel.slot_widgets) == count
                and not getattr(panel, "_recompose_pending", False)):
            return
        await pilot.pause(0.05)
    raise AssertionError(f"expected {count} stable Slot widgets")


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
    Path(first).write_bytes(b"first")
    Path(second).write_bytes(b"second")
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

    def write_chain(chain, **_kwargs):
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
            await _wait_for_dynamic_slots(pilot, panel, 1)
            panel.slot_widgets[0].focus()
            await pilot.pause()

            assert panel.state.target_index == 0
            assert detail._pack_mode
            assert detail._pack_slot_index == 0
            assert detail._pack_table.get_cell("m101", "sel") == \
                "\\[ ] [bold $success]▶[/]"

            await pilot.press("enter")
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.BYPASS
            assert detail._pack_table.get_cell("m101", "sel") == \
                "\\[ ] [bold $error]▷[/]"

            await pilot.press("enter")
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.ACTIVE
            assert panel.state.slot(0).path == first
            assert current["chain"]["slots"][0]["path"] == first

    run(scenario())


def test_slot_pack_lists_remote_missing_models_and_installs_cursor_row(
        monkeypatch, tmp_path):
    first = str(tmp_path / "first.nam")
    local_model = {
        "id": 101, "tone_id": 10, "name": "first.nam",
        "local_path": first, "architecture": "SlimmableContainer",
    }
    Path(first).write_bytes(b"first")
    tone = {
        "id": 10, "title": "Slot Tone", "gear": "amp",
        "username": "creator", "models_count": 2, "a2_models_count": 2,
        "models": [local_model],
    }
    remote_models = [
        {"id": 101, "tone_id": 10, "name": "first.nam",
         "architecture": "SlimmableContainer"},
        {"id": 102, "tone_id": 10, "name": "second.nam",
         "architecture": "SlimmableContainer"},
    ]
    current = {"chain": _chain([first])}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda _path: [dict(local_model)])
    monkeypatch.setattr("tui.panels.library.local_models_by_tone",
                        lambda _path: [dict(local_model)])
    monkeypatch.setattr("tui.app.library.get_tone", lambda _tone_id: tone)
    monkeypatch.setattr("tui.panels.library.get_tone", lambda _tone_id: tone)
    remote_calls = []

    def slow_models(_tone_id, a2_only=False):
        remote_calls.append(_tone_id)
        time.sleep(0.5)
        return remote_models

    monkeypatch.setattr("tui.panels.tone3000.models", slow_models)
    calls = {}

    def fake_import(tone_id, _progress, **kwargs):
        calls["import"] = (tone_id, kwargs.get("model_ids"))
        return {"id": tone_id}

    monkeypatch.setattr("tui.panels.library.import_tone", fake_import)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            panel.slot_widgets[0].focus()
            detail = app.query_one(DetailPane)
            for _ in range(20):
                if detail._pack_mode:
                    break
                await pilot.pause(0.05)
            assert detail._pack_mode
            assert not detail._pack_remote
            assert remote_calls == []
            assert "1 model not loaded" in border_hint_label(detail)
            assert "x expand" in border_hint_label(detail)

            # Explicitly opening the remote-backed view keeps the existing
            # focus-restoration regression covered without making Slot entry
            # perform that request implicitly.
            detail.show_remote_pack(tone)
            for _ in range(20):
                if "m102" in detail._pack_rows and not detail._pack_loading:
                    break
                await pilot.pause(0.05)
            assert detail._pack_remote
            detail._pack_table.focus()
            await pilot.pause()
            assert [row.key.value for row in detail._pack_table.ordered_rows] == [
                "m101", "m102"
            ]
            assert "(not downloaded)" in str(
                detail._pack_table.get_cell("m102", "file"))

            detail._pack_table.focus()
            detail._pack_table.move_cursor(row=1, animate=False)
            await pilot.press("i")
            await pilot.pause(0.3)
            assert calls["import"] == (10, [102])

    run(scenario())


def test_managed_file_poll_promotes_own_write_before_reconcile(monkeypatch, tmp_path):
    current, first, _second, _models, _tone = _patch_canonical_chain(
        monkeypatch, tmp_path)
    monkeypatch.setattr("tui.panels.live.chain_file_fingerprint",
                        lambda: "pending-write")
    monkeypatch.setattr("tui.panels.live.last_chain_write_fingerprint",
                        lambda: "pending-write")

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            panel.state.focus_slot(0)
            panel.state.toggle_bypass(0)
            panel.state.mark_managed_write("previous-write", 1)

            incoming = dict(current["chain"])
            incoming["slots"] = [{"path": None, "candidate": first}]
            incoming["master"] = 1.35
            incoming["revision"] = 2
            current["chain"] = incoming

            # The file is visible before the background commit callback. The
            # panel must recognize it as this process's write and keep BYPASS.
            panel.watch_chain(incoming)
            assert panel.state.slot(0).status is SlotStatus.BYPASS
            assert panel.state.slot(0).candidate == first

    run(scenario())


def test_adding_empty_slot_clears_previous_detail_pack(monkeypatch, tmp_path):
    """Adding an Empty Slot must not leave the old Pack context visible."""

    _current, _first, _second, _models, _tone = _patch_canonical_chain(
        monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            await _wait_for_dynamic_slots(pilot, panel, 1)
            panel.slot_widgets[0].focus()
            for _ in range(20):
                if detail._pack_mode:
                    break
                await pilot.pause(0.05)
            assert detail._pack_mode

            await pilot.click(panel.add_slot)
            for _ in range(20):
                await pilot.pause()
                if (len(panel.slot_widgets) == 2
                        and not detail._pack_mode):
                    break
            assert panel.state.slot(1).status is SlotStatus.EMPTY
            assert len(panel.slot_widgets) == 2
            assert panel.state.target_index == 1
            assert app.focused is panel.slot_widgets[1]
            assert detail._pack_mode is False
            assert detail._view_mode == "empty"
            assert "NONE" in str(detail._summary.content)

    run(scenario())


def test_empty_slot_detail_action_follows_identity_after_reorder(
        monkeypatch, tmp_path):
    """Empty-detail delete must not use the row that inherited its index."""
    current, first, second, _models, _tone = _patch_canonical_chain(
        monkeypatch, tmp_path)
    current["chain"] = _chain([first, None, second])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            await _wait_for_dynamic_slots(pilot, panel, 3)
            panel.slot_widgets[1].focus()
            await pilot.pause()
            assert detail._view_mode == "empty"
            empty_identity = panel.state.slot(1).identity

            app._move_slot(1, -1)
            await pilot.pause()
            assert panel.state.index_for_identity(empty_identity) == 0

            detail.action_delete_empty_slot()
            await pilot.pause()
            assert [slot.path for slot in panel.state.slots] == [first, second]

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
            await _wait_for_dynamic_slots(pilot, panel, 1)
            panel.slot_widgets[0].focus()
            for _ in range(40):
                await pilot.pause(0.05)
                if (detail._pack_mode
                        and "m102" in detail._pack_rows):
                    break
            assert detail._pack_mode
            table = detail._pack_table
            table.focus()
            table.move_cursor(row=1, animate=False)
            await pilot.press("enter")
            await pilot.pause()

            assert panel.state.slot(0).path == second
            assert current["chain"]["slots"][0]["path"] == second
            assert detail._pack_slot_index == 0
            assert table.get_cell("m102", "sel") == \
                "\\[ ] [bold $success]▶[/]"
            assert app.focused is table

            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is panel.slot_widgets[0]
            assert panel.state.target_index == 0

    run(scenario())


def test_deferred_pack_focus_does_not_survive_view_switch(
        monkeypatch, tmp_path):
    """A queued PACK focus must not run after switching to Description."""
    _current, _first, _second, models, tone = _patch_canonical_chain(
        monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            await _wait_for_dynamic_slots(pilot, panel, 1)
            panel.slot_widgets[0].focus()
            await pilot.pause()

            callbacks = []
            monkeypatch.setattr(
                detail, "call_after_refresh",
                lambda callback: callbacks.append(callback),
            )
            detail.show_slot_pack(
                tone, models, panel.state.to_chain(), 0,
                panel.state.slot(0), focus_table=True)
            assert len(callbacks) == 1

            outside = panel.input_node
            outside.focus()
            await pilot.pause()
            assert app.focused is outside
            callbacks[0]()
            await pilot.pause()

            assert detail._view_mode == "selection"
            assert app.focused is outside

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
                "\\[ ] [bold $success]▶[/]"
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
            assert detail._view_mode == "selection"
            assert detail._pack_mode
            # No Slot is focused in this scenario, so Library Enter preserves
            # the viewing context without inventing a load target.
            assert detail._pack_slot_index is None
            assert detail._current_tone["id"] == tone["id"]

    run(scenario())


def test_library_enter_auto_loads_first_downloaded_model_into_target(
        monkeypatch, tmp_path):
    current, first, second, _models, tone = _patch_canonical_chain(
        monkeypatch, tmp_path)
    current["chain"] = _chain([second])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            panel.state.focus_slot(0)
            app.on_tone_selected(ToneSelected(tone["id"]))
            await pilot.pause()
            assert current["chain"]["slots"][0]["path"] == first
            assert panel.state.slot(0).path == first
            assert app.query_one(DetailPane)._pack_mode

    run(scenario())


def test_remote_library_enter_auto_loads_first_downloaded_model(
        monkeypatch, tmp_path):
    current, first, second, models, tone = _patch_canonical_chain(
        monkeypatch, tmp_path)
    current["chain"] = _chain([second])
    remote_tone = {key: value for key, value in tone.items() if key != "models"}
    monkeypatch.setattr(
        "tui.panels.tone3000.models",
        lambda _tone_id, a2_only=False: [dict(models[0])],
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.15)
            panel = app.query_one(ChainPanel)
            panel.state.focus_slot(0)
            app.on_remote_tone_selected(RemoteToneSelected(remote_tone))
            await pilot.pause(0.8)
            assert current["chain"]["slots"][0]["path"] == first
            assert panel.state.slot(0).path == first

    run(scenario())


def test_library_pack_x_expands_to_large_pack_screen(monkeypatch, tmp_path):
    _first, _second, models, tone = _slot_models(tmp_path)
    monkeypatch.setattr("tui.install_screen.tone3000.models",
                        lambda _tone_id, a2_only=False: [dict(model)
                                                         for model in models])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_library_pack(tone)
            await pilot.pause()
            assert any(token == "x expand"
                       for token, _action in pane._border_hint_actions())
            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, PackInstallScreen)
            assert app.screen._tone["id"] == tone["id"]
            assert [model["id"] for model in app.screen._models] == [101, 102]

    run(scenario())


def test_local_pack_marks_unloaded_models_without_fetching(monkeypatch, tmp_path):
    first = str(tmp_path / "first.nam")
    local_model = {
        "id": 101, "tone_id": 10, "name": "first.nam",
        "local_path": first, "architecture": "SlimmableContainer",
    }
    tone = {
        "id": 10, "title": "Local Tone", "gear": "amp",
        "username": "creator", "models_count": 2, "a2_models_count": 2,
        "models": [local_model],
    }
    remote_models = [
        dict(local_model),
        {"id": 102, "tone_id": 10, "name": "second.nam",
         "architecture": "SlimmableContainer"},
    ]
    remote_calls = []
    monkeypatch.setattr(
        "tui.panels.tone3000.models",
        lambda tone_id, a2_only=False: remote_calls.append(tone_id) or remote_models,
    )
    monkeypatch.setattr(
        "tui.install_screen.tone3000.models",
        lambda _tone_id, a2_only=False: remote_models,
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one(DetailPane)
            pane.show_library_pack(tone)
            await pilot.pause()

            assert remote_calls == []
            label = border_hint_label(pane)
            assert "1 model not loaded" in label
            assert "x expand" in label

            pane._pack_table.focus()
            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, PackInstallScreen)

    run(scenario())


def test_empty_local_description_pack_does_not_fetch(monkeypatch):
    tone = {
            "id": 10, "title": "Empty Local Metadata", "gear": "amp",
            "username": "creator", "models_count": 2, "a2_models_count": 2,
            "models": [],
    }
    remote_calls = []
    monkeypatch.setattr(
        "tui.panels.tone3000.models",
        lambda tone_id, a2_only=False: remote_calls.append(tone_id) or [],
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one(DetailPane)
            pane.show(tone)
            await pilot.pause()
            pane.action_view_selection()
            await pilot.pause()

            assert pane._view_mode == "selection"
            assert pane._pack_mode
            assert remote_calls == []
            assert "2 models not loaded" in border_hint_label(pane)

    run(scenario())


def test_description_header_resolves_model_id_from_canonical_slots(
        monkeypatch, tmp_path):
    """The v0.2 slots[] chain supplies the model id shown in Description."""
    _current, first, _second, _models, tone = _patch_canonical_chain(
        monkeypatch, tmp_path)
    relative_tone = {
        **tone,
        "models": [{
            **model,
            "local_path": str(tmp_path.joinpath(
                Path(model["local_path"]).name).relative_to(tmp_path)),
        } for model in tone["models"]],
    }
    monkeypatch.setattr("tui.panels.library.ROOT", tmp_path)
    monkeypatch.setattr("tui.panels.live.read_chain",
                        lambda: _chain([first]))

    assert DetailPane._chain_model_id(relative_tone) == 101
