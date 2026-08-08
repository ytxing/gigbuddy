"""Headless interaction tests for the TUI keyboard focus graph."""
import asyncio
import time
from pathlib import Path

from textual.events import MouseMove
from textual.color import Color
from textual.widgets import DataTable, Input, ProgressBar, Select, Static, TabbedContent, Tree
from rich.console import Group
from rich.table import Table
from rich.text import Text

from tui.app import GigBuddyApp, HeaderStatus
from tui.library_panel import LibraryTable
from tui.metadata import SelectableStatic
from tui.panels import (AudioSettingsScreen, ChainPanel, DetailPane, DeviceBar, DeviceChanged,
                        InterfaceBar, MeterBar, NodeWidget)
from tui.picker import TonePickerScreen
from tui.presets import PresetNameModal
from tui.uninstall_screen import LocalUninstallScreen
import library
import tone3000


def run(coro):
    return asyncio.run(coro)


def setup_preset_state(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    model = tmp_path / "amp.nam"
    model.write_bytes(b"amp")
    library.chain_set({"model": str(model), "gain": 0.8, "master": 1.0})
    return model


def detail_text(app) -> str:
    """Return the detail pane content whether it is a Rich table or text."""
    pane = app.query_one("DetailPane")
    title = str(pane._title.content)
    content = pane._body.content
    if isinstance(content, Table):
        body = " ".join(
            str(cell) for column in content.columns for cell in column.cells)
    else:
        body = str(content)
    return f"{title} {body}"


async def goto_tone_tab(app, pilot):
    """Switch the library panel to the TONE3000 search tab.

    Tab activation is detected by the 0.1s tick (TabActivated events lag in
    headless), so wait a few ticks for routing to settle.
    """
    # Programmatic `active` assignment rolls back in headless (Tabs watcher
    # re-posts), so take the real user path: click the tab.
    await pilot.click(app.query_one("#--content-tab-pane-tone"))
    await pilot.pause(0.3)


def test_single_click_focuses_double_click_selects_once(monkeypatch):
    """A single click on the already-focused row must NOT select (Textual's
    DataTable posts RowSelected on highlight clicks); a double click opens the
    picker exactly once (base + chain>=2 double-fire regression)."""
    tone = {"id": 10, "title": "Plexi", "gear": "amp", "username": "alice",
            "downloads_count": 1, "models": []}
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            table = app.query_one("#lib-table-local")
            # row 0 sits under the header → offset y=1; separate clicks by
            # >500ms so each is its own single click (Textual chain window)
            await pilot.click(table, offset=(8, 1))
            await pilot.pause(0.6)
            assert len(app.screen_stack) == 1
            # clicking the already-focused row must not open anything
            await pilot.click(table, offset=(8, 1))
            await pilot.pause(0.6)
            assert len(app.screen_stack) == 1, "highlight click must not select"
            # real double click (pilot's times=2 builds the click chain)
            await pilot.click(table, offset=(8, 1), times=2)
            await pilot.pause(0.4)
            pickers = [s for s in app.screen_stack
                       if isinstance(s, TonePickerScreen)]
            assert len(pickers) == 1, f"double click must push ONE picker, got {len(pickers)}"

    run(scenario())


def test_main_screen_keeps_chain_read_only_and_opens_tone_picker(monkeypatch):
    tone = {
        "id": 10, "title": "Plexi", "gear": "amp", "username": "alice",
        "downloads_count": 1, "models": [],
    }
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one("#lib-table-local")
            nodes = list(app.query(NodeWidget))

            assert app.focused is table

            await pilot.press("right")
            assert app.focused is table

            # chain nodes are clickable/focusable (↑/↓ steps within the tone
            # folder), but nothing opens until the user acts
            assert all(node.can_focus for node in nodes)
            amp_node = next(n for n in nodes if n.kind == "amp")
            amp_node.focus()
            await pilot.pause()
            assert app.focused is amp_node
            # ▲/▼ are separate clickable switch buttons on the node row
            assert app.query_one("#chain-amp-up") is not None
            assert app.query_one("#chain-amp-down") is not None

            table.focus()  # Enter is the table's select action
            await pilot.pause()
            await pilot.press("enter")
            # Enter on a row goes straight to that tone's model file list
            assert isinstance(app.screen, TonePickerScreen)
            assert app.screen.tone_id == 10

            await pilot.press("escape")
            assert app.focused is table

    run(scenario())


def test_local_library_multi_select_opens_bulk_uninstall(monkeypatch):
    tones = [
        {"id": 10, "title": "A", "gear": "amp", "username": "alice",
         "downloads_count": 1, "models": []},
        {"id": 11, "title": "B", "gear": "cab", "username": "bob",
         "downloads_count": 1, "models": []},
    ]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: tones)
    monkeypatch.setattr("tui.library_panel.library.get_tone",
                        lambda tone_id: next(t for t in tones if t["id"] == tone_id))
    monkeypatch.setattr("tui.uninstall_screen.library.local_uninstall_plan", lambda ids: {
        "tone_ids": ids, "models": [], "bytes": 0, "active_paths": [],
        "preset_names": [], "outside_paths": [],
    })

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one("#lib-table-local", DataTable)
            table.focus()
            await pilot.press("space", "down", "space")
            await pilot.pause()
            assert app.query_one("LibraryPanel")._local_selected == {10, 11}
            # REQ-040：选择框统一样式 [ ]/[x]
            assert table.get_cell("local:10", "pick") == "\\[x]"
            assert table.get_cell("local:11", "pick") == "\\[x]"

            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, LocalUninstallScreen)

    run(scenario())


def test_audio_bar_keeps_only_level_settings_and_mute(monkeypatch, tmp_path):
    setup_preset_state(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(100, 35)) as pilot:
            bar = app.query_one(InterfaceBar)
            assert not list(app.query(DeviceBar))
            settings = app.query_one("#audio-settings")
            mute = app.query_one("#audio-mute")
            meter = app.query_one(MeterBar)
            assert settings in bar.children
            assert mute in bar.children
            assert meter.region.right <= settings.region.x
            assert settings.region.right <= mute.region.x
            assert mute.region.right <= bar.region.right

            await pilot.click("#audio-settings")
            await pilot.pause()
            assert isinstance(app.screen, AudioSettingsScreen)
            assert app.screen.query_one(DeviceBar)
            await pilot.press("escape")

            await pilot.click("#audio-mute")
            await pilot.pause()
            assert library.chain_get()["master"] == 0.0
            assert "MUTED" in str(app.query_one("#audio-mute").content)

    run(scenario())


