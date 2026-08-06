"""REQ-038 专项回归：pack 交互改造。

① 未下载标识：tone details pack 表未下载模型行 = 斜体 (not downloaded)
   （无连字符）；② Enter 路由：library TONE3000 行 / detail selection 行
   Enter/双击 → 二级菜单详情页（PackInstallScreen 必须收到 tone dict——
   修复 model id 被当作 tone id 的 bug）；③ 多选下载/卸载：tone details
   pack 表与 pack install 屏都支持 space 多选 + i 安装 + u 卸载（u 语义
   与 uninstall_screen 一致：活动链/库外拦截、preset 引用二次确认）；
   ④ 提示条：selection 视图右下角常驻 i install · u uninstall，多选计数
   靠左（REQ-024/025）。
"""
import asyncio

from textual.widgets import DataTable

from tui.app import GigBuddyApp
from tui.install_screen import PackInstallScreen
from tui.panels import ChainSlotWidget, DetailPane


def run(coro):
    return asyncio.run(coro)


def _hits(n=1):
    return [{"id": 77, "title": "Remote Pack", "gear": "amp",
             "downloads_count": 2, "username": "tester",
             "a1_models_count": 1, "a2_models_count": 2, "irs_count": 0,
             "description": "Remote tone description."} for _ in range(n)]


def _remote_models():
    return [
        {"id": 1, "name": "one.nam", "architecture": "SlimmableContainer"},
        {"id": 2, "name": "two.nam", "architecture": "SlimmableContainer"},
        {"id": 3, "name": "cab.wav", "architecture": "IR"},
    ]


def _monkey_remote(monkeypatch):
    """TONE3000 场景基础 patch：搜索 + 远程模型列表 + verified 探测。"""
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda query, page_size, **kwargs: [dict(h) for h in _hits()])
    monkeypatch.setattr("tui.panels.tone3000.models",
                        lambda tid, a2_only=False: _remote_models())
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)


async def _goto_remote_selection(app, pilot):
    """TONE3000 tab → 聚焦 Detail tabs → ``]`` 切到 Remote Pack。"""
    await pilot.click(app.query_one("#--content-tab-pane-tone"))
    await pilot.pause(0.6)
    pane = app.query_one(DetailPane)
    pane._view_tabs.focus()
    await pilot.press("]")
    await pilot.pause(0.6)
    assert pane._view_mode == "selection"
    assert pane._pack_remote
    return pane


def test_not_downloaded_row_is_italic_parens(monkeypatch):
    """REQ-038-①：未下载模型 = 斜体 (not downloaded)，无连字符。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await _goto_remote_selection(app, pilot)
            pane = app.query_one(DetailPane)
            cell = str(pane._pack_table.get_cell("m1", "file"))
            assert "(not downloaded)" in cell
            assert "[i]" in cell and "[/i]" in cell, "未下载标识必须斜体"
            assert "— not downloaded" not in cell, "不要连字符写法"
            # 已下载行没有斜体标记
            assert "download" not in str(pane._pack_table.get_cell("m3", "file")).split("]")[0]

    _monkey_remote(monkeypatch)
    run(scenario())


def test_detail_pack_enter_opens_install_with_tone_id(monkeypatch):
    """REQ-038-②：tone details selection 行 Enter → 二级菜单详情页，且
    PackInstallScreen 收到 tone dict（修复：旧实现把 model dict 传入，
    model 的 id 被当作 tone id 拉列表/导入）。"""
    _monkey_remote(monkeypatch)  # 先打基础 patch（含 tone3000.models 的返回值）
    import tui.install_screen as install_screen
    seen = {}

    def fake_models(tid, a2_only=True):
        seen["tid"] = tid
        return _remote_models()

    # panels 与 install_screen 共享同一 tone3000 模块：后打的 patch 生效
    monkeypatch.setattr(install_screen.tone3000, "models", fake_models)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await _goto_remote_selection(app, pilot)
            await pilot.press("enter")
            await pilot.pause(0.5)
            assert isinstance(app.screen, PackInstallScreen)
            tone = app.screen._tone
            assert tone.get("id") == 77, "二级菜单必须收到 tone id（非 model id）"
            assert tone.get("title") == "Remote Pack"
            # PackInstallScreen 用 tone id 拉模型列表
            assert seen["tid"] == 77

    run(scenario())


def test_detail_pack_double_click_opens_install_with_tone_id(monkeypatch):
    """REQ-038-②：detail selection 行双击 → 同样进二级菜单详情页。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = await _goto_remote_selection(app, pilot)
            # 双击行内容区（避开 Pick 列——该列单击是鼠标点选）
            await pilot.double_click(pane._pack_table, offset=(8, 2))
            await pilot.pause(0.5)
            assert isinstance(app.screen, PackInstallScreen)
            assert app.screen._tone.get("id") == 77

    _monkey_remote(monkeypatch)
    run(scenario())


