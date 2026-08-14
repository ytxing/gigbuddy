"""Narrow T05 acceptance tests for the v0.2 dynamic ChainPanel.

The state cases use the Textual-free ChainState seam. Renderer assertions use
the small semi-public ChainSlotWidget seam. Keeping that adapter here prevents
the old fixed AMP/CAB widgets from becoming the v0.2 oracle.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import threading
import time
from pathlib import Path

import pytest

from tui.chain_state import (
    CommitReceipt,
    MAX_SLOTS,
    ChainState,
    ChainStateError,
    PreparedCommit,
    SlotStatus,
)
from tui import live
from tui.live import CHAIN_PARAMETER_DEFAULTS
from tui.panels import ChainSlotAction, ChainSlotWidget


def _chain(paths: list[str | None], **overrides: object) -> dict:
    chain = {
        "slots": [{"path": path} for path in paths],
        "gain": 1.0,
        "master": 1.0,
        "quality": 1.0,
    }
    chain.update(overrides)
    return chain


def _render_slot_text(slot, tone: dict | None) -> str:
    """Call the narrow renderer seam without depending on Textual widgets."""
    pytest.importorskip("textual", reason="T05 renderer smoke needs Textual")
    from tui.panels import ChainSlotWidget

    widget = ChainSlotWidget(
        slot.index,
        slot,
        title=tone.get("title") if tone else None,
        gear=tone.get("gear") if tone else None,
    )
    return f"{widget._state_lamp()} {widget._display_label()}\n{widget.render()}"


def _render_slot_with_quality_warning(slot, *, unsupported: bool) -> str:
    pytest.importorskip("textual", reason="T05 renderer smoke needs Textual")
    from tui.panels import ChainSlotWidget

    widget = ChainSlotWidget(
        slot.index,
        slot,
        title="Tone",
        gear="amp",
        quality_unsupported=unsupported,
    )
    return _widget_plain(widget)


def _widget_plain(widget) -> str:
    rendered = widget.render()
    plain = getattr(rendered, "plain", rendered)
    return plain if isinstance(plain, str) else str(plain)


def test_quality_warning_only_marks_unsupported_non_empty_nam() -> None:
    unsupported = ChainState(_chain(["amp.nam"])).slot(0)
    supported = ChainState(_chain(["amp.nam"])).slot(0)
    wav = ChainState(_chain(["cab.wav"])).slot(0)
    empty = ChainState(_chain([None])).slot(0)

    assert "quality unsupported" in _render_slot_with_quality_warning(
        unsupported, unsupported=True)
    assert "quality unsupported" not in _render_slot_with_quality_warning(
        supported, unsupported=False)
    assert "quality unsupported" not in _render_slot_with_quality_warning(
        wav, unsupported=False)
    assert "quality unsupported" not in _render_slot_with_quality_warning(
        empty, unsupported=False)


@pytest.mark.parametrize("count", [0, 1, 6])
def test_chain_state_supports_zero_one_and_six_slots(count: int):
    paths = [f"slot-{index}.nam" for index in range(count)]
    state = ChainState(_chain(paths))

    assert MAX_SLOTS == 6
    assert state.slot_count == count
    assert [slot.index for slot in state.slots] == list(range(count))
    assert [slot.path for slot in state.slots] == paths
    assert len(state.to_chain()["slots"]) == count


def test_add_slot_appends_empty_and_rejects_the_seventh_slot():
    state = ChainState(_chain([]))

    for expected_index in range(MAX_SLOTS):
        assert state.add_slot() == expected_index
        assert state.slot(expected_index).status is SlotStatus.EMPTY
        assert state.target_index == expected_index

    assert state.slot_count == 6
    with pytest.raises(ChainStateError, match="more than 6"):
        state.add_slot()


def test_delete_slot_removes_active_bypass_and_empty_rows_at_boundaries():
    state = ChainState(_chain(["active.nam", "bypass.nam", None]))
    state.focus_slot(1)
    state.toggle_bypass(1)

    deleted = state.delete_slot(1)
    assert deleted.status is SlotStatus.BYPASS
    assert deleted.candidate == "bypass.nam"
    assert state.slot_count == 2
    assert state.target_index == 1
    assert state.slot(1).status is SlotStatus.EMPTY

    while state.slot_count:
        state.delete_slot(0)
    assert state.slot_count == 0
    assert state.target_index is None
    with pytest.raises(ChainStateError):
        state.delete_slot(0)


def test_reorder_swaps_adjacent_rows_and_noops_at_edges():
    state = ChainState(_chain(["a.nam", "b.nam", "c.nam"]))
    state.focus_slot(1)

    assert state.move_slot(0, -1) is False
    assert [slot.path for slot in state.slots] == ["a.nam", "b.nam", "c.nam"]
    assert state.target_index == 1

    assert state.move_slot(1, -1) is True
    assert [slot.path for slot in state.slots] == ["b.nam", "a.nam", "c.nam"]
    assert state.target_index == 0

    assert state.move_slot(0, 1) is True
    assert [slot.path for slot in state.slots] == ["a.nam", "b.nam", "c.nam"]
    assert state.target_index == 1
    assert state.move_slot(2, 1) is False
    with pytest.raises(ChainStateError, match="direction"):
        state.move_slot(1, 0)


def test_slot_move_arrow_posts_reorder_message_not_model_switch():
    state = ChainState(_chain(["a.nam", "b.nam"]))
    slot = ChainSlotWidget(1, state.slot(1))
    messages = []
    slot.focus = lambda: None
    slot.post_message = messages.append

    action = ChainSlotAction(slot, -1)
    action.on_click(SimpleNamespace(stop=lambda: None))

    assert len(messages) == 1
    assert isinstance(messages[0], ChainSlotWidget.MoveRequested)
    assert messages[0].index == 1
    assert messages[0].direction == -1


def _patch_managed_runtime(monkeypatch, tmp_path):
    """Give managed UI tests a private file/runtime boundary."""
    chain_file = tmp_path / "live_chain.json"
    level_file = tmp_path / "level.json"
    control_file = tmp_path / "live_control.json"
    control_reply_file = tmp_path / "live_control.reply.json"
    for name, value in (
            ("CHAIN_FILE", chain_file), ("LEVEL_FILE", level_file),
            ("CONTROL_FILE", control_file),
            ("CONTROL_REPLY_FILE", control_reply_file)):
        monkeypatch.setattr(live, name, value)
    live._chain_cache.clear()
    model_paths = sorted(live.TONES_DIR.rglob("*.nam"))[:2]
    assert len(model_paths) == 2
    chain = _chain([str(path) for path in model_paths], revision=1)
    live.write_chain(chain, revision=1)

    class SlowAdapter:
        def __init__(self, _app, *, expected_chain=None):
            self._base_fingerprint = live.chain_file_fingerprint()
            self._base_revision = expected_chain.get("revision", 0)

        def snapshot_runtime(self):
            return ({}, None)

        def prepare(self, candidate):
            time.sleep(0.15)
            prepared = deepcopy(candidate)
            prepared["revision"] = int(candidate["revision"]) + 1
            return PreparedCommit(
                prepared, {}, int(prepared["revision"]))

        def write_file(self, candidate):
            persisted = live.write_chain(
                candidate,
                expected_fingerprint=self._base_fingerprint,
                expected_revision=self._base_revision,
                revision=candidate["revision"],
            )
            return CommitReceipt(
                live.chain_file_fingerprint(), persisted["revision"])

        def apply_runtime(self, _prepared):
            return None

        def restore_file(self, _chain):
            return None

        def restore_runtime(self, _snapshot):
            return None

    monkeypatch.setattr("tui.app._ManagedChainAdapter", SlowAdapter)


async def _wait_for_slots(pilot, panel, count=2):
    """Wait for the first external chain refresh before indexing its slots."""
    for _ in range(20):
        # ChainState is constructed synchronously, so slot count alone does
        # not prove that the app's first file poll has finished. Without this
        # guard a focus assertion can race that poll and observe no target.
        if (len(panel.state.slots) >= count
                and len(panel.slot_widgets) >= count
                and not getattr(panel, "_recompose_pending", False)
                and getattr(panel.app, "_last_refresh_chain_fingerprint", None)
                is not None):
            return
        await pilot.pause(0.05)
    raise AssertionError(f"expected at least {count} slots")


def test_managed_move_does_not_block_ui_and_publishes_after_worker(
        monkeypatch, tmp_path):
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            before = [slot.path for slot in panel.state.slots]
            started = time.perf_counter()
            app._move_slot(1, -1)
            elapsed = time.perf_counter() - started

            assert elapsed < 0.05
            await pilot.pause(0.35)
            assert [slot.path for slot in panel.state.slots] == [
                before[1], before[0]]
            await pilot.pause(0.2)

    asyncio.run(scenario())


def test_managed_bypass_keeps_target_when_file_poll_wins_race(
        monkeypatch, tmp_path):
    """A managed ACTIVE -> BYPASS poll must not clear the focused target."""
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel, DetailPane

    _patch_managed_runtime(monkeypatch, tmp_path)
    written = threading.Event()
    release_runtime = threading.Event()

    class PollWinsAdapter:
        def __init__(self, _app, *, expected_chain=None):
            self._base_fingerprint = live.chain_file_fingerprint()
            self._base_revision = expected_chain.get("revision", 0)

        def snapshot_runtime(self):
            return ({}, None)

        def prepare(self, candidate):
            prepared = deepcopy(candidate)
            prepared["revision"] = int(candidate["revision"]) + 1
            return PreparedCommit(
                prepared, {}, int(prepared["revision"]))

        def write_file(self, candidate):
            persisted = live.write_chain(
                candidate,
                expected_fingerprint=self._base_fingerprint,
                expected_revision=self._base_revision,
                revision=candidate["revision"],
            )
            written.set()
            return CommitReceipt(
                live.chain_file_fingerprint(), persisted["revision"])

        def apply_runtime(self, _prepared):
            assert release_runtime.wait(2.0)

        def restore_file(self, _chain):
            return None

        def restore_runtime(self, _snapshot):
            return None

    monkeypatch.setattr("tui.app._ManagedChainAdapter", PollWinsAdapter)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            panel.slot_widgets[0].focus()
            await pilot.pause()
            assert panel.state.target_index == 0

            app._toggle_slot(0)
            for _ in range(40):
                if written.is_set():
                    break
                await pilot.pause(0.025)
            assert written.is_set()
            detail = app.query_one(DetailPane)
            detail._pack_table.focus()
            await pilot.pause()
            try:
                # The file is visible while the managed runtime callback is
                # still blocked. This is the race the regression covers.
                await pilot.pause(0.25)
                assert panel.state.slot(0).status is SlotStatus.BYPASS
                assert panel.state.target_index == 0
            finally:
                release_runtime.set()
            await pilot.pause(0.3)
            assert panel.state.target_index == 0
            assert app.focused is detail._pack_table

    asyncio.run(scenario())


def test_managed_runtime_failure_rollback_restores_slot_identity(
        monkeypatch, tmp_path):
    """A rollback poll must reverse the forward Slot identity transition."""
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)
    written = threading.Event()
    release_runtime = threading.Event()
    temporary_rollback_written = threading.Event()
    release_final_rollback = threading.Event()

    class FailingAdapter:
        def __init__(self, _app, *, expected_chain=None):
            self._app = _app
            self._previous_payload = live.CHAIN_FILE.read_bytes()
            self._base_fingerprint = live.chain_file_fingerprint()
            self._base_revision = expected_chain.get("revision", 0)
            self._forward_fingerprint = None
            self._forward_revision = None

        def snapshot_runtime(self):
            return ({}, None)

        def prepare(self, candidate):
            prepared = deepcopy(candidate)
            prepared["revision"] = int(candidate["revision"]) + 1
            return PreparedCommit(
                prepared, {}, int(prepared["revision"]))

        def write_file(self, candidate):
            persisted = live.write_chain(
                candidate,
                expected_fingerprint=self._base_fingerprint,
                expected_revision=self._base_revision,
                revision=candidate["revision"],
            )
            self._forward_fingerprint = live.chain_file_fingerprint()
            self._forward_revision = persisted["revision"]
            written.set()
            return CommitReceipt(
                self._forward_fingerprint, persisted["revision"])

        def apply_runtime(self, _prepared):
            assert release_runtime.wait(2.0)
            raise RuntimeError("runtime rejected")

        def restore_file(self, _chain):
            previous = json.loads(self._previous_payload.decode("utf-8"))
            previous["_transaction_id"] = "rollback-temp"
            previous["revision"] = self._base_revision
            temporary_payload = (
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            live.restore_chain_bytes(
                temporary_payload,
                expected_fingerprint=self._forward_fingerprint,
            )
            self._app._remember_managed_rollback_target(
                (self._forward_fingerprint, self._forward_revision))
            temporary_rollback_written.set()
            assert release_final_rollback.wait(2.0)
            live.restore_chain_bytes(
                self._previous_payload,
                expected_fingerprint=live.chain_file_fingerprint(),
            )
            self._app._remember_managed_rollback_target(
                (self._forward_fingerprint, self._forward_revision))

        def restore_runtime(self, _snapshot):
            return None

    monkeypatch.setattr("tui.app._ManagedChainAdapter", FailingAdapter)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            before_paths = panel.state.slot_paths
            before_identities = panel.state.slot_identities
            panel.slot_widgets[1].focus()
            await pilot.pause()
            target_identity = panel.state.target_identity
            assert target_identity == before_identities[1]

            app._move_slot(1, -1)
            for _ in range(40):
                if written.is_set():
                    break
                await pilot.pause(0.025)
            assert written.is_set()
            await pilot.pause(0.25)
            assert panel.state.slot_paths == (before_paths[1], before_paths[0])

            release_runtime.set()
            for _ in range(40):
                if temporary_rollback_written.is_set():
                    break
                await pilot.pause(0.025)
            assert temporary_rollback_written.is_set()
            for _ in range(40):
                if panel.state.slot_paths == before_paths:
                    break
                await pilot.pause(0.025)
            assert panel.state.slot_paths == before_paths
            assert panel.state.slot_identities == before_identities
            assert panel.state.target_identity == target_identity
            release_final_rollback.set()
            for _ in range(40):
                if panel.state.slot_paths == before_paths:
                    break
                await pilot.pause(0.025)
            assert panel.state.slot_paths == before_paths
            assert panel.state.slot_identities == before_identities
            assert panel.state.target_identity == target_identity

    asyncio.run(scenario())


def test_managed_detail_pack_bypass_keeps_target_when_file_poll_wins_race(
        monkeypatch, tmp_path):
    """The real DetailPane PackFilePicked path keeps the target too."""
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel, DetailPane

    _patch_managed_runtime(monkeypatch, tmp_path)
    model_paths = sorted(live.TONES_DIR.rglob("*.nam"))[:2]
    models = [
        {"id": 201 + index, "tone_id": 20, "name": path.name,
         "local_path": str(path), "architecture": "SlimmableContainer"}
        for index, path in enumerate(model_paths)
    ]
    tone = {"id": 20, "title": "Managed Slot Tone", "gear": "pedal",
            "models": models}
    for module in ("tui.app.library", "tui.panels.library"):
        monkeypatch.setattr(
            f"{module}.local_models_by_tone",
            lambda _path: [dict(model) for model in models])
        monkeypatch.setattr(f"{module}.get_tone", lambda _tone_id: tone)

    written = threading.Event()
    release_runtime = threading.Event()

    class PollWinsAdapter:
        def __init__(self, _app, *, expected_chain=None):
            self._base_fingerprint = live.chain_file_fingerprint()
            self._base_revision = expected_chain.get("revision", 0)

        def snapshot_runtime(self):
            return ({}, None)

        def prepare(self, candidate):
            prepared = deepcopy(candidate)
            prepared["revision"] = int(candidate["revision"]) + 1
            return PreparedCommit(
                prepared, {}, int(prepared["revision"]))

        def write_file(self, candidate):
            persisted = live.write_chain(
                candidate,
                expected_fingerprint=self._base_fingerprint,
                expected_revision=self._base_revision,
                revision=candidate["revision"],
            )
            written.set()
            return CommitReceipt(
                live.chain_file_fingerprint(), persisted["revision"])

        def apply_runtime(self, _prepared):
            assert release_runtime.wait(2.0)

        def restore_file(self, _chain):
            return None

        def restore_runtime(self, _snapshot):
            return None

    monkeypatch.setattr("tui.app._ManagedChainAdapter", PollWinsAdapter)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            panel.slot_widgets[0].focus()
            await pilot.pause()
            detail = app.query_one(DetailPane)
            for _ in range(20):
                if detail._pack_rows:
                    break
                await pilot.pause(0.05)
            assert detail._pack_rows
            detail._pack_table.focus()
            await pilot.press("enter")

            for _ in range(40):
                if written.is_set():
                    break
                await pilot.pause(0.025)
            assert written.is_set()
            try:
                await pilot.pause(0.25)
                assert panel.state.slot(0).status is SlotStatus.BYPASS
                assert panel.state.target_index == 0
            finally:
                release_runtime.set()
            await pilot.pause(0.3)
            assert panel.state.target_index == 0

    asyncio.run(scenario())


def test_queued_move_then_detail_load_follows_slot_identity(
        monkeypatch, tmp_path):
    """A Pack selection queued behind a move must still load the moved Slot."""
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel, DetailPane

    _patch_managed_runtime(monkeypatch, tmp_path)
    model_paths = sorted(live.TONES_DIR.rglob("*.nam"))[:2]
    replacement = str(sorted(live.TONES_DIR.rglob("*.nam"))[2])
    models = [
        {"id": 301 + index, "tone_id": 30, "name": path.name,
         "local_path": str(path), "architecture": "SlimmableContainer"}
        for index, path in enumerate((*model_paths, Path(replacement)))
    ]
    tone = {"id": 30, "title": "Queued Slot Tone", "gear": "pedal",
            "models": models}
    for module in ("tui.app.library", "tui.panels.library"):
        monkeypatch.setattr(
            f"{module}.local_models_by_tone",
            lambda _path: [dict(model) for model in models])
        monkeypatch.setattr(f"{module}.get_tone", lambda _tone_id: tone)

    first_runtime_started = threading.Event()
    release_first_runtime = threading.Event()
    adapter_count = 0

    class BlockingFirstAdapter:
        def __init__(self, _app, *, expected_chain=None):
            nonlocal adapter_count
            adapter_count += 1
            self._block_runtime = adapter_count == 1
            self._base_fingerprint = live.chain_file_fingerprint()
            self._base_revision = expected_chain.get("revision", 0)

        def snapshot_runtime(self):
            return ({}, None)

        def prepare(self, candidate):
            prepared = deepcopy(candidate)
            prepared["revision"] = int(candidate["revision"]) + 1
            return PreparedCommit(prepared, {}, int(prepared["revision"]))

        def write_file(self, candidate):
            persisted = live.write_chain(
                candidate,
                expected_fingerprint=self._base_fingerprint,
                expected_revision=self._base_revision,
                revision=candidate["revision"],
            )
            return CommitReceipt(
                live.chain_file_fingerprint(), persisted["revision"])

        def apply_runtime(self, _prepared):
            if self._block_runtime:
                first_runtime_started.set()
                assert release_first_runtime.wait(2.0)

        def restore_file(self, _chain):
            return None

        def restore_runtime(self, _snapshot):
            return None

    monkeypatch.setattr("tui.app._ManagedChainAdapter", BlockingFirstAdapter)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            panel.slot_widgets[1].focus()
            await pilot.pause()
            detail = app.query_one(DetailPane)
            target_identity = panel.state.target_identity
            assert target_identity is not None

            app._move_slot(1, -1)
            for _ in range(40):
                if first_runtime_started.is_set():
                    break
                await pilot.pause(0.025)
            assert first_runtime_started.is_set()

            detail.on_data_table_row_selected(SimpleNamespace(
                data_table=detail._pack_table,
                row_key=SimpleNamespace(value="m303"),
            ))
            # The second job is queued while the first runtime acknowledgement
            # is blocked. Its target must be the original Slot B, now at index 0.
            release_first_runtime.set()
            await pilot.pause(0.8)
            assert panel.state.slot(0).path == replacement
            assert panel.state.slot(1).path == str(model_paths[0])
            assert panel.state.target_identity == target_identity

    asyncio.run(scenario())


def test_queued_delete_then_gain_follows_slot_identity(
        monkeypatch, tmp_path):
    """A gain edit queued behind delete must not retarget the next row."""
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)
    first_runtime_started = threading.Event()
    release_first_runtime = threading.Event()
    adapter_count = 0

    class BlockingFirstAdapter:
        def __init__(self, _app, *, expected_chain=None):
            nonlocal adapter_count
            adapter_count += 1
            self._block_runtime = adapter_count == 1
            self._base_fingerprint = live.chain_file_fingerprint()
            self._base_revision = expected_chain.get("revision", 0)

        def snapshot_runtime(self):
            return ({}, None)

        def prepare(self, candidate):
            prepared = deepcopy(candidate)
            prepared["revision"] = int(candidate["revision"]) + 1
            return PreparedCommit(prepared, {}, int(prepared["revision"]))

        def write_file(self, candidate):
            persisted = live.write_chain(
                candidate,
                expected_fingerprint=self._base_fingerprint,
                expected_revision=self._base_revision,
                revision=candidate["revision"],
            )
            return CommitReceipt(
                live.chain_file_fingerprint(), persisted["revision"])

        def apply_runtime(self, _prepared):
            if self._block_runtime:
                first_runtime_started.set()
                assert release_first_runtime.wait(2.0)

        def restore_file(self, _chain):
            return None

        def restore_runtime(self, _snapshot):
            return None

    monkeypatch.setattr("tui.app._ManagedChainAdapter", BlockingFirstAdapter)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            panel.slot_widgets[1].focus()
            await pilot.pause()
            target_identity = panel.state.target_identity
            assert target_identity is not None

            app._delete_slot(0)
            for _ in range(40):
                if first_runtime_started.is_set():
                    break
                await pilot.pause(0.025)
            assert first_runtime_started.is_set()

            # The real Slot widget carries the logical identity even after
            # the delete has shifted the remaining Slot to index 0.
            app._adjust_slot_gain(
                1, "input_gain_db", 3.0, identity=target_identity)
            release_first_runtime.set()
            await pilot.pause(0.8)
            assert panel.state.slot_count == 1
            assert panel.state.slot(0).input_gain_db == pytest.approx(3.0)
            assert panel.state.target_identity == target_identity

    asyncio.run(scenario())


def test_queued_slot_mutation_is_discarded_after_same_path_replacement(
        monkeypatch, tmp_path):
    """A queued job must not reuse identities after a same-path replacement."""
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)
    first_runtime_started = threading.Event()
    release_first_runtime = threading.Event()
    adapter_count = 0

    class BlockingFirstAdapter:
        def __init__(self, _app, *, expected_chain=None):
            nonlocal adapter_count
            adapter_count += 1
            self._block_runtime = adapter_count == 1
            self._base_fingerprint = live.chain_file_fingerprint()
            self._base_revision = expected_chain.get("revision", 0)

        def snapshot_runtime(self):
            return ({}, None)

        def prepare(self, candidate):
            prepared = deepcopy(candidate)
            prepared["revision"] = int(candidate["revision"]) + 1
            return PreparedCommit(prepared, {}, int(prepared["revision"]))

        def write_file(self, candidate):
            persisted = live.write_chain(
                candidate,
                expected_fingerprint=self._base_fingerprint,
                expected_revision=self._base_revision,
                revision=candidate["revision"],
            )
            return CommitReceipt(
                live.chain_file_fingerprint(), persisted["revision"])

        def apply_runtime(self, _prepared):
            if self._block_runtime:
                first_runtime_started.set()
                assert release_first_runtime.wait(2.0)

        def restore_file(self, _chain):
            return None

        def restore_runtime(self, _snapshot):
            return None

    monkeypatch.setattr("tui.app._ManagedChainAdapter", BlockingFirstAdapter)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            original_paths = panel.state.slot_paths
            target_identity = panel.state.slot_identities[1]
            panel.slot_widgets[1].focus()
            await pilot.pause()

            app._move_slot(1, -1)
            for _ in range(40):
                if first_runtime_started.is_set():
                    break
                await pilot.pause(0.025)
            assert first_runtime_started.is_set()

            # Let the polling path publish the managed reorder, so the next
            # UI action captures the reordered paths and identities.
            for _ in range(40):
                if panel.state.slot_paths == original_paths[::-1]:
                    break
                await pilot.pause(0.025)
            assert panel.state.slot_paths == original_paths[::-1]

            app._adjust_slot_gain(
                0, "input_gain_db", 3.0, identity=target_identity)

            # The external writer replaces the whole chain with the same Slot
            # paths but a new revision before the queued gain job starts.
            external = _chain(
                list(original_paths[::-1]), revision=3)
            external["gain"] = 2.0
            live.chain_protocol.write_chain_file(
                live.CHAIN_FILE, external, root=live.ROOT, revision=3)
            release_first_runtime.set()
            await pilot.pause(1.0)

            current = live.read_chain()
            assert current["gain"] == pytest.approx(2.0)
            assert current["slots"][0].get("input_gain_db", 0.0) == pytest.approx(0.0)

    asyncio.run(scenario())


def test_managed_calibration_eventually_updates_slot_output(monkeypatch,
                                                            tmp_path):
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            path = panel.state.slot(1).path
            app._calibration_generation = 1
            app._apply_slot_calibration(1, 1, path, -5.875)
            await pilot.pause(0.35)
            assert panel.state.slot(1).output_gain_db == pytest.approx(-5.88)

    asyncio.run(scenario())


def test_calibration_worker_resolves_original_slot_after_reorder(
        monkeypatch, tmp_path):
    """A late calibration request must use the moved Slot's current index."""
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)
    requested = []
    monkeypatch.setattr(
        "tui.app.live.request_output_calibration",
        lambda index: requested.append(index) or -2.0)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            panel.slot_widgets[1].focus()
            await pilot.pause()
            before_paths = panel.state.slot_paths
            before_identities = panel.state.slot_identities
            target_identity = panel.state.target_identity
            assert target_identity is not None

            app._move_slot(1, -1)
            for _ in range(40):
                if panel.state.slot_paths == (
                        before_paths[1], before_paths[0]):
                    break
                await pilot.pause(0.025)
            assert panel.state.slot_paths == (
                before_paths[1], before_paths[0])

            # This models the worker having captured Slot B before the move,
            # then reaching the control sidecar after the move committed.
            app._calibration_generation = 1
            monkeypatch.setattr(
                app, "_apply_slot_calibration",
                lambda *_args, **_kwargs: None)
            app._calibrate_slot_worker(
                1, 1, target_identity, before_paths[1],
                before_paths, before_identities)

            assert requested == [0]

    asyncio.run(scenario())