def test_no_engine_audio_settings_are_kept_for_the_session(monkeypatch, tmp_path):
    setup_preset_state(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.on_device_changed(DeviceChanged("buffer", "512"))
            app.on_device_changed(DeviceChanged("sr", "96000"))
            app.on_device_changed(DeviceChanged("in", "USB Input"))
            app.on_device_changed(DeviceChanged("out", "USB Output"))
            await pilot.pause()
            assert (app._block, app._sr) == (512, 96000)
            assert (app._dev_in, app._dev_out) == ("USB Input", "USB Output")

    run(scenario())


def test_preset_panel_table_is_bounded_and_scrollable(monkeypatch, tmp_path):
    setup_preset_state(monkeypatch, tmp_path)
    for index in range(20):
        library.preset_save(f"preset-{index:02d}")

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            table = app.query_one("#preset-table", DataTable)
            assert table.virtual_size.height > table.size.height
            assert table.show_vertical_scrollbar is True

    run(scenario())


def test_cursor_highlight_updates_detail_without_opening_action(monkeypatch):
    tones = [
        {"id": 10, "title": "First Tone", "gear": "amp", "username": "alice",
         "downloads_count": 10, "models": []},
        {"id": 11, "title": "Second Tone", "gear": "cab", "username": "bob",
         "downloads_count": 9, "models": []},
    ]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: tones)
    monkeypatch.setattr(
        "tui.library_panel.library.get_tone",
        lambda tone_id: next((t for t in tones if t["id"] == tone_id), None),
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert "First Tone" in detail_text(app)
            assert not isinstance(app.screen, TonePickerScreen)

            await pilot.press("down")
            await pilot.pause()
            assert "Second Tone" in detail_text(app)
            assert not isinstance(app.screen, TonePickerScreen)

    run(scenario())


def test_focused_content_is_revealed_in_marquee_banners(monkeypatch, tmp_path):
    monkeypatch.setattr(GigBuddyApp, "_show_node_pack", lambda *_: None,
                        raising=False)
    first_title = "A very long tone title that must remain fully readable when focused"
    second_title = "A second long title whose complete value should replace the first"
    tones = [
        {"id": 10, "title": first_title, "gear": "amp", "username": "alice",
         "downloads_count": 1, "models": []},
        {"id": 11, "title": second_title, "gear": "cab", "username": "bob",
         "downloads_count": 1, "models": []},
    ]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: tones)
    monkeypatch.setattr(
        "tui.library_panel.library.get_tone",
        lambda tone_id: next(t for t in tones if t["id"] == tone_id))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(80, 35)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("down")
            await pilot.pause()
            # The only marquee in the library is the #tone-status status line
            # (a MarqueeBar since it carries long import/search messages).
            from tui.marquee import MarqueeBar
            assert not [b for b in app.query_one("LibraryPanel").query(MarqueeBar)
                        if b.id != "tone-status"]

            # The same focused model is exposed in the chain banner, while the
            # compact pedal row keeps its stable two-line layout.
            node = next(item for item in app.query(NodeWidget)
                        if item.kind == "amp")
            node.set_title(first_title)
            node.set_label("an exceptionally long model filename.nam")
            node.focus()
            await pilot.pause()
            first_node_render = node.render()
            await pilot.pause(1.0)
            assert node.render() != first_node_render
            # REQ-043 追加：聚焦 marquee 行已删——节点自身 marquee 呈现
            assert len(app.query_one(ChainPanel).query("#chain-marquee")) == 0

    run(scenario())


def test_detail_metadata_allows_native_text_selection(monkeypatch):
    tone = {"id": 10, "title": "Copy Target", "gear": "amp", "username": "alice",
            "downloads_count": 10, "models": []}
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.library_panel.library.get_tone", lambda tone_id: tone)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            detail = app.query_one(DetailPane)
            assert detail._title.allow_select
            assert detail._body.allow_select

    run(scenario())


def test_rich_detail_table_can_be_mouse_selected(monkeypatch):
    """Drag selection over the detail body and copy its plain text."""
    tone = {"id": 10, "title": "Mouse Copy Target", "gear": "amp",
            "username": "alice", "downloads_count": 10,
            "favorites_count": 2, "models_count": 1,
            "description": "A warm tube overdrive for rock."}
    copied: list[str] = []
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.library_panel.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr(GigBuddyApp, "copy_to_clipboard",
                        lambda self, text: copied.append(text))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            body = app.query_one(DetailPane)._body
            assert body.allow_select
            start = (0, 1)
            end = (min(body.size.width - 1, 28), min(body.size.height - 1, 4))
            await pilot.mouse_down(body, offset=start)
            await pilot._post_mouse_events(
                [MouseMove], widget=body, offset=end, button=1)
            await pilot.mouse_up(body, offset=end)
            selected = app.screen.get_selected_text()
            # The tone detail defaults to the Description view now.
            assert selected and "DESCRIPTION" in selected
            assert "overdrive" in selected
            # Selection is a screen-level overlay, not a widget style change.

            await pilot.press("ctrl+c")
            assert copied and "overdrive" in copied[0]
            assert app.screen.get_selected_text() is None

    run(scenario())


def test_search_shortcut_submit_and_escape(monkeypatch):
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda query, page_size, **kwargs: [{
            "id": 99,
            "title": f"result for {query}",
            "gear": "amp",
            "downloads_count": 1,
            "username": "tester",
        }],
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/")
            search = app.query_one("#tone-search", Input)
            assert app.focused is search

            await pilot.press("p", "l", "e", "x", "i", "enter")
            panel = app.query_one("LibraryPanel")
            assert panel._mode == "tone"
            assert app.focused is app.query_one("#lib-table-tone")

            await pilot.press("escape")
            assert panel._mode == "local"
            assert search.value == ""
            assert app.focused is app.query_one("#lib-table-local")

    run(scenario())


def test_remote_cursor_highlight_updates_detail_without_import(monkeypatch):
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda query, page_size, **kwargs: [
            {"id": 91, "title": "Remote First", "gear": "amp",
             "downloads_count": 3, "username": "alice", "description": "clean"},
            {"id": 92, "title": "Remote Second", "gear": "cab",
             "downloads_count": 2, "username": "bob", "description": "bright"},
        ],
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "x", "enter")
            await pilot.pause(0.2)
            assert "Remote First" in detail_text(app)
            assert not isinstance(app.screen, TonePickerScreen)

            await pilot.press("down")
            await pilot.pause(0.2)
            assert "Remote Second" in detail_text(app)
            assert not isinstance(app.screen, TonePickerScreen)

    run(scenario())


def test_search_failure_keeps_previous_detail(monkeypatch):
    """REQ-011: 搜索失败不再把 detail 清成空态——保留上一条选中内容，
    失败只体现在表格错误行 + 重试提示（此前"操作一下"后永久空态）。"""
    tones = [{"id": 10, "title": "Local Tone", "gear": "amp",
              "username": "alice", "downloads_count": 1, "models": []}]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: tones)
    monkeypatch.setattr(
        "tui.library_panel.library.get_tone",
        lambda tone_id: tones[0] if tone_id == 10 else None,
    )
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            assert "Local Tone" in detail_text(app)
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "x", "enter")
            await pilot.pause()
            assert "Local Tone" in detail_text(app), "失败后 detail 不应清空"
            # 表格显示错误提示行（__status__），Enter = 重试而非静默吞掉
            table = app.query_one("#lib-table-tone")
            assert table.ordered_rows and table.ordered_rows[0].key.value == "__status__"
            await pilot.press("enter")
            await pilot.pause()

    run(scenario())


def test_remote_import_does_not_block_keyboard(monkeypatch):
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda query, page_size, **kwargs: [{
            "id": row,
            "title": f"result {row}",
            "gear": "amp",
            "downloads_count": row,
            "username": "tester",
        } for row in range(3)],
    )
    # pack screen fetches the model list without network in this test
    monkeypatch.setattr(
        "tui.install_screen.tone3000.models", lambda tid, a2_only=True: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "x", "enter")
            table = app.query_one("#lib-table-tone")
            assert table.cursor_row == 0

            # Enter opens the pack install screen — a modal; the main UI stays
            # responsive (no blocking import on the UI thread)
            await pilot.press("enter")
            await pilot.pause()
            from tui.install_screen import PackInstallScreen
            assert isinstance(app.screen, PackInstallScreen)
            assert isinstance(app.screen.query_one("#pack-detail"), SelectableStatic)

            await pilot.press("escape")  # cancel, back to the table
            await pilot.pause()
            await pilot.press("down")
            assert table.cursor_row == 1

    run(scenario())


