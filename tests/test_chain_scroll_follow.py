"""REQ-002: 选择与聚焦统一（tone chain ↑/↓ 换 model 后，pack 表光标滚动跟随）。

用户反馈：tonechain 下选中 AMP/CAB 后 ↑/↓ 换模型，选中标志会滚出视界；
且"选择三角"与"聚焦条"是两个独立标志，会互相冲突。语义（用户确认）：

- 选择 = 聚焦：链上换 model 时，detail pack 表光标（聚焦条）同步移到被选
  model 行——DataTable 光标移动自带 scroll-into-view，视口以聚焦行为锚。
- ▶ 三角仅作信息标记（链上当前激活槽位），不再作为视口铆定。
- 用户自己在 pack 表内 ↑/↓ 浏览时（链未变），光标/视口不被 tick 拉回。

这些用例断言三条路径（节点键盘 ↑/↓、NodeSwitchButton 点击、tick 同步）
之后光标行始终在可视区内，且光标行与 ▶ 行一致（单一选择/聚焦标志）。
"""
import asyncio
from pathlib import Path

from tui.app import GigBuddyApp
from tui.panels import ChainPanel, DetailPane, NodeWidget

import library  # noqa: F401  conftest 把 src/ 注入 sys.path


def run(coro):
    return asyncio.run(coro)


def _tone_pack(tmp_path, count=30):
    """A tone folder with `count` amp models (all same tone, paths under tmp)."""
    models = [
        {"id": i, "tone_id": 10, "name": f"Model {i:02d}",
         "architecture": "SlimmableContainer",
         "local_path": str(tmp_path / f"Model {i:02d}.nam")}
        for i in range(1, count + 1)
    ]
    for m in models:
        Path(m["local_path"]).write_bytes(b"x")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": models}
    return tone, models


def _patch_chain(monkeypatch, tone, models, chain):
    """Route library lookups + live chain through in-memory state."""
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: models)
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = dict(chain)   # 初始即当前链；write_chain 覆盖后续变更
    monkeypatch.setattr("tui.app.live.read_chain", lambda: dict(written))
    monkeypatch.setattr("tui.app.live.write_chain",
                        lambda cfg: written.update(cfg))
    return written


def _row_y(tbl, idx) -> int:
    """Row top offset in content coordinates: header + heights above."""
    return tbl.header_height + sum(
        tbl.get_row_height(r.key) for r in tbl.ordered_rows[:idx])


def _assert_cursor_visible(tbl, *, scrolled=False):
    """聚焦（光标）行必须整体落在表格可视区内。"""
    idx = tbl.cursor_row
    y = _row_y(tbl, idx)
    view_top = tbl.scroll_y
    view_h = tbl.content_region.height
    assert view_top <= y < view_top + view_h, (
        f"cursor row {idx} (y={y}) outside viewport [{view_top}, "
        f"{view_top + view_h}) — scroll_y={tbl.scroll_y}")
    if scrolled:
        assert tbl.scroll_y > 0, "table never scrolled"


def _assert_cursor_on_active(tbl, pane, path, *, scrolled=False):
    """光标行 == 链上激活槽位所在行，且 ▶ 落在同一行（选择=聚焦，单一标志）。"""
    idx = next(i for i, m in enumerate(pane._pack_rows.values())
               if m.get("local_path") == path)
    assert tbl.cursor_row == idx, (
        f"cursor at {tbl.cursor_row}, active slot row is {idx}")
    key = tbl.ordered_rows[tbl.cursor_row].key.value
    assert "▶" in tbl.get_cell(key, "sel"), "▶ 标记不在光标行上"
    _assert_cursor_visible(tbl, scrolled=scrolled)


async def _click_amp_node(app, pilot):
    panel = app.query_one(ChainPanel)
    node = next(n for n in panel.query(NodeWidget) if n.kind == "amp")
    await pilot.click(node)   # 打开 pack 视图，焦点留在节点上
    await pilot.pause()
    return node


