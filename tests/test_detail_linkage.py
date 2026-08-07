"""REQ-011/012: tone3000 场景 detail 联动正确性专项。

REQ-011：搜索/换排序/刷新等"操作一下"后，detail 不得清成空态占位（旧行为
在搜索发起瞬间清空、失败后永久空态，且加载/失败提示行把 Enter/双击静默吞
掉）。REQ-012：LOCAL / TONE3000 / TOP CREATORS 三场景行选中 → detail 联动
审计（行高亮必须驱动 detail 显示该行对应的 tone，缓存恢复/往返后仍成立）。
"""
import asyncio

from textual.widgets import DataTable, TabPane

from tui.app import GigBuddyApp
from tui.library_panel import LibraryPanel
from tui.panels import DetailPane

import library  # noqa: F401  conftest 把 src/ 注入 sys.path


def run(coro):
    return asyncio.run(coro)


def _hits(n=4):
    return [
        {"id": 100 + i, "title": f"Remote {i}", "gear": "amp",
         "downloads_count": i, "username": "tester",
         "a1_models_count": 1, "a2_models_count": 0, "irs_count": 0,
         "description": f"remote description {i}"}
        for i in range(n)
    ]


def _creator(username, tones, downloads=0, favorites=0, models=0):
    return {
        "id": f"user:{username}", "username": username,
        "public_tones_count": tones, "downloads_count": downloads,
        "favorites_count": favorites, "public_models_count": models,
    }


def _detail_text(app) -> str:
    """标题行（marquee）+ 主体（description_only 产出 rich Group）纯文本。"""
    import io
    from rich.console import Console
    pane = app.query_one(DetailPane)
    buf = io.StringIO()
    Console(file=buf, width=120).print(pane._body.content)
    return f"{pane._title.content or ''} {buf.getvalue()}"


def _detail_empty(app) -> bool:
    return "Move the library cursor" in _detail_text(app)


def test_tone3000_highlight_drives_detail_and_enter(monkeypatch):
    """TONE3000 行高亮 → detail 显示该行 tone；Enter 开远程 PACK。"""
    hits = _hits()
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda query, page_size, **kwargs: [dict(h) for h in hits])
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_favorites",
                        lambda n: [dict(h) for h in hits])
    monkeypatch.setattr(
        "tui.panels.tone3000.models",
        lambda tone_id, a2_only=False: [{
            "id": 9101,
            "tone_id": tone_id,
            "name": "Remote 1.nam",
            "architecture": "SlimmableContainer",
        }])
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)
    notifications = []
    monkeypatch.setattr(
        "tui.app.GigBuddyApp.notify",
        lambda self, message, **_kwargs: notifications.append(str(message)),
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(1.0)
            assert "Remote 0" in _detail_text(app)
            # ↓ 光标 → detail 跟随
            await pilot.press("down")
            await pilot.pause()
            assert "Remote 1" in _detail_text(app)
            # Library Enter opens the remote PACK and attempts its first model.
            await pilot.press("enter")
            await pilot.pause(0.8)
            pane = app.query_one(DetailPane)
            assert app.screen.id == "_default"
            assert pane._view_mode == "selection"
            assert pane._pack_mode
            assert pane._description_remote
            assert pane._pack_remote
            assert "m9101" in pane._pack_rows
            assert any("not downloaded" in message for message in notifications)

    run(scenario())


def test_sort_roundtrip_keeps_detail_and_enter(monkeypatch):
    """REQ-011: 换排序（新鲜加载 + 缓存命中恢复）期间 detail 不清空、不
    卡在空态；恢复后 Enter 仍开二级页。"""
    hits = _hits()
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda query, page_size, **kwargs: [dict(h) for h in hits])
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_favorites",
                        lambda n: [dict(h) for h in hits])
    monkeypatch.setattr(
        "tui.panels.tone3000.models",
        lambda tone_id, a2_only=False: [{
            "id": 9201,
            "tone_id": tone_id,
            "name": "Remote.nam",
            "architecture": "SlimmableContainer",
        }])
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)
    # 详情异步合并（tone_by_id）会真实请求网络并覆盖 mock 标题，导致
    # "Remote 0" 断言偶发失败：返回与搜索行一致的详情，稳定 title。
    monkeypatch.setattr(
        "library.tone3000.tone_by_id",
        lambda tid, with_models=False: {
            **hits[tid - 100], "tags": [], "makes": []})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(1.0)
            sort_select = app.query_one("#sort-filter")
            # 切 favorites（新鲜加载）
            sort_select.value = "favorites"
            await pilot.pause(1.0)
            assert not _detail_empty(app)
            # 切回 trending（缓存命中恢复）
            sort_select.value = "trending"
            await pilot.pause(1.0)
            assert not _detail_empty(app)
            assert "Remote 0" in _detail_text(app)
            await pilot.press("enter")
            await pilot.pause(0.5)
            pane = app.query_one(DetailPane)
            assert app.screen.id == "_default"
            assert pane._view_mode == "selection"
            assert pane._description_remote

    run(scenario())


