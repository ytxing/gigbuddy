"""GigBuddy TUI panels: chain (focusable nodes) / tone detail pane / level meter."""
import asyncio
from functools import partial
from itertools import product
import sys
import threading
from pathlib import Path

from rich.cells import cell_len
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from typing import Callable

from textual.events import (Blur, Key, Leave, MouseDown, MouseEvent, MouseMove,
                            MouseUp, Unmount)
from textual.widgets import DataTable, Select, Static

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402
import tone3000  # noqa: E402

from . import live  # noqa: E402
from .marquee import (MarqueeBar, ellipsis_window, marquee_window)  # noqa: E402
from .metadata import (SelectableStatic, description_only, metadata_table,
                       preset_metadata_table, signed_fixed, theme_colors)  # noqa: E402
from .modals import (ClickSelectTable, GigBuddyModal, ModalBox,
                     border_hint_action_token, border_hint_click,
                     border_hint_hit, hint_span,
                     refresh_border_hint_layout, set_border_hint_hover,
                     set_border_hint_layout)  # noqa: E402
from .library_panel import VerifiedAuthor
from .selection import NonSelectableStatic  # noqa: E402


def _escape(text: str) -> str:
    """Escape EVERY '[' for Textual markup.

    rich.markup.escape only escapes brackets that look like style tags, so a
    TONE3000 title like "VOX AC30 CH [Hyper Accuracy+]" — or one clipped
    mid-bracket — leaks through and swallows the following closing tag.
    """
    return text.replace("[", "\\[")


def _single_line(text: str) -> str:
    """单行化：换行/制表/连续空白 → 单个空格，首尾 trim。

    REQ-026：作者简介（bio）含大量换行/多空格，放进单行 banner
    （marquee 滚动）时难看得要命——展示前压成一行。详情页多行文本区
    保留原始换行（多行可读性更好），不做此处理。
    """
    return " ".join(str(text).split())


class NodeWidget(Static):
    """Chain node (AMP / IR): click to focus, then ↑/↓ steps through the sibling
    models in the same tone folder (engine hot-swaps via live_chain.json).

    Two-line pedal readout: tone title on top, the exact model file below.
    """

    can_focus = True
    ALLOW_SELECT = False

    class SwitchRequested(Message):
        def __init__(self, kind: str, direction: int) -> None:
            super().__init__()
            self.kind = kind  # "amp" | "ir"
            self.direction = direction  # +1 next / -1 prev

    class DeleteRequested(Message):
        def __init__(self, kind: str) -> None:
            super().__init__()
            self.kind = kind  # "amp" | "cab"

    BINDINGS = [
        Binding("up", "switch_up", "prev model", show=False),
        Binding("down", "switch_down", "next model", show=False),
        Binding("d", "delete_node", "delete (unload)", show=False),
    ]

    def __init__(self, kind: str, label: str = "—") -> None:
        self.kind = kind.lower()
        self.bypassed = False
        self.title: str | None = None
        super().__init__(classes=f"chain-node chain-node-{self.kind}")
        self.label = label
        self._marquee_offset = 0
        self._marquee_timer = None
        # 边框标题在 on_mount 设：子类（INPUT）可能在 __init__ 里先备好
        # 自己的属性（is_file 等），_update_border 覆写依赖它们。

    def _display_limit(self) -> int:
        return max(12, (self.size.width or 24) - 8)

    def _stop_marquee(self) -> None:
        if self._marquee_timer is not None:
            self._marquee_timer.stop()
            self._marquee_timer = None

    def _maybe_animate(self, *, focus_event: bool = False) -> None:
        self._stop_marquee()
        if not self.is_mounted or (not self.has_focus and not focus_event):
            return
        limit = self._display_limit()
        values = [value for value in (self.title, self.label) if value]
        if any(cell_len(value) > limit for value in values):
            self._marquee_timer = self.set_interval(0.12, self._advance_marquee)

    def _advance_marquee(self) -> None:
        self._marquee_offset += 1
        self.refresh()

    def _window(self, text: str, width: int) -> str:
        if self.has_focus and cell_len(text) > width:
            return marquee_window(text, width, self._marquee_offset)
        return ellipsis_window(text, width)

    def on_mount(self) -> None:
        self._update_border()   # REQ-043：边框标题（type + 状态灯）
        self._maybe_animate()

    def on_focus(self, event) -> None:
        self._marquee_offset = 0
        # Textual dispatches Focus before the final focus state is visible to
        # ``has_focus``; start once for the event, then re-check after refresh.
        self._maybe_animate(focus_event=True)
        self.call_after_refresh(self._maybe_animate)
        self.refresh()

    def on_blur(self, event) -> None:
        self._stop_marquee()
        self._marquee_offset = 0
        self.refresh()

    def on_resize(self) -> None:
        self._maybe_animate()
        self.refresh()

    def _state_lamp(self) -> str:
        """状态灯三态（REQ-043 移入边框标题）：● 绿 = 工作（有模型/IR）·
        ● 红 + BYPASS 字样 = bypass（双击，内容保留显示）· ○ 灰 = 缺位
        （AMP/CAB 未加载）。静音走底部 InterfaceBar。"""
        label = self.label or "—"
        if self.bypassed:
            return "[bold $error]●[/]"
        if label not in {"—", "NONE"}:
            return "[bold $success]●[/]"
        return "[bold $state-idle]○[/]"

    def _update_border(self) -> None:
        """REQ-043：type + 状态灯放到行框左上角边框标题，不挤占框内空间。

        边框画在行容器（.chain-node-row Horizontal，border: round）上，
        node 自身 border: none——标题必须设到父容器才渲染。
        """
        parent = self.parent
        if parent is not None:
            parent.border_title = f"{self._state_lamp()} {self.kind.upper()}"

    def render(self) -> str:
        limit = self._display_limit()

        label = self.label or "—"
        active = label not in {"—", "NONE"}
        # BYPASS 字样（双击时显示，红色与状态灯同色系）；内容保留显示。
        bypass_tag = "[b $error]BYPASS[/] " if self.bypassed else ""
        label_limit = max(limit - len("BYPASS "), 1) if self.bypassed else limit
        # Escape user-provided names so brackets in a TONE3000 title/filename
        # cannot be interpreted as Rich markup.
        if self.title:
            return (
                f"[b]{_escape(self._window(self.title, limit))}[/]\n"
                f"{bypass_tag}"
                f"[dim]{_escape(self._window(label, label_limit))}[/]"
            )
        value_style = "bold" if active else "dim"
        return (f"{bypass_tag}"
                f"[{value_style}]{_escape(self._window(label, label_limit))}[/]")

    def set_label(self, label: str) -> None:
        if self.label == label:
            return
        self.label = label
        self._marquee_offset = 0
        self._update_border()   # 缺位/工作状态变化 → 边框灯更新
        self._maybe_animate()
        self.refresh()

    def set_title(self, title: str | None) -> None:
        if self.title == title:
            return
        self.title = title
        self._marquee_offset = 0
        self._maybe_animate()
        self.refresh()

    def set_bypassed(self, bypassed: bool) -> None:
        """Mark the node as bypassed without touching its displayed content."""
        self.bypassed = bypassed
        self._update_border()
        self.refresh()

    # ↑/↓ 在 AMP/CAB 节点上换同 pack 的模型；←/→ 留给 detail 视图切换
    # （description/selection），避免节点截获 detail 的左右切换。
    def action_switch_up(self) -> None:
        if self.kind in ("amp", "cab"):
            self.post_message(self.SwitchRequested(self.kind, -1))

    def action_switch_down(self) -> None:
        if self.kind in ("amp", "cab"):
            self.post_message(self.SwitchRequested(self.kind, +1))

    def action_delete_node(self) -> None:
        if self.kind in ("amp", "cab"):
            self.post_message(self.DeleteRequested(self.kind))


