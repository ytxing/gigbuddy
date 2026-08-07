"""REQ-021 回归：ChainParams 参数值手动填写。

单击数值区域进入编辑（预填当前显示值，光标在末尾）；backspace 删、
数字/小数点追加、Enter 应用（越界 clamp + notify）、Esc 或失焦取消；
编辑态吞掉全局步进/播放/删除/切换键；与 REQ-007 点按/长按共存。
"""
import asyncio

from tui.app import GigBuddyApp
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


def value_x(params, index: int) -> int:
    """参数 index（0 gain / 1 master / 2 quality）数值区域局部 x（+padding）。"""
    return params._value_spans[index][1] + 1


async def clear_and_type(pilot, params, text: str) -> None:
    """删空预填值后逐字符输入。"""
    for _ in range(len(params._edit_text)):
        await pilot.press("backspace")
    for char in text:
        await pilot.press(char)


def test_click_value_enters_edit_and_enter_applies(monkeypatch, tmp_path):
    """单击数值 → 编辑态（显示光标）→ 输入 → Enter 应用写链。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params
            gx = value_x(params, 0)  # gain 值

            await pilot.click(params, offset=(gx, 0))
            await pilot.pause(0.1)  # focus 是异步 call_later
            assert params._editing == 0
            assert "▌" in str(params.render())

            await clear_and_type(pilot, params, "0.55")
            await pilot.press("enter")
            await pilot.pause(0.2)

            assert params._editing is None, "应用后退出编辑"
            assert writes[-1]["gain"] == 0.55, writes[-1]
            assert state["chain"]["gain"] == 0.55
            assert "0.55" in str(params.render())

    run(scenario())


def test_escape_cancels_without_writing(monkeypatch, tmp_path):
    """Esc 取消：编辑文本丢弃，链不变。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            await clear_and_type(pilot, params, "2.0")
            await pilot.press("escape")
            await pilot.pause(0.2)

            assert params._editing is None
            assert writes == [], "取消不得写链"
            assert state["chain"]["gain"] == 0.8

    run(scenario())


def test_out_of_range_clamps_and_notifies(monkeypatch, tmp_path):
    """越界 clamp 到边界并 notify：gain 11→10、master 20→10、quality 2→1。

    值域（REQ-027 用户定案）：gain/master 0–10（NAM ±20dB 线性刻度）、
    quality 0–1。
    """
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params
            notes = []
            app.notify = lambda message, **kw: notes.append(str(message))

            for index, text, expected in ((0, "11", 10.0), (1, "20", 10.0),
                                          (2, "2", 1.0)):
                await pilot.click(params, offset=(value_x(params, index), 0))
                await pilot.pause(0.1)
                await clear_and_type(pilot, params, text)
                await pilot.press("enter")
                await pilot.pause(0.2)
                assert writes[-1][["gain", "master", "quality"][index]] == expected
                assert notes, "越界必须 notify"
                notes.clear()

    run(scenario())


def test_negative_value_clamps_to_zero(monkeypatch, tmp_path):
    """负值（- 无法输入，但 0 下限 clamp）：gain 0 → 0.0。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            await clear_and_type(pilot, params, "0")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert writes[-1]["gain"] == 0.0

    run(scenario())


def test_max_two_decimals_and_length_cap(monkeypatch, tmp_path):
    """小数最多 2 位、总长最多 8 字符：超限输入被拒绝。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            await clear_and_type(pilot, params, "0.555")
            assert params._edit_text == "0.55", "第 3 位小数必须被拒绝"
            # 再追加到长度上限
            await clear_and_type(pilot, params, "0.123456789")
            assert len(params._edit_text) <= 8, "长度必须受限"

    run(scenario())


def test_invalid_chars_and_double_dot_rejected(monkeypatch, tmp_path):
    """非数字字符与多余小数点拒绝（不进入编辑文本）。"""
    state, writes = setup_chain(monkeypatch, tmp_path, master=0.7)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            await pilot.click(params, offset=(value_x(params, 1), 0))
            await pilot.pause(0.1)
            await clear_and_type(pilot, params, "0.8")
            await pilot.press("a")   # 非法字符
            await pilot.press("x")
            await pilot.press(".")   # 已有点，拒绝
            assert params._edit_text == "0.8"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert writes[-1]["master"] == 0.8

    run(scenario())