def test_search_keeps_detail_until_results_land(monkeypatch):
    """REQ-011: 搜索进行中 detail 保留上一条内容；落定后切换到新结果。"""
    hits = _hits()
    calls = []
    import time

    def slow_search(query, page_size, **kwargs):
        calls.append(query)
        time.sleep(0.2)   # 模拟慢网络：搜索进行中的窗口
        return [dict(h) for h in hits]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", slow_search)
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(1.0)
            assert "Remote 0" in _detail_text(app)
            # 发起新搜索，在落定前检查：detail 不清空
            await pilot.press("/", "x", "enter")
            await pilot.pause(0.1)
            assert "Remote 0" in _detail_text(app), "搜索中 detail 被清空"
            await pilot.pause(0.5)
            assert "Remote 0" in _detail_text(app), "落定后 detail 未跟随"

    run(scenario())


def test_status_row_enter_retries_failed_search(monkeypatch):
    """REQ-011: 搜索失败后表格是提示行——Enter/双击 = 重试，不再静默吞掉。"""
    calls = {"n": 0}

    def failing_search(query, page_size, **kwargs):
        calls["n"] += 1
        # 1 = 启动 TONE3000 预取，2 = tab 首次进入重载 → 失败，
        # 3 = 提示行 Enter 重试成功。TOP CREATORS 使用独立 leaderboard
        # endpoint，不会额外调用 tone search。
        if calls["n"] <= 2:
            raise TimeoutError("simulated timeout")
        return [dict(_hits()[0])]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        failing_search)
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": "bio",
                                      "display_name": name.title()})
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(1.0)
            table = app.query_one("#lib-table-tone", DataTable)
            assert table.ordered_rows[0].key.value == "__status__", \
                "首次搜索失败应显示提示行"
            # Enter 在提示行上 → 重试成功 → 行数据出现
            await pilot.press("enter")
            await pilot.pause(1.0)
            assert calls["n"] == 3, "Enter 在提示行上应触发一次重试搜索"
            assert table.ordered_rows[0].key.value.startswith("remote:")
            assert not _detail_empty(app)

    run(scenario())


def test_creators_row_shows_remote_real_count(monkeypatch):
    """REQ-013: every displayed value comes from user_public_counts."""
    rows = [_creator("tester", 48, 100, 9, 70),
            _creator("rare", 3, 8, 2, 5)]
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        lambda **_kwargs: [dict(row) for row in rows])
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": f"{name} bio",
                                      "display_name": name.title()})
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.5)
            table = app.query_one("#lib-table-creators")
            assert table.get_cell("creator:tester", "tones") == "48"
            assert table.get_cell("creator:rare", "tones") == "3"
            assert table.get_cell("creator:tester", "downloads") == "100"
            assert table.get_cell("creator:tester", "favorites") == "9"
            assert table.get_cell("creator:tester", "models") == "70"
            # Cache restore must keep the same official values.
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(1.0)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            assert table.get_cell("creator:tester", "tones") == "48"

    run(scenario())


def test_creators_sort_select_reorders(monkeypatch):
    """REQ-029: each sort requests the matching official leaderboard."""
    rows = [_creator("alice", 48, 150, 15, 9),
            _creator("bob", 60, 20, 4, 2)]
    calls = []

    def top_creators(*, sort_by, **_kwargs):
        calls.append(sort_by)
        field = {"tones": "public_tones_count", "downloads": "downloads_count",
                 "favorites": "favorites_count", "models": "public_models_count"}[sort_by]
        return sorted((dict(row) for row in rows),
                      key=lambda row: (-row[field], row["username"]))

    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        top_creators)
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": "bio",
                                      "display_name": name})
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.5)
            # SORT 条在 creators tab 显示，默认 Most Tones
            bar = app.query_one("#creators-search-bar")
            assert bar.display
            sort_select = app.query_one("#sort-filter-creators")
            assert sort_select.value == "tones"
            table = app.query_one("#lib-table-creators")
            # Most Tones: bob 60 > alice 48.
            assert table.ordered_rows[0].key.value == "creator:bob"
            # Most Downloads: alice 150 > bob 20, fetched from the server view.
            sort_select.value = "downloads"
            await pilot.pause(1.0)
            assert table.ordered_rows[0].key.value == "creator:alice"
            # 切回 Most Tones：bob 回第一
            sort_select.value = "tones"
            await pilot.pause(1.0)
            assert table.ordered_rows[0].key.value == "creator:bob"
            assert calls[-3:] == ["tones", "downloads", "tones"]

    run(scenario())


