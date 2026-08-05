"""REQ-040 专项回归：全局提示等效 + 选择框 [ ] 统一。

① 右下角提示 token 与点击等效：每个提示词都是真实可点目标（点击 =
   触发对应快捷键动作），遍历主要页面断言 action 列表与提示词一一命中；
   关键 token（i install / u uninstall / enter detail）点击实测。
② 选择框统一样式：[ ]（未选）/ [x]（选中），宽 5 列对称 padding 居中，
   仅批量增删场景显示；鼠标点选（点击框列切换选中态）在 pack 表、
   pack install 屏、local 表、preset 表全部生效。
"""
import asyncio

from rich.cells import cell_len
from rich.style import Style
from textual.events import Click
from textual.widgets import DataTable

from tui.app import GigBuddyApp
from tui.install_screen import PackInstallScreen
from tui.modals import (border_hint_click, border_hint_label,
                        border_hint_segments, hint_span)
from tui.panels import DetailPane
from tui.presets import PresetPanel
from tui.uninstall_screen import LocalUninstallScreen

import library


def run(coro):
    return asyncio.run(coro)


# ---- 复用的 TONE3000 场景 mock（与 test_req038 同构）----

def _hits():
    return [{"id": 77, "title": "Remote Pack", "gear": "amp",
             "downloads_count": 2, "username": "tester",
             "a1_models_count": 1, "a2_models_count": 2, "irs_count": 0,
             "description": "Remote tone description."}]


def _remote_models():
    return [
        {"id": 1, "name": "one.nam", "architecture": "SlimmableContainer"},
        {"id": 2, "name": "two.nam", "architecture": "SlimmableContainer"},
    ]


def _monkey_remote(monkeypatch):
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda query, page_size, **kwargs: [dict(h) for h in _hits()])
    monkeypatch.setattr("tui.panels.tone3000.models",
                        lambda tid, a2_only=False: _remote_models())
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)


async def _goto_remote_selection(app, pilot):
    await pilot.click(app.query_one("#--content-tab-pane-tone"))
    await pilot.pause(0.6)
    pane = app.query_one(DetailPane)
    pane.focus()
    await pilot.press("right")
    await pilot.pause(0.6)
    return pane


def _click_border_token(widget, box, label, token, pilot_or_app=None):
    """在 widget 边框副标题上点击 token（复用 test_hint_audit 的坐标法）。"""
    span = hint_span(label, token)
    assert span is not None, f"token {token!r} 不在提示词 {label!r} 中"
    label_start = box.region.x + max(1, box.region.width - cell_len(label) - 2)
    x = label_start + (span[0] + span[1]) // 2
    y = box.region.bottom - 1
    return Click(box, x, y, 0, 0, 1, False, False, False, x, y, Style(), 1)


# ---- ① 提示等效 ----

# 已知快捷键词首（用于识别"键+动作"提示段；状态段如计数/加载态跳过）
_KNOWN_KEYS = {"d", "space", "s", "l", "u", "i", "a", "r", "enter", "esc",
               "e", "n", "ctrl+s", "ctrl+shift+s", "change", "⌥"}


def _segment_key(segment: str) -> str:
    return segment.split()[0].casefold() if segment.split() else ""


def _assert_no_orphan_hints(selector, widget, box=None) -> None:
    """提示词与点击等效（REQ-040）：副标题里每个"键+动作"段，都必须存在
    同键且能在文案中命中的可点 token——否则该提示词是死 token（点击无效，
    如旧的 "space" vs "space select" 宽度脱节）。"""
    box = box or widget
    label = border_hint_label(box)
    actions = widget._border_hint_actions()
    for segment in border_hint_segments(box):
        key = _segment_key(segment)
        if key not in _KNOWN_KEYS:
            continue  # 状态段（计数/加载态）或方向符
        if not any(_segment_key(token) == key and hint_span(label, token) is not None
                   for token, _action in actions):
            raise AssertionError(
                f"{selector}: 提示段 {segment!r} 无同键可点 token（文案 "
                f"{label!r}，动作 {[t for t, _ in actions]!r}）")


