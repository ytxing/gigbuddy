"""Focused checks for incremental Preset table reconciliation."""

import asyncio

import library
from textual.widgets import DataTable

from tui.app import GigBuddyApp


def run(coro):
    return asyncio.run(coro)


def _key_for_name(table: DataTable, name: str) -> str:
    for row in table.ordered_rows:
        if str(table.get_cell(row.key, "name")).split(" *", 1)[0] == name:
            return str(row.key.value)
    raise AssertionError(f"missing preset row: {name}")


def test_preset_mutations_keep_rows_focus_and_filtered_order(monkeypatch, tmp_path):
    root = tmp_path
    data = root / "data"
    (data / "tones").mkdir(parents=True)
    (data / "dry_inputs").mkdir(parents=True)
    chain_file = data / "live_chain.json"
    monkeypatch.setattr(library, "ROOT", root)
    monkeypatch.setattr(library, "DB_FILE", data / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", chain_file)
    monkeypatch.setattr(library, "TONES_DIR", data / "tones")
    monkeypatch.setattr(library, "PRESETS_DIR", data / "presets")
    monkeypatch.setattr("tui.app.live.ROOT", root)
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", chain_file)
    monkeypatch.setattr("tui.app.live.TONES_DIR", data / "tones")
    library.chain_set({"slots": []})
    for name in ("alpha", "beta", "gamma"):
        library.preset_save(name, set_active=False)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            table = app.query_one("#preset-table", DataTable)
            beta_key = _key_for_name(table, "beta")
            beta_index = next(
                index for index, row in enumerate(table.ordered_rows)
                if str(row.key.value) == beta_key
            )
            table.move_cursor(row=beta_index, animate=False, scroll=False)
            beta_row = table.rows[beta_key]

            added = library.preset_save("delta", set_active=False)
            app._publish_mutation("preset-save", (f"preset:{added['id']}",))
            await pilot.pause(0.1)

            assert beta_key in table.rows
            assert table.rows[beta_key] is beta_row
            assert table.ordered_rows[table.cursor_row].key.value == beta_key
            assert _key_for_name(table, "delta") in table.rows

            rows_before_delete = [str(row.key.value) for row in table.ordered_rows]
            beta_index = rows_before_delete.index(beta_key)
            expected_after_delete = (
                rows_before_delete[beta_index + 1]
                if beta_index + 1 < len(rows_before_delete)
                else rows_before_delete[beta_index - 1]
            )
            assert library.preset_delete("beta") is True
            app._publish_mutation("preset-delete", (beta_key,))
            await pilot.pause(0.1)

            assert beta_key not in table.rows
            assert table.ordered_rows[table.cursor_row].key.value == expected_after_delete

            search = app.query_one("#preset-search")
            search.value = "alpha"
            await pilot.pause(0.1)
            alpha_key = _key_for_name(table, "alpha")
            assert [row.key.value for row in table.ordered_rows] == [alpha_key]
            table.move_cursor(row=0, animate=False, scroll=False)
            alpha_row = table.rows[alpha_key]

            filtered = library.preset_save("alphabet", set_active=False)
            app._publish_mutation("preset-save", (f"preset:{filtered['id']}",))
            await pilot.pause(0.1)

            assert [row.key.value for row in table.ordered_rows] == [
                _key_for_name(table, "alphabet"), alpha_key
            ]
            assert table.rows[alpha_key] is alpha_row
            assert table.ordered_rows[table.cursor_row].key.value == alpha_key

    run(scenario())


def test_preset_filter_restores_existing_selection_focus_and_viewport(
        monkeypatch, tmp_path):
    root = tmp_path
    data = root / "data"
    (data / "tones").mkdir(parents=True)
    (data / "dry_inputs").mkdir(parents=True)
    chain_file = data / "live_chain.json"
    monkeypatch.setattr(library, "ROOT", root)
    monkeypatch.setattr(library, "DB_FILE", data / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", chain_file)
    monkeypatch.setattr(library, "TONES_DIR", data / "tones")
    monkeypatch.setattr(library, "PRESETS_DIR", data / "presets")
    monkeypatch.setattr("tui.app.live.ROOT", root)
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", chain_file)
    monkeypatch.setattr("tui.app.live.TONES_DIR", data / "tones")
    library.chain_set({"slots": []})
    for name in [f"row-{index:02d}" for index in range(24)]:
        library.preset_save(name, set_active=False)
    library.preset_save("only-visible", set_active=False)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            table = app.query_one("#preset-table", DataTable)
            panel = app.query_one("PresetPanel")
            target_key = _key_for_name(table, "row-12")
            other_key = _key_for_name(table, "row-18")
            target_index = next(
                index for index, row in enumerate(table.ordered_rows)
                if str(row.key.value) == target_key
            )
            table.move_cursor(row=target_index, animate=False, scroll=False)
            panel.action_toggle_selected()
            other_index = next(
                index for index, row in enumerate(table.ordered_rows)
                if str(row.key.value) == other_key
            )
            table.move_cursor(row=other_index, animate=False, scroll=False)
            panel.action_toggle_selected()
            table.move_cursor(row=target_index, animate=False, scroll=False)
            table.scroll_to(y=max(0, target_index - 2), animate=False, force=True)
            await pilot.pause()
            first_index = min(
                max(int(table.scroll_y), 0), len(table.ordered_rows) - 1
            )
            first_visible_key = str(table.ordered_rows[first_index].key.value)

            search = app.query_one("#preset-search")
            search.value = "only-visible"
            await pilot.pause(0.1)
            assert panel._selected == {target_key, other_key}
            assert [row.key.value for row in table.ordered_rows] == [
                _key_for_name(table, "only-visible")
            ]

            assert library.preset_delete("row-18") is True
            app._publish_mutation("preset-delete", (other_key,))
            await pilot.pause(0.1)
            assert panel._selected == {target_key}

            search.value = ""
            await pilot.pause(0.1)
            assert panel._selected == {target_key}
            assert table.ordered_rows[table.cursor_row].key.value == target_key
            restored_first_index = min(
                max(int(table.scroll_y), 0), len(table.ordered_rows) - 1
            )
            assert table.ordered_rows[restored_first_index].key.value == (
                first_visible_key
            )
            assert other_key not in table.rows

    run(scenario())
