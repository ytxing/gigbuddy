"""REQ-003: 链节点状态灯改色/BYPASS 字样 · 双击不改 detail 选中态 ·
未选中无残留背景行 · INPUT 高亮不盖 PLAY 块。

用户原话：双击的 BYPASS 字样没了、半满圆不好看（work=绿 / BYPASS=红 /
空=灰）；tonedetail 会跟着双击错误变"未选中"（须永远跟点选一致）；
detail 未选中时残留一行背景色（去掉）；INPUT 与 AMP/CAB 选中位置不一致
（以 AMP 为准，PLAY 位置不被浅色 hover 覆盖）。
"""
import asyncio
from pathlib import Path

from tui.app import GigBuddyApp
from tui.panels import ChainPanel, DetailPane, MarqueeBar, NodeWidget
import tui.live as live

import library  # noqa: F401  conftest 把 src/ 注入 sys.path


def run(coro):
    return asyncio.run(coro)


def _amp_tone(tmp_path, extra_models=()):
    """AMP tone with one local model (+ optional extra rows)."""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1",
           "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    models = [amp, *extra_models]
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": models}
    return amp, tone, models


def _patch(monkeypatch, tone, models, chain):
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: models)
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = dict(chain)
    monkeypatch.setattr("tui.app.live.read_chain", lambda: dict(written))
    monkeypatch.setattr("tui.app.live.write_chain",
                        lambda cfg: written.update(cfg))
    return written


