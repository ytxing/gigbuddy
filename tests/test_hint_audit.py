"""REQ-024: 全项目右下角提示审计回归。

三个维度：① 提示 token 与真实绑定的大小写一致（Textual 区分大小写，
提示 "D delete" 实际绑定 d，按提示的大写键无效——REQ-006a 教训推广到
全部面板）② 每个 token 可点击且点击行为正确（install 屏 "a all"/"a none"
动态文案 token 必须匹配，全选态点击 "a none" 取消全选）③ 有提示就有
点击/hover 接入（AudioSettings 补齐）。

特殊键也按冻结规格使用小写 token，不纳入单字母大小写例外。
"""
import asyncio

from rich.cells import cell_len
from textual.events import MouseMove
from textual.widgets import DataTable

import library
from tui import live
from tui.app import GigBuddyApp
from tui.install_screen import PackInstallScreen
from tui.modals import border_hint_label, border_hint_segments
from tui.panels import ChainPanel, DetailPane  # noqa: F401 (import side effects)


def run(coro):
    return asyncio.run(coro)


# 单字母键 token 必须与绑定同为小写；下列惯例写法豁免：
#   ← → ↓ —— 方向指示符，非键名
_ALLOWED_UPPER_WORDS = ("←", "→", "↓", "↑")


def _assert_no_misleading_case(label: str, where: str) -> None:
    for word in label.split():
        stripped = word.strip("[]·")
        if not stripped or stripped in _ALLOWED_UPPER_WORDS:
            continue
        letters = [ch for ch in stripped if ch.isalpha()]
        if not letters:
            continue
        first_letter = letters[0]
        assert not first_letter.isupper(), (
            f"{where}: token {stripped!r} 大写开头与绑定不一致（提示应是 "
            f"{first_letter.lower()}{stripped[1:]}）：{label!r}")


def test_all_border_hints_use_lowercase_key_tokens(monkeypatch, tmp_path):
    """每个面板的右下角提示：单字母键 token 必须小写（与 Textual 绑定
    一致），延续 REQ-006a 对 ChainPanel 的断言到全部面板。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            # 主界面面板
            for selector in ("LibraryPanel", "PresetPanel", "ChainPanel"):
                widget = app.query_one(selector)
                label = border_hint_label(widget)
                _assert_no_misleading_case(label, selector)
            # DetailPane 双模式提示
            _assert_no_misleading_case(
                border_hint_label(app.query_one(DetailPane)), "DetailPane")
            # 模态：input source
            app.action_open_input_source()
            await pilot.pause(0.4)
            box = app.screen.query_one("ModalBox")
            _assert_no_misleading_case(border_hint_label(box), "InputSource")
            await pilot.press("escape")
            await pilot.pause(0.3)
    run(scenario())


def test_install_screen_all_none_token_click_toggles(monkeypatch, tmp_path):
    """install 屏全选态时副标题显示 "a none"：点击该 token 必须取消全选
    （token 与动态文案脱节时静默无效——REQ-024 修复）。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    tone = {"id": 1, "title": "Pack tone", "gear": "amp", "username": "alice",
            "downloads_count": 1, "models": []}

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            screen = PackInstallScreen(tone)
            app.push_screen(screen)
            await pilot.pause(0.4)
            # 注入模型数据并全选（表格行也要填：action_toggle_all 空表直接 return）
            screen._models = [{"id": 1, "name": "a.nam",
                               "architecture": "SlimmableContainer"},
                              {"id": 2, "name": "b.nam",
                               "architecture": "SlimmableContainer"}]
            table = screen.query_one("#pack-table", DataTable)
            for m in screen._models:
                table.add_row("\\[x]", m["name"], m["architecture"],
                              key=str(m["id"]))
            screen._selected = {1, 2}
            screen._load_state = "ready"
            screen._update_status()
            box = app.screen.query_one("ModalBox")
            label = border_hint_label(box)
            assert "a none" in label
            # 点击 "a none" token（动作列表动态匹配当前文案）
            from tui.modals import border_hint_click
            from textual.events import Click
            from rich.style import Style
            from textual.geometry import Offset
            # 找到 "a none" 的屏幕坐标
            from tui.modals import hint_span
            span = hint_span(label, "a none")
            assert span is not None
            label_start = box.region.x + max(
                1, box.region.width - cell_len(label) - 2)
            x = label_start + (span[0] + span[1]) // 2
            y = box.region.bottom - 1
            clicked = border_hint_click(
                box, Click(box, x, y, 0, 0, 1, False, False, False,
                           x, y, Style(), 1),
                screen._border_hint_actions())
            assert clicked, "a none token 必须可点击"
            assert screen._selected == set(), "点击 a none 应取消全选"
            assert "a all" in border_hint_label(box)
            await pilot.press("escape")
            await pilot.pause(0.2)
    run(scenario())


