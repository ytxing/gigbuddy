"""Narrow T05 acceptance tests for the v0.2 dynamic ChainPanel.

The state cases use the Textual-free ChainState seam. Renderer assertions use
the small semi-public ChainSlotWidget seam. Keeping that adapter here prevents
the old fixed AMP/CAB widgets from becoming the v0.2 oracle.
"""

from __future__ import annotations

import asyncio
from itertools import combinations
from pathlib import Path

import pytest

from tui.chain_state import (
    MAX_SLOTS,
    ChainState,
    ChainStateError,
    SlotStatus,
)
from tui.live import CHAIN_PARAMETER_DEFAULTS


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


def _widget_plain(widget) -> str:
    rendered = widget.render()
    plain = getattr(rendered, "plain", rendered)
    return plain if isinstance(plain, str) else str(plain)


def _region_tuple(region) -> tuple[int, int, int, int]:
    return region.x, region.y, region.width, region.height


def _regions_overlap(first, second) -> bool:
    return (
        first.x < second.x + second.width
        and second.x < first.x + first.width
        and first.y < second.y + second.height
        and second.y < first.y + first.height
    )


def _hint_click_offset(panel, token: str) -> tuple[int, int]:
    from rich.cells import cell_len
    from tui.modals import hint_span

    label = str(panel.border_subtitle)
    span = hint_span(label, token)
    assert span is not None, f"token {token!r} not in hint {label!r}"
    label_width = cell_len(label)
    label_start = panel.region.x + max(1, panel.region.width - label_width - 2)
    return (
        label_start + span[0] + 1 - panel.region.x,
        panel.region.bottom - 1 - panel.region.y,
    )


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

    def write_chain(chain: dict):
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

            await pilot.click(panel.add_slot)
            await pilot.pause()
            assert panel.state.slot_count == 2
            assert panel.state.slot(1).status is SlotStatus.EMPTY
            assert panel.state.target_index == 1

    asyncio.run(scenario())


def test_dynamic_panel_tab_order_has_one_stop_per_parameter(monkeypatch, tmp_path):
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

            for index in range(3):
                await pilot.press("tab")
                assert app.focused.id == f"chain-param-{index}"

            await pilot.press("shift+tab")
            assert app.focused.id == "chain-param-1"
            await pilot.press("shift+tab")
            assert app.focused.id == "chain-param-0"
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

    def write_chain(chain: dict):
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


def test_dynamic_panel_mouse_actions_match_slot_keyboard_actions(
    monkeypatch, tmp_path
):
    pytest.importorskip("textual", reason="mouse action smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    a0 = str(tmp_path / "a-0.nam")
    a1 = str(tmp_path / "a-1.nam")
    b0 = str(tmp_path / "b-0.nam")
    c0 = str(tmp_path / "c-0.nam")
    models = {
        a0: [{"id": 10, "local_path": a0, "tone_id": 1},
             {"id": 11, "local_path": a1, "tone_id": 1}],
        a1: [{"id": 10, "local_path": a0, "tone_id": 1},
             {"id": 11, "local_path": a1, "tone_id": 1}],
        b0: [{"id": 20, "local_path": b0, "tone_id": 2}],
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

    def write_chain(chain: dict):
        current["chain"] = dict(chain)

    monkeypatch.setattr("tui.app.live.write_chain", write_chain)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            slot = panel.slot_widgets[0]
            slot.focus()
            await pilot.pause()

            await pilot.press("down")
            await pilot.pause()
            assert current["chain"]["slots"][0]["path"] == a1

            await pilot.click(panel.query_one("#chain-slot-0-up"))
            await pilot.pause()
            assert current["chain"]["slots"][0]["path"] == a0
            assert app.focused is slot

            await pilot.press("enter")
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.BYPASS

            await pilot.double_click(slot)
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.ACTIVE

            await pilot.click(
                panel, offset=_hint_click_offset(panel, "⌥↓ move"))
            await pilot.pause()
            assert [slot.path for slot in panel.state.slots] == [b0, a0, c0]
            assert panel.state.target_index == 1
            assert app.focused is panel.slot_widgets[1]

    asyncio.run(scenario())


def test_dynamic_hint_move_tokens_preserve_direction(monkeypatch, tmp_path):
    pytest.importorskip("textual", reason="hint action smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = _chain([
        str(tmp_path / "a.nam"), str(tmp_path / "b.wav"),
        str(tmp_path / "c.nam"),
    ])
    monkeypatch.setattr("tui.app.live.read_chain", lambda: dict(current))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(160, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await pilot.pause(0.15)
            panel._last_focus_slot = 1
            moves = []
            monkeypatch.setattr(
                app, "_move_slot",
                lambda index, direction: moves.append((index, direction)),
            )

            actions = dict(panel._dynamic_hint_actions())
            actions["⌥↑ move"]()
            actions["⌥↓ move"]()
            await pilot.pause()

            assert moves == [(1, -1), (1, +1)]

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [0, 1, 6])
def test_dynamic_panel_geometry_keeps_add_and_params_in_order(
    monkeypatch, tmp_path, count
):
    pytest.importorskip("textual", reason="geometry smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = _chain([
        str(tmp_path / f"slot-{index}.nam") for index in range(count)
    ])
    monkeypatch.setattr("tui.app.live.read_chain", lambda: dict(current))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            await pilot.pause(0.15)
            detail = app.query_one("DetailPane")

            assert panel.add_slot.region.bottom <= panel.params.region.y
            assert panel.region.bottom <= detail.region.y
            rows = list(panel.query(".chain-slot-row"))
            assert len(rows) == count
            assert all(row.region.height == 4 for row in rows)
            assert all(
                current_row.region.y - previous.region.y == 4
                for previous, current_row in zip(rows, rows[1:])
            )

    asyncio.run(scenario())