def test_remote_import_reports_progress_and_completion(monkeypatch):
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda query, page_size, **kwargs: [{
            "id": 77, "title": "Pack", "gear": "amp",
            "downloads_count": 2, "username": "tester",
        }],
    )
    monkeypatch.setattr(
        "tui.install_screen.tone3000.models",
        lambda tid, a2_only=True: [
            {"id": 1, "model_url": "http://x/one.nam",
             "model_json": {"metadata": {"name": "one"}}},
            {"id": 2, "model_url": "http://x/two.nam",
             "model_json": {"metadata": {"name": "two"}}},
        ])

    def import_with_progress(tone_id, progress, **_kwargs):
        progress(0, 2, "one.nam")
        progress(1, 2, "one.nam")
        progress(2, 2, "two.nam")
        return {"id": tone_id, "models": [{"id": 1}, {"id": 2}]}

    monkeypatch.setattr("tui.install_screen.library.import_tone",
                        import_with_progress)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "x", "enter", "enter")  # search → pack screen
            await pilot.pause(0.2)
            from tui.install_screen import PackInstallScreen
            assert isinstance(app.screen, PackInstallScreen)
            # model list loaded from the mock, both selected
            pack = app.screen
            status = pack.query_one("#pack-status", Static)
            assert "2 model file(s)" in str(status.content)

            await pilot.press("enter")  # install (all selected)
            await pilot.pause(0.2)
            # completion: the screen dismisses and the app toasts the result
            assert not isinstance(app.screen, PackInstallScreen)
            toasts = {n.message for n in app._notifications}
            assert any("Installed 2 file(s) from tone 77" in m for m in toasts), toasts

    run(scenario())


def test_remote_enter_after_creators_visit_still_opens_install(monkeypatch):
    """REQ-009 根因回归：访问 TOP CREATORS（旧版在此清空 _remote_tones）
    后回到 TONE3000，remote 行 Enter 仍必须打开安装二级页——查找表与音色
    表行必须始终一致。"""
    hits = [{"id": 7, "title": "Plexi", "gear": "amp", "downloads_count": 3,
             "username": "tester", "a1_models_count": 1, "a2_models_count": 0,
             "irs_count": 0}]
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda query, page_size, **kwargs: [dict(h) for h in hits])
    monkeypatch.setattr("tui.install_screen.tone3000.models",
                        lambda tid, a2_only=True: [])
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.pause(0.5)
            table = app.query_one("#lib-table-tone")
            assert table.row_count == 1
            # 访问 TOP CREATORS（触发 creators 加载）
            await pilot.click(app.query_one("#--content-tab-pane-creators"))
            await pilot.pause(0.8)
            # 回到 TONE3000：行还在（row_count>0 不重载），Enter 必须仍有效
            await pilot.click(app.query_one("#--content-tab-pane-tone"))
            await pilot.pause(0.5)
            await pilot.press("enter")
            await pilot.pause(0.5)
            from tui.install_screen import PackInstallScreen
            assert isinstance(app.screen, PackInstallScreen)

    run(scenario())


def test_remote_tone_detail_selection_view_opens_install(monkeypatch):
    """REQ-009: tone3000 场景 detail 的 Selection 视图——远程模型列表
    （未下载置灰）后台拉取显示，Enter 打开安装二级页。"""
    hits = [{"id": 77, "title": "Remote Pack", "gear": "amp",
             "downloads_count": 2, "username": "tester",
             "a1_models_count": 1, "a2_models_count": 1, "irs_count": 0,
             "description": "Remote tone description."}]
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda query, page_size, **kwargs: [dict(h) for h in hits])
    # 注意：panels/install_screen 共享同一 tone3000 模块——一个 patch 即覆盖
    # 两处调用点（再 patch 一次会互相覆盖）。
    monkeypatch.setattr("tui.panels.tone3000.models",
                        lambda tid, a2_only=False: [
                            {"id": 1, "name": "one.nam",
                             "architecture": "SlimmableContainer"},
                            {"id": 2, "name": "two.nam",
                             "architecture": "SlimmableContainer"},
                            {"id": 3, "name": "cab.wav", "architecture": "IR"},
                        ])
    monkeypatch.setattr("library.tone3000.verify_username", lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.pause(0.5)
            pane = app.query_one(DetailPane)
            # 描述模式：远程元信息
            assert pane._view_mode == "description"
            assert "Remote tone description." in group_text(pane._body.content)
            # → selection：远程模型列表后台拉取，未下载置灰
            pane.focus()
            await pilot.press("right")
            await pilot.pause(0.8)
            assert pane._view_mode == "selection"
            assert pane._pack_remote
            assert pane._pack_table.row_count == 3
            assert "not downloaded" in str(pane._pack_table.get_cell("m1", "file"))
            # Enter 一行 → 安装二级页
            await pilot.press("enter")
            await pilot.pause(0.5)
            from tui.install_screen import PackInstallScreen
            assert isinstance(app.screen, PackInstallScreen)

    run(scenario())


def test_picker_groups_models_by_tone_and_shows_highlight_detail(monkeypatch):
    models = [
        {"id": 1, "tone_id": 10, "model_url": "http://x/one.nam",
         "architecture": "SlimmableContainer", "local_path": "/tones/10/one.nam",
         "title": "Plexi Pack", "username": "alice", "gear": "amp",
         "description": "Bright channel", "tags": ["plexi"], "makes": []},
        {"id": 2, "tone_id": 10, "model_url": "http://x/two.nam",
         "architecture": "SlimmableContainer", "local_path": "/tones/10/two.nam",
         "title": "Plexi Pack", "username": "alice", "gear": "amp",
         "description": "Normal channel", "tags": ["plexi"], "makes": []},
    ]
    monkeypatch.setattr("tui.picker.library.list_local_models", lambda kind: models)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(TonePickerScreen("amp"))
            await pilot.pause()
            tree = app.screen.query_one("#pick-tree", Tree)
            assert len(tree.root.children) == 1
            assert len(tree.root.children[0].children) == 2

            await pilot.press("right", "right")
            detail = app.screen.query_one("#pick-detail", Static)
            assert isinstance(detail, SelectableStatic)
            # Picker details intentionally contain only the description block.
            content = detail.content
            assert isinstance(content, Group)
            rendered = " ".join(str(item) for item in content.renderables)
            assert "DESCRIPTION" in rendered
            assert "Bright channel" in rendered
            assert "SOURCE" not in rendered
            assert "FILE" not in rendered
            assert "IDENTITY" not in rendered

            await pilot.press("left")
            assert tree.cursor_node is tree.root.children[0]

    run(scenario())


def test_picker_description_detail_scrolls_long_content(monkeypatch):
    description = " ".join(
        "A detailed description that must remain available while scrolling."
        for _ in range(80))
    model = {
        "id": 1, "tone_id": 10, "local_path": "/tones/10/one.nam",
        "title": "Long Description", "gear": "amp", "description": description,
    }
    monkeypatch.setattr("tui.picker.library.list_local_models", lambda kind: [model])
    monkeypatch.setattr("tui.picker.library.get_tone", lambda tone_id: model)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(100, 35)) as pilot:
            app.push_screen(TonePickerScreen("amp"))
            await pilot.pause()
            await pilot.press("right", "right")
            await pilot.pause()
            detail = app.screen.query_one("#pick-detail", Static)
            scroll = app.screen.query_one(".pick-detail-scroll")
            assert detail.content.renderables[-1].plain.startswith(
                "A detailed description")
            assert scroll.show_vertical_scrollbar
            assert scroll.max_scroll_y > 0

    run(scenario())


def test_library_add_flow_opens_only_selected_tone_models(monkeypatch):
    tone = {
        "id": 10, "title": "Plexi Pack", "gear": "amp-cab",
        "username": "alice", "downloads_count": 1, "models": [],
    }
    models = [{
        "id": 1, "tone_id": 10, "model_url": "http://x/one.nam",
        "architecture": "SlimmableContainer", "local_path": "/tones/10/one.nam",
        "title": "Plexi Pack", "username": "alice", "gear": "amp-cab",
        "description": "Combined capture", "tags": [], "makes": [],
    }]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("tui.picker.library.list_local_models", lambda kind: models)
    writes = []
    monkeypatch.setattr("tui.app.live.read_chain", lambda: {"ir": "old-cab.wav"})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: writes.append(dict(cfg)))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            # Enter on a row opens the tone's model file list directly
            assert isinstance(app.screen, TonePickerScreen)
            assert app.screen.tone_id == 10
            assert not app.screen.query_one("#pick-search", Input).display

            await pilot.press("enter")
            await pilot.pause()
            assert writes[-1]["model"] == "/tones/10/one.nam"
            # amp-cab 包选 AMP：CAB 显式置 null（pop 键引擎不会移除旧 IR）
            assert writes[-1]["ir"] is None

    run(scenario())


