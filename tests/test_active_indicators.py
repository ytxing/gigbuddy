"""Regression coverage for compact active-chain markers."""

import asyncio

import library
from textual.widgets import DataTable

from tui.app import GigBuddyApp
from tui.panels import ChainPanel


def run(coro):
    return asyncio.run(coro)


def _seed_active_chain(monkeypatch, tmp_path):
    root = tmp_path
    data = root / "data"
    tones = data / "tones"
    tones.mkdir(parents=True)
    (data / "dry_inputs").mkdir(parents=True)
    model_file = tones / "active.nam"
    second_file = tones / "second.nam"
    model_file.write_bytes(b"nam")
    second_file.write_bytes(b"nam")
    chain_file = data / "live_chain.json"

    monkeypatch.setattr(library, "ROOT", root)
    monkeypatch.setattr(library, "DB_FILE", data / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", chain_file)
    monkeypatch.setattr(library, "TONES_DIR", tones)
    monkeypatch.setattr("tui.app.live.ROOT", root)
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", chain_file)
    monkeypatch.setattr("tui.app.live.TONES_DIR", tones)

    with library.connect() as conn:
        library.upsert_tone(conn, {
            "id": 10, "title": "Active Tone", "gear": "amp",
            "username": "tester", "models_count": 1,
        }, commit=False)
        library.upsert_tone(conn, {
            "id": 20, "title": "Second Tone", "gear": "amp",
            "username": "tester", "models_count": 1,
        }, commit=False)
        library.upsert_model(conn, {
            "id": 101, "tone_id": 10, "name": model_file.name,
            "model_url": None, "architecture": "SlimmableContainer",
            "local_path": str(model_file),
        }, commit=False)
        library.upsert_model(conn, {
            "id": 201, "tone_id": 20, "name": second_file.name,
            "model_url": None, "architecture": "SlimmableContainer",
            "local_path": str(second_file),
        }, commit=False)
        conn.commit()

    library.chain_set({
        "slots": [{"path": str(model_file)}, {"path": str(second_file)}],
        "gain": 1.0, "master": 1.0, "quality": 1.0,
    })
    library.preset_save("active-preset", set_active=True)


def test_preset_and_local_active_markers_are_compact_and_cursor_stable(
        monkeypatch, tmp_path):
    _seed_active_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)

            presets = app.query_one("#preset-table", DataTable)
            local = app.query_one("#lib-table-local", DataTable)
            assert presets.columns["pick"].width == 4
            assert local.columns["pick"].width == 4

            preset_key = next(
                row.key.value for row in presets.ordered_rows
                if str(row.key.value).startswith("preset:")
                and str(presets.get_cell(row.key, "name")).startswith(
                    "active-preset")
            )
            assert presets.get_cell(preset_key, "pick") == (
                "\\[ ] [bold $success]▶[/]")

            local.focus()
            await pilot.pause()
            local.move_cursor(row=1, animate=False, scroll=False)
            cursor_before = local.cursor_row
            chain = app.query_one(ChainPanel)
            chain.slot_widgets[0].focus()
            for _ in range(12):
                await pilot.pause(0.05)
                if "[bold $success]▶[/]" in local.get_cell(
                        "local:10", "pick"):
                    break

            assert local.get_cell("local:10", "pick") == (
                "\\[ ] [bold $success]▶[/]")
            assert local.cursor_row == cursor_before

            chain.slot_widgets[1].focus()
            for _ in range(12):
                await pilot.pause(0.05)
                if "[bold $success]▶[/]" in local.get_cell(
                        "local:20", "pick"):
                    break
            assert local.get_cell("local:10", "pick") == "\\[ ]"
            assert local.get_cell("local:20", "pick") == (
                "\\[ ] [bold $success]▶[/]")

    run(scenario())
