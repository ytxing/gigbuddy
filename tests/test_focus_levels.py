"""REQ-039: 两级聚焦视觉 — 聚焦页面（focus-within）边框亮 + 内容全亮 +
聚焦元素最亮；非聚焦页面内容 dim（opacity 0.8）+ 聚焦元素降一级
（$primary → $secondary）。模态不在主面板内，其聚焦元素恒为最亮。

断言走 Textual 计算样式：面板 opacity 与 DataTable cursor 组件背景色。
"""
import asyncio

from textual.widgets import DataTable, Tree

from tui.app import GigBuddyApp
from tui.install_screen import PackInstallScreen
from tui.input_screen import InputSourceScreen
from tui.library_panel import LibraryPanel
from tui.panels import ChainPanel, DetailPane, InterfaceBar, NodeWidget
from tui.presets import PresetPanel


def run(coro):
    return asyncio.run(coro)


def _hex(color) -> str:
    return str(color.hex6).upper()


def _cursor_bg(table: DataTable) -> str:
    bg = table.get_component_styles("datatable--cursor").background
    return _hex(bg)


def _theme_var(app, name: str) -> str:
    return str(app.theme_variables[name]).upper()


def test_focused_panel_full_brightness_primary_cursor():
    """初始焦点在库表：LibraryPanel 是聚焦页面（opacity 1 + primary 光标），
    其余面板全部降级（opacity 0.8 + secondary 光标）。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            library = app.query_one(LibraryPanel)
            preset = app.query_one(PresetPanel)
            chain = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            interface = app.query_one(InterfaceBar)
            lib_table = app.query_one("#lib-table-local", DataTable)
            preset_table = app.query_one("#preset-table", DataTable)

            assert library.styles.opacity == 1.0
            assert preset.styles.opacity == 0.8
            assert chain.styles.opacity == 0.8
            assert detail.styles.opacity == 0.8
            assert interface.styles.opacity == 0.8
            # 聚焦页面光标最亮 = $primary；非聚焦页面光标降一级 = $secondary
            assert _cursor_bg(lib_table) == _theme_var(app, "primary")
            assert _cursor_bg(preset_table) == _theme_var(app, "secondary")

    run(scenario())


def test_focus_switch_flips_both_levels():
    """焦点移到 preset 表后两级翻转：PresetPanel 全亮，LibraryPanel 降级。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            library = app.query_one(LibraryPanel)
            preset = app.query_one(PresetPanel)
            lib_table = app.query_one("#lib-table-local", DataTable)
            preset_table = app.query_one("#preset-table", DataTable)

            app.query_one("#preset-table", DataTable).focus()
            await pilot.pause(0.1)

            assert preset.styles.opacity == 1.0
            assert library.styles.opacity == 0.8
            assert _cursor_bg(preset_table) == _theme_var(app, "primary")
            assert _cursor_bg(lib_table) == _theme_var(app, "secondary")

    run(scenario())


def test_chain_node_focus_activates_only_chain_panel():
    """链节点聚焦只激活 ChainPanel；DetailPane 独立降级（pack 表同步降级）。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            chain = app.query_one(ChainPanel)
            detail = app.query_one(DetailPane)
            node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            node.focus()
            await pilot.pause(0.1)

            assert chain.styles.opacity == 1.0
            assert detail.styles.opacity == 0.8

    run(scenario())


def test_interface_bar_two_level():
    """InterfaceBar 平时 dim；聚焦其按钮（focus-within）时全亮。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            interface = app.query_one(InterfaceBar)
            assert interface.styles.opacity == 0.8

            app.query_one("#audio-mute").focus()
            await pilot.pause(0.1)
            assert interface.styles.opacity == 1.0

    run(scenario())


def test_modal_cursor_stays_brightest():
    """模态（独立 screen）不在主面板内：其表/树光标保持 $primary 最亮，
    不受面板降级规则影响。"""

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            app.push_screen(PackInstallScreen(
                {"id": 1, "title": "T", "gear": "amp",
                 "username": "u", "downloads_count": 1}))
            await pilot.pause(0.5)
            pack_table = app.screen.query_one("#pack-table", DataTable)
            assert _cursor_bg(pack_table) == _theme_var(app, "primary")

            app.pop_screen()
            await pilot.pause(0.2)
            app.push_screen(InputSourceScreen())
            await pilot.pause(0.3)
            tree = app.screen.query_one("#input-tree", Tree)
            bg = tree.get_component_styles("tree--cursor").background
            assert _hex(bg) == _theme_var(app, "primary")

    run(scenario())