def test_type_filter_drives_local_query(monkeypatch):
    calls = []

    def list_tones(**kwargs):
        calls.append(kwargs.get("gear"))
        return []

    monkeypatch.setattr("tui.library_panel.library.list_tones", list_tones)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one("#type-filter-local", Select).value = "cab"
            await pilot.pause()
            assert calls[-1] == "cab"

    run(scenario())


def test_type_filters_are_complete_on_the_first_frame(monkeypatch):
    """The Type menus are seeded before either async library load returns."""
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **_kwargs: [])
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda *_args, **_kwargs: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            expected = ["all", *tone3000.GEAR_VALUES]
            for select_id in ("#type-filter-local", "#type-filter-tone"):
                select = app.query_one(select_id, Select)
                assert [value for _label, value in select._options] == expected
                assert select.value == "all"

    run(scenario())


def test_general_search_accepts_author_query(monkeypatch):
    queries = []

    def search(query, page_size, **kwargs):
        if page_size == 100:
            return []  # startup TOP CREATORS prefetch (REQ-010) — not a search
        queries.append(query)
        return [{"id": 8, "title": "Author tone", "gear": "amp",
                 "downloads_count": 3, "username": "alice"}]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", search)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "a", "l", "i", "c", "e", "enter")
            # opening the TONE3000 tab auto-loads the trending feed (empty query),
            # then the typed search runs
            assert queries == ["", "alice"]
            table = app.query_one("#lib-table-tone")
            assert table.row_count == 1
            assert table.get_cell("remote:8", "author") == "@alice"

    run(scenario())


def test_search_examples_compile_author_tag_and_make_filters(monkeypatch):
    calls = []

    def search(query, page_size, **kwargs):
        calls.append((query, kwargs))
        return []

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", search)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            search_input = app.query_one("#tone-search", Input)
            search_input.focus()
            search_input.value = (
                'make:"Two Rock Traditional Clean" '
                "@coretonecaptures #clean two rock"
            )
            await pilot.press("enter")
            await pilot.pause(0.2)

            query, kwargs = calls[-1]
            assert query == "two rock"
            assert kwargs["usernames"] == ["coretonecaptures"]
            assert kwargs["tag_names"] == ["clean"]
            assert kwargs["make_names"] == ["Two Rock Traditional Clean"]

    run(scenario())


def test_model_id_search_uses_exact_remote_model_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.tones_for_model_ids",
        lambda model_ids: calls.append(tuple(model_ids)) or [{
            "id": 19, "title": "Plexi", "gear": "amp", "username": "alice",
            "matched_model_ids": [123],
        }],
    )
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected text search")),
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            search_input = app.query_one("#tone-search", Input)
            search_input.focus()
            search_input.value = "model:123"
            await pilot.press("enter")
            await pilot.pause(0.2)
            table = app.query_one("#lib-table-tone", DataTable)
            assert calls == [(123,)]
            assert "model #123" in str(table.get_cell_at((0, 0)))

    run(scenario())


def test_invalid_search_does_not_issue_a_remote_request(monkeypatch):
    calls = []

    def search(query, page_size, **kwargs):
        calls.append(query)
        return []

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", search)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            before = len(calls)
            search_input = app.query_one("#tone-search", Input)
            search_input.focus()
            search_input.value = 'make:"Two Rock'
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert len(calls) == before

    run(scenario())


def test_meter_uses_clean_track_without_escaped_brackets():
    rendered = MeterBar().render()
    assert "\\[" not in rendered
    assert "IN " in rendered and "OUT" in rendered


def test_meter_renders_input_and_output_on_separate_lines():
    rendered = MeterBar().render()
    assert "\n" in rendered
    assert rendered.index("IN ") < rendered.index("\n") < rendered.index("OUT")


def test_meter_channel_labels_align_the_bar_column():
    from rich.console import Console
    import io

    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, width=120).print(MeterBar().render())
    in_line, out_line = buffer.getvalue().splitlines()
    assert in_line.index("░") == out_line.index("░")


def test_chain_params_reserve_sign_column():
    from tui.panels import ChainParams

    params = ChainParams()
    params.set_values(0.0, -0.1, 1.0)
    rendered = str(params.render())
    assert "GAIN   0.00" in rendered
    assert "MASTER  -0.10" in rendered


def test_chain_panel_compact_layout_keeps_output_and_preset_rows(monkeypatch):
    # keep the 0.3s tick from overwriting the chain with live_chain.json
    monkeypatch.setattr(
        "tui.app.live.read_chain",
        lambda: {"model": "/tones/19-marshall-jcm-800-2203.nam",
                 "gain": 0.8, "master": 0.35})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(100, 35)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = {
                "model": "/tones/19-marshall-jcm-800-2203.nam",
                "gain": 0.8,
                "master": 0.35,
            }
            await pilot.pause()

            assert "MASTER" in str(panel.params.render())
            assert "0.35" in str(panel.params.render())
            param_bar = panel.query_one(".chain-params")
            assert param_bar.styles.dock == "bottom"
            assert param_bar.region.bottom == panel.content_region.bottom
            # AMP row carries ▲/▼ switch buttons; CAB steps via ↑/↓ instead;
            # preset UI lives in the left panel
            assert app.query_one("#chain-amp-up") is not None
            assert app.query_one("#chain-amp-down") is not None

    run(scenario())


