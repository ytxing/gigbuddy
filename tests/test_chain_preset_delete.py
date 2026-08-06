"""REQ-016 回归：固定节点上的 d 键/提示行 delete 必须生效。

Preset 的固定 AMP/CAB 缺位显示案例属于旧的 model/ir UI 契约，已由 v0.2
canonical Slot preset 测试取代；本文件只保留仍覆盖删除路径的回归案例。
"""
import asyncio
from pathlib import Path

from tui.app import GigBuddyApp
from tui.panels import ChainPanel, NodeWidget
import library


def run(coro):
    return asyncio.run(coro)


def setup_app(monkeypatch, tmp_path, *, ir: Path | None = None):
    """隔离 DB/链文件；state 驱动 read/write_chain（模拟文件通道）。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    amp = tmp_path / "MV5.nam"
    amp.write_bytes(b"a")
    ir_path = tmp_path / "GB.wav"
    ir_path.write_bytes(b"b")
    state = {"chain": {"model": str(amp), "gain": 0.8}}
    if ir is not None:
        state["chain"]["ir"] = str(ir_path) if ir is True else str(ir)

    def read():
        return dict(state["chain"])

    def write(cfg):
        state["chain"] = dict(cfg)

    monkeypatch.setattr("tui.app.live.read_chain", read)
    monkeypatch.setattr("tui.app.live.write_chain", write)
    monkeypatch.setattr(
        "tui.app.library.tone_title_for_path", lambda path: "TONE")
    return state


def node(app, kind: str) -> NodeWidget:
    return next(n for n in app.query(NodeWidget) if n.kind == kind)


def test_d_key_delete_amp_and_cab(monkeypatch, tmp_path):
    """d 键删除聚焦的 AMP/CAB：链值置 null + 节点回到缺位空态。"""
    state = setup_app(monkeypatch, tmp_path, ir=True)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            amp, cab = node(app, "amp"), node(app, "cab")

            # 删 AMP
            await pilot.click(amp)
            await pilot.pause(0.2)
            await pilot.press("d")
            await pilot.pause(0.3)
            assert state["chain"]["model"] is None, "链文件 AMP 必须卸载"
            assert amp.label == "NONE" and amp.bypassed is False
            assert amp.has_class("chain-node-empty")

            # 删 CAB
            await pilot.click(cab)
            await pilot.pause(0.2)
            await pilot.press("d")
            await pilot.pause(0.3)
            assert state["chain"]["ir"] is None, "链文件 CAB 必须卸载"
            assert cab.label == "NONE" and cab.bypassed is False
            assert cab.has_class("chain-node-empty")

    run(scenario())


def test_delete_on_bypassed_node_resets_it(monkeypatch, tmp_path):
    """BYPASS 态（链值已 null）下 d 键必须生效：清残留内容与备份（REQ-016
    bug②——此前被 already-empty 守卫拒绝）。"""
    state = setup_app(monkeypatch, tmp_path, ir=True)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            cab = node(app, "cab")
            # 双击 bypass：备份 + 链 ir=null
            app._ir_backup = state["chain"]["ir"]
            state["chain"]["ir"] = None
            app.query_one(ChainPanel).chain = dict(state["chain"])
            await pilot.pause(0.2)
            assert cab.bypassed is True and cab.label == "GB.wav"

            await pilot.click(cab)
            await pilot.pause(0.2)
            await pilot.press("d")
            await pilot.pause(0.3)

            assert app._ir_backup is None, "delete 必须清 BYPASS 备份"
            assert cab.label == "NONE", f"BYPASS 节点必须被清空: {cab.label}"
            assert cab.title is None
            assert cab.bypassed is False
            assert cab.has_class("chain-node-empty")

    run(scenario())


def test_hint_delete_clears_bypassed_node(monkeypatch, tmp_path):
    """提示行 d delete 同样清掉 BYPASS 残留（走同一 handler 的兜底路径）。"""
    state = setup_app(monkeypatch, tmp_path, ir=True)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            cab = node(app, "cab")
            app._ir_backup = state["chain"]["ir"]
            state["chain"]["ir"] = None
            app.query_one(ChainPanel).chain = dict(state["chain"])
            await pilot.pause(0.2)
            assert cab.bypassed is True

            # 记录 _last_focus_node 后移走焦点，走提示行兜底
            await pilot.click(cab)
            await pilot.pause(0.2)

            from rich.cells import cell_len
            from tui.modals import border_hint_segments, hint_span
            panel = app.query_one(ChainPanel)
            segments = border_hint_segments(panel)
            label = " · ".join(segments)
            span = hint_span(label, segments[0])
            label_width = cell_len(label)
            label_start = panel.region.x + max(
                1, panel.region.width - label_width - 2)
            offset = (label_start + span[0] + 1 - panel.region.x,
                      panel.region.bottom - 1 - panel.region.y)
            await pilot.click(panel, offset=offset)
            await pilot.pause(0.3)

            assert app._ir_backup is None
            assert cab.label == "NONE"
            assert cab.bypassed is False

    run(scenario())


def test_delete_fully_empty_slot_still_notifies(monkeypatch, tmp_path):
    """节点本就空态时 delete 提示 already empty，不误动作。"""
    state = setup_app(monkeypatch, tmp_path, ir=True)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            amp, cab = node(app, "amp"), node(app, "cab")
            # 两个槽都已空
            state["chain"]["model"] = None
            state["chain"]["ir"] = None
            app.query_one(ChainPanel).chain = dict(state["chain"])
            await pilot.pause(0.2)
            assert amp.label == "NONE" and cab.label == "NONE"

            await pilot.click(amp)
            await pilot.pause(0.2)
            await pilot.press("d")
            await pilot.pause(0.2)
            # 无异常、链不变、仍空态
            assert state["chain"]["model"] is None
            assert amp.label == "NONE" and not amp.bypassed

    run(scenario())