def test_detail_pack_multi_select_i_installs_picked(monkeypatch):
    """REQ-038-③：detail selection 多选（space 勾选）+ i 安装选中模型。"""
    calls = {}

    def fake_import(tone_id, progress, **_kw):
        calls.setdefault("import", (tone_id, _kw.get("model_ids")))
        return {"id": tone_id, "models": []}

    monkeypatch.setattr("tui.panels.library.import_tone", fake_import)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = await _goto_remote_selection(app, pilot)
            table = pane._pack_table
            # space 勾选前两行（m1、m2），i 只装这两行
            await pilot.press("space")
            await pilot.press("down", "space")
            await pilot.pause()
            assert pane._pack_picked == {"m1", "m2"}
            assert "2 sel" in pane.border_subtitle, "多选计数靠左"
            assert "i install" in pane.border_subtitle
            await pilot.press("i")
            await pilot.pause(0.5)
            assert calls.get("import") == (77, [1, 2]), calls

    _monkey_remote(monkeypatch)
    run(scenario())


def test_detail_pack_i_falls_back_to_cursor_row(monkeypatch):
    """REQ-038-③：未多选时 i 安装光标单行（单行语义与多选共存）。"""
    calls = {}

    def fake_import(tone_id, progress, **_kw):
        calls.setdefault("import", (tone_id, _kw.get("model_ids")))
        return {"id": tone_id, "models": []}

    monkeypatch.setattr("tui.panels.library.import_tone", fake_import)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await _goto_remote_selection(app, pilot)
            await pilot.press("i")
            await pilot.pause(0.5)
            assert calls.get("import") == (77, [1]), "未多选 = 光标行"

    _monkey_remote(monkeypatch)
    run(scenario())


def test_install_keeps_remote_pack_and_marks_local_rows(monkeypatch, tmp_path):
    """成功安装后仍留在 Remote Pack，已下载行可直接加载。"""
    state = {"installed": False}
    local_path = str(tmp_path / "one.nam")
    local_tone = {
        "id": 77,
        "title": "Remote Pack",
        "gear": "amp",
        "username": "tester",
        "models": [{
            "id": 1,
            "tone_id": 77,
            "name": "one.nam",
            "architecture": "SlimmableContainer",
            "local_path": local_path,
        }],
    }

    monkeypatch.setattr(
        "tui.panels.library.get_tone",
        lambda tone_id: local_tone if state["installed"] else None)
    monkeypatch.setattr(
        "tui.install_screen.library.get_tone",
        lambda tone_id: local_tone if state["installed"] else None)
    monkeypatch.setattr(
        "tui.install_screen.library.downloaded_model_ids_by_tone",
        lambda: {77: {1}} if state["installed"] else {})

    def fake_import(tone_id, progress, **_kwargs):
        state["installed"] = True
        return local_tone

    monkeypatch.setattr("tui.install_screen.library.import_tone", fake_import)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = await _goto_remote_selection(app, pilot)
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert isinstance(app.screen, PackInstallScreen)
            await pilot.press("i")
            await pilot.pause(0.7)

            assert not isinstance(app.screen, PackInstallScreen)
            assert pane._pack_mode
            assert pane._view_mode == "selection"
            assert pane._pack_remote
            assert pane._view_tabs.display
            assert "(not downloaded)" not in str(
                pane._pack_table.get_cell("m1", "file"))
            assert local_path == pane._pack_rows["m1"]["local_path"]

    _monkey_remote(monkeypatch)
    run(scenario())


def test_detail_pack_u_uninstalls_picked(monkeypatch, tmp_path):
    """REQ-038-③：detail selection u 卸载选中（模型粒度，元数据保留）。"""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1",
           "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2",
             "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp, amp_b]}
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
    # 卸载后的本地重查：两个文件都已搬走 → 无本地模型
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
            await pilot.press("space")          # 勾选光标行（m1）
            await pilot.press("down", "space")  # 勾选 m2
            await pilot.press("u")
            await pilot.pause(0.5)
            assert calls.get("uninstall") == ([1, 2], False), calls
            assert "Uninstalled" in "".join(
                m.message for m in app._notifications)

    run(scenario())


def test_detail_pack_u_preset_refs_need_second_confirm(monkeypatch, tmp_path):
    """REQ-038-③：u 卸载遇 preset 引用须二次按 u 确认（与 uninstall_screen
    同语义）；活动链占用直接拦截。"""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1",
           "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp]}
    calls = {}
    plan_state = {"preset": True, "active": False}
    monkeypatch.setattr(
        "tui.panels.library.local_uninstall_models_plan",
        lambda ids: {"tone_ids": [10], "models": [], "bytes": 0,
                     "active_paths": ["/x"] if plan_state["active"] else [],
                     "preset_names": ["p1"] if plan_state["preset"] else [],
                     "outside_paths": []})
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
            # 第一次 u：preset 引用 → 提示，不执行
            await pilot.press("u")
            await pilot.pause(0.3)
            assert "uninstall" not in calls
            # 第二次 u：确认执行
            await pilot.press("u")
            await pilot.pause(0.5)
            assert calls.get("uninstall") == ([1], True), calls
            # 活动链占用：直接拦截
            plan_state["active"] = True
            plan_state["preset"] = False
            await pilot.press("u")
            await pilot.pause(0.3)
            assert calls.get("uninstall") == ([1], True), "活动链不得卸载"

    run(scenario())


