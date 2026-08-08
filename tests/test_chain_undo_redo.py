"""REQ-017: undo/redo of preset-applied chain config.

Applying a preset rewrites live_chain.json (preset content domain:
ordered slots/gain/master/quality; the input source stays untouched by preset
semantics). Undo/redo restores that domain from a snapshot stack:
- every successful preset application pushes the pre-application snapshot
  onto the undo stack and clears the redo stack;
- ctrl+z restores the previous snapshot (current config moves to redo);
- ctrl+shift+z restores the redo snapshot;
- empty stacks do nothing (a notify only).
"""
import asyncio
from types import SimpleNamespace

import library
import library as lib
from tui import live
from tui.app import GigBuddyApp


def run(coro):
    return asyncio.run(coro)


def _make_env(monkeypatch, tmp_path) -> None:
    root = tmp_path
    tones = root / "data" / "tones"
    dry_inputs = root / "data" / "dry_inputs"
    tones.mkdir(parents=True)
    dry_inputs.mkdir(parents=True)
    monkeypatch.setattr(library, "ROOT", root)
    monkeypatch.setattr(library, "DB_FILE", root / "data" / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", root / "data" / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", tones)
    monkeypatch.setattr(lib, "ROOT", root)
    monkeypatch.setattr(lib, "CHAIN_FILE", root / "data" / "live_chain.json")
    monkeypatch.setattr(lib, "TONES_DIR", tones)
    monkeypatch.setattr(lib, "DRY_INPUTS_DIR", dry_inputs, raising=False)
    monkeypatch.setattr(live, "ROOT", root)
    monkeypatch.setattr(live, "CHAIN_FILE", root / "data" / "live_chain.json")
    monkeypatch.setattr(live, "TONES_DIR", tones)
    monkeypatch.setattr(live, "DRY_INPUTS_DIR", dry_inputs)
    (tones / "amp-a.nam").write_bytes(b"a")
    (tones / "amp-b.nam").write_bytes(b"b")
    (tones / "amp-c.nam").write_bytes(b"c")
    (tones / "cab-a.wav").write_bytes(b"c")
    (dry_inputs / "dry.wav").write_bytes(b"dry")


def _seed_presets(tmp_path) -> None:
    """Three presets whose snapshots differ in slots/gain."""
    tones = tmp_path / "data" / "tones"
    live.write_chain({"slots": [{"path": str(tones / "amp-a.nam")},
                                 {"path": str(tones / "cab-a.wav")}],
                      "gain": 1.0, "master": 0.8, "quality": 1.0})
    library.preset_save("preset-a", note="chain A (amp-a + cab-a)")
    live.write_chain({"slots": [{"path": str(tones / "amp-b.nam")}],
                      "gain": 0.5, "master": 0.6})
    library.preset_save("preset-b", note="chain B (amp-b, no IR)")
    live.write_chain({"slots": [{"path": str(tones / "amp-c.nam")}],
                      "gain": 0.2, "master": 0.9})
    library.preset_save("preset-c", note="chain C (amp-c)")


def test_undo_restores_preset_config_and_redo_reapplies(monkeypatch, tmp_path):
    """Applying preset-b then ctrl+z restores the pre-application chain
    (ordered slots and parameters); ctrl+shift+z re-applies preset-b."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    # 初始链 = A
    tones = tmp_path / "data" / "tones"
    live.write_chain({"slots": [{"path": str(tones / "amp-a.nam")},
                                 {"path": str(tones / "cab-a.wav")}],
                      "gain": 1.0, "master": 0.8, "quality": 1.0})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            assert live.read_chain()["slots"][0]["path"].endswith("amp-a.nam")
            # 应用 preset-b → 链 B（只有一个 Slot）
            app._apply_preset("preset-b")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["slots"] == [{"path": str(tones / "amp-b.nam")}]
            assert cfg["gain"] == 0.5
            # undo → 链 A 完整恢复
            await pilot.press("ctrl+z")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["slots"] == [
                {"path": str(tones / "amp-a.nam")},
                {"path": str(tones / "cab-a.wav")},
            ]
            assert cfg["gain"] == 1.0 and cfg["master"] == 0.8
            # redo → 链 B 再应用
            await pilot.press("ctrl+shift+z")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["slots"] == [{"path": str(tones / "amp-b.nam")}]
    run(scenario())


def test_undo_stack_keeps_the_latest_50_snapshots():
    holder = SimpleNamespace(_undo_stack=[], _CHAIN_UNDO_LIMIT=50)

    for index in range(51):
        GigBuddyApp._push_undo(holder, {"slots": [{"path": f"m{index}"}]})

    assert len(holder._undo_stack) == 50
    assert holder._undo_stack[0]["slots"][0]["path"] == "m1"
    assert holder._undo_stack[-1]["slots"][0]["path"] == "m50"


def test_undo_with_empty_stack_is_a_noop(monkeypatch, tmp_path):
    """No preset applied yet → ctrl+z must not touch the chain file."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    tones = tmp_path / "data" / "tones"
    live.write_chain({"slots": [{"path": str(tones / "amp-a.nam")},
                                 {"path": str(tones / "cab-a.wav")}],
                      "gain": 1.0, "master": 0.8, "quality": 1.0})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            before = live.read_chain()
            await pilot.press("ctrl+z")
            await pilot.pause(0.2)
            assert live.read_chain() == before
            assert not app._undo_stack
            await pilot.press("ctrl+shift+z")
            await pilot.pause(0.2)
            assert live.read_chain() == before
    run(scenario())