class InputNodeWidget(NodeWidget):
    """Chain input-source node (chain head, before AMP): instrument device or
    dry-file playback.

    Single-line readout: the file name, plain-text playback state (12s loop),
    and a clickable PLAY block pinned to the row's right edge — its hover
    highlight matches the width/alignment of the switch-arrow blocks on the
    AMP/CAB rows below. Focused keys: space=play/pause, s=stop, l=loop.
    """

    class PlaybackRequested(Message):
        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action  # "toggle" | "stop" | "loop"

    class SourceRequested(Message):
        """Enter on the focused INPUT node opens the input-source picker."""

    BINDINGS = [
        Binding("space", "playback_toggle", "play/pause", show=False),
        Binding("s", "playback_stop", "stop", show=False),
        Binding("l", "playback_loop", "loop", show=False),
        Binding("enter", "open_source", "input source", show=True),
    ]

    def action_open_source(self) -> None:
        self.post_message(self.SourceRequested())

    # PLAY 块宽度 = 下方箭头切换按钮的 hover 块宽度（奇数内容宽，右缘对齐）。
    PLAY_W = 9

    def __init__(self) -> None:
        super().__init__("INPUT", "instrument")
        self.is_file = False
        self.play_state = live.PLAY_STOPPED
        self.play_pos = 0.0
        self.play_loop = False
        self._play_hover = False
        self._node_hover = False  # 悬停在左侧文本区（PLAY 块之外）
        self._left_len = 0  # 行首文本的可视宽度（PLAY 块坐标命中用）
        self._update_border()

    def _update_border(self) -> None:
        """REQ-043：INPUT 灯随播放状态（playing=绿 ● / 停止=灰 ○）。"""
        active = self.is_file and self.play_state != live.PLAY_STOPPED
        lamp = ("[bold $success]●[/]" if active
                else "[bold $state-idle]○[/]")
        parent = self.parent
        if parent is not None:
            parent.border_title = f"{lamp} INPUT"

    def set_instrument(self, device: str) -> None:
        self.is_file = False
        self.set_title(None)
        self.set_label(device or "default device")

    def set_file(self, path: str) -> None:
        self.is_file = True
        # INPUT is a chain row, not a section heading: the spec requires the
        # selected file name without a "Dry file" prefix.
        self.set_title(None)
        self.set_label(live.short_name(path))

    def set_playback(self, state: str, pos_sec: float, loop: bool) -> None:
        self.play_state = state
        self.play_pos = pos_sec
        self.play_loop = loop
        self._update_border()   # 播放灯（边框）跟随状态
        self.refresh()

    def _play_block(self) -> str:
        """Right-pinned PLAY/PAUSE block: fixed PLAY_W cells so the hover
        highlight lines up with the switch-arrow blocks below."""
        word = "PAUSE" if self.play_state == live.PLAY_PLAYING else "PLAY"
        pad = self.PLAY_W - len(word)
        text = " " * (pad // 2) + word + " " * (pad - pad // 2)
        if self._play_hover:
            return f"[b $background on $accent]{text}[/]"
        return f"[b]{text}[/]"

    def _play_span(self) -> tuple[int, int]:
        """PLAY 块实际显示区间（相对 widget 内容区 x）。

        render 里 PLAY 块右对齐贴节点右缘：起点 = max(左缘, 宽 - PLAY_W)，
        命中范围必须与显示位置一致，否则点击命中与显示漂移。
        """
        width = self.size.width or self._display_limit()
        start = max(self._left_len, width - self.PLAY_W)
        return start, start + self.PLAY_W

    def on_click(self, event: MouseEvent) -> None:
        if not self.is_file:
            return  # 乐器模式没有 PLAY 块，旧 _left_len 会误命中
        span = self._play_span()
        if span[0] <= event.x < span[1]:
            event.stop()
            self.post_message(self.PlaybackRequested("toggle"))

    def on_mouse_move(self, event: MouseMove) -> None:
        if not self.is_file:
            if self._play_hover or self._node_hover:
                self._play_hover = False
                self._node_hover = False
                self.refresh()
            return
        span = self._play_span()
        hovered = span[0] <= event.x < span[1]
        node_hover = not hovered
        if hovered != self._play_hover or node_hover != self._node_hover:
            self._play_hover = hovered
            self._node_hover = node_hover
            self.refresh()

    def on_leave(self, event: Leave) -> None:
        if self._play_hover or self._node_hover:
            self._play_hover = False
            self._node_hover = False
            self.refresh()

    def render(self) -> str:
        limit = self._display_limit()
        # 选中高亮（以 AMP 行为准）画在文本层、只盖左侧区域——行尾 PLAY 块
        # 保持独立 hover，不被浅色整块盖住（CSS 侧已对 INPUT 节点关掉
        # background，见 app.py）。
        hl = "[on $panel-lighten-1]" if (self.has_focus or self._node_hover) else ""
        end = "[/]" if hl else ""

        if self.is_file:
            tail, tail_plain = "", ""
            if self.play_state != live.PLAY_STOPPED:
                parts = [f"{self.play_pos:.0f}s"]
                if self.play_loop:
                    parts.append("loop")
                tail = " [dim]" + " ".join(parts) + "[/]"
                tail_plain = " " + " ".join(parts)
            # 文件名与行尾 PLAY 块之间按剩余宽度分配：PLAY 块固定 9 宽并
            # 右对齐贴节点右缘 —— 与下方 AMP/CAB 行的箭头切换列右缘对齐。
            # REQ-043：INPUT 类型与灯已移入边框标题，框内只有文件名。
            label = self._window(
                self.label, max(limit - self.PLAY_W - len(tail_plain), 1))
            left = f"[b]{_escape(label)}[/]{tail}"
            self._left_len = cell_len(label) + len(tail_plain)
            width = self.size.width or limit
            gap = max(0, width - self._left_len - self.PLAY_W)
            return f"{hl}{left}{' ' * gap}{end}{self._play_block()}"
        return (
            f"{hl}[b]Instrument[/] "
            f"[dim]{_escape(self._window(self.label, limit))}[/]{end}")

    def action_playback_toggle(self) -> None:
        self.post_message(self.PlaybackRequested("toggle"))

    def action_playback_stop(self) -> None:
        self.post_message(self.PlaybackRequested("stop"))

    def action_playback_loop(self) -> None:
        self.post_message(self.PlaybackRequested("loop"))


class NodeSwitchButton(Static):
    """Wide flat click target beside a chain node for stepping sibling models.

    ▲ steps to the previous model in the tone folder, ▼ to the next. The pair
    sits in a .chain-switch-col next to the pedal-style node, vertically
    centered; clicks are routed by App.on_click via the widget id.
    """

    ALLOW_SELECT = False

    def __init__(self, kind: str, direction: int) -> None:
        self.kind = kind.lower()
        self.direction = direction
        # 线条箭头与键位一一对应：AMP/CAB 行都是 ↑/↓（上按钮 ↑ prev ·
        # 下按钮 ↓ next）——CAB 键盘切换同样走 ↑/↓（←/→ 留给 detail
        # 双模式切换，CAB 按钮不再用 ←/→）。
        arrow = "↑" if direction > 0 else "↓"
        arrow_name = "up" if direction > 0 else "down"
        super().__init__(f"[bold]{arrow}[/]",
                         id=f"chain-{self.kind}-{arrow_name}",
                         classes="chain-switch-btn")


class ChainParams(Static):
    """Plain-text chain controls with clickable keyboard-hint keys.

    Each hint is a lowercase/uppercase pair split by a dot (g·G): the two
    halves highlight and click independently — lowercase steps down, uppercase
    steps up — so the hover shows exactly the key that will fire.

    Mouse interaction uses each parameter's frozen base step. Holding a token
    for 350ms repeats that same step every 100ms. Releasing, moving off the
    pressed token, or leaving the widget stops the repeat. Keyboard bindings
    (g/G/m/M/q/Q) remain equivalent to the mouse path.

    Manual entry (REQ-021): clicking a parameter value (the number) enters
    edit mode — digits/backspace edit at the end, Enter applies through the
    same chain-write path as the key tokens, Esc (or clicking anywhere /
    losing focus) cancels. Values are clamped to their semantic range and
    limited to 2 decimals (matching the display). While editing, the global
    step/playback bindings are swallowed by this widget's BINDINGS.
    """

    ALLOW_SELECT = False
    can_focus = True
    FOCUS_ON_CLICK = False  # 点击 token 不抢节点焦点；仅单击值区域进编辑时聚焦

    # 编辑态拦截全局绑定（g/G/m/M/q/Q 步进、space/s/l 播放、d 删除、↑/↓ 切换）
    BINDINGS = [
        Binding("g", "edit_guard", "gain -", show=False),
        Binding("G", "edit_guard", "gain +", show=False),
        Binding("m", "edit_guard", "master -", show=False),
        Binding("M", "edit_guard", "master +", show=False),
        Binding("q", "edit_guard", "quality -", show=False),
        Binding("Q", "edit_guard", "quality +", show=False),
        Binding("space", "edit_guard", "play/pause", show=False),
        Binding("s", "edit_guard", "stop", show=False),
        Binding("l", "edit_guard", "loop", show=False),
        Binding("d", "edit_guard", "delete", show=False),
        Binding("up", "edit_guard", "prev model", show=False),
        Binding("down", "edit_guard", "next model", show=False),
    ]

    def action_edit_guard(self) -> None:
        """编辑态吞掉全局步进/播放/删除/切换键（ChainParams 聚焦时才生效）。"""

    # Parameter-specific base steps and the single long-press cadence from the
    # frozen UI spec. Long press deliberately has no second acceleration tier.
    BASE_STEPS = {"gain": 0.10, "master": 0.05, "quality": 0.05}
    LONG_PRESS_DELAY = 0.35
    LONG_REPEAT_INTERVAL = 0.10

    # 手动填写限制：小数 ≤ 2 位（与 signed_fixed 显示一致）、总长 ≤ 8 字符
    EDIT_MAX_DECIMALS = 2
    EDIT_MAX_LENGTH = 8

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # 按下状态：_press_span 为按下的 token 下标（None=未按下）；
        # _long_press_active 为真时释放的合成 click 不再步进；
        # _press_cancelled 为真时（移出 token/面板）click 被丢弃。
        self._press_span: int | None = None
        self._long_press_timer = None
        self._repeat_timer = None
        self._long_press_active = False
        self._press_cancelled = False
        # 手动填写（REQ-021）：_editing 为参数下标（0/1/2）或 None
        self._editing: int | None = None
        self._edit_text = ""
        # 编辑态闪烁光标（REQ-028）：_cursor_visible 决定 ▌/空格占位，
        # _cursor_timer 每 0.5s 切换一次。
        self._cursor_visible = True
        self._cursor_timer = None

    def set_values(self, gain: float, master: float, quality: float) -> None:
        controls = [
            ("GAIN", signed_fixed(gain), "g · G",
             lambda step: self.app.action_bump_gain(-step),
             lambda step: self.app.action_bump_gain(+step)),
            ("MASTER", signed_fixed(master), "m · M",
             lambda step: self.app.action_bump_master(-step),
             lambda step: self.app.action_bump_master(+step)),
            ("QUALITY", signed_fixed(quality), "q · Q",
             lambda step: self.app.action_bump_quality(-step),
             lambda step: self.app.action_bump_quality(+step)),
        ]
        # (token, start, end, action) — one span per key half, so the dot
        # separates the two click targets instead of merging them.  action
        # takes the step size (always positive); the sign is baked in.
        self._spans: list[tuple[str, int, int, Callable[[float], None]]] = []
        # 数值区域（单击进编辑）：(参数下标, start, end)，value 文本含
        # signed_fixed 的符号空格，整段可点。
        self._value_spans: list[tuple[int, int, int]] = []
        # 分隔点（点击恢复该参数默认值，REQ-027）：(参数下标, start, end)
        self._dot_spans: list[tuple[int, int, int]] = []
        offset = 0
        for index, (label, value, hint, decrease, increase) in enumerate(controls):
            if index:
                offset += 3
            prefix = f"{label}  {value} "
            value_start = offset + len(label) + 2
            self._value_spans.append((index, value_start, value_start + len(value)))
            offset += len(prefix)
            lo, hi = (part.strip() for part in hint.split("·"))
            self._spans.append((lo, offset, offset + len(lo), decrease))
            offset += len(lo)  # " · " 分隔符
            self._dot_spans.append((index, offset + 1, offset + 2))
            offset += 3
            self._spans.append((hi, offset, offset + len(hi), increase))
            offset += len(hi)
        self._controls = controls
        self._param_keys = ("gain", "master", "quality")
        # 长按进行中时 set_values（每次步进都会触发）保持按下 token 的高亮，
        # 否则重建会清掉它；普通更新则清空悬停。
        self._hover_index = self._press_span if self._long_press_active else None
        self._refresh_hint(self._hover_index)

    def _refresh_hint(self, hovered: int | None = None) -> None:
        """Rebuild the whole line, highlighting only the hovered key half.

        Rebuilding by position (not str.replace) keeps the highlight on the
        actual g/G token — a bare replace would hit the G in "GAIN" or the m
        in "MASTER" first.
        """
        parts: list[str] = []
        span_index = 0
        for index, (label, value, hint, _dec, _inc) in enumerate(self._controls):
            if index:
                parts.append("   ")
            # 值区域：编辑态显示输入文本+闪烁光标（REQ-028，光标亮时 ▌、
            # 暗时空格占位保持行宽）；悬停值区域（hovered 10+index 编码）
            # 提示可点击编辑。
            if self._editing == index:
                cursor = "▌" if self._cursor_visible else " "
                parts.append(f"[b]{label}[/]  "
                             f"[b $background on $accent]{self._edit_text}[/]"
                             f"[b $accent on $background]{cursor}[/] [dim]")
            else:
                value_style = "$background on $accent" if (
                    hovered is not None and hovered >= 10
                    and hovered - 10 == index) else "$accent"
                parts.append(f"[b]{label}[/]  [b {value_style}]{value}[/] [dim]")
            lo, hi = (part.strip() for part in hint.split("·"))
            # g · G 顺序渲染：lo → 点 → hi（点必须夹在中间，REQ-027）
            if hovered is not None and hovered == span_index:
                parts.append(f"[b $background on $accent]{lo}[/]")
            else:
                parts.append(lo)
            # 分隔点 = 恢复参数默认值（REQ-027）：自身 hover（20+index 编码）高亮，
            # 与 g/G 风格一致；hover 别的 token/值/空白时点不亮（20+ 编码
            # 保证 None 与其它 hovered 值都不会误命中）。
            if hovered is not None and hovered >= 20 and hovered - 20 == index:
                parts.append(" [b $background on $accent]·[/] ")
            else:
                parts.append(" · ")
            if hovered is not None and hovered == span_index + 1:
                parts.append(f"[b $background on $accent]{hi}[/]")
            else:
                parts.append(hi)
            span_index += 2
            parts.append("[/]")
        self.update("".join(parts))

    # ---- 鼠标点按/长按步进（REQ-007）----

    def _span_at(self, x: int) -> int | None:
        """局部列 x 命中的 token span 下标（空白返回 None）"""
        return next(
            (index for index, (token, start, end, _)
             in enumerate(getattr(self, "_spans", []))
             if start <= x < end),
            None,
        )

    # ---- 手动填写数值（REQ-021）----

    # 参数值域（用户定案 REQ-027）：gain/master 0..10 对齐 NAM 官方 ±20dB
    # 线性刻度（0.1=-20dB、10=+20dB、0=静音）；quality
    # 0..1（SlimmableContainer 子模型尺寸，app 侧既有 clamp）。
    PARAM_RANGES = {
        "gain": (0.0, 10.0),
        "master": (0.0, 10.0),
        "quality": (0.0, 1.0),
    }
    PARAM_DEFAULTS = live.CHAIN_PARAMETER_DEFAULTS

    def _value_at(self, x: int) -> int | None:
        """局部列 x 命中的数值区域（返回 10+参数下标，供 hover 编码）"""
        return next(
            (10 + index for index, start, end
             in getattr(self, "_value_spans", [])
             if start <= x < end),
            None,
        )

    def _dot_at(self, x: int) -> int | None:
        """局部列 x 命中的分隔点（返回 20+参数下标，供 hover 编码）"""
        return next(
            (20 + index for index, start, end
             in getattr(self, "_dot_spans", [])
             if start <= x < end),
            None,
        )

    def _begin_edit(self, index: int) -> None:
        """进入编辑态：预填当前显示值，聚焦本 widget 接收键盘。"""
        self._editing = index
        self._edit_text = self._controls[index][1]  # signed_fixed 显示值
        self._cursor_visible = True
        if self._cursor_timer is None:
            self._cursor_timer = self.set_interval(0.5, self._toggle_cursor)
        self._refresh_hint()
        self.focus()

    def _toggle_cursor(self) -> None:
        """0.5s 闪烁：光标 ▌/空格占位切换（占位保持行宽，token 不位移）。"""
        self._cursor_visible = not self._cursor_visible
        if self._editing is not None:
            self._refresh_hint(self._hover_index)

    def _stop_cursor(self) -> None:
        if self._cursor_timer is not None:
            self._cursor_timer.stop()
            self._cursor_timer = None
        self._cursor_visible = True

    def _exit_edit(self) -> None:
        """退出编辑（取消）：恢复显示当前值，停闪烁光标。"""
        self._editing = None
        self._edit_text = ""
        self._stop_cursor()
        self._refresh_hint()

    def _apply_edit(self) -> None:
        """Enter 应用：解析 → clamp → 走既有写链路径（与 g·G 同一条链）。"""
        index = self._editing
        if index is None:
            return
        text = self._edit_text.strip()
        self._editing = None
        self._edit_text = ""
        self._stop_cursor()
        if not text or text == ".":
            self._refresh_hint()
            return
        try:
            value = float(text)
        except ValueError:
            self._refresh_hint()
            return
        key = self._param_keys[index]
        lo, hi = self.PARAM_RANGES[key]
        if value < lo or value > hi:
            self.app.notify(
                f"{key.upper()} range {lo:g}–{hi:g} — clamped",
                severity="warning")
            value = max(lo, min(hi, value))
        self.app._set_chain_param(key, round(value, 2))
        self._refresh_hint()

    def _append_edit_char(self, char: str) -> None:
        """追加一个数字/小数点，超限与非法输入直接拒绝（不进入文本）。"""
        if char == "." and "." in self._edit_text:
            return  # 多余小数点
        if char != "." and "." in self._edit_text:
            if self._edit_text.index(".") < len(self._edit_text) - self.EDIT_MAX_DECIMALS:
                return  # 小数位已满
        if len(self._edit_text) >= self.EDIT_MAX_LENGTH:
            return
        self._edit_text += char
        self._cursor_visible = True  # 输入即亮，闪烁重新计时
        self._refresh_hint()

    def on_key(self, event: Key) -> None:
        if self._editing is None:
            return
        event.stop()
        if event.key == "escape":
            self._exit_edit()
        elif event.key == "enter":
            self._apply_edit()
        elif event.key == "backspace":
            if self._edit_text:
                self._edit_text = self._edit_text[:-1]
                self._cursor_visible = True  # 输入即亮，闪烁重新计时
                self._refresh_hint()
        else:
            # 兼容 "."（终端）与 "full_stop"（pilot 的 xterm 键名）
            char = "." if event.key == "full_stop" else event.key
            if len(char) == 1 and char in "0123456789.":
                self._append_edit_char(char)

    def on_blur(self, event: Blur) -> None:
        """焦点被别处拿走（点击他处/tab）：取消编辑，不应用。"""
        if self._editing is not None:
            self._exit_edit()

    def _base_step(self, span_index: int) -> float:
        key = self._param_keys[span_index // 2]
        return self.BASE_STEPS[key]

    def _step(self, span_index: int, step_size: float | None = None) -> None:
        """按 span 下标步进一次（step_size 恒为正，方向由 token 决定）"""
        spans = getattr(self, "_spans", [])
        if 0 <= span_index < len(spans):
            spans[span_index][3](self._base_step(span_index)
                                  if step_size is None else step_size)

    def _cancel_timers(self) -> None:
        for attr in ("_long_press_timer", "_repeat_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                timer.stop()
                setattr(self, attr, None)

    def _cancel_press(self) -> None:
        """终止当前按下：停止全部定时器并丢弃随后的合成 click"""
        self._cancel_timers()
        self._press_span = None
        self._long_press_active = False
        self._press_cancelled = True

    def _begin_long_press(self) -> None:
        self._long_press_timer = None
        if self._press_span is None or self._press_cancelled:
            return
        self._long_press_active = True
        self._step(self._press_span)
        self._repeat_timer = self.set_interval(
            self.LONG_REPEAT_INTERVAL, self._repeat_long_press)

    def _repeat_long_press(self) -> None:
        if self._press_span is not None and not self._press_cancelled:
            self._step(self._press_span)

    def on_mouse_down(self, event: MouseDown) -> None:
        x = max(0, event.x - 1)  # chain-params has one cell of left padding
        if self._editing is not None:
            # 编辑态中按下任何位置：先取消编辑（不应用），并复用
            # _press_cancelled 让随后的合成 click 不步进
            self._exit_edit()
            self._press_cancelled = True
            event.stop()
            return
        self._cancel_timers()
        self._press_cancelled = False
        self._long_press_active = False
        self._press_span = self._span_at(x)
        if self._press_span is None:
            return
        event.stop()
        # capture 保证按住拖出面板时仍能收到 move（移出即停止长按）；
        # mouse up 时在 on_mouse_up 里显式释放。
        self.capture_mouse()
        self._long_press_timer = self.set_timer(
            self.LONG_PRESS_DELAY, self._begin_long_press)

    def on_mouse_up(self, event: MouseUp) -> None:
        self._cancel_timers()
        self._press_span = None
        self.release_mouse()
        # _long_press_active / _press_cancelled 留给紧随的合成 click 消费

    def on_click(self, event: MouseEvent) -> None:
        if self._long_press_active or self._press_cancelled:
            # 长按释放 / 已取消的按下：合成 click 不再步进
            self._long_press_active = False
            self._press_cancelled = False
            event.stop()
            return
        if self._editing is not None:
            # 编辑中点击：取消编辑（不应用）
            self._exit_edit()
            event.stop()
            return
        x = max(0, event.x - 1)  # chain-params has one cell of left padding
        # 分隔点：恢复参数默认值（REQ-027，走同一条写链路径）
        dot_hit = self._dot_at(x)
        if dot_hit is not None:
            event.stop()
            key = self._param_keys[dot_hit - 20]
            self.app._set_chain_param(key, self.PARAM_DEFAULTS[key])
            return
        # 数值区域：进入手动编辑
        value_hit = self._value_at(x)
        if value_hit is not None:
            event.stop()
            self._begin_edit(value_hit - 10)
            return
        span = self._span_at(x)
        if span is not None:
            event.stop()
            self._step(span)

    def on_mouse_move(self, event: MouseMove) -> None:
        x = max(0, event.x - 1)
        if self._press_span is not None:
            # 按住期间：移出按下的 token 立即停止长按（capture 保证按住
            # 拖出仍收到 move）；停在原 token 上时不更新悬停。
            if self._span_at(x) != self._press_span:
                self._cancel_press()
            else:
                return
        hovered = self._span_at(x)
        if hovered is None:
            hovered = self._value_at(x)  # 值区域 → 10+参数下标（可编辑提示）
        if hovered is None:
            hovered = self._dot_at(x)  # 分隔点 → 20+参数下标（恢复默认提示）
        if hovered == self._hover_index:
            return
        self._hover_index = hovered
        self._refresh_hint(hovered)

    def on_leave(self, event: Leave) -> None:
        self._cancel_press()
        if self._hover_index is not None:
            self._hover_index = None
            self._refresh_hint()

    def on_unmount(self, event: Unmount) -> None:
        """链面板销毁时停止可能仍在跑的长按/闪烁光标定时器"""
        self._cancel_press()
        self._stop_cursor()


class ChainPanel(Vertical):
    """Read-only view of the live tone chain."""

    # Blank clicks land on the panel itself; ←/→ then switch the detail
    # pane between Description and Selection.
    can_focus = True

    BINDINGS = [
        Binding("left", "view_description", "description", show=False),
        Binding("right", "view_selection", "selection", show=False),
    ]

    chain: reactive[dict] = reactive({}, recompose=False)

    def action_view_description(self) -> None:
        self.screen.query_one(DetailPane).toggle_view(-1)

    def action_view_selection(self) -> None:
        self.screen.query_one(DetailPane).toggle_view(+1)

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "TONE CHAIN"
        self._hint_state = ""
        # 提示行 d delete 的兜底目标：最后一次聚焦的 AMP/CAB 节点。
        self._last_focus_node: NodeWidget | None = None

    def _hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        """Right-corner border hint, one clickable token per action.

        Same grammar as the tone-detail pane's description/selection hint:
        every token is a real click target (delete the AMP/CAB slot, or drive
        the dry-input playback) and hover-highlights. Keys are written in the
        case that actually works (d/space/s/l are lowercase bindings). ↑/↓
        model switching is a keyboard action with no click target — not hinted.
        """
        width = self.region.width or (self.size.width + 4)
        # Match Textual's border subtitle budget: two edge cells plus one
        # corner cell on each side are unavailable to the label content.
        inner = max(width - 6, 1)
        # Choose complete labels as a group.  Truncating the joined string can
        # turn the final ``l loop`` token into ``l l…``, which is neither
        # readable nor clickable.  The loop label is retained whenever the
        # available width permits it; only its description is dropped at the
        # smallest supported width.
        options = (
            ("d delete", "d del", "d"),
            ("space play/pause", "space play", "space"),
            ("s stop", "s"),
            ("l loop", "l"),
        )
        choices = []
        for candidate in product(*options):
            action_text = " · ".join(candidate)
            if cell_len(action_text) <= inner:
                score = (
                    candidate[3] == "l loop",
                    sum(cell_len(label) for label in candidate),
                    tuple(cell_len(label) for label in candidate),
                )
                choices.append((score, candidate))
        labels = max(choices, default=((), ("d", "space", "s", "l")))[1]
        return [
            (labels[0], self._delete_focused_node),
            (labels[1], lambda: self._fire_node_message(
                self.input_node, self.input_node.PlaybackRequested("toggle"))),
            (labels[2], lambda: self._fire_node_message(
                self.input_node, self.input_node.PlaybackRequested("stop"))),
            (labels[3], lambda: self._fire_node_message(
                self.input_node, self.input_node.PlaybackRequested("loop"))),
        ]

    @staticmethod
    def _fire_node_message(node: NodeWidget, message: Message) -> None:
        """提示行动作以节点为 sender 发送消息。

        Textual 消息的 _sender 取自创建时的活跃 pump（contextvar）；提示行
        动作在 ChainPanel 的 pump 上下文里运行，直接 post 会把 _sender 记成
        ChainPanel，消息从节点气泡到节点行时被 sender==parent 规则掐断，
        app 的处理器收不到（delete/space/s/l 全部失效）。set_sender 把
        sender 指回节点后正常送达。
        """
        message.set_sender(node)
        node.post_message(message)

    def _delete_focused_node(self) -> None:
        """Forward the border-hint delete command to the AMP/CAB slot.

        The keyboard ``d`` binding belongs to each node, so the shared hint
        keeps that same meaning; a hint click lands on the panel border (and
        may move focus away), so it falls back to the last-focused AMP/CAB.
        """
        node = getattr(self.app, "focused", None)
        if not isinstance(node, NodeWidget) or node.kind not in ("amp", "cab"):
            node = self._last_focus_node
        if node is None:
            self.app.notify("Focus AMP or CAB first", severity="warning")
            return
        self._fire_node_message(node, node.DeleteRequested(node.kind))

    def _refresh_hint(self) -> None:
        set_border_hint_layout(
            self, self._hint_state,
            [label for label, _action in self._hint_actions()])

    def on_mount(self) -> None:
        self._refresh_hint()

    def on_resize(self, _event) -> None:
        if hasattr(self, "input_node"):
            self._refresh_hint()

    def compose(self) -> ComposeResult:
        # REQ-043 追加：聚焦 marquee 行已删——节点框本身显示标题+文件名，
        # 边框标题显示 type；链面板顶部直接是 INPUT 行。
        with Horizontal(classes="chain-node-row chain-node-row-input"):
            self.input_node = InputNodeWidget()
            yield self.input_node
        with Horizontal(classes="chain-node-row chain-node-row-amp"):
            self.amp = NodeWidget("AMP")
            yield self.amp
            with Horizontal(classes="chain-switch-col"):
                yield NodeSwitchButton("amp", +1)  # ▲ prev
                yield NodeSwitchButton("amp", -1)  # ▼ next
        with Horizontal(classes="chain-node-row chain-node-row-ir"):
            self.ir = NodeWidget("CAB", "—")
            yield self.ir
            with Horizontal(classes="chain-switch-col"):
                yield NodeSwitchButton("cab", +1)  # ▲ prev
                yield NodeSwitchButton("cab", -1)  # ▼ next
        for _key, label, hint in live.CHAIN_ORDER[2:]:
            yield NonSelectableStatic(
                f" [bold $state-idle]○[/] [bold]{label:6s}[/] [dim]{hint}[/]",
                classes="chain-effect")
        self.params = ChainParams("", classes="chain-params")
        yield self.params

    def _border_hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        return self._hint_actions()

    def on_click(self, event: MouseEvent) -> None:
        hit = border_hint_hit(self, event.screen_x, event.screen_y)
        if hit is None:
            return
        label, offset = hit
        for token, action in self._border_hint_actions():
            span = hint_span(label, token)
            if span is not None and span[0] <= offset < span[1]:
                event.stop()
                action()
                return

    def on_mouse_move(self, event: MouseMove) -> None:
        tokens = [token for token, _ in self._border_hint_actions()]
        set_border_hint_hover(
            self,
            border_hint_action_token(self, event.screen_x, event.screen_y, tokens),
        )

    def on_leave(self, event: Leave) -> None:
        set_border_hint_hover(self, None)

    def update_playback(self, state: str, pos_sec: float) -> None:
        """0.1s levels tick: engine playback state reflected on the INPUT node
        (the in-row PLAY block redraws itself from play_state)."""
        if hasattr(self, "input_node") and self.input_node.is_file:
            self.input_node.set_playback(state, pos_sec, self.input_node.play_loop)

    def watch_chain(self, chain: dict) -> None:
        inp = live.chain_input(chain)
        if inp.get("source") == "file" and inp.get("file"):
            self.input_node.set_file(inp["file"])
            state = inp.get("state", live.PLAY_STOPPED)
            self.input_node.set_playback(state, 0.0, bool(inp.get("loop")))
        else:
            self.input_node.set_instrument(
                getattr(self.app, "_dev_in", "") or "default device")
        self._set_node(self.amp, chain.get("model"), empty="NONE")
        self._set_node(self.ir, chain.get("ir"), empty="NONE")
        gain = float(chain.get("gain", live.CHAIN_PARAMETER_DEFAULTS["gain"]))
        master = float(chain.get("master", live.CHAIN_PARAMETER_DEFAULTS["master"]))
        quality = float(chain.get("quality", live.CHAIN_PARAMETER_DEFAULTS["quality"]))
        self.params.set_values(gain, master, quality)

    def on_descendant_focus(self, event) -> None:
        if isinstance(event.widget, NodeWidget):
            if event.widget.kind in ("amp", "cab"):
                self._last_focus_node = event.widget

    def _set_node(self, node: NodeWidget, path: str | None, *, empty: str) -> None:
        """Node readout: tone title (line 1) + model filename (line 2).

        External files (not in the library DB) fall back to the filename only.
        Titles are cached by path — the 0.3s tick must not hit SQLite twice
        per refresh for an unchanged chain.
        """
        if not path:
            # 链值 null 的双义（无法从值区分）：双击 BYPASS 在 app 侧留有
            # 备份（_amp_model_backup/_ir_backup，恢复用）——模块加载但直通，
            # 保留内容显示；无备份（preset 缺位/外部链/delete 卸载）＝缺位
            # 空槽，强制 NONE 灰底。按节点残留 label 判断会在 preset 缺位
            # 加载（节点残留旧文件名）时误判为 bypass（REQ-016）。
            backup = getattr(
                self.app,
                "_ir_backup" if node.kind == "cab" else "_amp_model_backup",
                None)
            if backup:
                node.set_bypassed(True)
                node.set_class(False, "chain-node-empty")
            else:
                node.set_title(None)
                node.set_label(empty)
                node.set_bypassed(False)
                node.set_class(True, "chain-node-empty")
            return
        node.set_bypassed(False)
        node.set_class(False, "chain-node-empty")
        if not hasattr(self, "_title_cache"):
            self._title_cache: dict[str, str | None] = {}
        if path not in self._title_cache:
            self._title_cache[path] = library.tone_title_for_path(path)
        node.set_title(self._title_cache[path])
        node.set_label(live.short_name(path))


class PackFileTable(ClickSelectTable):
    """Interactive file list of the focused node's tone pack.

    Every model of the owning tone is listed (downloaded or not); Enter or
    double-click picks one and the app hot-swaps its chain slot. Single click
    only moves the cursor (ClickSelectTable). Esc returns keyboard focus to
    the chain node so ↑/↓ stepping works again.

    REQ-038 多选下载/卸载（与 pack install 二级菜单同语义）：
    space 勾选/取消光标行（Pick 列 □/■）、a 全选/全不选、i 安装选中、
    u 卸载选中（u 语义与 uninstall_screen 一致）。Enter/双击的单行语义
    与多选共存：已下载行热换链槽、未下载行打开该 tone 的二级菜单详情页。
    """

    BINDINGS = [
        Binding("escape", "close_pack", "back", show=False),
        Binding("left", "view_description", "description", show=False),
        Binding("right", "view_selection", "selection", show=False),
        Binding("space", "toggle_pick", "select", show=False),
        Binding("a", "toggle_all_pick", "all/none", show=False),
        Binding("i", "install_selected", "install", show=False),
        Binding("u", "uninstall_selected", "uninstall", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="detail-pack-table", cursor_type="row")
        self.pack_kind = "amp"  # the chain slot this pack was opened from
        self.add_column("Pick", key="pick", width=5)
        self.add_column("Sel", key="sel", width=4)
        self.add_column("Arch", key="arch", width=6)
        # REQ-019：不再显示 TONE id 列
        self.add_column("File", key="file")
        self.add_column("Size", key="size", width=10)

    # ---- REQ-038 多选安装/卸载（状态与动作都归 DetailPane 管理）----

    def action_toggle_pick(self) -> None:
        self.screen.query_one(DetailPane)._pack_toggle_pick()

    def action_toggle_all_pick(self) -> None:
        self.screen.query_one(DetailPane)._pack_toggle_all_pick()

    def action_install_selected(self) -> None:
        self.screen.query_one(DetailPane)._pack_install_selected()

    def action_uninstall_selected(self) -> None:
        self.screen.query_one(DetailPane)._pack_uninstall_selected()

    def action_close_pack(self) -> None:
        pane = self.screen.query_one(DetailPane)
        if pane._pack_origin == "description":
            # Entered via the view switch: Esc returns to the description
            # view instead of focusing a chain node the user never touched.
            pane.toggle_view(-1)
        elif pane._pack_origin == "creators":
            # TOP CREATORS 作者视图：Esc 回到作者表继续浏览
            self.screen.query_one("#lib-table-creators").focus()
        else:
            self.post_message(DetailPane.PackClosed(self.pack_kind))

    def action_view_description(self) -> None:
        pane = self.screen.query_one(DetailPane)
        pane.toggle_view(-1)
        # Keep keyboard focus inside the pane so → can switch right back;
        # hiding the table would otherwise drop focus back to the library.
        pane.focus()

    def action_view_selection(self) -> None:
        self.screen.query_one(DetailPane).toggle_view(+1)

    def on_click(self, event) -> None:
        """Pick 列单击 = 鼠标点选（REQ-040）；其余列单击移光标（基类），
        双击 = Enter 语义（热换链槽 / 打开二级菜单详情页）。"""
        meta = event.style.meta
        if (meta.get("column") == 0 and isinstance(meta.get("row"), int)
                and meta["row"] >= 0):
            rows = self.ordered_rows
            if meta["row"] < len(rows):
                # 光标跟随被点的行，再切换该行勾选态
                self.move_cursor(row=meta["row"], column=0,
                                 animate=False, scroll=False)
                self.screen.query_one(DetailPane)._pack_toggle_key(
                    rows[meta["row"]].key.value)
            event.stop()
            return
        if getattr(event, "chain", 1) >= 2:
            self.action_select_cursor()
            event.stop()



class DetailPane(Vertical):
    """Full metadata of the tone selected in the library (from the SQLite DB).

    The tone title is a frozen bold header above a scrollable body — scrolling
    the metadata never hides the title.

    Pack mode: when an AMP/IR chain node is focused, the pane shows that
    tone's whole file list as an interactive table (select a row → hot-swap
    the live chain). Other views (tone/preset/model detail) replace it.
    """

    class PackFilePicked(Message):
        """A file in the pack list was selected — hot-swap that chain slot."""

        def __init__(self, slot: str, path: str, tone_gear: str | None) -> None:
            super().__init__()
            self.slot = slot  # "model" | "ir"
            self.path = path
            self.tone_gear = tone_gear

    class PackClosed(Message):
        """Esc on the pack file table — return keyboard focus to the chain node."""

        def __init__(self, kind: str) -> None:
            super().__init__()
            self.kind = kind

    class PackInstallRequested(Message):
        """selection 视图里 Enter/双击一行 → 打开该 tone 的二级菜单详情页
        （PackInstallScreen，REQ-038——必须带 tone dict，不能带 model）。"""

        def __init__(self, tone: dict) -> None:
            super().__init__()
            self.tone = tone

    class PackFilesInstalled(Message):
        """pack 表 i 键安装完成（二级菜单内直接下载选中的模型）。"""

        def __init__(self, tone_id: int, count: int) -> None:
            super().__init__()
            self.tone_id = tone_id
            self.count = count

    class PackFilesUninstalled(Message):
        """pack 表 u 键卸载完成（模型粒度，元数据保留）。"""

        def __init__(self, tone_id: int, count: int) -> None:
            super().__init__()
            self.tone_id = tone_id
            self.count = count

    BINDINGS = [
        Binding("left", "view_description", "description", show=False),
        Binding("right", "view_selection", "selection", show=False),
        Binding("r", "retry_detail", "retry", show=False),
        Binding("escape", "back_from_creator", "back", show=False),
    ]

    # The pane itself takes focus so ←/→ switch Description/Selection even
    # when neither the description body nor the pack table is focused.
    can_focus = True

    def action_back_from_creator(self) -> None:
        """作者介绍页 Esc：回到 TOP CREATORS 表继续浏览（REQ-020）。"""
        if self._pack_origin == "creators":
            self.screen.query_one("#lib-table-creators").focus()

    DEFAULT_CSS = """
    DetailPane #detail-marquee {
        height: 1; padding: 0 1;
        background: $primary 15%;
    }
    /* Empty state: no title shown, so no tinted header either. */
    DetailPane #detail-marquee.detail-marquee--empty {
        background: transparent;
    }
    /* 未选中态：摘要行同样不允许带背景（残留一行浅色块）。 */
    DetailPane #detail-summary.detail-summary--empty {
        background: transparent;
    }
    DetailPane #detail-summary {
        height: 1; padding: 0 1; margin-bottom: 1;
        color: $text-muted; background: $panel;
        text-style: bold;
    }
    DetailPane #detail-slots {
        height: 1; padding: 0 1; margin-bottom: 1;
        color: $text-muted; background: $panel;
    }
    DetailPane #detail-scroll { height: 1fr; }
    /* The pack file list is a quieter table than the library: muted header,
       and a bottom hint line so the keys never scroll out of sight. */
    DetailPane #detail-pack-table { height: 1fr; }
    DetailPane #detail-pack-table > .datatable--header {
        background: $surface; color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "TONE DETAIL"
        # The marquee is the single visible title row, tinted with the theme's
        # primary so it reads as the headline above the metadata summary. Keep
        # the historical ``_title`` handle as an alias for callers.
        self._marquee = MarqueeBar(id="detail-marquee", style="b $primary")
        self._title: Static = self._marquee
        self._summary: MarqueeBar = MarqueeBar("", id="detail-summary", markup=True)
        # Tone detail is the intentional copy surface. Keep its title and
        # compact summary lines selectable alongside the metadata body.
        for text_widget in (self._marquee, self._summary):
            text_widget.ALLOW_SELECT = True
        self._body: SelectableStatic = SelectableStatic(
            "[dim]Select a tone in the library to see its full metadata here.[/dim]")
        # Pack mode: current AMP/IR slot readout + interactive file list.
        self._pack_table: PackFileTable = PackFileTable()
        self._pack_table.display = False
        self._pack_mode = False
        self._pack_remote = False  # 远程（未下载）pack：Enter 走安装二级页
        self._pack_kind = "amp"
        self._pack_tone: dict = {}
        # row key ("m<model id>") → model; local_path → row key; last chain.
        self._pack_rows: dict[str, dict] = {}
        self._pack_path_to_key: dict[str, str] = {}
        self._pack_chain: dict = {}
        # REQ-038 多选下载/卸载：勾选行 key 集合 + preset 引用二次确认标记。
        self._pack_picked: set[str] = set()
        self._pack_uninstall_confirmed = False
        self._pack_uninstall_target: tuple[int, ...] = ()
        self._pack_operation_generation = 0
        self._pack_operation_view_generation = 0
        self._pack_operation_target: tuple[int, tuple[int, ...]] | None = None
        self._pack_busy: str | None = None
        # 选择=聚焦：上一次光标同步时的激活槽位路径（仅槽位变化才移动光标）。
        self._pack_synced_model: str | None = None
        self._pack_synced_ir: str | None = None
        # Rebuild the current rich Table with fresh theme colors on theme
        # change; markup content (title/summary/plain text) recolors itself.
        self._rerender: Callable[[], None] | None = None
        # Tone shown right now; guards late background verification answers.
        self._current_tone: dict | None = None
        # Which summary line is displayed ("tone" | "model" | "preset") so a
        # late verification answer only rewrites the tone author line.
        self._summary_mode = "tone"
        # Authors already probed this session (one network check per author).
        self._probed_authors: set[str] = set()
        # Tone-detail view mode: "description" (default) | "selection" |
        # None (model/preset/other views without the mode switch).
        self._view_mode = "description"
        # How the pack view was entered — from a chain node (Esc returns
        # focus there) or by switching over from the description view (Esc
        # switches back instead).
        self._pack_origin = "node"
        self._pack_creator: str | None = None  # 当前聚焦的作者（verified 守卫用）
        self._view_generation = 0
        self._remote_models_cache: dict[int, list[dict]] = {}
        self._pack_error = False
        self._pack_loading = False
        self._creator_bio_cache: dict[str, str] = {}
        self._creator_error: str | None = None
        self._creator_loading = False

    def _set_marquee(self, content: str | None, *, markup: bool = False) -> None:
        """Update the shared title row with the correct parser mode.

        Tone/model/preset titles are arbitrary user data and stay escaped plain
        text.  Creator titles additionally carry the verified badge, so they
        opt into Rich markup for that one view.
        """
        self._marquee.set_markup(markup)
        self._marquee.update(content)

    def compose(self) -> ComposeResult:
        yield self._marquee
        yield self._summary
        with VerticalScroll(id="detail-scroll"):
            yield self._body
        yield self._pack_table

    def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, self._on_theme_changed)

    def on_resize(self, _event) -> None:
        """Keep the right-anchored hint fitted after pane resizes."""
        if getattr(self, "_view_mode", None) == "selection":
            # The selection view adds/removes the ←/→ tokens at a width
            # threshold. Rebuild the action set before fitting the strip;
            # refreshing cached actions alone would leave stale hit targets.
            self._update_pack_hint()
        else:
            refresh_border_hint_layout(self)

    def on_unmount(self) -> None:
        self._view_generation += 1
        self._pack_operation_generation += 1

    def _invalidate_view(self) -> int:
        self._view_generation += 1
        return self._view_generation

    def _view_alive(self, generation: int) -> bool:
        return (bool(getattr(self, "is_mounted", False))
                and generation == self._view_generation)

    def _pack_operation_alive(self, generation: int, tone_id: int) -> bool:
        return (self._view_alive(self._pack_operation_view_generation)
                and generation == self._pack_operation_generation
                and getattr(self, "_pack_mode", False)
                and int((self._pack_tone or {}).get("id") or 0) == tone_id)

    def _begin_pack_operation(self, kind: str, tone_id: int,
                              model_ids: list[int]) -> int | None:
        if self._pack_busy is not None or not self._pack_mode:
            return None
        self._pack_operation_generation += 1
        self._pack_operation_view_generation = self._view_generation
        self._pack_operation_target = (tone_id, tuple(model_ids))
        self._pack_busy = kind
        return self._pack_operation_generation

    def action_retry_detail(self) -> None:
        if self._view_mode == "creator" and self._pack_creator:
            self.retry_creator_view()
        elif self._pack_remote and self._pack_tone:
            self.retry_remote_pack()

    def _on_theme_changed(self, _theme) -> None:
        if self._rerender is not None:
            self._rerender()
        elif getattr(self, "_pack_mode", False):
            # Pack rows carry resolved hex colors — rebuild them for the new
            # theme (cursor position resets, acceptable on a theme switch).
            self._fill_pack_rows(list(self._pack_rows.values()))
            self.refresh_pack_active(self._pack_chain)

    def _theme_colors(self) -> dict[str, str]:
        return theme_colors(self.app)

    # Per-gear badge colors: amp burns orange, cab green, pedal yellow;
    # anything unknown (e.g. experimental) falls back to muted.
    _GEAR_STYLES = {"amp": "$primary", "cab": "$success", "pedal": "$warning"}

    @staticmethod
    def _gear_badge(gear: str) -> str:
        if gear == "amp-cab":
            return "[b $primary]AMP[/] + [b $success]CAB[/]"
        style = DetailPane._GEAR_STYLES.get(gear, "$text-muted")
        return f"[b {style}]{_escape(gear.upper())}[/]"

    @staticmethod
    def _tone_summary(tone: dict, model_id: int | None = None) -> str:
        """One-line header under the title: author (badged when verified),
        colored gear badges, then dim-label/bold-value counts.

        The TONE/MODEL ids live on the title row (see _marquee_content) so
        this line stays short."""
        parts = []
        username = tone.get("username")
        if username:
            # TONE3000's "Verified Profiles" set, mirrored from the website.
            verified = tone3000.is_verified(str(username))
            badge = " [b $success]✓[/]" if verified else ""
            parts.append(f"[b $accent]@{_escape(str(username))}[/]{badge}")
        if tone.get("gear"):
            parts.append(DetailPane._gear_badge(str(tone["gear"])))
        models = tone.get("models_count")
        if models is not None:
            parts.append(f"[b]{models}[/] [dim]MODELS[/dim]")
        if tone.get("downloads_count") is not None:
            parts.append(
                f"[dim]DL[/dim] [b]{tone.get('downloads_count') or 0}[/b]")
        if tone.get("favorites_count") is not None:
            parts.append(
                f"[dim]FAV[/dim] [b]{tone.get('favorites_count') or 0}[/b]")
        return " · ".join(parts)

    @staticmethod
    def _control(value, fallback: float = 1.0) -> str:
        return signed_fixed(value, fallback=signed_fixed(fallback))

    def _ensure_verification(self, tone: dict) -> None:
        """Probe a not-yet-known author's verified badge in the background.

        The REST API has no verified flag, so the first time a detail view
        meets an unknown author, a background thread fetches their tone3000.com
        page and, on success, persists the verdict and re-renders the summary.
        A changed selection is guarded by identity so a late answer never
        overwrites a newer tone.
        """
        name = str(tone.get("username") or "").lower()
        if not name or tone3000.is_verified(name):
            return
        if name in self._probed_authors:
            return
        self._probed_authors.add(name)
        generation = self._view_generation

        def check() -> None:
            ok = library.tone3000.verify_username(name)
            if ok is None:
                return

            def commit() -> None:
                if ok is True:
                    try:
                        self.app.post_message(VerifiedAuthor(name))
                    except Exception:
                        pass
                # Re-check on the UI thread immediately before touching widgets.
                if (not self._view_alive(generation)
                        or self._current_tone is not tone
                        or self._summary_mode != "tone"):
                    return
                model_id = None
                if self._view_mode == "selection":
                    # Keep the pack cursor's MODEL #id on the header row.
                    rows = self._pack_table.ordered_rows
                    if rows and 0 <= self._pack_table.cursor_row < len(rows):
                        model = self._pack_rows.get(
                            rows[self._pack_table.cursor_row].key.value)
                        if model:
                            model_id = model.get("id")
                self._summary.update(self._tone_summary(tone, model_id))

            try:
                self.app.call_from_thread(commit)
            except Exception:
                pass

        threading.Thread(target=check, daemon=True).start()

    # ---- pack mode (focused chain node → tone file list) -------------------

    def _set_pack_mode(self, on: bool) -> None:
        self._pack_mode = on
        self.query_one("#detail-scroll", VerticalScroll).display = not on
        self._pack_table.display = on

    def _exit_pack_mode(self) -> None:
        if getattr(self, "_pack_mode", False):
            self._set_pack_mode(False)
        # A pack response that belongs to the previous view must never be able
        # to re-enter the table after the user has moved elsewhere.
        self._pack_remote = False
        self._pack_tone = {}
        self._pack_error = False
        self._pack_busy = None
        self._pack_operation_generation += 1
        set_border_hint_layout(self, "", [])
        self._pack_progress_status = ""

    def _set_summary(self, content: str) -> None:
        """摘要行更新；空内容时去掉行背景——未选中态不允许任何带背景的行。"""
        self._summary.content = content
        self._summary.set_class(not content, "detail-summary--empty")

    # ---- description / selection view modes --------------------------------

    def action_view_description(self) -> None:
        if self._view_mode == "selection":
            self.toggle_view(-1)

    def action_view_selection(self) -> None:
        if self._view_mode == "description":
            self.toggle_view(+1)

    def toggle_view(self, direction: int) -> None:
        """Switch between the Description and Selection views.

        Direction -1 → description (left), +1 → selection (right). The
        selection view needs the tone's model list; a tone without models
        stays on the description view.
        """
        if self._view_mode == "selection" and direction < 0:
            self._enter_description(self._current_tone)
        elif self._view_mode == "description" and direction > 0:
            tone = self._current_tone
            if tone and not (tone.get("models") or []):
                # 远程/无本地模型的 tone：拉远程模型列表做 Selection 视图
                # （未下载置灰、Enter 进安装二级页）。
                self.show_remote_pack(tone)
            else:
                self._enter_selection(tone=tone,
                                      origin="description", focus_table=True)

    def _mode_hint(self, *, selection: bool) -> str:
        """Return the stable action vocabulary used by both detail modes."""
        return "← description · → selection"

    # ---- REQ-038 pack 表多选安装/卸载（i install / u uninstall）----

    def _pack_view_hint(self) -> str:
        """Selection 视图右下角常驻动作 token（REQ-024/025：状态靠左、
        动作靠右）。i install / u uninstall 是 pack 视图的核心动作，必须
        常驻；空间不足时优先省略 ←/→ 视图切换 token（Esc 仍可返回）。"""
        return " · ".join(token for token, _action in self._border_hint_actions())

    def _update_pack_hint(self) -> None:
        """提示条更新（REQ-024/025：状态变化靠左、常驻动作靠右）。

        左段状态 = 勾选计数 + 安装进度（有则显示），右段常驻 =
        i install · u uninstall（宽面板保留 ←/→ 切换 token）。
        """
        if self._view_mode != "selection":
            return
        left = []
        n = len(self._pack_picked)
        if n:
            left.append(f"{n} sel")
        progress = getattr(self, "_pack_progress_status", "") or ""
        if progress:
            left.append(progress)
        hint = self._pack_view_hint()
        set_border_hint_layout(self, " · ".join(left), hint.split(" · "))

    def _selected_pack_keys(self) -> list[str]:
        """勾选行 key；未勾选任何行时回退光标行（单行语义与多选共存）。"""
        if self._pack_picked:
            return list(self._pack_picked)
        table = self._pack_table
        rows = table.ordered_rows
        if 0 <= table.cursor_row < len(rows):
            return [rows[table.cursor_row].key.value]
        return []

    def _pack_toggle_pick(self) -> None:
        """space：勾选/取消光标行（Pick 列 [ ]/[x]）。"""
        if self._pack_busy is not None:
            return
        table = self._pack_table
        rows = table.ordered_rows
        if not 0 <= table.cursor_row < len(rows):
            return
        self._pack_toggle_key(rows[table.cursor_row].key.value)

    def _pack_toggle_key(self, key: str) -> None:
        """勾选/取消指定行 key（space 键与 Pick 列鼠标点选共用）。"""
        if self._pack_busy is not None or key not in self._pack_rows:
            return
        if key in self._pack_picked:
            self._pack_picked.discard(key)
            self._pack_uninstall_confirmed = False  # 选择变化，确认作废
            self._pack_uninstall_target = ()
        else:
            self._pack_picked.add(key)
            self._pack_uninstall_confirmed = False
            self._pack_uninstall_target = ()
        try:
            self._pack_table.update_cell(
                key, "pick",
                "\\[x]" if key in self._pack_picked else "\\[ ]")
        except Exception:
            pass
        self._update_pack_hint()

    def _pack_toggle_all_pick(self) -> None:
        """a：全选/全不选（当前表内全部行）。"""
        if self._pack_busy is not None:
            return
        keys = set(self._pack_rows)
        if not keys:
            return
        if keys <= self._pack_picked:
            self._pack_picked.clear()
        else:
            self._pack_picked = set(keys)
        self._pack_uninstall_confirmed = False
        self._pack_uninstall_target = ()
        table = self._pack_table
        for key in keys:
            try:
                table.update_cell(key, "pick",
                                  "\\[x]" if key in self._pack_picked else "\\[ ]")
            except Exception:
                pass
        self._update_pack_hint()

    def _pack_install_selected(self) -> None:
        """i：安装选中的未下载模型（未勾选时 = 光标单行）。

        二级菜单内直接下载，与 pack install 屏同一 import_tone 路径；
        全部已下载时提示，不发起请求。
        """
        if self._pack_busy is not None or not getattr(self, "_pack_mode", False):
            return
        tone = self._pack_tone or self._current_tone or {}
        tone_id = tone.get("id")
        if tone_id is None:
            self.app.notify("No tone selected", severity="warning")
            return
        picked = self._selected_pack_keys()
        todo = [m for m in (self._pack_rows.get(k) for k in picked)
                if m and not m.get("local_path")]
        if not todo:
            self.app.notify("All selected models are already downloaded",
                            severity="warning")
            return
        ids = sorted({m["id"] for m in todo})
        generation = self._begin_pack_operation("install", int(tone_id), ids)
        if generation is None:
            return
        self._pack_progress_status = f"installing 0/{len(ids)}"
        self._update_pack_hint()
        self.run_worker(partial(self._pack_install_models, int(tone_id), ids,
                                generation),
                        name="pack-install", exclusive=True)

    async def _pack_install_models(self, tone_id: int, model_ids: list[int],
                                   generation: int) -> None:
        """安装工作线程：逐文件进度 → import_tone（model_ids 限定子集）。"""
        def progress(done: int, total: int, filename: str) -> None:
            try:
                self.app.call_from_thread(
                    self._set_pack_install_status, generation, tone_id,
                    f"installing {done}/{total}  {filename}")
            except Exception:
                pass

        try:
            await asyncio.to_thread(
                library.import_tone, tone_id, progress, quiet=True,
                model_ids=model_ids)
        except Exception as e:
            if self._pack_operation_alive(generation, tone_id):
                self._pack_busy = None
                self._pack_progress_status = f"install failed: {e}"
                self._update_pack_hint()
                self.app.notify(f"Install failed: {e}", severity="error")
            return
        if not self._pack_operation_alive(generation, tone_id):
            return
        self._pack_busy = None
        self._pack_operation_target = None
        self._pack_uninstall_confirmed = False
        self._pack_uninstall_target = ()
        self.post_message(self.PackFilesInstalled(tone_id, len(model_ids)))
        self._refresh_pack_after_change(tone_id, self._view_generation)

    def _pack_uninstall_selected(self) -> None:
        """u：卸载选中的已下载模型（未勾选时 = 光标单行）。

        u 语义与 uninstall_screen 一致：活动链/库外文件拦截，preset
        引用需再次按 u 确认；卸载走模型粒度（元数据保留）。
        """
        if self._pack_busy is not None or not getattr(self, "_pack_mode", False):
            return
        picked = self._selected_pack_keys()
        todo = [m for m in (self._pack_rows.get(k) for k in picked)
                if m and m.get("local_path")]
        if not todo:
            self.app.notify("No downloaded model selected", severity="warning")
            return
        ids = sorted({m["id"] for m in todo})
        target = tuple(ids)
        if target != self._pack_uninstall_target:
            self._pack_uninstall_confirmed = False
            self._pack_uninstall_target = target
        plan = library.local_uninstall_models_plan(ids)
        if plan["active_paths"] or plan["outside_paths"]:
            self._pack_uninstall_confirmed = False
            self._pack_uninstall_target = ()
            self.app.notify(
                "Switch the active model or remove unmanaged paths "
                "before uninstalling.", severity="error")
            return
        if plan["preset_names"] and not self._pack_uninstall_confirmed:
            self._pack_uninstall_confirmed = True
            self.app.notify(
                "Presets keep their references and may not load · "
                "press u again to continue", severity="warning")
            return
        tone = self._pack_tone or self._current_tone or {}
        tone_id = int(tone.get("id") or 0)
        generation = self._begin_pack_operation("uninstall", tone_id, ids)
        if generation is None:
            return
        self._pack_progress_status = f"uninstalling 0/{len(ids)}"
        self._update_pack_hint()
        self.run_worker(
            partial(self._pack_uninstall_models, tone_id, ids,
                    bool(plan["preset_names"]), generation),
            name="pack-uninstall", exclusive=True)

    async def _pack_uninstall_models(self, tone_id: int, model_ids: list[int],
                                     allow_preset_references: bool,
                                     generation: int) -> None:
        try:
            result = await asyncio.to_thread(
                library.local_uninstall_models, model_ids,
                allow_preset_references=allow_preset_references)
        except Exception as e:
            if self._pack_operation_alive(generation, tone_id):
                self._pack_busy = None
                self._pack_progress_status = f"uninstall failed: {e}"
                self._update_pack_hint()
                self.app.notify(f"Uninstall failed: {e}", severity="error")
            return
        if not self._pack_operation_alive(generation, tone_id):
            return
        self._pack_busy = None
        self._pack_operation_target = None
        self._pack_uninstall_confirmed = False
        self._pack_uninstall_target = ()
        self.post_message(self.PackFilesUninstalled(tone_id, result["removed"]))
        self._refresh_pack_after_change(tone_id, self._view_generation)

    def _set_pack_install_status(self, generation: int, tone_id: int,
                                 message: str) -> None:
        """安装进度 → 提示条左段状态（REQ-024/025：状态变化靠左）。"""
        if not self._pack_operation_alive(generation, tone_id):
            return
        self._pack_progress_status = message
        self._update_pack_hint()

    def _refresh_pack_after_change(self, tone_id: int,
                                   generation: int) -> None:
        """安装/卸载后重拉模型行：远程 pack 重拉网络，本地 pack 重查 DB。"""
        if (not tone_id or not self._view_alive(generation)
                or not getattr(self, "_pack_mode", False)
                or int((self._pack_tone or {}).get("id") or 0) != tone_id):
            return
        if getattr(self, "_pack_remote", False):
            self._pack_loading = True
            self._pack_error = False
            self._pack_progress_status = "loading…"
            self._update_pack_hint()
            self.run_worker(partial(self._fetch_remote_models, tone_id, generation),
                            name="remote-pack")
        else:
            tone = library.get_tone(tone_id) or {}
            self._fill_pack_rows(tone.get("models") or [])
            self.refresh_pack_active(live.read_chain())
            self._update_pack_hint()

    def _chain_model_id(self, tone: dict) -> int | None:
        """The live chain's model id, when that model belongs to this tone."""
        path = live.read_chain().get("model")
        if not path:
            return None
        for m in tone.get("models") or []:
            if m.get("local_path") == path:
                return m.get("id")
        return None

    def _marquee_content(self, tone: dict, model_id: int | None) -> str:
        """Title row: the tone title plus its ids, so the summary row below
        stays short.

        MarqueeBar escapes every ``[`` before rendering, so nested markup
        tags here would show up literally — ids are plain text.
        """
        parts = [str(tone.get("title") or "")]
        tone_id = tone.get("id")
        if tone_id is not None:
            parts.append(f"TONE #{tone_id}")
        if model_id is not None:
            parts.append(f"MODEL #{model_id}")
        return " · ".join(parts)

    def _enter_description(self, tone: dict | None) -> None:
        """Description view: the two-line header carries the key metadata and
        the body shows the tone's description — the text for understanding
        how the tone is named."""
        generation = self._invalidate_view()
        tone = tone or self._current_tone or {}
        self._exit_pack_mode()
        self.border_title = "TONE DETAIL"
        self._current_tone = tone
        self._summary_mode = "tone"
        self._view_mode = "description"
        self._set_marquee(self._marquee_content(
            tone, self._chain_model_id(tone)))
        self._marquee.set_class(False, "detail-marquee--empty")
        self._set_summary(self._tone_summary(tone))
        self._ensure_verification(tone)
        colors = self._theme_colors()
        self._body.update(description_only(tone, colors=colors))
        self._rerender = lambda: self._body.update(
            description_only(tone, colors=self._theme_colors()))
        set_border_hint_layout(
            self, "", [token for token, _action in self._border_hint_actions()])
        return generation

    def _enter_selection(self, *, tone: dict | None = None,
                         models: list[dict] | None = None,
                         chain: dict | None = None,
                         kind: str | None = None,
                         origin: str | None = None,
                         focus_table: bool = False,
                         remote: bool = False) -> None:
        """Selection view: the tone's whole pack as the interactive file list.

        Enter / double-click picks a row and hot-swaps the chain slot; the
        active rows keep their ▶ markers from the live chain. ``origin``
        records how the view was entered (chain node → Esc returns there;
        view switch → Esc goes back to Description); ``focus_table`` is only
        true on an explicit user switch — cursor-follow updates must not
        steal keyboard focus from the library. ``remote`` marks a TONE3000
        pack (未下载): shell 先立起来，模型列表由 show_remote_pack 后台拉取，
        Enter 走安装二级页而不是热换链槽。
        """
        self._invalidate_view()
        tone = tone or self._current_tone or {}
        models = models if models is not None else tone.get("models") or []
        if not models:
            if not remote and focus_table:
                self.app.notify("This tone has no downloadable models",
                                severity="warning")
            if not remote:
                return
        self._pack_tone = tone
        self._pack_remote = remote
        self._pack_creator = None
        self._pack_kind = kind or "amp"
        self._pack_table.pack_kind = self._pack_kind
        self._current_tone = tone
        self.border_title = "TONE DETAIL"
        self._pack_progress_status = ""
        self._pack_error = False
        if origin is not None:
            self._pack_origin = origin
        self._view_mode = "selection"
        self._summary_mode = "tone"
        self._set_pack_mode(True)
        self._update_pack_hint()
        self._fill_pack_rows(models)
        first = next(iter(self._pack_rows.values()), None)
        self._set_marquee(self._marquee_content(
            tone, first.get("id") if first else None))
        self._marquee.set_class(False, "detail-marquee--empty")
        self._set_summary(self._tone_summary(tone))
        self._ensure_verification(tone)
        self.refresh_pack_active(chain or live.read_chain())
        self._rerender = None
        if focus_table:
            # Take keyboard focus so Esc/← (the pack table bindings) work
            # right after the view switch.
            self.call_after_refresh(self._pack_table.focus)

    @staticmethod
    def _arch_tag(architecture: str | None, colors: dict[str, str]) -> str:
        """A2 (SlimmableContainer) burns amber, IR green, older A1 amber-warn."""
        if architecture == "IR":
            return f"[b {colors['value']}]IR[/]"
        if architecture == "SlimmableContainer":
            return f"[b {colors['header']}]A2[/]"
        return f"[b {colors['warn']}]A1[/]"

    @staticmethod
    def _fmt_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        for unit in ("KB", "MB", "GB"):
            size /= 1024.0
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
        return "—"

    def _fill_pack_rows(self, models: list[dict]) -> None:
        colors = self._theme_colors()
        table = self._pack_table
        table.clear()
        self._pack_rows = {}
        self._pack_path_to_key = {}
        # 表重建 = 行集合变化：勾选态与卸载二次确认一并作废。
        self._pack_picked = set()
        self._pack_uninstall_confirmed = False
        self._pack_uninstall_target = ()
        # 表重建后光标回到 (0,0)：清掉同步记录，下次 refresh 重新对齐激活行。
        self._pack_synced_model = None
        self._pack_synced_ir = None
        for model in models:
            model_id = model.get("id")
            key = f"m{model_id}"
            self._pack_rows[key] = model
            local_path = model.get("local_path")
            filename = Path(local_path).name if local_path else (
                model.get("name") or model.get("title") or "?")
            if local_path:
                self._pack_path_to_key[local_path] = key
                try:
                    size = Path(local_path).stat().st_size
                except OSError:
                    size = None
                file_cell = f"[b]{_escape(filename)}[/]"
                size_cell = (f"[dim]{self._fmt_size(size)}[/]" if size is not None
                             else "[dim]—[/]")
            else:
                # REQ-038：未下载标识 = 斜体 (not downloaded)，不用连字符。
                file_cell = (f"[dim]○ {_escape(filename)} "
                             f"[i](not downloaded)[/i][/dim]")
                size_cell = "[dim]—[/]"
            table.add_row("\\[ ]", "·", self._arch_tag(model.get("architecture"), colors),
                          file_cell, size_cell, key=key)

    def show_pack(self, tone: dict, models: list[dict], chain: dict,
                  kind: str, *, focus_table: bool = False) -> None:
        """Pack view: the focused chain node's whole tone folder, all files.

        Enter picks a row and the app hot-swaps its chain slot. ``focus_table``
        is only true on the click path — keyboard focus must stay on the chain
        node (its ↑/↓ stepping and double-click toggle keep working) and Esc
        from the table returns there without being grabbed back.
        """
        self._exit_pack_mode()
        self._enter_selection(tone=tone, models=models, chain=chain,
                              kind=kind, origin="node",
                              focus_table=focus_table)

    def show_remote_pack(self, tone: dict) -> None:
        """TONE3000 场景的 Selection 视图：shell 先立起来（Description 同款
        头两行），模型列表后台拉取后填入 pack 表——远程文件未下载置灰，
        Enter 一行打开安装二级页（PackInstallRequested）。"""
        tone_id = int(tone.get("id") or 0)
        self._exit_pack_mode()
        self._enter_selection(tone=tone, models=[], origin="description",
                              focus_table=True, remote=True)
        if tone_id:
            generation = self._view_generation
            self._pack_loading = True
            self._pack_progress_status = "loading…"
            self._update_pack_hint()
            self.run_worker(partial(self._fetch_remote_models, tone_id, generation),
                            name="remote-pack")

    def retry_remote_pack(self) -> None:
        if (self._pack_loading or not self._pack_remote
                or not self._pack_tone):
            return
        tone_id = int(self._pack_tone.get("id") or 0)
        if not tone_id:
            return
        self._pack_loading = True
        self._pack_error = False
        self._pack_progress_status = "loading…"
        self._update_pack_hint()
        self.run_worker(
            partial(self._fetch_remote_models, tone_id, self._view_generation),
            name="remote-pack", exclusive=True)

    async def _fetch_remote_models(self, tone_id: int,
                                   generation: int | None = None) -> None:
        generation = self._view_generation if generation is None else generation
        try:
            ms = await asyncio.to_thread(tone3000.models, tone_id, a2_only=False)
        except Exception as e:
            if self._view_alive(generation) and self._pack_remote \
                    and int(self._pack_tone.get("id") or 0) == tone_id:
                self._pack_loading = False
                self._pack_error = True
                self._pack_progress_status = f"load failed: {e}"
                self._update_pack_hint()
                self.app.notify(f"Failed to load pack contents: {e}",
                                severity="warning")
            return
        if (not self._view_alive(generation)
                or not getattr(self, "_pack_remote", False)
                or self._view_mode != "selection"
                or int(self._pack_tone.get("id") or 0) != tone_id):
            return  # 视图已切走：晚到的回答不覆盖
        self._remote_models_cache[tone_id] = list(ms)
        self._pack_loading = False
        self._pack_error = False
        self._pack_progress_status = ""
        self._fill_pack_rows(ms)
        first = next(iter(self._pack_rows.values()), None)
        self._set_marquee(self._marquee_content(
            self._current_tone or {}, first.get("id") if first else None))
        self._pack_table.focus()
        self._update_pack_hint()

    def show_creator(self, username: str) -> None:
        """TOP CREATORS 作者聚焦视图（REQ-030/REQ-033）。

        detail 始终与聚焦作者对应：标题行 @名 + verified ✓（异步探测），
        正文 = bio 完整多行。REQ-033 后 Enter/双击走 @author 搜索，
        full 作者介绍页已无入口（统计信息在排行榜 6 列可见）。
        """
        generation = self._invalidate_view()
        self._exit_pack_mode()
        self._current_tone = None
        self._summary_mode = "creator"
        self._view_mode = "creator"
        self._pack_origin = "creators"
        self._pack_creator = username   # 入口即记录：晚到的旧作者回答不覆盖
        self._creator_loading = True
        self._creator_error = None
        self.border_title = "CREATOR DETAIL"
        set_border_hint_layout(
            self, "loading…",
            [token for token, _action in self._border_hint_actions()])
        self._set_marquee(self._creator_title(username), markup=True)
        self._marquee.set_class(False, "detail-marquee--empty")
        self._set_summary("")   # REQ-030：无第二行摘要
        self._set_pack_mode(False)
        # 聚焦即显示作者信息（REQ-030）；不抢焦点（用户按 ↓ 继续换作者）
        cached_bio = self._creator_bio_cache.get(str(username).lower())
        self._body.update(_escape(cached_bio) if cached_bio
                          else "[dim]loading…[/dim]")
        self.run_worker(partial(self._fetch_creator_view, username, generation),
                        name="creator-view")

    def _update_creator_hint(self, state: str) -> None:
        set_border_hint_layout(
            self, state,
            [token for token, _action in self._border_hint_actions()])

    def retry_creator_view(self) -> None:
        if self._creator_loading or self._view_mode != "creator" \
                or not self._pack_creator:
            return
        self._creator_loading = True
        self._creator_error = None
        self._update_creator_hint("loading…")
        self.run_worker(
            partial(self._fetch_creator_view, self._pack_creator,
                    self._view_generation),
            name="creator-view", exclusive=True)

    def _creator_title(self, username: str) -> str:
        """标题行：@名 + verified ✓ 徽章（已知时）。"""
        badge = (" [b $success]✓[/]"
                 if tone3000.is_verified(str(username))
                 else "")
        return f"@{_escape(username)}{badge}"

    def _ensure_creator_verification(self, username: str) -> None:
        """聚焦作者的 verified 异步探测（复用 tone 详情的探测模式）。

        结果落地时带视图守卫：仅当仍是该 creator 的聚焦视图才更新标题。
        """
        name = str(username).lower()
        if tone3000.is_verified(name) or name in self._probed_authors:
            return
        self._probed_authors.add(name)
        generation = self._view_generation

        def check() -> None:
            ok = library.tone3000.verify_username(name)
            if ok is None:
                return

            def commit() -> None:
                if ok is True:
                    try:
                        self.app.post_message(VerifiedAuthor(name))
                    except Exception:
                        pass
                if (not self._view_alive(generation)
                        or self._view_mode != "creator"
                        or self._pack_origin != "creators"
                        or self._pack_creator != username):
                    return
                self._set_marquee(self._creator_title(username), markup=True)

            try:
                self.app.call_from_thread(commit)
            except Exception:
                pass  # 视图已卸载：晚到的更新不再需要

        threading.Thread(target=check, daemon=True).start()

    async def _fetch_creator_view(self, username: str,
                                  generation: int | None = None) -> None:
        """作者资料拉取；晚到回答带视图守卫不覆盖（REQ-030 聚焦视图）。"""
        generation = self._view_generation if generation is None else generation
        try:
            info = await asyncio.to_thread(tone3000.user, username)
        except Exception as e:
            if (self._view_alive(generation)
                    and self._view_mode == "creator"
                    and self._pack_creator == username):
                self._creator_loading = False
                self._creator_error = str(e)
                cached = self._creator_bio_cache.get(str(username).lower())
                if not cached:
                    self._body.update(
                        "[bold $error]creator unavailable[/] · "
                        "press r to retry")
                self._update_creator_hint("load failed")
                self.app.notify(f"Failed to load creator: {e}", severity="warning")
            return
        if (not self._view_alive(generation)
                or self._view_mode != "creator"
                or self._pack_origin != "creators"):
            return  # 视图已切走：晚到的回答不覆盖
        if getattr(self, "_pack_creator", None) != username:
            return  # 已聚焦其他作者：晚到的旧回答不覆盖
        # 标题行 @名 + verified ✓（异步探测），正文 = bio 完整多行
        # （保留换行，markup 转义）；无第二行摘要（REQ-030）。
        self._set_marquee(self._creator_title(username), markup=True)
        self._ensure_creator_verification(username)
        bio = (info or {}).get("bio") or ""
        self._creator_bio_cache[str(username).lower()] = bio
        self._creator_loading = False
        self._creator_error = None
        self._body.update(
            _escape(bio) if bio else "[dim]No bio yet.[/dim]")
        self._update_creator_hint("ready")

    def refresh_pack_active(self, chain: dict | None = None) -> None:
        """Sync the pack view's active markers with the live chain.

        Called from show_pack, file picks, and the 0.3s app tick so external
        chain edits (preset loads, node stepping) move the ▶ markers too.
        """
        if not getattr(self, "_pack_mode", False):
            return
        chain = chain or {}
        self._pack_chain = chain
        model_path = chain.get("model")
        ir_path = chain.get("ir")
        active_path = model_path if self._pack_kind == "amp" else ir_path
        backup_attr = "_amp_model_backup" if self._pack_kind == "amp" else "_ir_backup"
        candidate_path = (getattr(self.app, backup_attr, None)
                          if active_path is None else None)
        model_idx = ir_idx = None
        for index, (key, model) in enumerate(self._pack_rows.items()):
            path = model.get("local_path")
            if path and path == active_path:
                mark = "[bold $success]▶[/]"
                model_idx = index
            elif path and path == candidate_path:
                mark = "[bold $error]▷[/]"
                ir_idx = index
            else:
                mark = "[dim]·[/]"
            try:
                if self._pack_table.get_cell(key, "sel") != mark:
                    self._pack_table.update_cell(key, "sel", mark)
            except Exception:
                pass
        # 选择=聚焦统一：链上激活槽位变更时，pack 表光标同步到该行——DataTable
        # 光标移动自带 scroll-into-view，视口以聚焦（光标）行为锚，▶ 行自然
        # 可见；▶ 三角仅作信息标记，不再参与视口铆定。仅当槽位路径真正变化
        # 才移动光标：0.3s tick 不能在用户浏览时把光标拉回（浏览中的光标位置
        # 就是用户自己的选择意向）。model 与 ir 同时变化时以 model 行为准。
        sync_index = None
        if model_idx is not None and active_path != self._pack_synced_model:
            sync_index = model_idx
        elif ir_idx is not None and candidate_path != self._pack_synced_ir:
            sync_index = ir_idx
        if sync_index is not None:
            try:
                self._pack_table.move_cursor(
                    row=sync_index, animate=False, scroll=True)
            except Exception:
                pass
        self._pack_synced_model = active_path
        self._pack_synced_ir = candidate_path

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """The pack cursor moves: the title row follows with MODEL #id."""
        if not getattr(self, "_pack_mode", False):
            return
        if event.data_table is not self._pack_table:
            return
        if self._pack_origin == "creators":
            return  # 作者视图：标题行是作者名，不被行高亮 MODEL #id 覆盖
        model = self._pack_rows.get(event.row_key.value)
        if model:
            self._set_marquee(self._marquee_content(
                self._current_tone or {}, model.get("id")))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not getattr(self, "_pack_mode", False):
            return
        if event.data_table is not self._pack_table:
            return
        model = self._pack_rows.get(event.row_key.value)
        if not model:
            return
        tone = self._pack_tone or self._current_tone or {}
        if getattr(self, "_pack_remote", False) or not model.get("local_path"):
            # 未下载行 Enter/双击 = 打开二级菜单详情页（REQ-038）。
            # 必须传 tone dict——旧实现把 model 当 tone 传，PackInstallScreen
            # 用 model 的 id 当作 tone id 拉列表/导入，内容全错。
            if not tone:
                self.app.notify("No tone metadata for this pack",
                                severity="warning")
                return
            self.post_message(self.PackInstallRequested(tone))
            return
        slot = "ir" if model.get("architecture") == "IR" else "model"
        self.post_message(self.PackFilePicked(
            slot, model["local_path"], tone.get("gear")))

    def _border_hint_actions(self) -> list:
        """DetailPane 右下角可点 token → 动作。

        Selection 视图：i install / u uninstall 常驻（REQ-038 多选批量），
        宽面板额外保留 ←/→ 视图切换 token（窄面板省略，Esc 仍可返回）。
        """
        if self._view_mode == "selection":
            if self._pack_busy is not None:
                return []
            actions = [
                ("i install", self._pack_install_selected),
                ("u uninstall", self._pack_uninstall_selected),
            ]
            if self._pack_error and not self._pack_loading:
                actions.insert(0, ("r retry", self.retry_remote_pack))
            if (self.region.width or 44) - 4 >= 56:
                actions = [
                    ("← description", lambda: self.toggle_view(-1)),
                    ("→ selection", lambda: self.toggle_view(+1)),
                ] + actions
            return actions
        if self._view_mode == "description":
            return [
                ("← description", lambda: self.toggle_view(-1)),
                ("→ selection", lambda: self.toggle_view(+1)),
            ]
        if self._view_mode == "creator" and self._creator_error:
            return [("r retry", self.retry_creator_view)]
        return []

    def on_click(self, event: MouseEvent) -> None:
        """The right-aligned border hint is a real control: switching between
        the Description and Selection views, plus REQ-038 的 i install /
        u uninstall 批量动作 token。"""
        if self._view_mode not in ("description", "selection"):
            return
        border_hint_click(self, event, self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        if self._view_mode not in ("description", "selection"):
            set_border_hint_hover(self, None)
            return
        set_border_hint_hover(
            self, border_hint_action_token(
                self, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))

    def on_leave(self, event: Leave) -> None:
        set_border_hint_hover(self, None)

    def show(self, t: dict) -> None:
        """Library focus → Description view (the default mode).

        Mouse focus decides the mode: focusing the library shows the tone's
        description; focusing a chain node shows its Selection (pack list).
        """
        self._enter_description(t)

    def show_model(self, tone: dict | None, model: dict) -> None:
        """Render a chain node's current model: FILE section (name/id/arch/path)
        on top of its owning tone, matching the chain-folder stepping view."""
        self._invalidate_view()
        self._exit_pack_mode()
        self._view_mode = None
        set_border_hint_layout(self, "", [])
        self.border_title = "TONE DETAIL"
        tone = tone or {}
        self._current_tone = tone
        self._summary_mode = "model"
        self._set_marquee(self._marquee_content(tone, model.get("id")))
        self._marquee.set_class(False, "detail-marquee--empty")
        filename = Path(model.get("local_path") or model.get("name") or "").name
        architecture = model.get("architecture") or "model"
        self._set_summary(
            f"[b $success]{_escape(str(architecture).upper())}[/] · "
            f"{_escape(filename or 'external file')}"
        )
        self._ensure_verification(tone)
        colors = self._theme_colors()
        self._body.update(metadata_table(tone, model, skip_title=True,
                                         condensed=True, colors=colors))
        self._rerender = lambda: self._body.update(
            metadata_table(tone, model, skip_title=True, condensed=True,
                           colors=self._theme_colors()))

    def show_preset(self, preset: dict, resolved: dict, *, active: bool = False,
                    dirty: bool = False) -> None:
        """Render a preset using the same aligned detail surface as tones."""
        self._invalidate_view()
        self._exit_pack_mode()
        self._current_tone = None
        self._view_mode = None
        set_border_hint_layout(self, "", [])
        name = preset.get("name") or "Preset"
        self.border_title = "PRESET DETAIL"
        self._summary_mode = "preset"
        self._set_marquee(name)
        self._marquee.set_class(False, "detail-marquee--empty")
        state = "ACTIVE" if active else "SAVED"
        if dirty:
            state += " · DIRTY"
        chain = preset.get("chain") or {}
        amp_id = chain.get("model_id")
        ir_id = chain.get("ir_model_id")
        amp = f"#{amp_id}" if amp_id is not None else "external"
        ir = f"#{ir_id}" if ir_id is not None else "bypass"
        self._set_summary(
            f"[b $accent]{state}[/] · "
            f"[b $success]AMP[/] {_escape(amp)} · "
            f"[b $success]IR[/] {_escape(ir)} · "
            f"G {self._control(resolved.get('gain', 1.0))} · "
            f"M {self._control(resolved.get('master', 1.0))} · "
            f"Q {self._control(resolved.get('quality', 1.0))}"
        )
        colors = self._theme_colors()
        self._body.update(
            preset_metadata_table(preset, resolved, active=active, dirty=dirty,
                                  colors=colors))
        self._rerender = lambda: self._body.update(
            preset_metadata_table(preset, resolved, active=active, dirty=dirty,
                                  colors=self._theme_colors()))

    def show_text(self, text: str) -> None:
        """Render plain (rich-markup) text, e.g. a preset chain summary."""
        self._invalidate_view()
        self._exit_pack_mode()
        self._current_tone = None
        self._view_mode = None
        set_border_hint_layout(self, "", [])
        self.border_title = "DETAIL"
        self._set_marquee(None)
        self._marquee.set_class(True, "detail-marquee--empty")
        self._set_summary("")
        self._body.update(text)
        self._rerender = None

    def clear(self) -> None:
        """Clear stale metadata when the table has no highlighted tone."""
        self._invalidate_view()
        self._exit_pack_mode()
        self._current_tone = None
        self._summary_mode = "empty"
        self._pack_creator = None
        self._view_mode = "description"
        self.border_title = "TONE DETAIL"
        self._set_marquee(None)
        self._marquee.set_class(True, "detail-marquee--empty")
        self._set_summary("")
        self._body.update(
            "[dim]Move the library cursor onto a tone to see its metadata here.[/dim]")
        self._rerender = None


class MeterBar(Static):
    """Bottom level meter: color-graded (green/yellow/red) + peak-hold marker.

    Refresh 0.3s from the engine level file; peaks hold ~1s then follow.
    """

    ALLOW_SELECT = False

    # ``tui.live.read_levels`` carries playback metadata after the input/output
    # levels. The meter only renders the first two values, but accepting the
    # full protocol tuple keeps the 0.3s refresh tick compatible with the
    # engine's current level file.
    levels: reactive[tuple] = reactive((0.0, 0.0), recompose=False)
    HOLD_S = 1.0

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.border_title = "LEVEL"
        self._pk_in = self._pk_out = 0.0
        self._pk_in_at = self._pk_out_at = 0.0

    def watch_levels(self, levels: tuple) -> None:
        import time

        inl, outl = levels[:2]
        now = time.monotonic()
        if inl > self._pk_in:
            self._pk_in, self._pk_in_at = inl, now
        if outl > self._pk_out:
            self._pk_out, self._pk_out_at = outl, now
        self.refresh()

    @staticmethod
    def _db(v: float) -> float:
        import math

        return -99.0 if v < 1e-5 else max(-60.0, 20.0 * math.log10(v))

    @classmethod
    def _bar(cls, v: float, pk: float, w: int = 24) -> str:
        """Color-graded bar with a peak-hold marker (▍). -60..-24 green, -24..-12
        yellow, -12..0 red; dim fill below level."""
        import math

        n = int(max(0.0, min(1.0, (cls._db(v) + 60.0) / 60.0)) * w)
        pp = int(max(0.0, min(1.0, (cls._db(pk) + 60.0) / 60.0)) * w)
        s = "█" * n + "░" * (w - n)
        if pk >= 1e-5:
            s = s[: min(pp, w - 1)] + "▍" + s[min(pp, w - 1) + 1:]
        out = []
        for i, ch in enumerate(s):
            if ch == "█":
                d = -60.0 + (i + 0.5) / w * 60.0
                color = "$success" if d < -24 else ("$warning" if d < -12 else "$error")
                out.append(f"[{color}]{ch}[/]")
            elif ch == "▍":
                out.append("[bold $text]▍[/]")
            else:
                out.append("[dim]░[/]")
        return "".join(out)

    def render(self) -> str:
        import time

        now = time.monotonic()
        inl, outl = self.levels[:2]
        # decay peaks after HOLD_S without new signal
        if now - self._pk_in_at > self.HOLD_S:
            self._pk_in = inl
        if now - self._pk_out_at > self.HOLD_S:
            self._pk_out = outl
        width = max(8, min(40, (self.size.width or 100) - 18))
        return (
            f"[b]IN [/] {self._bar(inl, self._pk_in, width)} {self._db(inl): 6.1f} dBFS\n"
            f"[b]OUT[/] {self._bar(outl, self._pk_out, width)} "
            f"{self._db(outl): 6.1f} dBFS"
        )


class DeviceChanged(Message):
    """The user picked a different audio interface — app restarts the engine."""

    def __init__(self, kind: str, name: str) -> None:
        super().__init__()
        self.kind = kind  # "in" | "out"
        self.name = name


BUFFER_CHOICES = [128, 256, 512, 1024]
SAMPLE_RATE_CHOICES = [44100, 48000, 88200, 96000]
SR_HZ = 48000  # engine default sample rate
# First pick in the INPUT/OUTPUT pickers; "" tells the engine to fall back to
# the OS default device (no --in/--out flag), re-detected on every start.
DEFAULT_DEVICE_LABEL = "System Default"


class DeviceBar(Vertical):
    """Audio settings form shown only inside AudioSettingsScreen.

    Device/buffer changes are forwarded to the app, which restarts the engine.
    """

    def __init__(self, ins: list[str] | None = None, outs: list[str] | None = None,
                 cur_in: str = "", cur_out: str = "", block: int = 256,
                 sample_rate: int = SR_HZ) -> None:
        super().__init__()
        self._ins = ins or []
        self._outs = outs or []
        self._last = {"in": cur_in, "out": cur_out,
                      "buffer": block, "sr": sample_rate}

    def compose(self) -> ComposeResult:
        in_options = self._device_options(self._ins)
        out_options = self._device_options(self._outs)
        in_value = self._device_value(self._last["in"], self._ins)
        out_value = self._device_value(self._last["out"], self._outs)
        self._last["in"], self._last["out"] = in_value, out_value
        with Horizontal(classes="audio-setting-row"):
            yield NonSelectableStatic("INPUT", classes="device-label")
            yield Select(in_options, value=in_value,
                         allow_blank=False, compact=True, id="dev-in",
                         disabled=not self._ins)
        with Horizontal(classes="audio-setting-row"):
            yield NonSelectableStatic("OUTPUT", classes="device-label")
            yield Select(out_options, value=out_value,
                         allow_blank=False, compact=True, id="dev-out",
                         disabled=not self._outs)
        with Horizontal(classes="audio-setting-row"):
            yield NonSelectableStatic("BUFFER", classes="device-label")
            yield Select(self._buffer_options(self._last["buffer"]),
                         value=self._last["buffer"], allow_blank=False,
                         compact=True, id="dev-buffer")
        with Horizontal(classes="audio-setting-row"):
            yield NonSelectableStatic("SAMPLE RATE", classes="device-label")
            yield Select(self._sample_rate_options(self._last["sr"]), value=self._last["sr"],
                         allow_blank=False, compact=True, id="dev-sr")
        self.latency = MarqueeBar("")
        self.latency.add_class("device-latency")
        yield self.latency

    @staticmethod
    def _device_options(names: list[str]) -> list[tuple[str, str]]:
        """Device picker options; the "" value means "system default / auto".

        The engine resolves an absent --in/--out to PortAudio's default device,
        so the first entry lets the user switch back to automatic selection.
        """
        if not names:
            return [("(none)", "")]
        return [(DEFAULT_DEVICE_LABEL, "")] + [(n, n) for n in names]

    @staticmethod
    def _device_value(cur: str, names: list[str]) -> str:
        """Keep the current pick ("" = system default) if it is still valid."""
        if cur in names or (names and cur == ""):
            return cur
        return names[0] if names else ""

    @staticmethod
    def _buffer_options(block: int) -> list[tuple[str, int]]:
        out = []
        for b in BUFFER_CHOICES:
            ms = b * 1000.0 / SR_HZ
            tag = " ◀" if b == block else ""
            out.append((f"{b}·{ms:.1f}ms{tag}", b))
        return out

    @staticmethod
    def _sample_rate_options(current: int) -> list[tuple[str, int]]:
        return [(f"{sr / 1000:g} kHz" + (" ◀" if sr == current else ""), sr)
                for sr in SAMPLE_RATE_CHOICES]

    def _refresh_latency(self) -> None:
        try:
            block = int(self.query_one("#dev-buffer", Select).value)
        except (TypeError, ValueError):
            block = 256
        try:
            sample_rate = int(self.query_one("#dev-sr", Select).value)
        except (TypeError, ValueError):
            sample_rate = SR_HZ
        ms = block * 1000.0 / sample_rate
        self.latency.content = (
            f"block {block} ≈ {ms:.1f} ms @ {sample_rate / 1000:g} kHz")

    def set_devices(self, ins: list[str], outs: list[str],
                    cur_in: str = "", cur_out: str = "") -> None:
        """Fill the pickers from the engine's device list; keep the current pick."""

        def fill(sel_id: str, names: list[str], cur: str) -> None:
            sel = self.query_one(f"#{sel_id}", Select)
            options = self._device_options(names)
            value = self._device_value(cur, names)
            sel.set_options(options)
            sel.value = value
            sel.disabled = False
            self._last[sel_id.removeprefix("dev-")] = value

        fill("dev-in", ins, cur_in)
        fill("dev-out", outs, cur_out)

    def on_mount(self) -> None:
        self._refresh_latency()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id not in ("dev-in", "dev-out", "dev-buffer", "dev-sr"):
            return
        kind = {"dev-in": "in", "dev-out": "out", "dev-buffer": "buffer",
                "dev-sr": "sr"}[event.select.id]
        if kind in ("buffer", "sr"):
            if event.value in (None, ""):
                return
            val = int(event.value)
        else:
            # "" is the System Default pick — a valid selection, forward it
            val = "" if event.value is None else str(event.value)
        if val == self._last[kind]:
            return  # programmatic fill (set_devices), not a user change
        self._last[kind] = val
        self.post_message(DeviceChanged(kind, val))
        if event.select.id == "dev-buffer":
            self._refresh_latency()
        elif event.select.id == "dev-sr":
            self._refresh_latency()


class AudioActionButton(Static):
    # Detail metadata is selectable; command controls must never acquire the
    # screen's text-selection highlight when clicked or dragged over.
    ALLOW_SELECT = False
    can_focus = True
    BINDINGS = [
        Binding("enter", "activate", "activate", show=False),
        Binding("space", "activate", "activate", show=False),
    ]

    def __init__(self, label: str, action_kind: str, widget_id: str) -> None:
        super().__init__(label, id=widget_id, classes="audio-action")
        self.action_kind = action_kind

    def action_activate(self) -> None:
        if self.action_kind == "settings":
            self.post_message(InterfaceBar.SettingsRequested())
        else:
            self.post_message(DeviceChanged("mute", ""))

    def on_click(self, event) -> None:
        event.stop()
        self.action_activate()


class InterfaceBar(Horizontal):
    """Compact main-screen audio surface: levels, settings and mute only."""

    class SettingsRequested(Message):
        pass

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "LEVEL"

    def compose(self) -> ComposeResult:
        yield MeterBar()
        yield AudioActionButton("AUDIO SETTINGS", "settings", "audio-settings")
        self.mute = AudioActionButton("MUTE", "mute", "audio-mute")
        yield self.mute

    def set_muted(self, muted: bool) -> None:
        self.mute.set_classes("audio-action muted" if muted else "audio-action")
        self.mute.update("MUTED" if muted else "MUTE")


class AudioSettingsScreen(GigBuddyModal):
    """Secondary page for low-frequency audio interface settings."""

    CSS = """
    AudioSettingsScreen > ModalBox { width: 72%; height: auto; margin: 4 14; }
    AudioSettingsScreen DeviceBar { height: auto; }
    AudioSettingsScreen .audio-setting-row { height: 3; align: center middle; }
    AudioSettingsScreen .device-label {
        width: 16; color: $primary; text-style: bold; content-align: right middle;
        padding-right: 2;
    }
    AudioSettingsScreen Select { width: 1fr; }
    AudioSettingsScreen .device-latency {
        height: 2; margin-top: 1; color: $text-muted; content-align: center middle;
        border-top: solid $surface-lighten-2;
    }
    """

    def __init__(self, ins: list[str], outs: list[str], cur_in: str, cur_out: str,
                 block: int, sample_rate: int) -> None:
        super().__init__()
        self._args = (ins, outs, cur_in, cur_out, block, sample_rate)

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "AUDIO SETTINGS"
        set_border_hint_layout(box, "", ["enter close", "esc close"])
        with box:
            yield DeviceBar(*self._args)

    def _confirm(self) -> None:
        self.dismiss()

    # ---- clickable border hints (REQ-024: 与其他模态一致) ----

    def _border_hint_actions(self) -> list:
        return [
            ("enter close", self._confirm),
            ("esc close", self.dismiss),
        ]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))