def test_calibration_notice_explains_backend_clamp(monkeypatch, tmp_path):
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            path = panel.state.slot(1).path
            notes = []

            def capture(_mutation, note, **_kwargs):
                notes.append(note)
                return True

            monkeypatch.setattr(app, "_commit_slot_mutation", capture)
            app._calibration_generation = 1
            app._apply_slot_calibration(
                1, 1, path, 24.0, True, 30.0)

            assert notes == [
                "Calibrated Slot 02 output to +24.0 dB "
                "(clamped from +30.0 dB)"
            ]

    asyncio.run(scenario())


def test_managed_gain_and_master_bumps_do_not_block_ui(monkeypatch, tmp_path):
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.1)
            started = time.perf_counter()
            app._bump("gain", 0.05)
            app._bump("master", 0.05)
            elapsed = time.perf_counter() - started

            assert elapsed < 0.05
            await pilot.pause(0.5)
            panel = app.query_one(ChainPanel)
            assert panel.state.chain["gain"] == pytest.approx(1.05)
            assert panel.state.chain["master"] == pytest.approx(1.05)

    asyncio.run(scenario())


def test_active_bypass_and_empty_are_distinct_state_values():
    state = ChainState(_chain(["amp.nam", None]))

    assert state.slot(0).status is SlotStatus.ACTIVE
    assert state.slot(1).status is SlotStatus.EMPTY
    assert state.toggle_bypass(1) is False

    assert state.toggle_bypass(0) is True
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.slot(0).path is None
    assert state.slot(0).candidate == "amp.nam"

    assert state.toggle_bypass(0) is True
    assert state.slot(0).status is SlotStatus.ACTIVE
    assert state.slot(0).path == "amp.nam"
    assert state.slot(0).candidate is None


