"""REQ-027 回归：g·G / m·M / q·Q 分隔点点击恢复默认值。

点击点 = 该参数恢复默认值（走 _set_chain_param 应用路径）；点 hover 高亮（与
g/G 半字风格一致，别处 hover 不误亮）；不触发步进/编辑/长按。
"""
import asyncio

from textual.events import MouseMove

from tui.app import GigBuddyApp
from tui.live import CHAIN_PARAMETER_DEFAULTS
from tui.panels import ChainPanel
import library


def run(coro):
    return asyncio.run(coro)


def setup_chain(monkeypatch, tmp_path, **overrides):
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    writes: list[dict] = []
    state = {"chain": {"gain": 0.8, "master": 0.8, "quality": 1.0, **overrides}}

    def read():
        return dict(state["chain"])

    def write(cfg):
        state["chain"] = dict(cfg)
        writes.append(dict(cfg))

    monkeypatch.setattr("tui.app.live.read_chain", read)
    monkeypatch.setattr("tui.app.live.write_chain", write)
    return state, writes


def dot_x(params, index: int) -> int:
    """参数 index（0 gain / 1 master / 2 quality）分隔点局部 x（+padding）。"""
    return params._dot_spans[index][1] + 1


def send_move(params, x):
    params.on_mouse_move(
        MouseMove(params, x, 0, 0, 0, 1, False, False, False, x, 0, None))


def test_click_dot_resets_each_parameter(monkeypatch, tmp_path):
    """三个点分别点击 → 对应参数恢复协议默认值（链文件 + UI）。"""
    state, writes = setup_chain(monkeypatch, tmp_path, quality=0.8)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            for index, key in ((0, "gain"), (1, "master"), (2, "quality")):
                await pilot.click(params, offset=(dot_x(params, index), 0))
                await pilot.pause(0.2)
                expected = CHAIN_PARAMETER_DEFAULTS[key]
                assert writes[-1][key] == expected, writes[-1]
                assert state["chain"][key] == expected
                assert f"{expected:.2f}" in str(params.render())

            assert len(writes) == 3, "三点各写一次链"

    run(scenario())


def test_dot_renders_between_lower_and_upper(monkeypatch, tmp_path):
    """渲染顺序回归：点必须夹在 g·G / m·M / q·Q 中间（曾渲染成 gG ·）。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            plain = panel.params.render().plain
            for pair in ("g · G", "m · M", "q · Q"):
                assert pair in plain, f"{pair!r} not in {plain!r}"
            # 不得出现点跑末尾的旧渲染
            assert "gG ·" not in plain
            assert "mM ·" not in plain
            assert "qQ ·" not in plain

    run(scenario())


def test_dot_click_restores_default_not_step(monkeypatch, tmp_path):
    """点点击恢复默认值，不是基础步进；也不进入编辑。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            await pilot.click(params, offset=(dot_x(params, 0), 0))
            await pilot.pause(0.2)
            assert writes[-1]["gain"] == CHAIN_PARAMETER_DEFAULTS["gain"]
            assert params._editing is None, "点点击不得进入编辑态"

    run(scenario())


def test_dot_hover_highlights_only_itself(monkeypatch, tmp_path):
    """hover 点自身高亮（与 g/G 风格一致）；hover g 或空白时点不亮。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    def highlighted(rendered, char_pos):
        """char_pos 字符是否有反色高亮 span（on $accent 标记才算，dim 不算）。"""
        return any(span.start <= char_pos < span.end
                   and "on $accent" in span.style
                   for span in rendered.spans)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params
            text = params.render()
            # 渲染文本（markup 已解析）里的三个点位置
            dots = [i for i, ch in enumerate(text.plain) if ch == "·"]
            assert len(dots) == 3, text.plain

            # hover 第一个点 → 该点高亮，其余点不亮
            send_move(params, dot_x(params, 0))
            text = params.render()
            assert highlighted(text, dots[0]), "hover 的点必须高亮"
            assert not highlighted(text, dots[1])
            assert not highlighted(text, dots[2])
            # hover 第二个点 → 仅第二个点高亮
            send_move(params, dot_x(params, 1))
            text = params.render()
            assert highlighted(text, dots[1])
            assert not highlighted(text, dots[0])
            # hover g token → 所有点不亮
            send_move(params, params._spans[0][1] + 1)
            text = params.render()
            assert all(not highlighted(text, d) for d in dots), \
                "hover g 时点不得误亮"
            # hover 空白 → 点不亮
            send_move(params, 0)
            text = params.render()
            assert all(not highlighted(text, d) for d in dots)

    run(scenario())


def test_dot_press_does_not_long_press(monkeypatch, tmp_path):
    """按住点 >350ms 也只恢复一次（点不进长按步进）。"""
    state, writes = setup_chain(monkeypatch, tmp_path, quality=0.8)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params
            from textual.events import MouseDown, MouseUp

            dx = dot_x(params, 2)  # quality 点
            down = MouseDown(params, dx, 0, 0, 0, 1, False, False, False, dx, 0, None)
            params.on_mouse_down(down)
            await pilot.pause(0.4)  # >350ms 长按阈值
            up = MouseUp(params, dx, 0, 0, 0, 1, False, False, False, dx, 0, None)
            params.on_mouse_up(up)
            click = params._press_cancelled or None
            from textual.events import Click
            params.on_click(Click(params, dx, 0, 0, 0, 1, False, False, False,
                                  dx, 0, None))
            await pilot.pause(0.2)

            assert len(writes) == 1, f"长按点只能恢复一次: {writes}"
            assert writes[-1]["quality"] == CHAIN_PARAMETER_DEFAULTS["quality"]

    run(scenario())


def test_dot_reset_after_manual_edit_path_consistency(monkeypatch, tmp_path):
    """恢复默认与手动编辑/步进写同一条链（顺序操作互不干扰）。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            # 恢复默认 → 点按步进 → 再恢复默认
            await pilot.click(params, offset=(dot_x(params, 0), 0))
            await pilot.pause(0.15)
            await pilot.click(params, offset=(params._spans[1][1] + 1, 0))  # G
            await pilot.pause(0.15)
            assert writes[-1]["gain"] == 1.5
            await pilot.click(params, offset=(dot_x(params, 0), 0))
            await pilot.pause(0.15)
            assert writes[-1]["gain"] == CHAIN_PARAMETER_DEFAULTS["gain"]
            assert state["chain"]["gain"] == CHAIN_PARAMETER_DEFAULTS["gain"]

    run(scenario())