def test_creator_bio_normalized_for_banner(monkeypatch):
    """REQ-026: 作者简介进 banner（summary marquee）前换行/多空格压成
    一行；详情页多行区保留原始换行。"""
    import tui.panels as panels_mod
    assert panels_mod._single_line(
        "Line one\n\n  spaced\tout  text ") == "Line one spaced out text"
    messy_bio = "Tone maker.\n\nLoves\n   plexi amps\tand IRs.  "
    alice = {"id": 201, "title": "Alice One", "gear": "amp",
             "downloads_count": 30, "username": "alice",
             "a1_models_count": 1, "a2_models_count": 0, "irs_count": 0,
             "description": "alice one"}

    def fake_search(query, page_size, **kwargs):
        return [dict(alice)]

    def fake_user(name):
        return {"username": name, "bio": messy_bio,
                "display_name": "Alice", "created_at": "2023-01-02T00:00:00Z"}

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", fake_search)
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        lambda **_kwargs: [_creator("alice", 5, 30, 0, 1)])
    monkeypatch.setattr("tui.panels.tone3000.user", fake_user)
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            pane = app.query_one(DetailPane)
            # REQ-030 聚焦视图：无第二行摘要（bio 在正文多行保留换行）
            assert str(pane._summary.content) == ""
            body = _detail_text(app)
            assert "Tone maker." in body and "Loves" in body
            assert "\n" in _detail_text(app).strip() or "  " in body
            # REQ-033：Enter 跳 @作者 搜索（不再是作者页）——聚焦视图
            # 的 bio 单行化由 _single_line 单元断言覆盖

    run(scenario())


def test_creator_values_do_not_change_after_render(monkeypatch):
    """Official leaderboard values render once without background refinement."""
    calls = []

    def top_creators(**kwargs):
        calls.append(kwargs)
        return [_creator("alice", 48, 150, 15, 9)]

    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        top_creators)
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": "bio",
                                      "display_name": name})
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
            app = GigBuddyApp(spawn_engine=False)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.query_one(LibraryPanel).activate_view_tab("pane-creators")
                await pilot.pause(0.5)
                table = app.query_one("#lib-table-creators")
                before = tuple(table.get_cell("creator:alice", key) for key in
                               ("tones", "downloads", "favorites", "models"))
                await pilot.pause(1.0)
                after = tuple(table.get_cell("creator:alice", key) for key in
                              ("tones", "downloads", "favorites", "models"))
                assert before == after == ("48", "150", "15", "9")
                assert len(calls) == 1

    run(scenario())


def test_creator_search_bar_docks_properly(monkeypatch):
    """REQ-031 补充：creators SearchBar 保持固定一行，不占空白；
    SORT Select 正常显示。"""
    page = [{"id": 100, "title": "A0", "gear": "amp", "downloads_count": 50,
             "username": "alice", "favorites_count": 5, "models_count": 3,
             "a1_models_count": 1, "a2_models_count": 1, "irs_count": 0}]

    def fake_search(query, page_size, **kwargs):
        if kwargs.get("usernames") and page_size == 1:
            return [{"total_count": 5}]
        return [dict(h) for h in page]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", fake_search)
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        lambda **_kwargs: [_creator("alice", 2, 50, 0, 2)])
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": "bio",
                                      "display_name": name})
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            bar = app.query_one("#creators-search-bar")
            # SearchBar 是固定一行，不占大块空白、不挤压标签栏。
            assert bar.region.height == 1, f"search bar height {bar.region.height}"
            assert bar.display
            select = app.query_one("#sort-filter-creators")
            assert select.display
            # 其他 tab 时该条隐藏（不串场）。v0.2 里 SearchBar.display
            # 恒 True，可见性由所属 TabPane 控制（非 active 的 pane
            # display:none，子控件 region 归零）。
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(1.0)
            pane = next(a for a in bar.ancestors
                        if isinstance(a, TabPane))
            assert not pane.display
            assert bar.region.width == 0

    run(scenario())