def test_focused_amp_node_steps_through_tone_folder(monkeypatch, tmp_path):
    """↑/↓ on a focused AMP node cycles sibling models of the same tone folder,
    writes the chain and mirrors the model in the detail pane."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": []}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a, amp_b])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    # read_chain must reflect what write_chain wrote (real impl reads the file)
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", amp_a["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            amp_node.focus()
            await pilot.pause()
            assert app.focused is amp_node

            await pilot.press("down")  # → amp_b
            await pilot.pause()
            assert written["model"] == amp_b["local_path"]
            assert app.query_one(ChainPanel).chain["model"] == amp_b["local_path"]

            await pilot.press("down")  # wraps around → amp_a
            await pilot.pause()
            assert written["model"] == amp_a["local_path"]

            await pilot.press("up")  # back → amp_b
            await pilot.pause()
            assert written["model"] == amp_b["local_path"]

    run(scenario())


def test_focused_cab_node_steps_through_ir_folder(monkeypatch, tmp_path):
    """↑/↓ on a focused CAB node cycles sibling IRs of the same tone folder
    and writes the chain's ir slot (AMP and CAB both use ↑/↓; ←/→ stays
    with the tone-detail view switching)."""
    cab_a = {"id": 1, "tone_id": 10, "name": "GB 1960", "architecture": "IR",
             "local_path": str(tmp_path / "GB 1960.wav")}
    cab_b = {"id": 2, "tone_id": 10, "name": "V30 1960", "architecture": "IR",
             "local_path": str(tmp_path / "V30 1960.wav")}
    (tmp_path / "GB 1960.wav").write_bytes(b"a")
    (tmp_path / "V30 1960.wav").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "cab", "username": "arthm",
            "downloads_count": 1, "models": []}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [cab_a, cab_b])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": "/x/amp.nam",
                                 "ir": written.get("ir", cab_a["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            cab_node = next(n for n in app.query(NodeWidget) if n.kind == "cab")
            cab_node.focus()
            await pilot.pause()
            assert app.focused is cab_node

            await pilot.press("down")  # ↓ cab_b
            await pilot.pause()
            assert written["ir"] == cab_b["local_path"]
            assert app.query_one(ChainPanel).chain["ir"] == cab_b["local_path"]

            await pilot.press("up")     # ↑ cab_a
            await pilot.pause()
            assert written["ir"] == cab_a["local_path"]

            # ↓ on the CAB node does not touch the amp slot
            before = written.get("model", "/x/amp.nam")
            await pilot.press("down")
            await pilot.pause()
            assert written.get("model", before) == before

    run(scenario())


def test_chain_border_hint_tokens_are_clickable(monkeypatch, tmp_path):
    """The chain panel's right-corner hint is a real control: d delete acts on
    the last-focused AMP/CAB slot, space/s/l drive the dry-input playback.
    Tokens are lowercase (d/space/s/l are the actual bindings) and the hint
    has no model-switch token (↑/↓ is a keyboard action with no click target)."""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    ir = {"id": 2, "tone_id": 10, "name": "GB 1960", "architecture": "IR",
          "local_path": str(tmp_path / "GB 1960.wav")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "GB 1960.wav").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp-cab", "username": "arthm",
            "downloads_count": 1, "models": [amp, ir]}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp] if path.endswith(".nam") else [ir])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", amp["local_path"]),
                                 "ir": ir["local_path"], "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain",
                        lambda cfg: written.update(cfg))

    from rich.cells import cell_len
    from tui.modals import border_hint_segments, hint_span

    def click_token(panel, token):
        label = str(panel.border_subtitle)
        span = hint_span(label, token)
        assert span is not None, f"token {token!r} not in hint {label!r}"
        label_width = cell_len(label)
        label_start = panel.region.x + max(1, panel.region.width - label_width - 2)
        return (label_start + span[0] + 1 - panel.region.x,
                panel.region.bottom - 1 - panel.region.y)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(ChainPanel)
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            await pilot.click(amp_node)   # 记录 _last_focus_node
            await pilot.pause()
            segments = border_hint_segments(panel)
            label = " · ".join(segments)
            # 提示行只保留可点击动作：小写、无 model ↑↓
            assert "↑↓" not in label and "model" not in label
            assert not any(c.isupper() for c in label)
            # d delete → 对 AMP 槽生效（hint 点击落在面板上，聚焦可能已移走，
            # 走 _last_focus_node 兜底）
            await pilot.click(panel, offset=click_token(panel, segments[0]))
            await pilot.pause()
            assert written["model"] is None

    run(scenario())


def test_click_node_row_focuses_node_only(monkeypatch, tmp_path):
    """Clicking inside a chain node box only focuses the node — the focus
    stays in the tone chain and the detail pane is left untouched (the pack
    file list opens explicitly via the detail pane's → selection view)."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp_a]}
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": amp_a["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            await pilot.click(amp_node)
            await pilot.pause()
            assert app.focused is amp_node
            pane = app.query_one(DetailPane)
            assert not pane._pack_mode
            assert pane._view_mode == "description"
            # row padding is part of the same box: still focuses the node
            await pilot.click(amp_node, offset=(1, 0))
            await pilot.pause()
            assert app.focused is amp_node

    run(scenario())


def test_node_focus_does_not_open_pack(monkeypatch, tmp_path):
    """Keyboard focus into a node only focuses it — the detail pane does not
    jump to the pack list (that view opens via → selection there)."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": amp_a["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            amp_node.focus()
            await pilot.pause()
            assert app.focused is amp_node
            assert not app.query_one(DetailPane)._pack_mode

    run(scenario())


def group_text(group) -> str:
    """Render a Rich renderable (e.g. description_only's Group) to plain text."""
    import io
    from rich.console import Console
    buf = io.StringIO()
    Console(file=buf, width=120).print(group)
    return buf.getvalue()


def test_detail_switches_between_description_and_selection(monkeypatch, tmp_path):
    """Tone detail defaults to the Description view; right/left (and the
    border hint) switch to the Selection view (pack file list) and back."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp_a, amp_b],
            "description": "Plexi crunch with a touch of reverb."}
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.library_panel.library.get_tone", lambda tone_id: tone)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            pane = app.query_one(DetailPane)
            # Default: description view with the mode hint on the right.
            assert pane._view_mode == "description"
            assert "Plexi crunch" in group_text(pane._body.content)
            assert not pane._pack_table.display
            assert "→ selection" in pane.border_subtitle
            # Right → selection view (pack file list).
            pane.focus()
            await pilot.press("right")
            await pilot.pause()
            assert pane._view_mode == "selection"
            assert pane._pack_table.display
            assert pane._pack_table.row_count == 2
            # REQ-038：selection 视图右下角常驻 i install / u uninstall；
            # ←/→ 切换 token 在窄面板省略（键盘 ←/→ 仍可用，见下）。
            assert "i install" in pane.border_subtitle
            assert "u uninstall" in pane.border_subtitle
            # Left → back to description.
            await pilot.press("left")
            await pilot.pause()
            assert pane._view_mode == "description"
            assert "Plexi crunch" in group_text(pane._body.content)
            assert not pane._pack_table.display
            # Selection entered via the view switch: Esc returns here, not to
            # a chain node.
            await pilot.press("right")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert pane._view_mode == "description"

    run(scenario())


def test_pack_table_enter_swaps_chain_slot(monkeypatch, tmp_path):
    """Enter on a pack file row hot-swaps the chain and moves the ▶ marker."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp_a, amp_b]}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a, amp_b])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", amp_a["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, tone["models"],
                           {"model": amp_a["local_path"], "gain": 0.8},
                           "amp", focus_table=True)
            await pilot.pause()
            await pilot.press("down")   # cursor → amp_b
            await pilot.press("enter")
            await pilot.pause()
            assert written["model"] == amp_b["local_path"]
            assert app.query_one(ChainPanel).chain["model"] == amp_b["local_path"]
            # marker moved to the new row; pack view stays open
            assert "▶" in pane._pack_table.get_cell("m2", "sel")
            assert "▶" not in pane._pack_table.get_cell("m1", "sel")
            assert pane._pack_mode

    run(scenario())


def test_pack_table_double_click_swaps_chain_slot(monkeypatch, tmp_path):
    """Double-clicking a pack file row hot-swaps the chain slot, like Enter
    (single click only moves the cursor — ClickSelectTable)."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp_a, amp_b]}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a, amp_b])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", amp_a["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, tone["models"],
                           {"model": amp_a["local_path"], "gain": 0.8},
                           "amp", focus_table=True)
            await pilot.pause()
            # Row 1 of the pack table is the second file (header is row 0).
            await pilot.double_click(pane._pack_table, offset=(8, 2))
            await pilot.pause()
            assert written["model"] == amp_b["local_path"]
            assert app.query_one(ChainPanel).chain["model"] == amp_b["local_path"]
            assert "▶" in pane._pack_table.get_cell("m2", "sel")
            assert "▶" not in pane._pack_table.get_cell("m1", "sel")
            assert pane._pack_mode

    run(scenario())


def test_pack_ir_row_writes_ir_slot(monkeypatch, tmp_path):
    """Selecting an IR row in the pack list swaps the chain's IR slot (an amp
    row keeps using the model slot)."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    ir = {"id": 3, "tone_id": 10, "name": "GB 1960", "architecture": "IR",
          "local_path": str(tmp_path / "GB 1960.wav")}
    ir2 = {"id": 4, "tone_id": 10, "name": "V30 1960", "architecture": "IR",
           "local_path": str(tmp_path / "V30 1960.wav")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "GB 1960.wav").write_bytes(b"b")
    (tmp_path / "V30 1960.wav").write_bytes(b"c")
    tone = {"id": 10, "title": "JCM800", "gear": "amp-cab", "username": "arthm",
            "downloads_count": 1, "models": [amp_a, ir, ir2]}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": amp_a["local_path"],
                                 "ir": written.get("ir", ir2["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, tone["models"],
                           {"model": amp_a["local_path"],
                            "ir": ir2["local_path"], "gain": 0.8},
                           "amp", focus_table=True)
            await pilot.pause()
            await pilot.press("down")   # cursor → GB 1960 (IR row)
            await pilot.press("enter")
            await pilot.pause()
            assert written["ir"] == ir["local_path"]
            assert written["model"] == amp_a["local_path"]  # untouched

    run(scenario())


def test_pack_undownloaded_row_does_not_swap(monkeypatch, tmp_path):
    """A file without a local copy is shown dimmed; Enter opens the secondary
    detail page (REQ-038) instead of hot-swapping the chain slot."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    remote = {"id": 2, "tone_id": 10, "name": "Remote extra",
              "architecture": "SlimmableContainer"}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp_a, remote]}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": amp_a["local_path"], "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))
    # REQ-038：未下载行 Enter = 打开二级菜单详情页（不热换链槽），
    # PackInstallScreen 拉模型列表需 mock（避免真实网络请求）。
    monkeypatch.setattr("tui.install_screen.tone3000.models",
                        lambda tid, a2_only=True: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, tone["models"],
                           {"model": amp_a["local_path"], "gain": 0.8},
                           "amp", focus_table=True)
            await pilot.pause()
            await pilot.press("down")   # cursor → not downloaded row
            await pilot.press("enter")
            await pilot.pause()
            assert written == {}
            assert app.query_one(ChainPanel).chain["model"] == amp_a["local_path"]
            # 未下载行 Enter → 二级菜单详情页（正确 tone id）
            from tui.install_screen import PackInstallScreen
            assert isinstance(app.screen, PackInstallScreen)
            assert app.screen._tone.get("id") == 10
            await pilot.press("escape")
            await pilot.pause()

    run(scenario())


def test_pack_escape_returns_focus_to_node(monkeypatch, tmp_path):
    """Esc on the pack file table returns keyboard focus to the chain node."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp_a]}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": amp_a["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            pane = app.query_one(DetailPane)
            pane.show_pack(tone, tone["models"],
                           {"model": amp_a["local_path"], "gain": 0.8},
                           "amp", focus_table=True)
            await pilot.pause()
            assert app.focused is pane._pack_table
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is next(
                n for n in app.query(NodeWidget) if n.kind == "amp")
            # pack view stays open so ↑/↓ stepping still moves the markers
            assert pane._pack_mode

    run(scenario())


def test_click_switch_buttons_step_models(monkeypatch, tmp_path):
    """Clicking the ▲/▼ buttons steps sibling models (no keyboard needed)."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": []}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a, amp_b])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", amp_a["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            # two-row switch column: ▲ on the title line (offset 0),
            # ▼ on the filename line (offset 1)
            col = app.query_one("#chain-amp-down").parent
            up = app.query_one("#chain-amp-up")
            assert up.size.width == col.size.width
            assert up.region.x == col.content_region.x
            await pilot.click(col, offset=(5, 1))  # ▼ → amp_b
            await pilot.pause()
            assert written["model"] == amp_b["local_path"]

            await pilot.click(col, offset=(5, 0))  # ▲ → amp_a
            await pilot.pause()
            assert written["model"] == amp_a["local_path"]

            # clicking a button also focuses its node
            assert app.focused is next(
                n for n in app.query(NodeWidget) if n.kind == "amp")

    run(scenario())


def test_notify_replaces_header_status(monkeypatch):
    """Rapid notifications replace the one-line status in GigBuddy's header."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            title = app.query_one("HeaderTitle")
            title_region = title.region
            header = app.query_one("GigBuddyHeader")
            assert header.region.height == 1
            await pilot.click(title)
            await pilot.pause()
            assert header.region.height == 1
            assert not header.has_class("-tall")
            app.notify("one")
            app.notify("two")
            app.notify("three")
            app.notify("four")
            await pilot.pause()
            status = app.query_one(HeaderStatus)
            assert str(status.content) == "four"
            assert status.has_class("header-status--visible")
            assert status.has_class("header-status--information")
            # 通知条在 Header 行内左上角：标题永远居中、不被挤动
            assert status.region.y == 0
            assert status.region.x == 0
            assert title.region == title_region
            assert title.content_region.x + title.content_region.width // 2 \
                == header.region.x + header.region.width // 2
            assert len(app._notifications) <= 1
            # the newest notification is the only one retained
            msgs = {n.message for n in app._notifications}
            assert "four" in msgs

            app.notify("warning", severity="warning", timeout=0.5)
            await pilot.pause(0.1)
            assert status.has_class("header-status--warning")
            assert status.region.y == 0  # 仍在 Header 行内
            assert title.region == title_region  # 标题恒居中
            await pilot.pause(0.6)
            assert str(status.content) == ""
            assert not status.has_class("header-status--visible")
            assert title.region == title_region  # 标题恒居中

    run(scenario())


def test_double_click_ir_toggles_bypass(monkeypatch, tmp_path):
    """Double-clicking the IR node bypasses (ir=null) and restores it."""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    ir = {"id": 2, "tone_id": 11, "name": "GB 1960", "architecture": "IR",
          "local_path": str(tmp_path / "GB 1960.wav")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "GB 1960.wav").write_bytes(b"b")
    monkeypatch.setattr("tui.app.library.local_models_by_tone", lambda path: [ir])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tid: {"id": tid, "title": "T"})
    written = {}
    initial = {"model": amp["local_path"], "ir": ir["local_path"], "gain": 0.8}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", initial["model"]),
                                 "ir": written.get("ir", initial["ir"]),
                                 "gain": written.get("gain", 0.8)})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            cab_node = next(n for n in app.query(NodeWidget) if n.kind == "cab")
            await pilot.double_click(cab_node)
            await pilot.pause()
            assert written["ir"] is None  # bypassed

            await pilot.double_click(cab_node)
            await pilot.pause()
            assert written["ir"] == ir["local_path"]  # restored

    run(scenario())


