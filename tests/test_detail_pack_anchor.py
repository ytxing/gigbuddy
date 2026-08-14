"""Focused DetailPane mutation and Pack viewport regressions."""

import asyncio

import pytest

from tui.app import GigBuddyApp
from tui.mutations import MutationCommitted
from tui.panels import DetailPane


def run(coro):
    return asyncio.run(coro)


def _pack_models(count=30):
    return [
        {"id": index, "name": f"model-{index}.nam",
         "architecture": "SlimmableContainer"}
        for index in range(1, count + 1)
    ]


def _pack_tone(models):
    return {"id": 10, "title": "Pack", "gear": "amp", "username": "tester",
            "models": models}


def test_detail_reconcile_uses_operations_set(monkeypatch):
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one(DetailPane)
            tone = {"id": 10, "title": "Pack", "description": "updated"}
            pane._current_tone = tone
            pane._summary_mode = "tone"
            pane._view_mode = "description"
            calls = []
            monkeypatch.setattr(
                "tui.panels.library.get_tone", lambda tone_id: tone)
            monkeypatch.setattr(
                pane, "show", lambda shown, **kwargs: calls.append((shown, kwargs)))

            # The aggregate operation name is not the source of truth for the
            # operations contained in a coalesced refresh.
            event = MutationCommitted(
                "chain-param", ("tone:10",), operations=("install",))
            pane.reconcile_after_mutation(event)

            assert calls == [(tone, {"remote": False})]

            calls.clear()
            pane.reconcile_after_mutation(
                MutationCommitted(
                    "batch", ("tone:10",), operations=("chain-param",)))
            assert calls == []

    run(scenario())


@pytest.mark.parametrize("operation", ["chain", "preset-load", "undo", "redo"])
def test_chain_replacement_clears_retained_slot_context(monkeypatch, operation):
    """A whole-chain event cannot leave DetailPane bound to an old Slot."""
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one(DetailPane)
            pane._pack_mode = True
            pane._view_mode = "selection"
            pane._pack_origin = "slot"
            pane._pack_slot_index = 1
            pane._pack_slot_identity = 42
            refreshed = []
            monkeypatch.setattr(
                "tui.panels.live.read_chain", lambda: {"slots": []})
            monkeypatch.setattr(
                pane, "refresh_pack_active",
                lambda chain: refreshed.append(chain),
            )

            pane.reconcile_after_mutation(MutationCommitted(operation))

            assert pane._pack_slot_index is None
            assert pane._pack_slot_identity is None
            assert pane._pack_origin == "description"
            assert refreshed == [{"slots": []}]

    run(scenario())


def test_pack_anchor_restores_first_visible_row_and_offset(monkeypatch):
    async def scenario():
        models = _pack_models()
        tone = _pack_tone(models)
        monkeypatch.setattr("tui.panels.library.get_tone", lambda tone_id: tone)
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, models, {}, "amp", focus_table=True)
            await pilot.pause()
            table = pane._pack_table
            table.scroll_to(y=4.5, animate=False, force=True)
            await pilot.pause()

            anchor = pane._capture_pack_anchor()
            assert anchor["first_key"] == "m4"
            assert anchor["row_offset"] == pytest.approx(0.5)

            pane._refresh_pack_after_change(tone["id"], pane._view_generation)
            await pilot.pause()

            assert table.scroll_y == pytest.approx(4.5)
            restored = pane._capture_pack_anchor()
            assert restored["first_key"] == "m4"
            assert restored["row_offset"] == pytest.approx(0.5)

    run(scenario())


def test_unrelated_pack_mutation_does_not_rebuild_rows(monkeypatch):
    async def scenario():
        models = _pack_models()
        tone = _pack_tone(models)
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, models, {}, "amp", focus_table=True)
            await pilot.pause()
            table = pane._pack_table
            table.scroll_to(y=4.5, animate=False, force=True)
            await pilot.pause()
            calls = []
            monkeypatch.setattr(
                pane, "_refresh_pack_after_change",
                lambda *args: calls.append(args),
            )

            pane.reconcile_after_mutation(
                MutationCommitted("install", ("tone:999",)))
            await pilot.pause()

            assert calls == []
            assert table.scroll_y == pytest.approx(4.5)

    run(scenario())