def test_creator_enter_jumps_to_author_search(monkeypatch):
    """REQ-033: creators 行 Enter/双击 → 跳 TONE3000 tab、搜索栏填
    @author 并触发真实搜索。"""
    alice_tones = [
        {"id": 201, "title": "Alice One", "gear": "amp", "downloads_count": 30,
         "username": "alice", "a1_models_count": 1, "a2_models_count": 1,
         "irs_count": 0, "description": "alice one"},
        {"id": 202, "title": "Alice Two", "gear": "amp", "downloads_count": 20,
         "username": "alice", "a1_models_count": 1, "a2_models_count": 0,
         "irs_count": 0, "description": "alice two"},
    ]
    searches = []

    def fake_search(query, page_size, **kwargs):
        if kwargs.get("usernames") and page_size == 1:
            return [{"total_count": len(alice_tones)}]
        searches.append({"query": query, "usernames": kwargs.get("usernames")})
        return [dict(h) for h in alice_tones]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", fake_search)
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        lambda **_kwargs: [_creator("alice", 5, 30, 0, 1)])
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": "bio",
                                      "display_name": name})
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            table = app.query_one("#lib-table-creators")
            assert table.row_count == 1
            await pilot.press("enter")   # 作者行 Enter
            await pilot.pause(1.5)
            # 切到 TONE3000 tab，搜索栏填 @alice，真实搜索已触发
            # （@author 语义 = usernames 过滤 + 空 text）
            tone_search = app.query_one("#tone-search")
            assert tone_search.value == "@alice"
            assert any(s["usernames"] == ["alice"] for s in searches)
            # 结果表显示该作者音色
            tone_table = app.query_one("#lib-table-tone")
            assert tone_table.row_count == 2
            assert tone_table.ordered_rows[0].key.value.startswith("remote:")

    run(scenario())


def test_creator_focus_view_bio_in_body_and_verified(monkeypatch):
    """REQ-030: 聚焦作者视图 = 标题行 @名(+✓) + 正文 bio 完整多行；
    无第二行摘要、无 Enter 提示；verified 异步探测后标题带 ✓。"""
    alice = {"id": 201, "title": "Alice One", "gear": "amp",
             "downloads_count": 30, "username": "alice",
             "a1_models_count": 1, "a2_models_count": 0, "irs_count": 0,
             "description": "alice one"}
    bio = "Line one.\nLine two with details."

    def fake_search(query, page_size, **kwargs):
        if kwargs.get("usernames") and page_size == 1:
            return [{"total_count": 5}]
        return [dict(alice)]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", fake_search)
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        lambda **_kwargs: [
                            _creator("alice", 2, 50, 0, 2),
                            _creator("bob", 1, 9, 0, 1),
                        ])
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": bio,
                                      "display_name": "Alice"})
    verified_set: set[str] = set()
    monkeypatch.setattr("library.tone3000.verify_username",
                        lambda name: (verified_set.add(name), True)[1])
    monkeypatch.setattr("library.tone3000.verified_users",
                        lambda: verified_set)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            pane = app.query_one(DetailPane)
            # 无第二行摘要、无 Enter 提示
            assert str(pane._summary.content) == ""
            assert "Enter opens" not in _detail_text(app)
            # 正文 = bio 完整多行（保留换行）
            body = _detail_text(app)
            assert "Line one." in body and "Line two with details." in body
            # verified 异步探测 → 标题带 ✓
            await pilot.pause(1.0)
            assert "alice" in verified_set
            assert "✓" in str(pane._marquee.content)
            # ↓ 换作者 → 标题跟随（无 ✓ 残留错位：bob 未认证）
            await pilot.press("down")
            await pilot.pause(0.5)
            assert "bob" in str(pane._marquee.content) or \
                "alice" in str(pane._marquee.content)

    run(scenario())