def test_quality_reset_default_is_one():
    assert CHAIN_PARAMETER_DEFAULTS["quality"] == pytest.approx(1.0)
    state = ChainState(_chain([], quality=0.35))
    assert state.chain["quality"] == pytest.approx(0.35)
    assert CHAIN_PARAMETER_DEFAULTS["quality"] == 1.0


def test_slot_renderer_reserves_two_digit_io_values():
    pytest.importorskip("textual", reason="T05 renderer smoke needs Textual")
    from rich.text import Text

    from tui.panels import ChainSlotIOWidget, ChainSlotWidget

    state = ChainState(_chain(["amp.nam"], slots=[{
        "path": "amp.nam", "input_gain_db": 9.0, "output_gain_db": -12.0,
    }]))
    widget = ChainSlotWidget(0, state.slot(0), title="Tone", gear="amp")

    assert widget._format_io_value("input_gain_db") == " 09.0"
    assert widget._format_io_value("output_gain_db") == "-12.0"
    io = ChainSlotIOWidget(widget)
    lines = [Text.from_markup(line).plain
             for line in io.render().splitlines()]
    assert lines == [
        "input  [-]  09.0 [+]",
        "output [-] -12.0 [+] [CAL]",
    ]
    assert [Text.from_markup(line).cell_len for line in lines] == [20, 26]


