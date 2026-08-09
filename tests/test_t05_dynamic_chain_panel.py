"""Narrow T05 acceptance tests for the v0.2 dynamic ChainPanel.

The state cases use the Textual-free ChainState seam. Renderer assertions use
the small semi-public ChainSlotWidget seam. Keeping that adapter here prevents
the old fixed AMP/CAB widgets from becoming the v0.2 oracle.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
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


def test_managed_move_does_not_block_ui_and_publishes_after_worker(
        monkeypatch, tmp_path):
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(ChainPanel)
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


def test_managed_calibration_eventually_updates_slot_output(monkeypatch,
                                                            tmp_path):
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    _patch_managed_runtime(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        app._managed_engine_active = lambda: True
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.1)
            panel = app.query_one(ChainPanel)
            path = panel.state.slot(1).path
            app._calibration_generation = 1
            app._apply_slot_calibration(1, 1, path, -5.875)
            await pilot.pause(0.35)
            assert panel.state.slot(1).output_gain_db == pytest.approx(-5.88)

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

    assert widget._format_io_value("input_gain_db") == "+09.0"
    assert widget._format_io_value("output_gain_db") == "-12.0"
    io = ChainSlotIOWidget(widget)
    lines = [Text.from_markup(line).plain
             for line in io.render().splitlines()]
    assert lines == [
        "input  [-] +09.0 [+]",
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
        "param", "input_gain_db", -1.0)
    plus_start = (slot.IO_LABEL_WIDTH + slot.IO_BUTTON_WIDTH + slot.IO_GAP
                  + slot.IO_VALUE_WIDTH + slot.IO_GAP)
    assert io._hit_at_offset(plus_start, 1)[:3] == (
        "param", "output_gain_db", 1.0)
    cal_start = plus_start + slot.IO_BUTTON_WIDTH + slot.IO_GAP
    assert io._hit_at_offset(cal_start + 2, 1)[:3] == (
        "calibrate", None, 0.0)
    assert io._hit_at_offset(cal_start + 2, 0) is None


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
            plus_x = (slot.IO_LABEL_WIDTH + slot.IO_BUTTON_WIDTH + slot.IO_GAP
                      + slot.IO_VALUE_WIDTH + slot.IO_GAP + 1)
            await pilot.click(io, offset=(plus_x, 0))
            await pilot.pause()
            assert panel.state.slot(0).input_gain_db == pytest.approx(1.0)

            await pilot.click(io, offset=(23, 1))
            await pilot.pause(0.15)
            assert panel.state.slot(0).output_gain_db == pytest.approx(4.5)

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
            await pilot.pause(0.15)

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