def test_no_orphan_hint_tokens_any_page(monkeypatch, tmp_path):
    """遍历所有主要页面/模态：副标题每个快捷键提示词都有可点的同键 token
    （点击 = 触发快捷键动作，REQ-040；含 resize 后宽度自适应不脱节）。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            for selector in ("LibraryPanel", "PresetPanel", "ChainPanel", "DetailPane"):
                widget = app.query_one(selector)
                _assert_no_orphan_hints(selector, widget)
            # DetailPane selection 视图（i install / u uninstall 常驻）
            pane = app.query_one(DetailPane)
            pane.focus()
            await pilot.press("right")
            await pilot.pause()
            _assert_no_orphan_hints("DetailPane(selection)", pane)
            # 模态：install 屏 / uninstall 屏 / input source / audio settings
            for screen_cls, args in (
                    (PackInstallScreen, ({"id": 1, "title": "T", "gear": "amp",
                                          "username": "u", "downloads_count": 1},)),
                    (LocalUninstallScreen, ([10],)),
            ):
                app.push_screen(screen_cls(*args))
                await pilot.pause(0.4)
                _assert_no_orphan_hints(screen_cls.__name__, app.screen)
                await pilot.press("escape")
                await pilot.pause(0.2)
            app.action_open_input_source()
            await pilot.pause(0.4)
            _assert_no_orphan_hints("InputSourceScreen", app.screen)
            await pilot.press("escape")
            await pilot.pause(0.2)
            app.action_open_audio_settings()
            await pilot.pause(0.4)
            _assert_no_orphan_hints("AudioSettingsScreen", app.screen)
            await pilot.press("escape")
            await pilot.pause(0.2)

    run(scenario())


def test_detail_hint_i_install_token_click_installs(monkeypatch):
    """tone details selection 视图：点击右下角 'i install' token = 按 i 键
    （安装光标行/选中行）。"""
    calls = {}

    def fake_import(tone_id, progress, **_kw):
        calls.setdefault("import", (tone_id, _kw.get("model_ids")))
        return {"id": tone_id, "models": []}

    monkeypatch.setattr("tui.panels.library.import_tone", fake_import)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = await _goto_remote_selection(app, pilot)
            label = border_hint_label(pane)
            click = _click_border_token(pane, pane, label, "i install")
            assert border_hint_click(pane, click, pane._border_hint_actions())
            await pilot.pause(0.5)
            assert calls.get("import") == (77, [1]), "点击 i install 应安装光标行"

    _monkey_remote(monkeypatch)
    run(scenario())


def test_detail_hint_u_uninstall_token_click_uninstalls(monkeypatch, tmp_path):
    """tone details selection 视图：点击 'u uninstall' token = 按 u 键。"""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1",
           "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp]}
    calls = {}
    monkeypatch.setattr("tui.panels.library.local_uninstall_models_plan",
                        lambda ids: {"tone_ids": [10], "models": [],
                                     "bytes": 0, "active_paths": [],
                                     "preset_names": [], "outside_paths": []})

    def fake_uninstall(ids, allow_preset_references=False):
        calls.setdefault("uninstall", (ids, allow_preset_references))
        return {"removed": len(ids), "trash_dir": None}

    monkeypatch.setattr("tui.panels.library.local_uninstall_models",
                        fake_uninstall)
    monkeypatch.setattr("tui.panels.library.get_tone", lambda tone_id: {
        **tone, "models": []})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, tone["models"],
                           {"model": amp["local_path"], "gain": 0.8},
                           "amp", focus_table=True)
            await pilot.pause()
            label = border_hint_label(pane)
            click = _click_border_token(pane, pane, label, "u uninstall")
            assert border_hint_click(pane, click, pane._border_hint_actions())
            await pilot.pause(0.5)
            assert calls.get("uninstall") == ([1], False), "点击 u uninstall 应卸载"

    run(scenario())


def test_install_screen_i_u_token_clicks(monkeypatch):
    """pack install 二级菜单：点击 'i install' = 安装选中；点击 'u uninstall'
    = 卸载选中的已下载模型（与快捷键等效）。"""
    monkeypatch.setattr(
        "tui.install_screen.tone3000.models",
        lambda tid, a2_only=True: [
            {"id": 1, "name": "one.nam", "architecture": "SlimmableContainer"},
            {"id": 2, "name": "two.nam", "architecture": "SlimmableContainer"},
        ])
    monkeypatch.setattr(
        "tui.install_screen.library.downloaded_model_ids_by_tone",
        lambda: {77: {1}})
    calls = {}

    def fake_import(tone_id, progress, **_kw):
        calls.setdefault("import", (tone_id, _kw.get("model_ids")))
        return {"id": tone_id, "models": []}

    monkeypatch.setattr("tui.install_screen.library.import_tone", fake_import)
    monkeypatch.setattr("tui.install_screen.library.local_uninstall_models_plan",
                        lambda ids: {"tone_ids": [77], "models": [],
                                     "bytes": 0, "active_paths": [],
                                     "preset_names": [], "outside_paths": []})

    def fake_uninstall(ids, allow_preset_references=False):
        calls.setdefault("uninstall", (ids, allow_preset_references))
        return {"removed": len(ids), "trash_dir": None}

    monkeypatch.setattr("tui.install_screen.library.local_uninstall_models",
                        fake_uninstall)

    tone = {"id": 77, "title": "Pack", "gear": "amp", "username": "tester",
            "downloads_count": 2}

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(PackInstallScreen(tone))
            await pilot.pause(0.5)
            screen = app.screen
            box = screen.query_one("ModalBox")
            label = border_hint_label(box)
            # 点击 u uninstall：默认勾选的是未下载的 model 2 → 无已下载可选
            click = _click_border_token(box, box, label, "u uninstall")
            assert border_hint_click(box, click, screen._border_hint_actions())
            await pilot.pause(0.3)
            assert "uninstall" not in calls
            # space 勾选已下载的 model 1（光标行）后再点 u
            await pilot.press("space")
            label = border_hint_label(box)
            click = _click_border_token(box, box, label, "u uninstall")
            assert border_hint_click(box, click, screen._border_hint_actions())
            await pilot.pause(0.5)
            assert calls.get("uninstall") == ([1], False)
            # 点击 i install → 安装当前选中（model 2）
            label = border_hint_label(box)
            click = _click_border_token(box, box, label, "i install")
            assert border_hint_click(box, click, screen._border_hint_actions())
            await pilot.pause(0.5)
            assert calls.get("import") == (77, [2])
            await pilot.press("escape")
            await pilot.pause(0.2)

    run(scenario())


def test_library_hint_enter_detail_token_click(monkeypatch):
    """library TONE3000 列表：点击 'enter detail' token = 打开二级菜单详情页。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click(app.query_one("#--content-tab-pane-tone"))
            await pilot.pause(1.0)  # 等 tab 切换 + 缓存恢复 + 副标题刷新
            panel = app.query_one("LibraryPanel")
            assert panel._active_pane == "pane-tone"
            label = border_hint_label(panel)
            click = _click_border_token(panel, panel, label, "enter detail")
            assert border_hint_click(panel, click, panel._border_hint_actions())
            await pilot.pause(0.5)
            assert isinstance(app.screen, PackInstallScreen)
            assert app.screen._tone.get("id") == 77

    _monkey_remote(monkeypatch)
    run(scenario())