def test_slot_io_hit_map_keeps_two_rows_and_button_columns():
    pytest.importorskip("textual", reason="T05 renderer smoke needs Textual")
    from tui.panels import ChainSlotIOWidget, ChainSlotWidget

    state = ChainState(_chain(["amp.nam"]))
    slot = ChainSlotWidget(0, state.slot(0), title="Tone", gear="amp")
    io = ChainSlotIOWidget(slot)

    # The standalone widget owns its coordinate system: input and output
    # controls share the same columns, while CAL is only on output's row.
    assert io._hit_at_offset(slot.IO_LABEL_WIDTH, 0)[:3] == (
        "param", "input_gain_db", -0.5)
    plus_start = (slot.IO_LABEL_WIDTH + slot.IO_BUTTON_WIDTH + slot.IO_GAP
                  + slot.IO_VALUE_WIDTH + slot.IO_GAP)
    assert io._hit_at_offset(plus_start, 1)[:3] == (
        "param", "output_gain_db", 0.5)
    cal_start = plus_start + slot.IO_BUTTON_WIDTH + slot.IO_GAP
    assert io._hit_at_offset(cal_start + 2, 1)[:3] == (
        "calibrate", None, 0.0)
    assert io._hit_at_offset(cal_start + 2, 0) is None


def test_slot_io_hover_uses_light_feedback_for_buttons_and_values():
    pytest.importorskip("textual", reason="T05 renderer smoke needs Textual")
    from tui.panels import ChainSlotIOWidget, ChainSlotWidget

    state = ChainState(_chain(["amp.nam"]))
    slot = ChainSlotWidget(0, state.slot(0), title="Tone", gear="amp")
    io = ChainSlotIOWidget(slot)

    slot._io_hover = ("input_gain_db", -slot.IO_STEP_DB)
    assert "$accent on $surface-lighten-1" in io.render()

    slot._io_hover = ("input_gain_db", "value")
    rendered = io.render()
    assert "not bold $text on $surface-lighten-1" in rendered
    assert "[b $text" not in rendered