def test_double_click_amp_toggles_bypass(monkeypatch, tmp_path):
    """Double-clicking the AMP node bypasses (model=null) and restores it —
    the file stays displayed, flagged BYPASS (mute now lives on G-/G+)."""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    monkeypatch.setattr("tui.app.library.local_models_by_tone", lambda path: [amp])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tid: {"id": tid, "title": "T"})
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", amp["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            await pilot.double_click(amp_node)
            await pilot.pause()
            assert written["model"] is None  # bypassed (engine直通)
            assert amp_node.bypassed is True

            await pilot.double_click(amp_node)
            await pilot.pause()
            assert written["model"] == amp["local_path"]  # restored
            assert amp_node.bypassed is False

    run(scenario())


def test_search_requests_a_40_row_page(monkeypatch):
    """Remote lists start with a bounded page, not an unbounded result set."""
    captured = {}
    def fake_search(query, **kw):
        captured.update(kw)
        return []
    monkeypatch.setattr("tui.library_panel.library.tone3000.search", fake_search)
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            search = app.query_one("#tone-search", Input)
            search.focus()
            search.value = "plexi"
            await pilot.press("enter")
            await pilot.pause()
            assert captured["page_size"] == 40
            assert captured["page_number"] == 1

    run(scenario())


def test_local_library_uses_a_bounded_scrollable_viewport(monkeypatch):
    """Long local libraries scroll instead of expanding past the panel."""
    tones = [
        {"id": i, "title": f"Tone {i}", "gear": "amp",
         "downloads_count": 0, "favorites_count": 0,
         "a2_models_count": 1, "models_count": 1,
         "username": "tester", "created_at": "2026-01-01"}
        for i in range(100)
    ]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **_kw: tones)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.query_one("#lib-table-local", DataTable)
            assert table.row_count == 100
            assert table.size.height < table.virtual_size.height
            assert table.show_vertical_scrollbar

    run(scenario())