def test_edit_coexists_with_click_and_long_press(monkeypatch, tmp_path):
    """编辑应用后点按/长按照常；编辑态中点击 token 先取消编辑不步进。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params
            G_x = params._spans[1][1] + 1  # G token

            # 编辑应用后：单击 token 使用 gain 的 0.05 鼠标步长
            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            await clear_and_type(pilot, params, "0.55")
            await pilot.press("enter")
            await pilot.pause(0.2)
            await pilot.click(params, offset=(G_x, 0))
            await pilot.pause(0.2)
            assert writes[-1]["gain"] == 0.60, writes[-1]

            # 编辑态中点击 token：取消编辑，不步进
            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            await clear_and_type(pilot, params, "1.0")
            n = len(writes)
            await pilot.click(params, offset=(G_x, 0))
            await pilot.pause(0.2)
            assert len(writes) == n, "编辑态点击不得步进"
            assert state["chain"]["gain"] == 0.60

    run(scenario())


def test_edit_swallows_global_step_keys(monkeypatch, tmp_path):
    """编辑态中 g/G/m/q 等全局绑定被吞：不步进、编辑文本不丢。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            await clear_and_type(pilot, params, "0.55")
            await pilot.press("g")
            await pilot.press("m")
            await pilot.press("q")
            await pilot.pause(0.2)
            assert params._edit_text == "0.55", "编辑文本不得被全局键干扰"
            assert writes == [], "编辑态全局步进键不得写链"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert writes[-1]["gain"] == 0.55

    run(scenario())


def test_edit_cursor_blinks_and_hides_when_dim(monkeypatch, tmp_path):
    """REQ-028：编辑态光标可见（▌），0.5s 闪烁（暗态空格占位保持行宽）。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params
            plain = lambda: params.render().plain

            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            assert params._editing == 0
            assert "▌" in plain(), "编辑态光标必须可见"
            assert params._cursor_timer is not None, "编辑态必须启动闪烁 timer"
            # 停掉真实 timer，避免手动 toggle 与 0.5s 闪烁竞态
            params._stop_cursor()
            # 闪烁机制：暗态 → 空格占位（行宽不变，token 不位移）
            params._toggle_cursor()
            assert "▌" not in plain(), "暗态不得显示光标"
            assert "0.80 " in plain(), "空格占位保持宽度"
            params._toggle_cursor()
            assert "▌" in plain(), "亮态恢复光标"
            # 输入即亮
            params._toggle_cursor()  # 暗
            await clear_and_type(pilot, params, "0.55")
            assert "▌" in plain(), "输入后光标即亮"

    run(scenario())


def test_no_cursor_left_after_exit_paths(monkeypatch, tmp_path):
    """REQ-028 硬性回归：Enter/Esc/失焦三路径退出后渲染零光标残留。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params
            plain = lambda: params.render().plain

            # Esc 退出
            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            assert "▌" in plain()
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert "▌" not in plain(), "Esc 退出后不得残留光标"

            # Enter 退出
            await pilot.click(params, offset=(value_x(params, 0), 0))
            await pilot.pause(0.1)
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert "▌" not in plain(), "Enter 应用后不得残留光标"

            # 失焦退出（点击链面板）
            await pilot.click(params, offset=(value_x(params, 1), 0))
            await pilot.pause(0.1)
            assert "▌" in plain()
            await pilot.click(app.query_one(ChainPanel))
            await pilot.pause(0.2)
            assert "▌" not in plain(), "失焦取消后不得残留光标"

    run(scenario())


def test_blur_cancels_edit(monkeypatch, tmp_path):
    """编辑中焦点被拿走（点击他处）：取消编辑，不应用。"""
    state, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(state["chain"])
            await pilot.pause()
            params = panel.params

            await pilot.click(params, offset=(value_x(params, 2), 0))
            await pilot.pause(0.1)
            await clear_and_type(pilot, params, "0.5")
            assert params._editing == 2
            # 点击别处（链面板）→ 焦点转移 → blur 取消编辑
            await pilot.click(app.query_one(ChainPanel))
            await pilot.pause(0.2)
            assert params._editing is None
            assert writes == [], "失焦取消不得写链"

    run(scenario())