@pytest.mark.parametrize(
    ("gear", "label"),
    [
        ("amp", "AMP"),
        ("amp-cab", "AMP-CAB"),
        ("future-device", "FUTURE-DEVICE"),
    ],
)
def test_slot_renderer_uppercases_native_gear_without_a_fixed_type_table(
    gear: str, label: str
):
    state = ChainState(_chain(["model.nam"]))
    rendered = _render_slot_text(
        state.slot(0), {"title": "Future Tone", "gear": gear}
    )

    assert label in rendered
    assert "SLOT" not in rendered
    assert "Future Tone" in rendered


def test_slot_renderer_titles_and_bypass_filename_order():
    path = "amp-cab-demo.nam"
    tone = {"title": "Combo Tone", "gear": "amp-cab"}
    state = ChainState(_chain([path]))

    active = _render_slot_text(state.slot(0), tone)
    assert "[bold $success]●[/]" in active
    assert "AMP-CAB" in active
    assert "Combo Tone" in active
    assert "BYPASS" not in active

    assert state.toggle_bypass(0) is True
    bypass = _render_slot_text(state.slot(0), tone)
    first_line = bypass.splitlines()[0]
    assert "[bold $error]●[/]" in first_line
    assert "AMP-CAB" in first_line
    assert "BYPASS" not in first_line
    assert bypass.index(Path(path).name) < bypass.index("BYPASS")
    assert "AMP-CAB - BYPASS" not in bypass

    empty = _render_slot_text(ChainState(_chain([None])).slot(0), None)
    assert "[bold $state-idle]○[/]" in empty
    assert "SLOT" in empty
    assert "NONE" in empty
    assert "BYPASS" not in empty

    for text in (active, bypass, empty):
        assert "▶" not in text
        assert "▷" not in text
        assert ">" not in text