# ---- ② 选择框 [ ] 统一 + 鼠标点选 ----

def test_pack_table_box_style_centered_and_mouse_toggle(monkeypatch):
    """tone details pack 表：Pick 列 [ ]/[x] 样式、宽 5 列对称居中、
    鼠标点选切换。"""
    _monkey_remote(monkeypatch)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = await _goto_remote_selection(app, pilot)
            table = pane._pack_table
            # 样式：未选 [ ]（转义存储，渲染不吞）
            assert str(table.get_cell("m1", "pick")) == "\\[ ]"
            assert table.columns["pick"].width == 5
            # 渲染居中：宽 5 = 2×cell_padding + [ ] 3 字符，框在列正中
            line = table._render_cell(0, 0, Style(), 5)[0]
            assert "".join(s.text for s in line) == " [ ] "
            # 鼠标点选：单击 Pick 列 → 勾选
            await pilot.click(table, offset=(3, 1))
            await pilot.pause(0.25)
            assert pane._pack_picked == {"m1"}
            assert str(table.get_cell("m1", "pick")) == "\\[x]"
            # 再点 → 取消
            await pilot.click(table, offset=(3, 1))
            await pilot.pause(0.25)
            assert pane._pack_picked == set()
            # 单击内容列只移光标，不勾选
            await pilot.click(table, offset=(10, 1))
            await pilot.pause(0.25)
            assert pane._pack_picked == set()
            assert table.cursor_row == 0

    run(scenario())