def test_new_preset_application_clears_redo(monkeypatch, tmp_path):
    """After undo, applying another preset invalidates the redo stack:
    ctrl+shift+z is a noop and the chain stays on the new preset."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    tones = tmp_path / "data" / "tones"
    live.write_chain({"slots": [{"path": str(tones / "amp-a.nam")},
                                 {"path": str(tones / "cab-a.wav")}],
                      "gain": 1.0, "master": 0.8, "quality": 1.0})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._apply_preset("preset-b")   # undo=[A]
            await pilot.pause(0.2)
            await pilot.press("ctrl+z")     # 回 A；redo=[B]
            await pilot.pause(0.2)
            assert live.read_chain()["slots"][0]["path"].endswith("amp-a.nam")
            app._apply_preset("preset-c")   # 新动作：undo=[A, A→?], redo 清空
            await pilot.pause(0.2)
            assert live.read_chain()["slots"][0]["path"].endswith("amp-c.nam")
            assert not app._redo_stack, "new preset application must clear redo"
            await pilot.press("ctrl+shift+z")
            await pilot.pause(0.2)
            assert live.read_chain()["slots"][0]["path"].endswith("amp-c.nam")
    run(scenario())


def test_undo_keeps_input_source(monkeypatch, tmp_path):
    """The input source is not part of the preset domain (preset semantics);
    undo/redo must preserve it exactly as the preset application did."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    tones = tmp_path / "data" / "tones"
    live.write_chain({"slots": [{"path": str(tones / "amp-a.nam")},
                                 {"path": str(tones / "cab-a.wav")}],
                      "gain": 1.0, "master": 0.8, "quality": 1.0,
                      "input": {"source": "file",
                                "file": str(tmp_path / "data" / "dry_inputs" / "dry.wav"),
                                "state": "playing", "loop": True}})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._apply_preset("preset-b")
            await pilot.pause(0.2)
            assert live.read_chain()["input"]["file"].endswith("dry.wav")
            await pilot.press("ctrl+z")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["input"]["source"] == "file"
            assert cfg["input"]["file"].endswith("dry.wav")
            # 链配置域已恢复 A，input 未动
            assert cfg["slots"][0]["path"].endswith("amp-a.nam")
    run(scenario())


def test_undo_with_missing_keys_does_not_inject_none(monkeypatch, tmp_path):
    """A chain whose file lacks a preset-domain key (e.g. no quality) must
    survive undo: the snapshot stores only keys actually present, so the
    restore never writes None into a protocol key (ChainPanel.watch_chain
    does float(chain.get('quality', 1.0)) and would crash on None)."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    # 链 A 只含 slots/gain/master —— quality 键缺失
    tones = tmp_path / "data" / "tones"
    live.write_chain({"slots": [{"path": str(tones / "amp-a.nam")},
                                 {"path": str(tones / "cab-a.wav")}],
                      "gain": 1.0, "master": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._apply_preset("preset-b")
            await pilot.pause(0.2)
            assert live.read_chain()["slots"][0]["path"].endswith("amp-b.nam")
            await pilot.press("ctrl+z")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["slots"][0]["path"].endswith("amp-a.nam")
            assert "quality" not in cfg or cfg["quality"] is not None
            assert cfg["slots"][1]["path"].endswith("cab-a.wav")
    run(scenario())