def test_dynamic_panel_routes_focus_bypass_reorder_delete_and_add(
    monkeypatch, tmp_path
):
    pytest.importorskip("textual", reason="dynamic operation smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.wav")
    ], revision=1)}

    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    def write_chain(chain: dict, **_kwargs):
        current["chain"] = dict(chain)
        current["chain"]["revision"] = int(chain.get("revision", 0)) + 1

    monkeypatch.setattr("tui.app.live.write_chain", write_chain)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            panel.slot_widgets[0].focus()
            await pilot.pause()
            assert panel.state.target_index == 0

            await pilot.press("enter")
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.BYPASS
            assert "BYPASS" in panel.slot_widgets[0].render()

            await pilot.press("alt+down")
            await pilot.pause()
            assert [slot.path for slot in panel.state.slots] == [
                str(tmp_path / "b.wav"), None
            ]
            assert panel.state.target_index == 1
            assert app.focused is panel.slot_widgets[1]

            await pilot.press("d")
            await pilot.pause()
            assert panel.state.slot_count == 1
            assert panel.state.target_index == 0
            assert app.focused is panel.slot_widgets[0]

            await pilot.click(panel.add_slot)
            await pilot.pause()
            assert panel.state.slot_count == 2
            assert panel.state.slot(1).status is SlotStatus.EMPTY
            assert panel.state.target_index == 1

    asyncio.run(scenario())