def test_uninstall_screen_u_key_confirms(monkeypatch, tmp_path):
    """REQ-025: uninstall 屏的卸载动作绑定 u 快捷键（提示写 u uninstall），
    u 键触发与 Enter 相同的确认路径。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(
        "tui.uninstall_screen.library.local_uninstall_plan", lambda ids: {
            "tone_ids": ids,
            "models": [{"id": ids[0], "local_path": "/managed/model.nam"}],
            "bytes": 1, "active_paths": [],
            "preset_names": [], "outside_paths": []})
    removed = {}
    monkeypatch.setattr(
        "tui.uninstall_screen.library.local_uninstall_tones",
        lambda ids, allow_preset_references=False: removed.setdefault(
            "r", {"removed": len(ids), "trash_dir": None}))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            from tui.uninstall_screen import LocalUninstallScreen
            screen = LocalUninstallScreen([10])
            app.push_screen(screen)
            await pilot.pause(0.4)
            box = app.screen.query_one("ModalBox")
            label = border_hint_label(box)
            assert "u uninstall" in label, label
            assert "Enter uninstall" not in label
            # u 键 = 确认卸载（与 Enter 同一 _confirm 路径）
            await pilot.press("u")
            await pilot.pause(0.4)
            assert removed.get("r"), "u 键应执行卸载"
            assert screen not in app.screen_stack
            # 点击 token 路径
            from tui.modals import hint_span, border_hint_click
            from rich.cells import cell_len
            from textual.events import Click
            from rich.style import Style
            from textual.geometry import Offset
            screen2 = LocalUninstallScreen([11])
            app.push_screen(screen2)
            await pilot.pause(0.4)
            box = app.screen.query_one("ModalBox")
            label = border_hint_label(box)
            span = hint_span(label, "u uninstall")
            assert span is not None
            label_start = box.region.x + max(
                1, box.region.width - cell_len(label) - 2)
            x = label_start + (span[0] + span[1]) // 2
            y = box.region.bottom - 1
            assert border_hint_click(
                box, Click(box, x, y, 0, 0, 1, False, False, False,
                           x, y, Style(), 1),
                screen2._border_hint_actions()), "u uninstall token 可点击"
            await pilot.pause(0.4)
            assert removed.get("r"), "点击 u uninstall 应执行卸载"
            assert screen2 not in app.screen_stack
    run(scenario())


def test_audio_settings_hint_is_clickable_and_hovers(monkeypatch, tmp_path):
    """AudioSettings 的 "enter close" / "esc close" 补齐点击与 hover
    （REQ-024：有提示就有接入，与其他模态一致）。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            from tui.panels import AudioSettingsScreen
            screen = AudioSettingsScreen([], [], None, None, 512, 48000)
            app.push_screen(screen)
            await pilot.pause(0.4)
            box = app.screen.query_one("ModalBox")
            label = border_hint_label(box)
            assert label == "enter close · esc close"
            # hover：token 高亮（styled Text 单一 span）
            from tui.modals import hint_span
            span = hint_span(label, "enter close")
            label_start = box.region.x + max(
                1, box.region.width - cell_len(label) - 2)
            x = label_start + (span[0] + span[1]) // 2
            y = box.region.bottom - 1
            await pilot._post_mouse_events(
                [MouseMove], widget=box,
                offset=(x - box.region.x, y - box.region.y), button=0)
            await pilot.pause()
            styled = box._border_subtitle
            assert styled.plain == label
            assert len(styled._spans) == 1
            assert (styled._spans[0].start, styled._spans[0].end) == span
            # 点击 "Esc close" → dismiss
            esc_span = hint_span(label, "esc close")
            x2 = label_start + (esc_span[0] + esc_span[1]) // 2
            from tui.modals import border_hint_click
            from textual.events import Click
            from rich.style import Style
            from textual.geometry import Offset
            assert border_hint_click(
                box, Click(box, x2, y, 0, 0, 1, False, False, False,
                           x2, y, Style(), 1),
                screen._border_hint_actions())
            await pilot.pause(0.2)
            assert screen not in app.screen_stack
    run(scenario())