def test_pack_install_screen_default_selects_missing_only(monkeypatch):
    """REQ-038：pack install 二级菜单默认只勾选未下载模型（已下载预取消，
    u 可卸）；i 键 = 安装选中（与 Enter 同路径）。"""
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

    tone = {"id": 77, "title": "Pack", "gear": "amp", "username": "tester",
            "downloads_count": 2}

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(PackInstallScreen(tone))
            await pilot.pause(0.5)
            screen = app.screen
            assert screen._selected == {2}, "已下载的模型不应默认勾选"
            # 已下载行带 ✓ downloaded 标记
            cell = str(screen.query_one("#pack-table", DataTable)
                       .get_cell("1", "name"))
            assert "downloaded" in cell
            # i 键安装选中（未下载的 model 2）
            await pilot.press("i")
            await pilot.pause(0.5)
            assert calls.get("import") == (77, [2]), calls
            await pilot.press("escape")
            await pilot.pause(0.2)

    run(scenario())


def test_pack_install_screen_u_uninstalls_selected(monkeypatch):
    """REQ-038：pack install 二级菜单 u 卸载选中的已下载模型，完成后留在
    本页并汇报卸载数。"""
    monkeypatch.setattr(
        "tui.install_screen.tone3000.models",
        lambda tid, a2_only=True: [
            {"id": 1, "name": "one.nam", "architecture": "SlimmableContainer"},
            {"id": 2, "name": "two.nam", "architecture": "SlimmableContainer"},
        ])
    monkeypatch.setattr(
        "tui.install_screen.library.downloaded_model_ids_by_tone",
        lambda: {77: {1, 2}})
    calls = {}
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
            assert screen._selected == set(), "全部已下载 → 默认无勾选"
            # space 勾选光标行（model 1）+ a 全选 → u 卸载两个已下载
            await pilot.press("a")
            await pilot.press("u")
            await pilot.pause(0.5)
            assert calls.get("uninstall") == ([1, 2], False), calls
            assert isinstance(app.screen, PackInstallScreen), "卸载后留在本页"
            status = str(screen.query_one("#pack-status").content)
            assert "uninstalled 2 file(s)" in status
            # 提示条含 i install / u uninstall token
            from tui.modals import border_hint_label
            label = border_hint_label(screen.query_one("ModalBox"))
            assert "i install" in label and "u uninstall" in label
            await pilot.press("escape")
            await pilot.pause(0.2)

    run(scenario())


def test_chain_click_opens_pack_with_old_absolute_db_rows(monkeypatch, tmp_path):
    """REQ-041：REQ-035 之前的旧库 local_path 存绝对路径——链节点点击
    → detail 必须打开对应 pack 视图（此前路径查找只匹配相对形式，
    反查失败 → detail 被清成 "Move the library cursor…" 空态）。"""
    import library as lib
    import tui.app as appmod
    import tui.panels as panels
    import tui.live as live

    monkeypatch.setattr(lib, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(lib, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(lib, "ROOT", tmp_path)
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(appmod.live, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(panels.live, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(live, "CHAIN_FILE", tmp_path / "live_chain.json")
    # 项目根内路径（相对/绝对两种形式不同，才能覆盖旧格式场景）
    f = lib.ROOT / "data" / "tones" / "10-jcm800" / "MV5 G1.nam"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"fixture")
    with lib.connect() as conn:
        lib.upsert_tone(conn, {"id": 10, "title": "JCM800", "gear": "amp",
                               "username": "a", "downloads_count": 1})
        lib.upsert_model(conn, {"id": 1, "tone_id": 10, "model_url": "u",
                                "name": "MV5 G1",
                                "architecture": "SlimmableContainer",
                                "local_path": str(f)})
        conn.execute("UPDATE models SET local_path = ? WHERE id = 1", (str(f),))
        conn.commit()
    lib.chain_set({"model": str(f), "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            pane = app.query_one(DetailPane)
            assert "Move the library cursor" not in _detail_plain(app)
            slot = next(n for n in app.query(ChainSlotWidget) if n.index == 0)
            await pilot.click(slot)
            await pilot.pause()
            # 链点击 → detail 打开该节点的 pack 视图（不是空态占位）
            assert pane._view_mode == "selection"
            assert pane._pack_mode
            assert pane._pack_table.row_count == 1
            assert "JCM800" in str(pane._marquee.content)
            assert pane._pack_tone.get("id") == 10

    run(scenario())


def _detail_plain(app) -> str:
    """DetailPane 标题 + 正文纯文本（空态占位检测用）。"""
    import io
    from rich.console import Console
    pane = app.query_one(DetailPane)
    buf = io.StringIO()
    Console(file=buf, width=120).print(pane._body.content)
    return f"{pane._marquee.content or ''} {buf.getvalue()}"