def test_dynamic_panel_long_content_and_state_changes_keep_row_geometry(
    monkeypatch, tmp_path
):
    pytest.importorskip("textual", reason="dynamic geometry needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    long_path = str(tmp_path / ("long-" + "x" * 96 + ".nam"))
    second_path = str(tmp_path / "second.nam")
    long_title = "A tone title that is deliberately much longer than one row " * 3
    models = {
        long_path: [{"id": 1, "local_path": long_path, "tone_id": 1}],
        second_path: [{"id": 2, "local_path": second_path, "tone_id": 2}],
    }
    tones = {
        1: {"title": long_title, "gear": "future-device"},
        2: {"title": "Second Tone", "gear": "amp-cab"},
    }
    current = {"chain": _chain([long_path, second_path])}

    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.live.last_chain_write_fingerprint",
                        lambda: None)
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: models.get(path, []))
    monkeypatch.setattr("tui.app.library.get_tone",
                        lambda tone_id: tones[tone_id])

    def write_chain(chain: dict):
        current["chain"] = dict(chain)

    monkeypatch.setattr("tui.app.live.write_chain", write_chain)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            rows = list(panel.query(".chain-slot-row"))
            assert len(rows) == 2

            def geometry():
                return {
                    row.id: (
                        _region_tuple(row.region),
                        _region_tuple(row.query_one(".chain-slot").region),
                        _region_tuple(row.query_one(".chain-slot-actions").region),
                    )
                    for row in rows
                }

            baseline = geometry()
            for row in rows:
                slot = row.query_one(".chain-slot")
                actions = row.query_one(".chain-slot-actions")
                assert row.region.height == 4
                assert actions.region.width == 11
                assert slot.region.x + slot.region.width <= actions.region.x
                assert actions.region.x + actions.region.width <= row.region.x + row.region.width

            await pilot.click(panel.slot_widgets[0])
            await pilot.pause()
            assert app.focused is panel.slot_widgets[0]
            await pilot.press("enter")
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.BYPASS
            assert geometry() == baseline

            current["chain"] = _chain([None, second_path], revision=2)
            panel.chain = dict(current["chain"])
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.EMPTY
            assert geometry() == baseline

            current["chain"] = _chain([long_path, second_path], revision=3)
            panel.chain = dict(current["chain"])
            await pilot.pause()
            assert panel.state.slot(0).status is SlotStatus.ACTIVE
            assert geometry() == baseline

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(120, 40), (80, 32)])
def test_dynamic_panel_normal_and_minimum_sizes_have_no_overlap(
    monkeypatch, tmp_path, size
):
    pytest.importorskip("textual", reason="dynamic size smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([
        str(tmp_path / "one.nam"), str(tmp_path / "two.wav")
    ])}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: dict(current["chain"]))
    monkeypatch.setattr("tui.app.library.local_models_by_tone", lambda path: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            panel = app.query_one(ChainPanel)
            detail = app.query_one("DetailPane")
            unsupported = app.query_one("#unsupported-size")

            assert not unsupported.has_class("unsupported-size--visible")
            assert panel.region.bottom <= detail.region.y

            children = [child for child in panel.children
                        if child.region.width and child.region.height]
            for first, second in combinations(children, 2):
                assert not _regions_overlap(first.region, second.region), (
                    f"{first!r} overlaps {second!r} at {size}"
                )

            rows = list(panel.query(".chain-slot-row"))
            assert len(rows) == 2
            for row in rows:
                slot = row.query_one(".chain-slot")
                actions = row.query_one(".chain-slot-actions")
                assert row.region.height == 4
                assert actions.region.width == 11
                assert slot.region.x + slot.region.width <= actions.region.x
                assert actions.region.x + actions.region.width <= row.region.x + row.region.width

            hint = str(panel.border_subtitle)
            assert "d" in hint or "enter" in hint, (
                f"main ChainPanel action missing at {size}: {hint!r}"
            )

    asyncio.run(scenario())


def test_dynamic_panel_smoke_tracks_zero_one_and_six_slot_rows(monkeypatch, tmp_path):
    pytest.importorskip("textual", reason="dynamic ChainPanel smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    gears = ["amp", "amp-cab", "future-device", "cab", "pedal", "outboard"]
    paths = [str(tmp_path / f"slot-{index}.nam") for index in range(6)]
    models = {
        path: {"id": index, "tone_id": index, "local_path": path}
        for index, path in enumerate(paths)
    }
    tones = {
        index: {"id": index, "title": f"Tone {index}", "gear": gear}
        for index, gear in enumerate(gears)
    }
    current = {"chain": _chain([])}

    def read_chain():
        return dict(current["chain"])

    monkeypatch.setattr("tui.app.live.read_chain", read_chain)
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [models[path]])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tones[tone_id])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(180, 48)) as pilot:
            panel = app.query_one(ChainPanel)
            for count in (0, 1, 6):
                current["chain"] = _chain(paths[:count])
                panel.chain = dict(current["chain"])
                await pilot.pause(0.15)

                rows = list(panel.query(".chain-slot"))
                assert len(rows) == count
                text = "\n".join(
                    f"{getattr(getattr(row, 'parent', None), 'border_title', '')}\n"
                    f"{_widget_plain(row)}"
                    for row in rows
                )
                assert "▶" not in text
                assert "▷" not in text
                assert ">" not in text
                for index in range(count):
                    assert gears[index].upper() in text

    asyncio.run(scenario())


def test_quality_dot_reset_writes_protocol_default(monkeypatch):
    pytest.importorskip("textual", reason="quality reset smoke needs Textual")
    from tui.app import GigBuddyApp
    from tui.panels import ChainPanel

    current = {"chain": _chain([], quality=0.35)}
    writes: list[dict] = []

    monkeypatch.setattr(
        "tui.app.live.read_chain", lambda: dict(current["chain"])
    )

    def write_chain(chain: dict):
        current["chain"] = dict(chain)
        writes.append(dict(chain))

    monkeypatch.setattr("tui.app.live.write_chain", write_chain)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(160, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(current["chain"])
            await pilot.pause(0.2)
            params = panel.params
            assert hasattr(params, "_dot_spans"), \
                "dynamic ChainPanel must initialize quality controls"
            dot_x = params._dot_spans[2][1] + 1
            await pilot.click(params, offset=(dot_x, 0))
            await pilot.pause()

            assert writes
            assert writes[-1]["quality"] == CHAIN_PARAMETER_DEFAULTS["quality"] == 1.0
            assert current["chain"]["quality"] == 1.0

    asyncio.run(scenario())