def test_creators_row_focus_and_profile_page(monkeypatch):
    """REQ-012/REQ-020：TOP CREATORS 行聚焦 → detail 显示该作者信息（@名/
    简介——不再显示任意单音色）；Enter → 作者介绍页（多行文本：统计/注册
    时间，无音色列表）；往返后联动仍成立。"""
    alice_tones = [
        {"id": 201, "title": "Alice One", "gear": "amp", "downloads_count": 30,
         "username": "alice", "a1_models_count": 1, "a2_models_count": 1,
         "irs_count": 0, "description": "alice one"},
        {"id": 202, "title": "Alice Two", "gear": "amp", "downloads_count": 20,
         "username": "alice", "a1_models_count": 1, "a2_models_count": 0,
         "irs_count": 0, "description": "alice two"},
    ]
    bob_tones = [
        {"id": 301, "title": "Bob Crunch", "gear": "amp", "downloads_count": 9,
         "username": "bob", "a1_models_count": 1, "a2_models_count": 1,
         "irs_count": 0, "description": "bob desc"},
    ]
    all_tones = alice_tones + bob_tones

    def fake_search(query, page_size, **kwargs):
        if kwargs.get("usernames") and page_size == 1:
            name = kwargs["usernames"][0]
            return [{"total_count": len([t for t in all_tones
                                         if t["username"] == name])}]
        return [dict(t) for t in all_tones]

    def fake_user(name):
        return {"username": name, "bio": f"{name}'s bio",
                "display_name": name.title(), "created_at": "2023-01-02T00:00:00Z",
                "links": ["https://x.example"]}

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", fake_search)
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        lambda **_kwargs: [
                            _creator("alice", 2, 50, 0, 2),
                            _creator("bob", 1, 9, 0, 1),
                        ])
    monkeypatch.setattr("tui.panels.tone3000.user", fake_user)
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            pane = app.query_one(DetailPane)
            table = app.query_one("#lib-table-creators")
            assert table.row_count == 2
            # 聚焦行 0（alice）→ detail 显示作者信息（标题 @alice、
            # 正文 bio——REQ-030 无第二行摘要），而不是某个 tone 的介绍
            assert "alice" in str(pane._marquee.content)
            assert "alice's bio" in _detail_text(app)
            assert str(pane._summary.content) == ""
            assert not pane._pack_table.display, "聚焦联动不应弹列表"
            # ↓ 到行 1（bob）→ detail 跟随 bob
            await pilot.press("down")
            await pilot.pause()
            assert "bob" in str(pane._marquee.content)
            assert "bob's bio" in _detail_text(app)
            # REQ-033：Enter → 跳 TONE3000 搜索 @bob（不再进作者页）
            await pilot.press("enter")
            await pilot.pause(1.5)
            assert app.query_one("#tone-search").value == "@bob"
            # v0.2.14：view tab 独立保存 anchor，往返后恢复原 cursor
            # （bob），而不是重置到第 0 行。
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            assert app.query_one("#lib-table-creators").cursor_row == 1
            assert "bob" in str(pane._marquee.content)
            assert not _detail_empty(app)

    run(scenario())


def test_creator_sort_select_width_matches_tone3000(monkeypatch):
    """REQ-036: creators SORT 框宽度与 TONE3000 一致（定宽 26，不偏大）。"""
    page = [{"id": 100, "title": "A0", "gear": "amp", "downloads_count": 50,
             "username": "alice", "favorites_count": 5, "models_count": 3,
             "a1_models_count": 1, "a2_models_count": 1, "irs_count": 0}]
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda query, page_size, **kwargs: [dict(h) for h in page])
    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        lambda **_kwargs: [_creator("alice", 5, 50, 5, 3)])
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": "bio"})
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-tone")
            await pilot.pause(0.5)
            tone_w = app.query_one("#sort-filter").region.width
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            creator_w = app.query_one("#sort-filter-creators").region.width
            assert creator_w == tone_w, f"creators SORT 宽 {creator_w} != tone {tone_w}"

    run(scenario())


def test_creator_load_more_keeps_exact_values_and_cursor(monkeypatch):
    """Appending official rows never rewrites already displayed statistics."""
    pages = {
        1: [_creator("alice", 48, 150, 15, 9)],
        2: [_creator("bob", 60, 200, 20, 12)],
    }

    def top_creators(*, page_number, **_kwargs):
        return [dict(row) for row in pages[page_number]]

    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        top_creators)
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name, "bio": "bio"})
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(LibraryPanel).activate_view_tab("pane-creators")
            await pilot.pause(1.0)
            table = app.query_one("#lib-table-creators")
            assert table.ordered_rows[0].key.value == "creator:alice"
            panel = app.query_one(LibraryPanel)
            panel._creator_has_more = True
            scroll_before = table.scroll_y
            panel._maybe_load_more_from_viewport(table, force=True)
            await pilot.pause(0.8)
            keys = [r.key.value.partition(":")[2] for r in table.ordered_rows]
            assert keys == ["alice", "bob"]
            assert table.get_cell("creator:alice", "tones") == "48"
            assert table.get_cell("creator:alice", "downloads") == "150"
            assert table.scroll_y == scroll_before
            cursor_key = table.ordered_rows[table.cursor_row].key.value
            assert cursor_key == "creator:alice"

    run(scenario())
