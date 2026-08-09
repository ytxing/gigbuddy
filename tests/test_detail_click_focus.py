"""Regression for DetailPane clicks bubbling into ChainPanel routing."""

import asyncio
import json

import library
from textual.widgets import DataTable

from tui.app import GigBuddyApp
from tui.panels import DetailPane


def run(coro):
    return asyncio.run(coro)


def _seed_local_tone(monkeypatch, tmp_path):
    db_file = tmp_path / "gigbuddy.db"
    chain_file = tmp_path / "live_chain.json"
    model_file = tmp_path / "Test Tone.nam"
    model_file.write_bytes(b"test nam")
    chain_file.write_text(json.dumps({
        "slots": [], "gain": 1.0, "master": 1.0, "quality": 1.0,
    }), encoding="utf-8")
    monkeypatch.setattr(library, "DB_FILE", db_file)
    monkeypatch.setattr(library, "CHAIN_FILE", chain_file)
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", chain_file)
    with library.connect() as conn:
        library.upsert_tone(conn, {
            "id": 1, "title": "Test Local Tone", "description": "details",
            "gear": "amp", "username": "tester", "downloads_count": 1,
            "models_count": 1,
        }, commit=False)
        library.upsert_model(conn, {
            "id": 11, "tone_id": 1, "name": model_file.name,
            "model_url": None,
            "architecture": "SlimmableContainer",
            "local_path": str(model_file),
        }, commit=False)
        conn.commit()


def test_detail_body_click_stays_in_detail_pane(monkeypatch, tmp_path):
    _seed_local_tone(monkeypatch, tmp_path)
    app_clicks = []
    original_on_click = GigBuddyApp.on_click

    def spy_on_click(self, event):
        app_clicks.append((event.screen_x, event.screen_y))
        return original_on_click(self, event)

    monkeypatch.setattr(GigBuddyApp, "on_click", spy_on_click)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one("#lib-table-local", DataTable)
            await pilot.pause(0.4)
            assert table.row_count == 1
            table.focus()
            await pilot.click(table, offset=(8, 1))
            await pilot.pause()
            pane = app.query_one(DetailPane)
            assert pane._current_tone["id"] == 1
            assert app.focused is table

            app_clicks.clear()
            await pilot.click(pane._body, offset=(5, 1))
            await pilot.pause()

            assert pane.has_focus
            assert app_clicks == []

    run(scenario())
