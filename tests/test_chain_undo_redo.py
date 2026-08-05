"""REQ-017: undo/redo of preset-applied chain config.

Applying a preset rewrites live_chain.json (preset content domain:
model/ir/gain/master/quality; the input source stays untouched by preset
semantics). Undo/redo restores that domain from a snapshot stack:
- every successful preset application pushes the pre-application snapshot
  onto the undo stack and clears the redo stack;
- ctrl+z restores the previous snapshot (current config moves to redo);
- ctrl+shift+z restores the redo snapshot;
- empty stacks do nothing (a notify only).
"""
import asyncio

import library
import library as lib
from tui import live
from tui.app import GigBuddyApp


def run(coro):
    return asyncio.run(coro)


def _make_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(lib, "CHAIN_FILE", tmp_path / "live_chain.json")
    (tmp_path / "amp-a.nam").write_bytes(b"a")
    (tmp_path / "amp-b.nam").write_bytes(b"b")
    (tmp_path / "amp-c.nam").write_bytes(b"c")
    (tmp_path / "cab-a.wav").write_bytes(b"c")


def _seed_presets(tmp_path) -> None:
    """Three presets whose snapshots differ in model/ir/gain."""
    live.write_chain({"model": str(tmp_path / "amp-a.nam"),
                      "ir": str(tmp_path / "cab-a.wav"),
                      "gain": 1.0, "master": 0.8, "quality": 1.0})
    library.preset_save("preset-a", note="chain A (amp-a + cab-a)")
    live.write_chain({"model": str(tmp_path / "amp-b.nam"),
                      "gain": 0.5, "master": 0.6})
    library.preset_save("preset-b", note="chain B (amp-b, no IR)")
    live.write_chain({"model": str(tmp_path / "amp-c.nam"),
                      "gain": 0.2, "master": 0.9, "ir": None})
    library.preset_save("preset-c", note="chain C (amp-c)")


def test_undo_restores_preset_config_and_redo_reapplies(monkeypatch, tmp_path):
    """Applying preset-b then ctrl+z restores the pre-application chain
    (model, IR, gain); ctrl+shift+z re-applies preset-b."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    # 初始链 = A
    live.write_chain({"model": str(tmp_path / "amp-a.nam"),
                      "ir": str(tmp_path / "cab-a.wav"),
                      "gain": 1.0, "master": 0.8, "quality": 1.0})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            assert live.read_chain()["model"].endswith("amp-a.nam")
            # 应用 preset-b → 链 B（无 IR：显式 null 移除旧 cab）
            app._apply_preset("preset-b")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["model"].endswith("amp-b.nam")
            assert cfg["ir"] is None and cfg["gain"] == 0.5
            # undo → 链 A 完整恢复
            await pilot.press("ctrl+z")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["model"].endswith("amp-a.nam")
            assert cfg["ir"].endswith("cab-a.wav")
            assert cfg["gain"] == 1.0 and cfg["master"] == 0.8
            # redo → 链 B 再应用
            await pilot.press("ctrl+shift+z")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["model"].endswith("amp-b.nam")
            assert cfg["ir"] is None
    run(scenario())


def test_undo_with_empty_stack_is_a_noop(monkeypatch, tmp_path):
    """No preset applied yet → ctrl+z must not touch the chain file."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    live.write_chain({"model": str(tmp_path / "amp-a.nam"),
                      "ir": str(tmp_path / "cab-a.wav"),
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
    live.write_chain({"model": str(tmp_path / "amp-a.nam"),
                      "ir": str(tmp_path / "cab-a.wav"),
                      "gain": 1.0, "master": 0.8, "quality": 1.0})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._apply_preset("preset-b")   # undo=[A]
            await pilot.pause(0.2)
            await pilot.press("ctrl+z")     # 回 A；redo=[B]
            await pilot.pause(0.2)
            assert live.read_chain()["model"].endswith("amp-a.nam")
            app._apply_preset("preset-c")   # 新动作：undo=[A, A→?], redo 清空
            await pilot.pause(0.2)
            assert live.read_chain()["model"].endswith("amp-c.nam")
            assert not app._redo_stack, "new preset application must clear redo"
            await pilot.press("ctrl+shift+z")
            await pilot.pause(0.2)
            assert live.read_chain()["model"].endswith("amp-c.nam")
    run(scenario())


def test_undo_keeps_input_source(monkeypatch, tmp_path):
    """The input source is not part of the preset domain (preset semantics);
    undo/redo must preserve it exactly as the preset application did."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    live.write_chain({"model": str(tmp_path / "amp-a.nam"),
                      "ir": str(tmp_path / "cab-a.wav"),
                      "gain": 1.0, "master": 0.8, "quality": 1.0,
                      "input": {"source": "file",
                                "file": str(tmp_path / "dry.wav"),
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
            assert cfg["model"].endswith("amp-a.nam")
    run(scenario())


def test_undo_with_missing_keys_does_not_inject_none(monkeypatch, tmp_path):
    """A chain whose file lacks a preset-domain key (e.g. no quality) must
    survive undo: the snapshot stores only keys actually present, so the
    restore never writes None into a protocol key (ChainPanel.watch_chain
    does float(chain.get('quality', 1.0)) and would crash on None)."""
    _make_env(monkeypatch, tmp_path)
    _seed_presets(tmp_path)
    # 链 A 只含 model/ir/gain/master —— quality 键缺失
    live.write_chain({"model": str(tmp_path / "amp-a.nam"),
                      "ir": str(tmp_path / "cab-a.wav"),
                      "gain": 1.0, "master": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._apply_preset("preset-b")
            await pilot.pause(0.2)
            assert live.read_chain()["model"].endswith("amp-b.nam")
            await pilot.press("ctrl+z")
            await pilot.pause(0.2)
            cfg = live.read_chain()
            assert cfg["model"].endswith("amp-a.nam")
            assert "quality" not in cfg or cfg["quality"] is not None
            assert live.read_chain()["ir"].endswith("cab-a.wav")
    run(scenario())


def test_undo_stack_bounded_at_50(monkeypatch, tmp_path):
    """The undo stack keeps at most 50 snapshots (FIFO drop of the oldest)."""
    _make_env(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            for i in range(51):
                app._push_undo({"model": f"m{i}"})
            assert len(app._undo_stack) == 50
            assert app._undo_stack[0]["model"] == "m1"  # oldest (m0) dropped
            assert app._undo_stack[-1]["model"] == "m50"
    run(scenario())