def test_state_lamp_work_green_bypass_red_empty_gray(monkeypatch, tmp_path):
    """状态灯：work=绿 ● / BYPASS=红 ● + BYPASS 字样 / 空=灰 ○。"""
    amp, tone, models = _amp_tone(tmp_path)
    written = _patch(monkeypatch, tone, models,
                     {"model": amp["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            # REQ-043：type + 状态灯在边框标题（AMP ●），框内只有内容
            await pilot.click(amp_node)
            await pilot.pause()
            assert "AMP" in amp_node.parent.border_title
            assert "$success" in amp_node.parent.border_title
            assert "[bold $success]●[/]" not in amp_node.render()
            assert "BYPASS" not in amp_node.render()
            # 空：d 键卸载 → 边框灯灰色 ○
            await pilot.press("d")
            await pilot.pause()
            assert amp_node.label == "NONE"
            assert "AMP" in amp_node.parent.border_title
            assert "$state-idle" in amp_node.parent.border_title
            assert "BYPASS" not in amp_node.render()
            # 重新加载 → 绿；双击 → 红 ●（边框）+ BYPASS 字样（框内）
            written["model"] = amp["local_path"]
            app.query_one(ChainPanel).chain = dict(written)
            await pilot.pause()
            assert "AMP" in amp_node.parent.border_title
            assert "$success" in amp_node.parent.border_title
            await pilot.double_click(amp_node)
            await pilot.pause()
            assert amp_node.bypassed is True
            assert "AMP" in amp_node.parent.border_title
            assert "$error" in amp_node.parent.border_title
            assert "BYPASS" in amp_node.render()
            assert amp_node.label == "MV5 G1.nam", "bypass 应保留内容显示"

    run(scenario())


def test_double_click_bypass_keeps_detail_selected(monkeypatch, tmp_path):
    """双击 bypass/恢复 都不把 detail 变成"未选中"——detail 永远跟点选一致。"""
    amp, tone, models = _amp_tone(tmp_path)
    written = _patch(monkeypatch, tone, models,
                     {"model": amp["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            await pilot.click(amp_node)   # 打开 pack（选中态）
            await pilot.pause()
            pane = app.query_one(DetailPane)
            assert pane._pack_mode
            assert not pane._marquee.has_class("detail-marquee--empty")
            assert not pane._summary.has_class("detail-summary--empty")
            # 双击 → bypass：pack 视图保持，不出现"未选中"空态
            await pilot.double_click(amp_node)
            await pilot.pause()
            assert written["model"] is None
            assert pane._pack_mode, "bypass 后 pack 视图被清掉了"
            assert not pane._marquee.has_class("detail-marquee--empty")
            assert not pane._summary.has_class("detail-summary--empty")
            # 再双击 → 恢复：pack 仍在，▶ 回到恢复的槽位行
            await pilot.double_click(amp_node)
            await pilot.pause()
            assert written["model"] == amp["local_path"]
            assert pane._pack_mode
            assert "▶" in pane._pack_table.get_cell("m1", "sel")
            assert pane._pack_table.cursor_row == 0

    run(scenario())


def test_cab_arrow_buttons_switch_cab_model(monkeypatch, tmp_path):
    """REQ-004: CAB 行上下箭头按钮（与 AMP 一致）点击切换同 pack 的 cab
    model；pack 光标（REQ-002 选择=聚焦）同步到新激活行。"""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1",
           "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    irs = [
        {"id": 2 + i, "tone_id": 11, "name": f"GB {i}", "architecture": "IR",
         "local_path": str(tmp_path / f"GB {i}.wav")}
        for i in range(3)
    ]
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    for m in irs:
        Path(m["local_path"]).write_bytes(b"b")
    tone = {"id": 11, "title": "CAB Pack", "gear": "cab", "username": "arthm",
            "downloads_count": 1, "models": irs}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: irs)
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    chain = {"model": amp["local_path"], "ir": irs[1]["local_path"],
             "gain": 0.8}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": chain["model"],
                                 "ir": written.get("ir", chain["ir"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain",
                        lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(ChainPanel)
            # 箭头与 AMP 一致：上按钮 ↑ / 下按钮 ↓
            assert panel.query_one("#chain-cab-up").render().plain == "↑"
            assert panel.query_one("#chain-cab-down").render().plain == "↓"
            # 打开 CAB pack
            cab = next(n for n in app.query(NodeWidget) if n.kind == "cab")
            await pilot.click(cab)
            await pilot.pause()
            pane = app.query_one(DetailPane)
            assert pane._pack_mode
            # ▼ next：ir1 → ir2
            await pilot.click(panel.query_one("#chain-cab-down"))
            await pilot.pause()
            assert written["ir"] == irs[2]["local_path"]
            # ▲ prev ×2：ir2 → ir1 → ir0（回绕方向正确）
            await pilot.click(panel.query_one("#chain-cab-up"))
            await pilot.pause()
            assert written["ir"] == irs[1]["local_path"]
            await pilot.click(panel.query_one("#chain-cab-up"))
            await pilot.pause()
            assert written["ir"] == irs[0]["local_path"]
            # 光标同步到激活行（选择=聚焦）
            idx = next(i for i, m in enumerate(pane._pack_rows.values())
                       if m.get("local_path") == written["ir"])
            assert pane._pack_table.cursor_row == idx
            key = pane._pack_table.ordered_rows[idx].key.value
            assert "▶" in pane._pack_table.get_cell(key, "sel")

    run(scenario())


def test_detail_unselected_rows_have_no_background(monkeypatch, tmp_path):
    """未选中态：marquee 与摘要行都不带空态背景类（CSS 透明化）。"""
    amp, tone, models = _amp_tone(tmp_path)
    written = _patch(monkeypatch, tone, models,
                     {"model": amp["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            # 未选中：clear() 与 show_text() 都要去掉摘要行背景
            pane.clear()
            assert pane._marquee.has_class("detail-marquee--empty")
            assert pane._summary.has_class("detail-summary--empty")
            pane.show_text("some text")
            assert pane._summary.has_class("detail-summary--empty")
            # 选中（model 视图）：摘要行恢复背景类
            pane.show_model(tone, amp)
            assert not pane._summary.has_class("detail-summary--empty")
            assert not pane._marquee.has_class("detail-marquee--empty")

    run(scenario())


def test_input_play_block_not_covered_by_light_hover(monkeypatch, tmp_path):
    """INPUT 行选中高亮只盖左侧文本区（以 AMP 行为准），PLAY 块独立 hover。"""
    amp, tone, models = _amp_tone(tmp_path)
    chain = {"model": amp["local_path"], "gain": 0.8,
             "input": {"source": "file", "file": "data/dry_inputs/A.wav",
                       "state": "paused", "loop": False}}
    written = _patch(monkeypatch, tone, models, chain)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            # 0.3s tick 会读真实 level.json（可能有残留播放态）——固定为停止态
            monkeypatch.setattr("tui.app.live.read_levels",
                                lambda: (0.0, 0.0, live.PLAY_STOPPED, 0.0))
            await pilot.pause(0.3)
            input_node = next(n for n in app.query(NodeWidget)
                              if n.kind == "input")
            assert input_node.is_file
            span = input_node._play_span()
            # 悬停左侧文本区：只亮左段，PLAY 块不在高亮范围内
            await pilot.hover(input_node, offset=(2, 0))
            await pilot.pause()
            assert input_node._node_hover is True
            assert input_node._play_hover is False
            render = input_node.render()
            assert "[on $panel-lighten-1]" in render
            assert render.find("[/]") < render.find("PLAY"), \
                "PLAY 块被浅色高亮盖住了"
            # 悬停 PLAY 块：PLAY 块自己亮（accent），左段不高亮
            await pilot.hover(input_node, offset=(span[0] + 2, 0))
            await pilot.pause()
            assert input_node._play_hover is True
            assert input_node._node_hover is False
            render = input_node.render()
            assert "$background on $accent" in render
            assert "[on $panel-lighten-1]" not in render

    run(scenario())


def test_node_row_border_titles_and_no_chain_marquee(monkeypatch, tmp_path):
    """REQ-043 修复：type+状态灯在行容器边框标题（node 自身无边框，
    标题必须设到父）；#chain-marquee 聚焦行已删除。"""
    amp, tone, models = _amp_tone(tmp_path)
    written = _patch(monkeypatch, tone, models,
                     {"model": amp["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(ChainPanel)
            # 三行边框标题：type + 灯（在行容器上）
            for kind, expect in (("input", "INPUT"), ("amp", "AMP"),
                                 ("cab", "CAB")):
                node = next(n for n in panel.query(NodeWidget)
                            if n.kind == kind)
                assert expect in node.parent.border_title
                assert "[bold " in node.parent.border_title
                assert not node.border_title, \
                    "node 自身无边框，标题不应设在 node 上"
            # 聚焦 marquee 行已删（链面板顶部直接是 INPUT 行）
            assert len(panel.query("#chain-marquee")) == 0
            # 状态灯随 bypass 变化（行边框标题）
            amp_node = next(n for n in panel.query(NodeWidget)
                            if n.kind == "amp")
            await pilot.double_click(amp_node)
            await pilot.pause()
            assert "AMP" in amp_node.parent.border_title
            assert "$error" in amp_node.parent.border_title
            await pilot.double_click(amp_node)
            await pilot.pause()
            assert "AMP" in amp_node.parent.border_title
            assert "$success" in amp_node.parent.border_title

    run(scenario())