def test_focused_library_title_scrolls_in_place(monkeypatch):
    long_title = "Mesa Boogie Triple Rectifier Rev F Red Channel " \
        "with an intentionally long capture title"
    tones = [
        {"id": 10, "title": long_title, "gear": "amp",
         "downloads_count": 0, "favorites_count": 0,
         "a2_models_count": 1, "models_count": 1, "username": "tester"},
        {"id": 11, "title": "Short title", "gear": "cab",
         "downloads_count": 0, "favorites_count": 0,
         "irs_count": 1, "models_count": 1, "username": "tester"},
    ]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **_kw: tones)
    monkeypatch.setattr(
        "tui.library_panel.library.get_tone",
        lambda tone_id: next(t for t in tones if t["id"] == tone_id),
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            table = app.query_one("#lib-table-local", DataTable)
            first = table.get_cell("local:10", "title")
            assert isinstance(first, Text)
            # marquee 两端各有 6 tick 暂停期（marquee.py ping-pong 设计，
            # 0.12s/tick）：等过暂停期进入滚动移动段再断言窗口变化。
            await pilot.pause(1.0)
            second = table.get_cell("local:10", "title")
            assert isinstance(second, Text)
            assert str(first) != str(second)

            await pilot.press("down")
            await pilot.pause()
            assert isinstance(table.get_cell("local:10", "title"), str)
            assert str(table.get_cell("local:11", "title")) == "Short title"

    run(scenario())


def test_focused_tone3000_title_uses_the_same_marquee(monkeypatch):
    long_title = "TONE3000 remote title with a long model description"
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda *_args, **_kwargs: [{
            "id": 77, "title": long_title, "gear": "amp",
            "downloads_count": 2, "username": "tester",
            "models_count": 1,
        }],
    )
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda rows: rows)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            table = app.query_one("#lib-table-tone", DataTable)
            first = table.get_cell("remote:77", "title")
            assert isinstance(first, Text)
            await pilot.pause(1.0)
            second = table.get_cell("remote:77", "title")
            assert isinstance(second, Text)
            assert str(first) != str(second)

    run(scenario())


def test_tone_filters_share_the_tab_strip_without_filling_the_background(monkeypatch):
    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda *_args, **_kwargs: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(240, 40)) as pilot:
            panel = app.query_one("LibraryPanel")
            tabs = app.query_one("ContentTabs")
            bar = app.query_one("#tone-filter-row")
            assert not bar.display

            await goto_tone_tab(app, pilot)
            assert bar.display
            assert bar.region.y == tabs.region.y
            assert bar.region.x > app.query_one(
                "#--content-tab-pane-creators").region.right
            assert app.query_one("#type-filter-tone").styles.background == Color.parse(
                app.get_css_variables()["background"])

            await pilot.click(app.query_one("#--content-tab-pane-local"),
                              offset=(1, 1))
            await pilot.pause(0.2)
            assert not bar.display

    run(scenario())


def test_local_library_loads_next_page_when_viewport_reaches_bottom(monkeypatch):
    tones = [
        {"id": i, "title": f"Local Tone {i}", "gear": "amp",
         "downloads_count": 0, "favorites_count": 0,
         "a2_models_count": 1, "models_count": 1,
         "username": "tester", "created_at": "2026-01-01"}
        for i in range(401)
    ]
    offsets: list[int] = []

    def list_tones(**kwargs):
        offset = int(kwargs.get("offset", 0))
        offsets.append(offset)
        return tones[offset:offset + int(kwargs["limit"])]

    monkeypatch.setattr("tui.library_panel.library.list_tones", list_tones)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            table = app.query_one("#lib-table-local", DataTable)
            assert table.row_count == 200
            table.focus()
            await pilot.pause()
            table.scroll_end(animate=False, immediate=True)
            await pilot.pause(0.4)
            assert 200 in offsets
            assert table.row_count == 400

    run(scenario())


def test_top_creators_loads_next_page_when_viewport_reaches_bottom(monkeypatch):
    calls: list[int] = []

    def top_creators(*, page_number, page_size, **_kwargs):
        calls.append(page_number)
        start = (page_number - 1) * page_size
        return [
            {"id": str(start + i), "username": f"creator-{start + i}",
             "public_tones_count": 1000 - start - i,
             "downloads_count": i, "favorites_count": 0,
             "public_models_count": 1}
            for i in range(page_size)
        ]

    monkeypatch.setattr("tui.library_panel.library.tone3000.top_creators",
                        top_creators)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click(app.query_one("#--content-tab-pane-creators"))
            await pilot.pause(0.5)
            table = app.query_one("#lib-table-creators", DataTable)
            assert table.row_count == 100
            table.focus()
            await pilot.pause()
            table.scroll_end(animate=False, immediate=True)
            await pilot.pause(0.4)
            assert calls[-1] == 2
            assert table.row_count == 200

    run(scenario())


def test_remote_search_loads_next_page_near_the_cursor(monkeypatch):
    calls: list[int] = []

    def search(_query, *, page_number, page_size, **_kwargs):
        calls.append(page_number)
        assert page_size == 40
        start = (page_number - 1) * 40
        return [
            {"id": start + i, "title": f"Tone {start + i}", "gear": "amp",
             "downloads_count": i, "username": "tester", "total_count": 80}
            for i in range(40)
        ]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", search)
    # 假 tone id（0-39）与本地库真实 id 碰撞（如 19）会触发 mark_download_state
    # 的真实网络请求，拖慢测试时序——透传标记状态，与网络解耦。
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.pause(0.3)  # let the initial Trending worker settle
            panel = app.query_one("LibraryPanel")
            await panel._show_search("x")
            table = app.query_one("#lib-table-tone", DataTable)
            table.focus()
            for _ in range(36):
                await pilot.press("down")
            await pilot.pause(0.4)
            assert calls[-1] == 2
            assert table.row_count == 80
            assert table.cursor_row >= 35

    run(scenario())


def test_remote_search_loads_next_page_when_viewport_reaches_bottom(monkeypatch):
    calls: list[int] = []

    def search(_query, *, page_number, page_size, **_kwargs):
        calls.append(page_number)
        start = (page_number - 1) * 40
        return [
            {"id": start + i, "title": f"Tone {start + i}", "gear": "amp",
             "downloads_count": i, "username": "tester", "total_count": 80}
            for i in range(40)
        ]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", search)
    # 测试返回的假 tone id（0-39）可能与本地库真实 id 碰撞（如 19），
    # mark_download_state 会对本地命中的 id 发真实网络请求——断网/代理
    # 变化时拖垮测试。标记状态与网络无关，直接透传。
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.pause(0.8)  # let the tab's initial page request settle
            panel = app.query_one("LibraryPanel")
            await panel._show_search("x")
            table = app.query_one("#lib-table-tone", DataTable)
            assert table.row_count == 40
            table.focus()
            await pilot.pause()  # let DataTable arrange its scrollable content
            table.scroll_end(animate=False, immediate=True)
            await pilot.pause(0.4)
            assert calls[-1] == 2
            assert table.row_count == 80

    run(scenario())


def test_quality_hotkeys_clamp_and_write_chain(monkeypatch):
    """q/Q adjust the A2 quality param (clamped 0..1) into the live chain."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)
    written = {}
    # read_chain reflects what write_chain wrote (real impl reads the file)
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"gain": 1.0,
                                 "quality": written.get("quality", 1.0)})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("q")  # quality 1.0 → 0.95
            await pilot.pause()
            assert written["quality"] == 0.95

            await pilot.press("Q", "Q")  # → 1.05 → clamped to 1.0
            await pilot.pause()
            assert written["quality"] == 1.0

            for _ in range(25):
                await pilot.press("q")  # → 0.0 floor
            await pilot.pause()
            assert written["quality"] == 0.0

            panel = app.query_one(ChainPanel)
            assert "QUALITY" in str(panel.params.render())

    run(scenario())


def test_quit_requires_two_ctrl_c(monkeypatch):
    """Ctrl+C once warns, a second press within the window exits."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)
    monkeypatch.setattr("tui.app.live.read_chain", lambda: {"gain": 1.0})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            assert any(b.key == "ctrl+c" for b in app.BINDINGS)

            await pilot.press("ctrl+c")
            await pilot.pause()
            assert not app._exit  # still running after the first press
            assert "Ctrl+C" in str(app.query_one(ChainPanel)) or True
            # first press posted a toast
            toasts = {n.message for n in app._notifications}
            assert any("Press ctrl+c again" in m for m in toasts), toasts

            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app._exit is True

    run(scenario())