def test_dynamic_panel_io_buttons_and_calibration_are_clickable(
    monkeypatch, tmp_path
):
    pytest.importorskip("textual", reason="dynamic I/O smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel, ChainSlotIOWidget

    current = {"chain": _chain([str(tmp_path / "a.nam")], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr(
        "tui.app.live.read_chain_snapshot",
        lambda: (dict(current["chain"]), None),
    )
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    def write_chain(chain: dict, **_kwargs):
        current["chain"] = dict(chain)
        current["chain"]["revision"] = int(chain.get("revision", 0)) + 1

    monkeypatch.setattr("tui.app.live.write_chain", write_chain)
    monkeypatch.setattr("tui.app.live.request_output_calibration",
                        lambda _index: 4.5)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            io = panel.query_one(ChainSlotIOWidget)

            slot = panel.slot_widgets[0]
            # Keep this single-click test independent from the real hold
            # threshold when the full Textual suite is under load.
            slot.IO_HOLD_INITIAL_DELAY = 10.0
            plus_x = (slot.IO_LABEL_WIDTH + slot.IO_BUTTON_WIDTH + slot.IO_GAP
                      + slot.IO_VALUE_WIDTH + slot.IO_GAP + 1)
            await pilot.click(io, offset=(plus_x, 0))
            await pilot.pause()
            assert panel.state.slot(0).input_gain_db == pytest.approx(0.5)

            io = panel.query_one(ChainSlotIOWidget)
            slot = panel.slot_widgets[0]
            value_x = (slot.IO_LABEL_WIDTH + slot.IO_BUTTON_WIDTH
                       + slot.IO_GAP + 1)
            await pilot.click(io, offset=(value_x, 0))
            await pilot.pause()
            assert slot._io_editing == "input_gain_db"
            assert "▌" in slot._format_io_value("input_gain_db")
            slot._io_cursor_visible = False
            assert "▌" not in slot._format_io_value("input_gain_db")
            slot._io_cursor_visible = True
            await pilot.double_click(io, offset=(value_x, 0))
            await pilot.pause()
            assert panel.state.slot(0).input_gain_db == pytest.approx(0.0)
            assert slot._io_editing is None

            await pilot.click(io, offset=(23, 1))
            await pilot.pause(0.15)
            assert panel.state.slot(0).output_gain_db == pytest.approx(4.5)

    asyncio.run(scenario())


def test_input_focus_does_not_change_target_slot(monkeypatch, tmp_path):
    """Focusing INPUT or its selected Slot's input value preserves the target."""
    pytest.importorskip("textual", reason="input focus regression needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel, ChainSlotIOWidget

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            assert len(panel.slot_widgets) == 2

            panel.slot_widgets[0].focus()
            await pilot.pause()
            assert panel.state.target_index == 0

            # ChainPanel asks every Slot row whether a bubbled click is a
            # border-hint action. A non-hint click must not focus those rows.
            input_click = SimpleNamespace(
                screen_x=panel.input_node.region.x + 1,
                screen_y=panel.input_node.region.y,
                stop=lambda: None,
            )
            assert panel.handle_slot_hint_click(input_click) is False
            assert panel.state.target_index == 0

            await pilot.click(panel.input_node)
            await pilot.pause()
            assert app.focused is panel.input_node
            assert panel.state.target_index == 0

            io_widgets = list(panel.query(ChainSlotIOWidget))
            slot = panel.slot_widgets[0]
            value_x = (slot.IO_LABEL_WIDTH + slot.IO_BUTTON_WIDTH
                       + slot.IO_GAP + 1)
            await pilot.click(io_widgets[0], offset=(value_x, 0))
            await pilot.pause()
            assert app.focused is slot
            assert panel.state.target_index == 0

    asyncio.run(scenario())


def test_dynamic_recompose_preserves_input_focus(monkeypatch, tmp_path):
    """A background Slot-list rebuild must not steal focus from INPUT."""
    pytest.importorskip("textual", reason="recompose focus regression needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            panel.slot_widgets[1].focus()
            await pilot.pause()
            assert panel.state.target_index == 1

            panel.input_node.focus()
            await pilot.pause()
            assert app.focused is panel.input_node

            current["chain"] = _chain([
                str(tmp_path / "a.nam"), str(tmp_path / "b.nam"),
                str(tmp_path / "c.nam"),
            ], revision=2)
            panel.chain = dict(current["chain"])
            await pilot.pause(0.2)

            assert app.focused is panel.input_node
            assert len(panel.slot_widgets) == 3

            search = app.query_one("#local-search")
            search.focus()
            await pilot.pause()
            assert app.focused is search

            current["chain"] = _chain([
                str(tmp_path / "a.nam"), str(tmp_path / "b.nam"),
                str(tmp_path / "c.nam"), str(tmp_path / "d.nam"),
            ], revision=3)
            panel.chain = dict(current["chain"])
            await pilot.pause(0.2)

            assert app.focused is search
            assert len(panel.slot_widgets) == 4

    asyncio.run(scenario())


def test_dynamic_recompose_restores_slot_focus_by_identity(
        monkeypatch, tmp_path):
    """A rebuilt row must not inherit focus from a deleted Slot index."""
    pytest.importorskip("textual", reason="recompose focus regression needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            original = panel.slot_widgets[1]
            original.focus()
            await pilot.pause()
            identity = panel.state.target_identity
            assert identity is not None

            # Recompose without the focused logical Slot. The remaining row
            # moves into its old index and must not receive the focus.
            current["chain"] = _chain([
                str(tmp_path / "a.nam")
            ], revision=2)
            panel.chain = dict(current["chain"])
            await pilot.pause(0.2)

            assert panel.state.index_for_identity(identity) is None
            assert app.focused is not panel.slot_widgets[0]

    asyncio.run(scenario())


def test_whole_chain_replacement_same_length_does_not_retarget_focus(
        monkeypatch, tmp_path):
    """A whole replacement must not reuse a focused row for a new Slot."""
    pytest.importorskip("textual", reason="recompose focus regression needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            old_widget = panel.slot_widgets[1]
            old_widget.focus()
            await pilot.pause()
            assert panel.state.target_identity == old_widget.snapshot.identity

            current["chain"] = _chain([
                str(tmp_path / "c.nam"), str(tmp_path / "d.nam")
            ], revision=2)
            panel.chain = dict(current["chain"])
            await pilot.pause(0.2)

            assert panel.state.target_identity is None
            assert app.focused is not panel.slot_widgets[1]

    asyncio.run(scenario())


def test_whole_chain_anchor_restore_does_not_retarget_new_slot(
        monkeypatch, tmp_path):
    """A stale mutation anchor must not focus a replacement row by index."""
    pytest.importorskip("textual", reason="anchor focus regression needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            panel.slot_widgets[1].focus()
            await pilot.pause()
            anchor = panel.capture_view_anchor()
            assert anchor.focused_widget == "slot:1"

            current["chain"] = _chain([
                str(tmp_path / "c.nam"), str(tmp_path / "d.nam")
            ], revision=2)
            panel.chain = dict(current["chain"])
            await pilot.pause(0.2)
            assert panel.state.target_identity is None

            # MutationRefreshCoordinator restores this anchor after its
            # reconcile call. Replaying it here covers the same public seam.
            panel.restore_view_anchor(anchor)
            await pilot.pause()
            assert panel.state.target_identity is None
            assert app.focused is not panel.slot_widgets[1]

    asyncio.run(scenario())


def test_pending_recompose_does_not_use_fallback_after_whole_replacement(
        monkeypatch, tmp_path):
    """A newer whole-chain replacement invalidates an older recompose fallback."""
    pytest.importorskip("textual", reason="recompose race needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            panel.slot_widgets[1].focus()
            await pilot.pause()
            focus_state = panel._capture_recompose_focus()

            recompose_started = asyncio.Event()
            release_recompose = asyncio.Event()
            original_recompose = panel.recompose

            async def delayed_recompose():
                recompose_started.set()
                await release_recompose.wait()
                await original_recompose()

            monkeypatch.setattr(panel, "recompose", delayed_recompose)
            panel._schedule_dynamic_recompose(1, focus_state=focus_state)
            for _ in range(40):
                if recompose_started.is_set():
                    break
                await pilot.pause(0.025)
            assert recompose_started.is_set()

            current["chain"] = _chain([
                str(tmp_path / "c.nam"), str(tmp_path / "d.nam")
            ], revision=2)
            panel.state.replace_chain(current["chain"])
            panel._schedule_dynamic_recompose(
                panel.state.target_index,
                focus_state=panel._capture_recompose_focus())
            release_recompose.set()
            await pilot.pause(0.2)

            assert panel.state.target_identity is None
            assert app.focused is not panel.slot_widgets[1]

    asyncio.run(scenario())


def test_same_identity_recompose_keeps_queued_force_focus_request(
        monkeypatch, tmp_path):
    """A later focus intent is not swallowed when Slot identities are stable."""
    pytest.importorskip("textual", reason="recompose request race needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            await _wait_for_slots(pilot, panel)
            panel.slot_widgets[0].focus()
            await pilot.pause()

            first_started = asyncio.Event()
            release_first = asyncio.Event()
            recompose_calls = 0
            original_recompose = panel.recompose

            async def delayed_recompose():
                nonlocal recompose_calls
                recompose_calls += 1
                if recompose_calls == 1:
                    first_started.set()
                    await release_first.wait()
                await original_recompose()

            monkeypatch.setattr(panel, "recompose", delayed_recompose)
            identities = panel.state.slot_identities
            panel._schedule_dynamic_recompose(
                focus_state=("panel", None, None))
            for _ in range(40):
                if first_started.is_set():
                    break
                await pilot.pause(0.025)
            assert first_started.is_set()

            target_identity = identities[1]
            panel.state.focus_identity(target_identity)
            panel._schedule_dynamic_recompose(
                1, focus_state=("slot", 1, target_identity), force_focus=True)
            release_first.set()

            for _ in range(60):
                if (recompose_calls >= 2
                        and not getattr(panel, "_recompose_pending", False)):
                    break
                await pilot.pause(0.025)

            assert recompose_calls == 2
            assert app.focused is panel.slot_widgets[1]

    asyncio.run(scenario())


def test_recompose_drains_update_arriving_after_compose(
        monkeypatch, tmp_path):
    """A structural update after compose completes must still rebuild rows."""
    pytest.importorskip("textual", reason="recompose queue regression needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            original_recompose = panel.recompose
            original_refresh = panel._refresh_dynamic_slots
            compose_finished = asyncio.Event()
            update_requested = False

            async def instrumented_recompose():
                await original_recompose()
                compose_finished.set()

            def refresh_with_race():
                nonlocal update_requested
                original_refresh()
                if compose_finished.is_set() and not update_requested:
                    update_requested = True
                    current["chain"] = _chain([
                        str(tmp_path / "a.nam"), str(tmp_path / "b.nam"),
                        str(tmp_path / "c.nam"), str(tmp_path / "d.nam"),
                    ], revision=2)
                    panel.state.replace_chain(current["chain"])
                    panel._schedule_dynamic_recompose(
                        panel.state.target_index,
                        focus_state=("none", None, None))

            monkeypatch.setattr(panel, "recompose", instrumented_recompose)
            monkeypatch.setattr(panel, "_refresh_dynamic_slots",
                                refresh_with_race)
            panel._schedule_dynamic_recompose(
                focus_state=("none", None, None))

            for _ in range(40):
                if len(panel.slot_widgets) == 4:
                    break
                await pilot.pause(0.025)
            assert update_requested
            assert panel.state.slot_count == 4
            assert len(panel.slot_widgets) == 4

    asyncio.run(scenario())


def test_recompose_keeps_input_focus_after_slot_reorder_wait(
        monkeypatch, tmp_path):
    """A Slot reorder must not reclaim focus moved to INPUT while waiting."""
    pytest.importorskip("textual", reason="recompose focus regression needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            panel.slot_widgets[0].focus()
            await pilot.pause()
            focus_state = panel._capture_recompose_focus()
            panel.state.move_slot(0, 1)
            panel._refresh_dynamic_slots()

            recompose_started = asyncio.Event()
            release_recompose = asyncio.Event()
            original_recompose = panel.recompose

            async def delayed_recompose():
                recompose_started.set()
                await release_recompose.wait()
                await original_recompose()

            monkeypatch.setattr(panel, "recompose", delayed_recompose)
            panel._schedule_dynamic_recompose(
                panel.state.target_index, focus_state=focus_state)
            for _ in range(40):
                if recompose_started.is_set():
                    break
                await pilot.pause(0.025)
            assert recompose_started.is_set()

            panel.input_node.focus()
            await pilot.pause()
            assert app.focused is panel.input_node

            release_recompose.set()
            await pilot.pause(0.2)
            assert app.focused is panel.input_node

    asyncio.run(scenario())


def test_dynamic_recompose_does_not_steal_external_focus_after_wait(
        monkeypatch, tmp_path):
    """A delayed Slot rebuild must not reclaim focus after the user leaves."""
    pytest.importorskip("textual", reason="recompose focus regression needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.nam")
    ], revision=1)}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.chain_file_fingerprint", lambda: None)
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint", lambda: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            panel.slot_widgets[0].focus()
            await pilot.pause()

            recompose_started = asyncio.Event()
            release_recompose = asyncio.Event()
            original_recompose = panel.recompose

            async def delayed_recompose():
                recompose_started.set()
                await release_recompose.wait()
                await original_recompose()

            monkeypatch.setattr(panel, "recompose", delayed_recompose)
            current["chain"] = _chain([
                str(tmp_path / "a.nam"), str(tmp_path / "b.nam"),
                str(tmp_path / "c.nam"),
            ], revision=2)
            panel.chain = dict(current["chain"])
            for _ in range(40):
                if recompose_started.is_set():
                    break
                await pilot.pause(0.025)
            assert recompose_started.is_set()

            search = app.query_one("#local-search")
            search.focus()
            await pilot.pause()
            assert app.focused is search

            release_recompose.set()
            await pilot.pause(0.2)
            assert app.focused is search
            assert len(panel.slot_widgets) == 3

    asyncio.run(scenario())


def test_dynamic_panel_io_button_hold_repeats_and_coalesces(
    monkeypatch, tmp_path
):
    pytest.importorskip("textual", reason="dynamic I/O hold smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel, ChainSlotIOWidget

    current = {"chain": _chain([str(tmp_path / "a.nam")], revision=1)}
    writes = []
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.read_chain_snapshot",
                        lambda: (dict(current["chain"]), None))
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)

    def write_chain(chain: dict, **_kwargs):
        writes.append(dict(chain))
        current["chain"] = dict(chain)
        current["chain"]["revision"] = int(chain.get("revision", 0)) + 1

    monkeypatch.setattr("tui.app.live.write_chain", write_chain)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            io = panel.query_one(ChainSlotIOWidget)
            slot = panel.slot_widgets[0]
            # The repeat tick is driven explicitly below; prevent a real
            # timer callback from racing before the test reaches that point.
            slot.IO_HOLD_INITIAL_DELAY = 10.0
            plus_x = (slot.IO_LABEL_WIDTH + slot.IO_BUTTON_WIDTH
                      + slot.IO_GAP + slot.IO_VALUE_WIDTH
                      + slot.IO_GAP + 1)

            await pilot.mouse_down(io, offset=(plus_x, 0))
            # Drive one repeat tick directly. Textual's virtual pause can
            # execute a variable number of interval callbacks under the
            # complete suite, while the hold state machine itself is stable.
            if slot._io_repeat_timer is not None:
                slot._io_repeat_timer.stop()
                slot._io_repeat_timer = None
            slot._io_next_repeat_at = 0.0
            slot._repeat_io_hold()
            await pilot.mouse_up(io, offset=(plus_x, 0))
            await pilot.pause(0.6)

            value = panel.state.slot(0).input_gain_db
            assert value == pytest.approx(1.0)
            assert slot._io_hold_generation is None
            assert writes

    asyncio.run(scenario())


def test_dynamic_panel_tab_order_has_single_params_stop(monkeypatch, tmp_path):
    """v0.1.1 契约：ChainParams 是整个参数行的单一焦点站（无每参数 overlay）。"""
    pytest.importorskip("textual", reason="focus order smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.wav")
    ])
    monkeypatch.setattr("tui.app.live.read_chain", lambda: dict(current))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(160, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            for _ in range(40):
                if (len(panel.slot_widgets) == 2
                        and not getattr(panel, "_recompose_pending", False)):
                    break
                await pilot.pause(0.05)
            assert len(panel.slot_widgets) == 2
            assert not getattr(panel, "_recompose_pending", False)

            panel.input_node.focus()
            await pilot.pause()
            assert app.focused is panel.input_node

            await pilot.press("tab")
            assert app.focused is panel.slot_widgets[0]
            await pilot.press("tab")
            assert app.focused is panel.slot_widgets[1]
            await pilot.press("tab")
            assert app.focused is panel.add_slot
            await pilot.press("tab")
            assert app.focused is panel.params

            await pilot.press("shift+tab")
            assert app.focused is panel.add_slot
            await pilot.press("tab")
            assert app.focused is panel.params
            await pilot.press("shift+tab")
            assert app.focused is panel.add_slot

    asyncio.run(scenario())


def test_dynamic_panel_keyboard_groups_remain_disjoint(monkeypatch, tmp_path):
    pytest.importorskip("textual", reason="focus/action isolation needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    a0 = str(tmp_path / "a-0.nam")
    a1 = str(tmp_path / "a-1.nam")
    b0 = str(tmp_path / "b-0.nam")
    b1 = str(tmp_path / "b-1.nam")
    c0 = str(tmp_path / "c-0.nam")
    models = {
        a0: [{"id": 10, "local_path": a0, "tone_id": 1},
             {"id": 11, "local_path": a1, "tone_id": 1}],
        a1: [{"id": 10, "local_path": a0, "tone_id": 1},
             {"id": 11, "local_path": a1, "tone_id": 1}],
        b0: [{"id": 20, "local_path": b0, "tone_id": 2},
             {"id": 21, "local_path": b1, "tone_id": 2}],
        b1: [{"id": 20, "local_path": b0, "tone_id": 2},
             {"id": 21, "local_path": b1, "tone_id": 2}],
        c0: [{"id": 30, "local_path": c0, "tone_id": 3}],
    }
    current = {"chain": _chain([a0, b0, c0])}

    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: models.get(path, []))
    monkeypatch.setattr("tui.app.library.get_tone",
                        lambda tone_id: {"title": f"Tone {tone_id}",
                                         "gear": "amp"})

    def write_chain(chain: dict, **_kwargs):
        current["chain"] = dict(chain)

    monkeypatch.setattr("tui.app.live.write_chain", write_chain)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            middle = panel.slot_widgets[1]
            middle.focus()
            await pilot.pause()
            assert app.focused is middle

            original = [slot.path for slot in panel.state.slots]
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused is panel.slot_widgets[2]
            assert [slot.path for slot in panel.state.slots] == original

            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.focused is middle

            await pilot.press("down")
            await pilot.pause()
            assert app.focused is middle
            assert [slot.path for slot in panel.state.slots] == [a0, b1, c0]

            await pilot.press("up")
            await pilot.pause()
            assert app.focused is middle
            assert [slot.path for slot in panel.state.slots] == original

            await pilot.press("alt+down")
            await pilot.pause()
            assert [slot.path for slot in panel.state.slots] == [a0, c0, b0]
            assert panel.state.target_index == 2
            assert app.focused is panel.slot_widgets[2]

            await pilot.press("alt+up")
            await pilot.pause()
            assert [slot.path for slot in panel.state.slots] == original
            assert panel.state.target_index == 1
            assert app.focused is panel.slot_widgets[1]

    asyncio.run(scenario())