def test_node_arrow_switch_syncs_pack_cursor(monkeypatch, tmp_path):
    """AMP 节点聚焦按 ↓ 换模型：pack 光标（聚焦条）同步到新 model 行并保持
    可见——选择=聚焦；节点本身保持聚焦。"""
    tone, models = _tone_pack(tmp_path)
    written = _patch_chain(monkeypatch, tone, models,
                           {"model": models[0]["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            node = await _click_amp_node(app, pilot)
            pane = app.query_one(DetailPane)
            tbl = pane._pack_table
            for _ in range(15):
                await pilot.press("down")
                await pilot.pause()
            assert written["model"] == models[15]["local_path"]
            _assert_cursor_on_active(tbl, pane, written["model"], scrolled=True)
            # 链上选择后聚焦仍在节点上（pack 光标只是视口锚，不抢焦点）
            assert app.focused is node

    run(scenario())


def test_node_switch_wrap_keeps_cursor_visible(monkeypatch, tmp_path):
    """标记在 pack 底部时再按 ↓ 回绕到第一行：光标跟着回到顶部并可见。"""
    tone, models = _tone_pack(tmp_path)
    written = _patch_chain(monkeypatch, tone, models,
                           {"model": models[-1]["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await _click_amp_node(app, pilot)
            pane = app.query_one(DetailPane)
            tbl = pane._pack_table
            # 打开时已滚到底部（首行就看不见了）
            _assert_cursor_on_active(tbl, pane, written["model"], scrolled=True)
            await pilot.press("down")   # 回绕到第一个 model
            await pilot.pause()
            assert written["model"] == models[0]["local_path"]
            _assert_cursor_on_active(tbl, pane, written["model"])
            assert tbl.scroll_y <= _row_y(tbl, 0), "未滚回顶部"

    run(scenario())


def test_pack_opens_with_cursor_on_active_slot(monkeypatch, tmp_path):
    """链槽深处时打开 pack：光标初始就在激活行，视口随之滚到深处。"""
    tone, models = _tone_pack(tmp_path)
    written = _patch_chain(monkeypatch, tone, models,
                           {"model": models[25]["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await _click_amp_node(app, pilot)
            pane = app.query_one(DetailPane)
            _assert_cursor_on_active(pane._pack_table, pane,
                                     written["model"], scrolled=True)

    run(scenario())


def test_switch_button_click_syncs_pack_cursor(monkeypatch, tmp_path):
    """AMP 行 ▼ 按钮（NodeSwitchButton 点击路径）换 model 后同样同步光标。"""
    tone, models = _tone_pack(tmp_path)
    written = _patch_chain(monkeypatch, tone, models,
                           {"model": models[0]["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(ChainPanel)
            await _click_amp_node(app, pilot)
            for _ in range(15):
                await pilot.click(panel.query_one("#chain-amp-down"))
                await pilot.pause()
            assert written["model"] == models[15]["local_path"]
            pane = app.query_one(DetailPane)
            _assert_cursor_on_active(pane._pack_table, pane,
                                     written["model"], scrolled=True)

    run(scenario())


def test_browsing_pack_keeps_focus_row_visible(monkeypatch, tmp_path):
    """pack 表内自身 ↑/↓ 浏览（链未变）：视口以聚焦行为锚，行始终可见，
    且不触发链切换（浏览 ≠ 选择）。"""
    tone, models = _tone_pack(tmp_path)
    written = _patch_chain(monkeypatch, tone, models,
                           {"model": models[0]["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, tone["models"],
                           dict(written), "amp", focus_table=True)
            await pilot.pause()
            tbl = pane._pack_table
            for _ in range(15):
                await pilot.press("down")
                await pilot.pause()
            assert tbl.cursor_row == 15
            _assert_cursor_visible(tbl, scrolled=True)
            assert written == {"model": models[0]["local_path"],
                               "gain": 0.8}, "浏览不应改写链"

    run(scenario())


def test_tick_does_not_yank_cursor_during_browsing(monkeypatch, tmp_path):
    """0.3s tick（refresh_pack_active）在链未变时不移动光标——浏览中的
    光标位置就是用户自己的选择意向。"""
    tone, models = _tone_pack(tmp_path)
    written = _patch_chain(monkeypatch, tone, models,
                           {"model": models[0]["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, tone["models"],
                           dict(written), "amp", focus_table=True)
            await pilot.pause()
            tbl = pane._pack_table
            for _ in range(10):
                await pilot.press("down")
                await pilot.pause()
            assert tbl.cursor_row == 10
            # tick 同步：链没变，光标必须留在用户浏览的位置
            pane.refresh_pack_active(dict(written))
            await pilot.pause()
            assert tbl.cursor_row == 10, "tick 把浏览中的光标拉回了"

    run(scenario())


def test_tick_syncs_cursor_after_external_chain_change(monkeypatch, tmp_path):
    """外部链变更 + tick 同步路径：激活槽位变化时，光标移到新激活行并可见。"""
    tone, models = _tone_pack(tmp_path)
    written = _patch_chain(monkeypatch, tone, models,
                           {"model": models[0]["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await _click_amp_node(app, pilot)
            pane = app.query_one(DetailPane)
            tbl = pane._pack_table
            # 外部把链槽换到 pack 深处（预设加载/其他会话编辑），tick 同步
            written["model"] = models[25]["local_path"]
            pane.refresh_pack_active(dict(written))
            await pilot.pause()
            _assert_cursor_on_active(tbl, pane, written["model"], scrolled=True)

    run(scenario())
