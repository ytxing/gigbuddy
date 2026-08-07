"""REQ-007: ChainParams 参数行鼠标点按/长按步进。

点按（按下即响应）= 该参数基础步长；继续按住时按时长分段加速重复
同一基础步长；释放或移出 token/面板即停止。
键盘 g/G/m/M/q/Q 绑定不在本文件覆盖（见 test_tui_keyboard.py）。

事件派发说明：pilot.mouse_down/mouse_up 直接走 screen._forward_event 绕
过 App 层的 Click 合成，且 headless driver 的事件处理有排队延迟。因此
交互测试改为同步构造事件调用 widget handler（事件时序确定），长按定时器
仍是真实 asyncio 计时（run_test 内）。
"""
import asyncio

from textual.events import Click, MouseDown, MouseMove, MouseUp

from tui.app import GigBuddyApp
from tui.panels import ChainPanel
import library


def run(coro):
    return asyncio.run(coro)


def mouse_event(cls, params, x, y=0):
    """构造局部坐标鼠标事件（widget 局部 x/y；screen 坐标无关紧要）。"""
    return cls(params, x, y, 0, 0, 1, False, False, False, x, y, None)


def send_down(params, x):
    params.on_mouse_down(mouse_event(MouseDown, params, x))


def send_move(params, x):
    params.on_mouse_move(mouse_event(MouseMove, params, x))


def send_up(params, x=0):
    params.on_mouse_up(mouse_event(MouseUp, params, x))


def send_click(params, x):
    params.on_click(mouse_event(Click, params, x))


def setup_chain(monkeypatch, tmp_path, **overrides):
    """隔离 DB/链文件并记录每次 write_chain 的完整 cfg。"""
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
    return state["chain"], writes


def token_x(params, index: int) -> int:
    """token 的 widget 局部 x（.chain-params padding: 0 1 → 内容列 + 1）。"""
    return params._spans[index][1] + 1


def test_click_steps_by_base_step(monkeypatch, tmp_path):
    """单击 g/G 每次步进 gain 的基础步长，且不触发长按。"""
    chain, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(chain)
            await pilot.pause()
            params = panel.params
            g_x, G_x = token_x(params, 0), token_x(params, 1)

            # G（上升）：0.8 → 1.3
            await pilot.click(params, offset=(G_x, 0))
            await pilot.pause()
            assert writes[-1]["gain"] == 1.3, writes[-1]

            # g（下降）：1.3 → 0.8
            await pilot.click(params, offset=(g_x, 0))
            await pilot.pause()
            assert writes[-1]["gain"] == 0.8, writes[-1]

            # 每次单击恰好一次写链
            assert len(writes) == 2, writes

    run(scenario())


def test_short_hold_then_release_is_single_base_step(monkeypatch, tmp_path):
    """按下 <200ms 即释放：只步进一次基础步长（不进重复）。"""
    chain, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(chain)
            await pilot.pause()
            params = panel.params
            m_x = token_x(params, 2)  # m（master 下降）

            send_down(params, m_x)
            await pilot.pause(0.1)  # 仍处于 200ms 初始保持窗口
            send_up(params, m_x)
            send_click(params, m_x)
            await pilot.pause(0.2)  # 给潜在重复步进留时间

            assert len(writes) == 1, writes
            assert writes[-1]["master"] == 0.3, writes[-1]

    run(scenario())


def test_long_press_repeats_by_0_5_and_stops_on_release(monkeypatch, tmp_path):
    """长按 G：以 0.5 粒度连续步进（步数明显多于单次），释放即停。"""
    chain, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(chain)
            await pilot.pause()
            params = panel.params
            G_x = token_x(params, 1)

            send_down(params, G_x)
            # 超过 200ms 后继续按住，让分段重复跑几拍
            await pilot.pause(0.4)
            await pilot.pause(0.30)
            steps_held = len(writes)
            gain_held = writes[-1]["gain"]
            send_up(params, G_x)
            # 真实路径 mouse_up 后 App 会合成 Click —— 按下已响应，合成 click 必须被丢弃
            send_click(params, G_x)
            await pilot.pause(0.25)

            # 长按必须明显多于一次（≥3 步），且每步 0.5（鼠标步长）
            # （第 index+1 步 = 0.8 + 0.5*(index+1)）
            assert steps_held >= 3, writes
            assert gain_held == round(0.8 + 0.5 * steps_held, 2), (
                f"held gain {gain_held} != 0.8 + 0.5*{steps_held}")
            for index, cfg in enumerate(writes):
                assert abs(cfg["gain"] - 0.8 - 0.5 * (index + 1)) < 1e-6, cfg

            # 释放（及随后的合成 click）不再步进：写链次数与值都冻结
            assert len(writes) == steps_held, writes
            assert writes[-1]["gain"] == gain_held

    run(scenario())


def test_long_press_stops_when_moving_off_token(monkeypatch, tmp_path):
    """长按中移出 token（仍在面板内）→ 立即停止步进；释放的 click 被丢弃。"""
    chain, writes = setup_chain(monkeypatch, tmp_path, quality=0.8)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(chain)
            await pilot.pause()
            params = panel.params
            G_x = token_x(params, 1)  # G（gain 上升）

            send_down(params, G_x)
            await pilot.pause(0.4)
            await pilot.pause(0.2)
            assert len(writes) >= 3, writes
            n_when_held = len(writes)

            # 按住状态移到空白（左 padding 列，x=0）
            send_move(params, 0)
            await pilot.pause(0.25)
            assert len(writes) == n_when_held, "移出 token 后不得继续步进"

            # 在空白处释放：合成 click 命中同 widget 但按下已取消 → 不步进
            send_up(params, 0)
            send_click(params, 0)
            await pilot.pause(0.2)
            assert len(writes) == n_when_held, writes

    run(scenario())


def test_long_press_clamps_quality_at_zero(monkeypatch, tmp_path):
    """长按 q（quality 下降）到达 0 后停住，不得越界为负。"""
    chain, writes = setup_chain(monkeypatch, tmp_path, quality=0.05)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(chain)
            await pilot.pause()
            params = panel.params
            q_x = token_x(params, 4)  # q（quality 下降）

            send_down(params, q_x)
            await pilot.pause(0.4)
            await pilot.pause(0.3)
            send_up(params, q_x)
            send_click(params, q_x)
            await pilot.pause(0.2)

            assert writes[-1]["quality"] == 0.0, writes[-1]
            for cfg in writes:
                assert cfg["quality"] >= 0.0, cfg

    run(scenario())


def test_click_m_uses_mouse_step(monkeypatch, tmp_path):
    """鼠标 master 单击使用 0.5 步长。"""
    chain, writes = setup_chain(monkeypatch, tmp_path)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(200, 40)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = dict(chain)
            await pilot.pause()
            params = panel.params
            M_x = token_x(params, 3)  # M（master 上升）

            await pilot.click(params, offset=(M_x, 0))
            await pilot.pause()
            assert writes[-1]["master"] == 1.3, writes[-1]

    run(scenario())