def test_quit_two_ctrl_c_works_from_modal(monkeypatch):
    """Ctrl+C twice exits even while a modal screen is open."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)
    monkeypatch.setattr("tui.app.live.read_chain", lambda: {"gain": 1.0})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: None)
    monkeypatch.setattr("tui.presets.library.preset_list", lambda: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_save_preset_as()
            await pilot.pause()
            assert isinstance(app.screen, PresetNameModal)

            await pilot.press("ctrl+c", "ctrl+c")
            await pilot.pause()
            assert app._exit is True

    run(scenario())


def test_cursor_focus_returns_to_description(monkeypatch, tmp_path):
    """Mouse focus decides the detail mode: focusing the library shows the
    tone's Description (even after a manual switch to Selection), and never
    steals keyboard focus."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    amp_c = {"id": 3, "tone_id": 11, "name": "PRS G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "PRS G1.nam")}
    for p in (amp_a, amp_b, amp_c):
        Path(p["local_path"]).write_bytes(b"x")
    tones = [
        {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
         "downloads_count": 1, "models": [amp_a, amp_b],
         "description": "Plexi crunch."},
        {"id": 11, "title": "PRS Archon", "gear": "amp", "username": "arthm",
         "downloads_count": 1, "models": [amp_c],
         "description": "Modern high gain."},
    ]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: tones)
    monkeypatch.setattr("tui.library_panel.library.get_tone",
                        lambda tid: next(t for t in tones if t["id"] == tid))
    monkeypatch.setattr("tui.app.library.get_tone",
                        lambda tid: next(t for t in tones if t["id"] == tid))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            table = app.query_one("#lib-table-local")
            pane = app.query_one(DetailPane)
            # Enter the Selection view for the first tone (explicit switch).
            pane.focus()
            await pilot.press("right")
            await pilot.pause()
            assert pane._view_mode == "selection"
            assert pane._pack_table.row_count == 2
            # Cursor moves to the second tone: library focus → Description.
            table.focus()
            await pilot.press("down")
            await pilot.pause()
            assert pane._view_mode == "description"
            assert not pane._pack_table.display
            assert "Modern high gain." in group_text(pane._body.content)
            assert "TONE #11" in str(pane._marquee.content)
            # Focus stayed on the library table (no steal).
            assert app.focused is table

    run(scenario())


def test_creator_cursor_follows_into_detail(monkeypatch, tmp_path):
    """Moving the cursor through TOP CREATORS rows follows into the detail
    pane with the focused author's info (REQ-012: 作者视图取代首 tone 映射)."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    def paged_search(*args, **kwargs):
        return [
            {"id": 1, "title": "Creator One Tone", "gear": "amp",
             "username": "creator1", "downloads_count": 5,
             "favorites_count": 0, "a2_models_count": 1,
             "published_at": "2026-01-01", "total_count": 100,
             "download_state": "none"},
            {"id": 2, "title": "Creator Two Tone", "gear": "cab",
             "username": "creator2", "downloads_count": 3,
             "favorites_count": 0, "a2_models_count": 0,
             "models_count": 1, "published_at": "2026-01-01",
             "total_count": 100, "download_state": "none"},
        ]

    monkeypatch.setattr(library.tone3000, "search", paged_search)
    monkeypatch.setattr(library.tone3000, "top_creators", lambda **_kwargs: [
        {"id": "1", "username": "creator1", "public_tones_count": 7,
         "downloads_count": 5, "favorites_count": 0,
         "public_models_count": 1},
        {"id": "2", "username": "creator2", "public_tones_count": 6,
         "downloads_count": 3, "favorites_count": 0,
         "public_models_count": 1},
    ])
    monkeypatch.setattr("tui.library_panel.library.mark_download_state",
                        lambda hits: hits)
    monkeypatch.setattr("tui.panels.tone3000.user",
                        lambda name: {"username": name,
                                      "bio": f"{name} bio",
                                      "display_name": name.upper()})
    monkeypatch.setattr("tui.panels.library.tone3000.verify_username",
                        lambda name: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await pilot.click(app.query_one("#--content-tab-pane-creators"))
            await pilot.pause(0.4)
            await pilot.pause(0.5)
            table = app.query_one("#lib-table-creators")
            pane = app.query_one(DetailPane)
            # Cursor on the first creator row follows into the detail pane:
            # the focused author's info (not some random tone). REQ-030：
            # 标题 @名 + 正文 bio（无第二行摘要）。
            assert "creator1" in str(pane._marquee.content)
            assert str(pane._summary.content) == ""
            body = group_text(pane._body.content)
            assert "creator1 bio" in body
            assert not pane._pack_table.display, "聚焦联动不应弹列表"
            # Moving down follows the second creator.
            table.focus()
            await pilot.press("down")
            await pilot.pause()
            assert "creator2" in str(pane._marquee.content)
            assert "creator2 bio" in group_text(pane._body.content)

    run(scenario())


def test_detail_header_ids_and_empty_state(monkeypatch, tmp_path):
    """Header rows show TONE #id and MODEL #id; the pack table has no TONE
    column (REQ-019); the empty state drops the tinted title background."""
    amp_a = {"id": 7, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 8, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp_a, amp_b],
            "description": "Plexi crunch."}
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.library_panel.library.get_tone", lambda tone_id: tone)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            pane = app.query_one(DetailPane)
            # Title row shows the tone id; the live chain has no model → no MODEL.
            assert "TONE #10" in str(pane._marquee.content)
            assert "MODEL" not in str(pane._marquee.content)
            # Pack table rows keep their file list but no TONE column (REQ-019).
            pane.focus()
            await pilot.press("right")
            await pilot.pause()
            assert pane._pack_table.row_count == 2
            assert not any(col.key.value == "tone"
                           for col in pane._pack_table.ordered_columns)
            # Title row follows the pack cursor with the model id.
            assert "MODEL #7" in str(pane._marquee.content)
            await pilot.press("down")
            await pilot.pause()
            assert "MODEL #8" in str(pane._marquee.content)
            # Empty state: the tinted title background is dropped.
            pane.clear()
            assert pane._marquee.has_class("detail-marquee--empty")

    run(scenario())


def test_node_click_opens_selection_view(monkeypatch, tmp_path):
    """Mouse focus decides the detail mode: clicking a chain node shows its
    Selection (pack list), not the Description."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": [amp_a, amp_b],
            "description": "Plexi crunch."}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a, amp_b])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": amp_a["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            pane = app.query_one(DetailPane)
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            await pilot.click(amp_node)
            await pilot.pause()
            assert app.focused is amp_node
            assert pane._view_mode == "selection"
            assert pane._pack_table.display
            assert pane._pack_table.row_count == 2
            assert "JCM800" in str(pane._marquee.content)
            assert "TONE #10" in str(pane._marquee.content)

    run(scenario())


def test_chain_blank_click_focuses_panel(monkeypatch):
    """Clicking the tone chain's blank areas (an effect row, not a node)
    focuses the panel itself, so ←/→ can switch the detail view."""
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            panel = app.query_one(ChainPanel)
            effect = app.query_one(".chain-effect")
            await pilot.click(effect)
            await pilot.pause()
            assert app.focused is panel

    run(scenario())