def test_install_screen_box_mouse_toggle(monkeypatch):
    """pack install 二级菜单：Pick 列 [ ]/[x] + 鼠标点选。"""
    monkeypatch.setattr(
        "tui.install_screen.tone3000.models",
        lambda tid, a2_only=True: [
            {"id": 1, "name": "one.nam", "architecture": "SlimmableContainer"},
            {"id": 2, "name": "two.nam", "architecture": "SlimmableContainer"},
        ])
    monkeypatch.setattr(
        "tui.install_screen.library.downloaded_model_ids_by_tone",
        lambda: {})

    tone = {"id": 77, "title": "Pack", "gear": "amp", "username": "tester",
            "downloads_count": 2}

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(PackInstallScreen(tone))
            await pilot.pause(0.5)
            screen = app.screen
            table = screen.query_one("#pack-table", DataTable)
            assert str(table.get_cell("1", "pick")) == "\\[x]"  # 默认全勾（未下载）
            assert str(table.get_cell("2", "pick")) == "\\[x]"
            assert table.columns["pick"].width == 5
            # 单击第一行 Pick 列 → 取消勾选
            await pilot.click(table, offset=(3, 1))
            await pilot.pause(0.25)
            assert screen._selected == {2}
            assert str(table.get_cell("1", "pick")) == "\\[ ]"
            # 再点 → 重新勾选
            await pilot.click(table, offset=(3, 1))
            await pilot.pause(0.25)
            assert screen._selected == {1, 2}
            await pilot.press("escape")
            await pilot.pause(0.2)

    run(scenario())


def test_local_table_box_style_and_mouse_toggle(monkeypatch, tmp_path):
    """library LOCAL 表：Sel 列 [ ]/[x] + 鼠标点选（批量卸载场景）。"""
    tone = {"id": 10, "title": "Plexi", "gear": "amp", "username": "alice",
            "downloads_count": 1}
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    with library.connect() as conn:
        library.upsert_tone(conn, tone)
    monkeypatch.setattr("tui.library_panel.library.list_tones",
                        lambda **kw: [dict(tone)])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            table = app.query_one("#lib-table-local")
            assert str(table.get_cell("local:10", "pick")) == "\\[ ]"
            assert table.columns["pick"].width == 5
            # 单击 Sel 列 → 勾选
            await pilot.click(table, offset=(3, 1))
            await pilot.pause(0.25)
            panel = app.query_one("LibraryPanel")
            assert panel._local_selected == {10}
            assert str(table.get_cell("local:10", "pick")) == "\\[x]"
            # 再点 → 取消
            await pilot.click(table, offset=(3, 1))
            await pilot.pause(0.25)
            assert panel._local_selected == set()

    run(scenario())


def test_preset_table_box_style(monkeypatch, tmp_path):
    """presets 面板：Sel 列 [ ]/[x]（批量删除场景），样式统一。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.presets.library.preset_list", lambda: [{
        "name": "p1", "chain": {"model_id": 1}, "note": "", "updated_at": ""}])
    monkeypatch.setattr("tui.presets.library.preset_get",
                        lambda name: {"name": name, "chain": {"model_id": 1}})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            table = app.query_one("#preset-table", DataTable)
            assert table.columns["pick"].width == 5
            assert str(table.get_cell("p1", "pick")) == "\\[ ]"
            # 单击 Sel 列 → 勾选（PresetTable 既有鼠标点选）
            await pilot.click(table, offset=(3, 1))
            await pilot.pause(0.25)
            panel = app.query_one(PresetPanel)
            assert "p1" in panel._selected
            assert str(table.get_cell("p1", "pick")) == "\\[x]"

    run(scenario())
