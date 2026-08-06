"""GigBuddy TUI: tone library browser (left) + NAM tone management (right) + meter (bottom)

v2: pure control surface — no embedded agent. The library DB (data/gigbuddy.db)
is open to external agents via the `gigbuddy` CLI; chain edits flow to the engine
through data/live_chain.json as before.

Run: .venv/bin/python -m tui            (spawns the realtime engine automatically)
     .venv/bin/python -m tui --no-engine (engine already running externally)
"""
import argparse
import asyncio
from functools import partial
import json
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from rich.cells import cell_len
from rich.markup import escape
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.theme import Theme
from textual.widgets import Footer, Header, Static
from textual.widgets._header import (HeaderClock, HeaderClockSpace, HeaderIcon,
                                     HeaderTitle)

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from . import live  # noqa: E402
import library  # noqa: E402
from .input_screen import InputSourceScreen  # noqa: E402
from .install_screen import PackInstallScreen  # noqa: E402
from .library_panel import (LibraryPanel, LibraryTable, ToneHighlighted,
                            ToneSelected, VerifiedAuthor)  # noqa: E402
from .marquee import MarqueeBar  # noqa: E402
from .metadata import signed_fixed  # noqa: E402
from .panels import (AudioSettingsScreen, ChainPanel, DetailPane, DeviceBar,
                     DeviceChanged, InterfaceBar, MeterBar, NodeSwitchButton,
                     NodeWidget)  # noqa: E402
from .picker import TonePickerScreen  # noqa: E402
from .presets import (PresetDeleteModal, PresetNameModal, PresetNoteModal,
                      PresetPanel, PresetPickerScreen, PresetRenameModal)  # noqa: E402
from .selection import ShiftSelectableScreen  # noqa: E402
from .uninstall_screen import LocalUninstallScreen  # noqa: E402

# Success, error and idle state colors are fixed across every theme. Warning is
# intentionally theme-provided: it is a semantic attention color, not one of
# the three cross-theme state colors frozen by the UI spec.
FIXED_SEMANTIC_COLORS = {
    "error": "#d96a55",
    "success": "#8fb573",
    "state-idle": "#8a817a",
}

# Warm guitar-amp palette: tube-amber accents on a dark cabinet-brown base.
GIGBUDDY_THEME = Theme(
    name="gigbuddy",
    dark=True,
    background="#1b1512",
    surface="#261d16",
    panel="#31251a",
    boost="#3d2e1f",
    foreground="#f0e2cc",
    primary="#e59a3c",
    secondary="#8f6b46",
    accent="#f5b042",
    success="#8fb573",
    warning="#e0b34a",
    error="#d96a55",
    variables={
        "block-cursor-background": "#f5b042",
        "block-cursor-foreground": "#1b1512",
        "block-cursor-text-style": "bold",
        "input-selection-background": "#e59a3c 35%",
        # metadata field-name warm beige, read by Python-side rich rendering
        # (not used in CSS); other themes fall back to their foreground.
        "field": "#d3bf9e",
    },
)


class HeaderStatus(MarqueeBar):
    """Single-line status message overlaid in the header's top-left corner."""

    def __init__(self, *, id: str = "header-status") -> None:
        super().__init__("", id=id)

    def show_status(self, message: str, severity: str) -> None:
        self.content = message
        self.remove_class(
            "header-status--information",
            "header-status--warning",
            "header-status--error",
        )
        self.add_class(f"header-status--{severity}", "header-status--visible")
        self._cap_width()

    def _cap_width(self) -> None:
        """Cap the strip below the centered title's left edge.

        The title sits centered in the header; the notification strip must
        never cover it (REQ-015/REQ-018), so its max width is the title's
        left edge minus one column. The title's left edge depends on the
        terminal width, so the cap is computed from the live layout instead
        of a fixed CSS constant.
        """
        header = self.app.query_one(GigBuddyHeader)
        title = header.query_one(HeaderTitle)
        content = title.content_region
        title_width = cell_len(str(title.render()))  # 终端列宽（em-dash 占 2 列）
        left_edge = content.x + (content.width - title_width) // 2
        cap = max(left_edge - 1, 16)
        # MarqueeBar renders from its own width, so the strip width must be
        # set explicitly (an auto width would measure a marquee of width 0).
        self.styles.width = cap
        self.styles.max_width = cap

    def clear_status(self) -> None:
        self.content = ""
        self.remove_class(
            "header-status--visible",
            "header-status--information",
            "header-status--warning",
            "header-status--error",
        )


class GigBuddyHeader(Header):
    """Single-line application header; the title is always centered.

    Notifications live inside the header row as an overlay strip in the
    top-left corner (REQ-015/REQ-018): overlay keeps them out of the flow (the
    title never moves) and their width is capped below the title's left edge
    (they never cover the centered title nor the library panel below).
    """

    def compose(self) -> ComposeResult:
        icon = HeaderIcon().data_bind(Header.icon)
        icon.ALLOW_SELECT = False
        yield icon
        yield HeaderStatus()
        title = HeaderTitle()
        title.ALLOW_SELECT = False
        yield title
        clock = (HeaderClock().data_bind(Header.time_format)
                 if self._show_clock else HeaderClockSpace())
        clock.ALLOW_SELECT = False
        yield clock

    def _on_click(self, event) -> None:
        """Keep the application header single-line.

        Textual's default Header click handler toggles ``-tall`` (height 3),
        which shifts every panel when the title is clicked. GigBuddy uses the
        header as a fixed status bar, so clicking it must have no layout effect.
        """
        event.prevent_default()
        event.stop()


def _parse_devices(output: str) -> tuple[list[str], list[str]]:
    """Parse `realtime_cli --list` lines into (input devices, output devices)."""
    ins, outs = [], []
    for line in output.splitlines():
        m = re.match(r"\[\d+\] (.*?) \(in=(\d+) out=(\d+)", line)
        if not m:
            continue
        name, ni, no = m.group(1), int(m.group(2)), int(m.group(3))
        if ni > 0:
            ins.append(name)
        if no > 0:
            outs.append(name)
    return ins, outs


class GigBuddyApp(App):
    TITLE = "GigBuddy"
    SUB_TITLE = "Your one-stop NAM tone manager"

    CSS = """
    Screen { layout: vertical; background: $background; }
    Header { background: $panel; color: $primary; text-style: bold; }
    Header .header--title { color: $primary; text-style: bold; }
    Header .header--sub-title { color: $text-muted; text-style: none; }
    HeaderIcon { display: none; }  /* palette stays on ctrl+p only */
    GigBuddyHeader { layout: horizontal; }
    GigBuddyHeader HeaderIcon, GigBuddyHeader HeaderTitle,
    GigBuddyHeader HeaderClock, GigBuddyHeader HeaderClockSpace {
        dock: none;
    }
    GigBuddyHeader HeaderTitle {
        width: 1fr; content-align: center middle;  /* 标题居中 */
        padding-left: 10;  /* 与右侧 HeaderClock 对称，内容精确居中 */
    }
    GigBuddyHeader HeaderClock, GigBuddyHeader HeaderClockSpace { width: 10; }
    GigBuddyHeader #header-status {
        overlay: screen;  /* 通知浮层：不参与流布局，标题恒居中不动 */
        background: $panel;  /* 与 Header 同色，视觉上是 Header 的一部分 */
        visibility: hidden;
        width: 0; max-width: 0; min-width: 0; height: 1;
        padding: 0; content-align: left middle;
        text-wrap: nowrap;
        text-style: bold;
    }
    GigBuddyHeader #header-status.header-status--visible {
        visibility: visible;
        /* 基础上限 43 列 < 120 列终端的居中标题左边缘；HeaderStatus 显示时
           Python 侧按实时布局收紧宽度，窄终端下也不遮标题 */
        width: 43; max-width: 43; min-width: 0;
        padding: 0 1;
    }
    GigBuddyHeader #header-status.header-status--information { color: $success; }
    GigBuddyHeader #header-status.header-status--warning { color: $warning; }
    GigBuddyHeader #header-status.header-status--error { color: $error; }
    Footer { background: $panel; }
    #top { layout: horizontal; height: 1fr; }

    /* panels: quiet warm border, amber when something inside has focus.
       REQ-039 两级聚焦视觉：聚焦页面（focus-within）边框亮 + 内容全亮；
       非聚焦页面边框回到 quiet 色 + 内容 opacity 0.8 降半级，让焦点所在
       页面一眼可辨。模态（独立 screen）不在此列，恒为最亮。 */
    LibraryPanel, PresetPanel, ChainPanel, DetailPane {
        border: round $surface-lighten-2;
        border-title-color: $text-muted;
        border-subtitle-color: $text-disabled;
        padding: 0 1;
        opacity: 0.8;
    }
    LibraryPanel:focus-within, PresetPanel:focus-within,
    ChainPanel:focus-within, DetailPane:focus-within {
        border: round $primary;
        border-title-color: $primary;
        opacity: 1;
    }
    #left-col { width: 3fr; layout: vertical; }
    LibraryPanel { height: 3fr; }
    /* The pane hierarchy must have a bounded height. Otherwise DataTable grows
       to every row, which suppresses its scrollbar in LOCAL and TOP CREATORS. */
    LibraryPanel > TabbedContent, LibraryPanel TabPane { height: 1fr; }
    LibraryPanel TabPane { layout: vertical; }
    LibraryPanel DataTable { height: 1fr; }
    /* Remote download states are a persistent list legend, not a result row. */
    #tone-status {
        dock: bottom; height: 1; padding: 0 1;
        color: $text-muted; content-align: left middle;
    }
    #import-progress { dock: bottom; height: 1; }
    /* explicit height:3 keeps the Select's round border inside the row —
       auto height (content+border) grows to 5 rows, clipping the border and
       skewing the TYPE label off-center */
    #type-filter-local, #type-filter-tone { width: 24; height: 3; }
    #sort-filter { width: 26; height: 3; }
    /* LOCAL/TONE3000 filters share their tab strip (tone3000.com 网站右上角
      风格). They use the same quiet outline as the tab labels rather than the
       filled input surface. */
    #tone-filter-row, #local-type-filter-row, #creator-filter-row {
        dock: top; layer: filter-controls;
        width: auto; height: 3; padding: 0;
        align: right middle; background: transparent;
        /* 初始 offset 避开 tab 标签（LOCAL/TONE3000/TOP CREATORS 约 47 列）：
           首帧布局就按此定位，防止 dock 行盖住 tab 并拦截最初的点击；
           Python 侧按最后一个标签右缘做精确修正 */
        offset: 47 0;
    }
    #tone-filter-row.filter-row--compact,
    #local-type-filter-row.filter-row--compact,
    #creator-filter-row.filter-row--compact {
        dock: none; layer: default;
        width: 100%; height: 3; margin-bottom: 1;
        align: left middle;
    }
    #tone-filter-row .filter-label, #local-type-filter-row .filter-label,
    #creator-filter-row .filter-label {
        width: auto; min-width: 5; height: 3;
        margin: 0 1; content-align: left middle;
        color: $text-muted; text-style: bold;
    }
    #tone-filter-row Select, #local-type-filter-row Select, #creator-filter-row Select {
        background: $background;
        border: round $surface-lighten-2;
        margin: 0 1 0 0;
    }
    /* REQ-036：creators 条只有单个控件，Select 会被 auto 宽度撑到 60——
       定宽与 TONE3000 的 SORT 框一致（26） */
    #creator-filter-row Select { width: 26; }
    #tone-filter-row Select > SelectCurrent,
    #local-type-filter-row Select > SelectCurrent,
    #creator-filter-row Select > SelectCurrent {
        background: $background;
    }
    #tone-filter-row Select:focus, #local-type-filter-row Select:focus,
    #creator-filter-row Select:focus {
        background: $background;
        border: round $accent;
    }
    #lib-status { color: $text-muted; padding: 0 1; }
    PresetPanel > MarqueeBar, ChainPanel > MarqueeBar {
        height: 1; padding: 0 1; color: $text;
    }
    /* Keep enough room for the banner, CRUD controls, and at least a few
       preset rows even on the compact 100x32 terminal used in practice. */
    PresetPanel { height: 2fr; min-height: 9; }
    PresetPanel DataTable { height: 1fr; }

    #right-col { width: 2fr; layout: vertical; }
    /* The chain has a fixed set of rows in v0.1.  Letting it grow as 1fr leaves
       empty rows between the effect readout and the docked parameter bar on
       taller terminals.  Eighteen rows cover input/AMP/CAB, six phase-2
       placeholders, and one independent parameter row (聚焦 marquee 行已删，
       REQ-043). */
    ChainPanel { height: 18; min-height: 18; max-height: 18; }
    ChainPanel .chain-node-row {
        /* 4 = round border 2 + content 2: title line with ▲, filename with ▼ */
        height: 4; width: 100%;
        background: transparent;
        border: round $surface-lighten-2;
        border-title-color: $text-muted;   /* REQ-043: type 在框左上角 */
    }
    ChainPanel .chain-node {
        width: 1fr; height: 2; padding: 0 1;
        background: transparent; border: none;
    }
    ChainPanel .chain-node:hover {
        background: $panel-lighten-1;
    }
    ChainPanel .chain-node:focus {
        background: $panel-lighten-1;
    }
    /* INPUT 节点的高亮画在文本层（render 里 on $panel-lighten-1，只盖左侧），
       这里关掉整块 CSS 背景——否则行尾 PLAY 块会被浅色 hover 整块盖住。 */
    ChainPanel .chain-node.chain-node-input:hover,
    ChainPanel .chain-node.chain-node-input:focus {
        background: transparent;
    }
    /* 缺位节点（AMP/CAB 未加载）：NONE 占位，无背景——模块不加载但
       音频同样直通（与 BYPASS 的区别只在 ◐ 灯与文件行） */
    ChainPanel .chain-node.chain-node-empty {
        background: transparent;
    }
    ChainPanel .chain-node-row:focus-within {
        /* 聚焦只给边框提示；高亮背景留在节点内容区（.chain-node:focus），
           整行不亮。 */
        border: round $accent;
        border-title-color: $accent;
    }
    ChainPanel .chain-switch-col {
        /* 奇数内容宽（11 - padding 2 = 9）：单字符箭头精确居中，
           偶数宽会让 1 字符内容偏向一侧 0.5 格 */
        width: 11; height: 4;
        layout: vertical;
        padding: 0 1;
    }
    ChainPanel .chain-switch-btn {
        /* Fill the switch column's content box. A fixed width of 11 extends
           past the column's padded 9-cell content area, so hover background
           gets clipped while the arrow is centered against the larger box. */
        width: 1fr; height: 1; padding: 0;
        content-align: center middle;
        color: $text; background: transparent; text-style: bold;
    }
    ChainPanel .chain-switch-btn:hover {
        background: $accent; color: $background;
    }
    /* INPUT 行是单行布局（文件名 + 状态 + 行内 PLAY 块，无符号按钮）：
       行高 3 = round 边框 2 + 内容 1，节点内容区相应收为一行 */
    ChainPanel .chain-node-row-input { height: 3; }
    ChainPanel .chain-node-row-input .chain-node { height: 1; }
    ChainPanel .chain-effect {
        height: 1; padding: 0 1 0 2; color: $text-muted;
    }
    ChainPanel .chain-params {
        dock: bottom; height: 1; margin: 0; padding: 0 1;
        background: $panel; color: $text;
    }
    DetailPane { height: 1fr; }
    DetailPane MarqueeBar { height: 1; color: $text; }

    #bottom { layout: vertical; height: 5; }
    /* InterfaceBar 与内容面板同一套两级聚焦（REQ-039）：平时 LEVEL 标题
       muted + 内容 dim；聚焦其按钮（focus-within）时标题/边框转 primary。 */
    InterfaceBar {
        height: 5; layout: horizontal;
        border: round $surface-lighten-2;
        border-title-color: $text-muted;
        padding: 0 1; align: center middle;
        opacity: 0.8;
    }
    InterfaceBar:focus-within {
        border: round $primary;
        border-title-color: $primary;
        opacity: 1;
    }
    InterfaceBar MeterBar { height: 2; width: 1fr; border: none; padding: 0 1; }
    InterfaceBar .audio-action {
        height: 3; width: 18; content-align: center middle;
        border: round $secondary; text-style: bold; color: $foreground;
        margin-left: 1;
    }
    InterfaceBar #audio-mute {
        width: 9;
        content-align: center middle;
    }
    InterfaceBar .audio-action:hover, InterfaceBar .audio-action:focus {
        background: $background; color: $accent; border: round $accent;
    }
    /* Muted is the error-coloured counterpart to the outlined idle action.
       Keeping the same surface and geometry makes MUTE and MUTED read as one
       control instead of two unrelated button treatments. */
    InterfaceBar #audio-mute.muted,
    InterfaceBar #audio-mute.muted:hover,
    InterfaceBar #audio-mute.muted:focus {
        background: $background; color: $error; border: round $error;
    }

    /* tables: warm header, amber cursor row */
    /* Keep both scrollbar axes to one terminal cell. This is intentionally
       narrow for dense panes; the axis-specific properties avoid Textual's
       horizontal/vertical shorthand order being misread. */
    ScrollableContainer {
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: $secondary;
        scrollbar-color-hover: $accent;
    }
    DataTable, Tree {
        background: $surface;
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: $secondary;
        scrollbar-color-hover: $accent;
    }
    DataTable > .datatable--header {
        background: $boost; color: $primary; text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: $primary; color: $background; text-style: bold;
    }
    /* REQ-039 两级光标：面板内默认 $secondary（非聚焦页面光标降一级，仍在
       页内最显眼），面板激活（focus-within）时提回 $primary 最亮。Textual
       无 :not() 伪类，用"默认降级 + 激活提亮"双规则表达。模态里的表不在
       这 3 个面板内，光标保持全局 $primary。 */
    LibraryPanel DataTable > .datatable--cursor,
    PresetPanel DataTable > .datatable--cursor,
    DetailPane DataTable > .datatable--cursor {
        background: $secondary;
    }
    LibraryPanel:focus-within DataTable > .datatable--cursor,
    PresetPanel:focus-within DataTable > .datatable--cursor,
    DetailPane:focus-within DataTable > .datatable--cursor {
        background: $primary;
    }
    DataTable > .datatable--hover { background: $panel-lighten-1; }
    Tree > .tree--cursor {
        background: $primary; color: $background; text-style: bold;
    }
    Tree > .tree--guides { color: $secondary; }
    OptionList { border: none; background: transparent; }
    OptionList > .option-list--option-highlighted {
        background: $primary; color: $background; text-style: bold;
    }
    /* persistent borders on inputs/selects: focus only recolors, never adds
       the 2-row round border that pushes the layout (the "TYPE jumps" bug) */
    Input, Select {
        background: $surface;
        border: round $surface-lighten-2;
    }
    Select > SelectCurrent {
        height: 1; padding: 0 1; content-align: left middle;
        border: none !important; background: $surface;
    }
    Select.-textual-compact > SelectCurrent {
        border: none !important; padding: 0 1;
    }
    Input:hover, Select:hover { border: round $surface-lighten-3; }
    Input:focus, Select:focus { border: round $accent; }
    Input > .input--placeholder { color: $text-disabled; }

    /* overlays (select dropdown / command palette) share one raised
       look: lighter surface + accent border, clearly off the main background */
    SelectOverlay {
        background: $panel-lighten-1; opacity: 1;
        border: round $accent;
    }
    /* Notifications live in GigBuddyHeader; hide Textual's fallback rack so
       it cannot reserve or cover any part of the workspace. */
    ToastRack { display: none; }
    CommandPalette {
        background: $panel-lighten-1;
        border: round $accent;
    }

    #unsupported-size {
        display: none;
        layer: unsupported-size;
        position: absolute;
        width: 100%; height: 100%; offset: 0 0;
        padding: 1 2;
        content-align: center middle;
        text-align: center;
        color: $error;
        background: $background;
        border: round $error;
        text-style: bold;
    }
    #unsupported-size.unsupported-size--visible { display: block; }

    /* tabs: Textual's built-in Tabs{height:2} + Tab{height:1} leave zero content
       rows under a round border (labels vanish) and clip the bottom border —
       both overridden here to height:3 (border 2 + label 1) */
    TabbedContent > ContentTabs { height: 3; }
    TabbedContent Tab {
        height: 3;
        padding: 0 2; margin: 0 1 0 0;
        color: $text-muted; background: $background; text-style: none;
        border: round $surface-lighten-2;
    }
    TabbedContent Tab:hover { color: $foreground; background: $boost; }
    TabbedContent Tab.-active {
        color: $accent; background: $background; text-style: bold;
        border: round $accent;
    }
    /* Textual themes may supply a focus fill for the active tab. The tab is a
       navigation marker, not a destructive/action button, so keep it outlined. */
    TabbedContent Tab.-active:hover { background: $background; }
    """

    COMMAND_PALETTE_BINDING = "ctrl+p"  # phosphor-style command menu
    BINDINGS = [
        Binding("/", "focus_search", "search"),
        Binding("t", "next_theme", "theme"),
        Binding("g", "bump_gain(-0.1)", "gain -"),
        Binding("G", "bump_gain(+0.1)", "gain +"),
        Binding("m", "bump_master(-0.05)", "master -"),
        Binding("M", "bump_master(+0.05)", "master +"),
        Binding("p", "open_preset_picker", "preset…"),
        Binding("ctrl+s", "save_preset", "save preset"),
        Binding("ctrl+shift+s", "save_preset_as", "save preset as", show=False),
        # REQ-017: preset 应用改的是链配置（live_chain.json）——undo/redo
        # 即链配置快照的恢复/还原（ctrl+shift+z = redo；无 y 键冲突）
        Binding("ctrl+z", "undo_chain", "undo preset"),
        Binding("ctrl+shift+z", "redo_chain", "redo preset"),
        Binding("q", "bump_quality(-0.05)", "quality -"),
        Binding("Q", "bump_quality(+0.05)", "quality +"),
        # 干声试听播放控制（输入源为干声文件时生效；库/预设表聚焦时 space 仍为行选中）
        Binding("space", "playback_toggle", "play/pause", show=False),
        Binding("s", "playback_stop", "stop", show=False),
        Binding("l", "playback_loop", "loop", show=False),
        # no single-key quit: Ctrl+C twice (from any screen/modal) exits
        Binding("ctrl+c", "request_quit", "quit (×2)", show=False,
                priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield GigBuddyHeader(show_clock=True)
        with Vertical():
            with Horizontal(id="top"):
                with Vertical(id="left-col"):
                    yield LibraryPanel()
                    yield PresetPanel()
                with Vertical(id="right-col"):
                    yield ChainPanel()
                    yield DetailPane()
            with Vertical(id="bottom"):
                yield InterfaceBar()
        yield Footer()
        yield Static("Minimum terminal size: 80x32", id="unsupported-size")

    def __init__(self, dev_in: str = "", dev_out: str = "", in_ch: int = 0,
                 spawn_engine: bool = True, theme: str | None = None) -> None:
        super().__init__()
        # Status messages are rendered by GigBuddyHeader instead of Textual's
        # floating ToastRack, which would cover the library tables.
        self._disable_notifications = True
        self._dev_in = dev_in
        self._dev_out = dev_out
        self._in_ch = in_ch
        self._spawn_engine = spawn_engine
        self._engine: subprocess.Popen | None = None
        self._block = 256
        self._sr = 48000
        self._audio_ins: list[str] = []
        self._audio_outs: list[str] = []
        # double-click toggles: remembered values for restoring IR / amp gain
        self._ir_backup: str | None = None
        self._amp_model_backup: str | None = None
        self._master_backup: float | None = None
        self._last_quit_at = 0.0  # Ctrl+C twice within QUIT_WINDOW_S exits
        # 播放控制操作时间戳：操作后短暂抑制 level.json 回传覆盖，
        # 避免引擎处理链延迟期间 play_state 来回跳变（0.1s tick 旧状态覆盖）
        self._playback_op_ts = 0.0
        self._header_status_timer = None
        self._header_status_identity: str | None = None
        self._device_request_generation = 0
        self._save_confirm_name: str | None = None
        self._save_confirm_at = 0.0
        self._save_confirm_chain: str | None = None
        self.register_theme(GIGBUDDY_THEME)
        # Pin danger/state colors across every theme (built-in ones included)
        # before the first CSS generation picks the theme.
        for th in self.available_themes.values():
            th.variables.update(FIXED_SEMANTIC_COLORS)
        self.theme = theme or GIGBUDDY_THEME.name

    def get_default_screen(self):
        """Use the selection-aware screen for the main application surface."""
        return ShiftSelectableScreen(id="_default")

    QUIT_WINDOW_S = 1.5

    def action_request_quit(self) -> None:
        """Copy a text selection, or require two Ctrl+C presses to quit.

        Bound at app level, so it works from every screen and modal; the
        command palette's Quit entry exits immediately. A selected detail
        table takes the usual terminal shortcut precedence over quitting.
        """
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.screen.clear_selection()
            self._last_quit_at = 0.0
            self.notify("Selected text copied")
            return
        now = time.monotonic()
        if now - self._last_quit_at < self.QUIT_WINDOW_S:
            self.exit()
        else:
            self._last_quit_at = now
            self.notify("Press ctrl+c again to quit")

    def get_system_commands(self, screen) -> SystemCommand:
        """Command palette (ctrl+p) entries — phosphor-style action menu."""
        yield from super().get_system_commands(screen)

        yield SystemCommand(title="Search TONE3000…", help="focus the library search box",
                            callback=self.action_focus_search, discover=True)
        yield SystemCommand(title="Gain -0.1", help="decrease input gain",
                            callback=lambda: self.action_bump_gain(-0.1))
        yield SystemCommand(title="Gain +0.1", help="increase input gain",
                            callback=lambda: self.action_bump_gain(0.1))
        yield SystemCommand(title="Master -0.05", help="decrease output volume",
                            callback=lambda: self.action_bump_master(-0.05))
        yield SystemCommand(title="Master +0.05", help="increase output volume",
                            callback=lambda: self.action_bump_master(0.05))
        yield SystemCommand(title="Quality -0.05", help="decrease model quality",
                            callback=lambda: self.action_bump_quality(-0.05))
        yield SystemCommand(title="Quality +0.05", help="increase model quality",
                            callback=lambda: self.action_bump_quality(0.05))
        yield SystemCommand(title="Preset…", help="load a named chain preset (also: p)",
                            callback=self.action_open_preset_picker, discover=True)
        yield SystemCommand(title="Save active preset", help="save the current chain (ctrl+s twice)",
                            callback=self.action_save_preset, discover=True)
        yield SystemCommand(title="Save preset as…", help="create a new named preset",
                            callback=self.action_save_preset_as, discover=True)
        yield SystemCommand(title="Audio settings…", help="input, output, buffer and sample rate",
                            callback=self.action_open_audio_settings, discover=True)
        yield SystemCommand(title="Next theme", help="cycle color themes (also: t)",
                            callback=self.action_next_theme, discover=True)
        yield SystemCommand(title="Quit", help="quit GigBuddy",
                            callback=self.action_quit, discover=True)

    def _on_notify(self, event) -> None:
        """Show the newest notification in the single-line application header."""
        notification = event.notification
        self._notifications.clear().add(notification)
        message = notification.message
        if notification.title:
            message = f"{notification.title}: {message}"

        try:
            self.query_one(HeaderStatus).show_status(message, notification.severity)
        except NoMatches:
            # Notifications raised while the initial screen is composing are
            # still retained in ``_notifications`` and will be shown once the
            # header is mounted by the next notification.
            pass

        if self._header_status_timer is not None:
            self._header_status_timer.stop()
        identity = notification.identity
        self._header_status_identity = identity
        self._header_status_timer = self.set_timer(
            max(0.0, notification.time_left),
            partial(self._clear_header_status, identity),
            name="clear header status",
        )

    def _clear_header_status(self, identity: str) -> None:
        """Clear a status only if it still belongs to the notification timer."""
        if identity != self._header_status_identity:
            return
        self._header_status_identity = None
        self._header_status_timer = None
        self._notifications.clear()
        try:
            self.query_one(HeaderStatus).clear_status()
        except NoMatches:
            pass

    def action_next_theme(self) -> None:
        themes = list(self.available_themes)
        i = themes.index(self.theme)
        self.theme = themes[(i + 1) % len(themes)]
        self.notify(f"Theme: {self.theme}")

    def on_mount(self) -> None:
        # REQ-017: 链配置撤销/重做栈（preset 应用快照），启动清空。
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._ensure_engine()
        self.set_interval(0.1, self.refresh_from_files)
        self.query_one("#lib-table-local").focus()
        self._device_request_generation += 1
        self.run_worker(partial(self._load_devices,
                                self._device_request_generation), name="devices")
        self._update_unsupported_size()

    def on_resize(self, _event) -> None:
        self._update_unsupported_size()

    def _update_unsupported_size(self) -> None:
        """Keep the minimum-size state tied to the live terminal region."""
        try:
            overlay = self.query_one("#unsupported-size", Static)
        except NoMatches:
            return
        unsupported = self.size.width < 80 or self.size.height < 32
        overlay.set_class(unsupported, "unsupported-size--visible")

    def _ensure_engine(self) -> None:
        """Spawn (or restart) the engine when the chain has a signal source and
        no engine is running. Without this a fresh chain-less boot would spawn
        the engine into an immediate silent exit (realtime_cli --live requires
        a model) — the user gets a hint instead, and picking a tone later starts
        audio. A dry-file input starts the engine even with AMP bypassed
        (model=null) so playback still runs through the chain.
        Also recovers from engine crashes via the 0.1s tick."""
        if not self._spawn_engine:
            return
        if self._engine is not None and self._engine.poll() is None:
            return
        cfg = live.read_chain()
        model_path = cfg.get("model") or ""
        inp = live.chain_input(cfg)
        dry_path = inp.get("file") if inp.get("source") == "file" else ""
        has_source = ((model_path and Path(model_path).exists())
                      or (dry_path and Path(dry_path).exists()))
        if not has_source:
            if self._engine is not None:
                self.notify("Engine stopped — pick a tone to restart audio",
                            severity="warning")
            return
        self._start_engine()

    def _start_engine(self) -> None:
        """Spawn realtime_cli as a child; it hot-swaps via live_chain.json and feeds
        level.json back. Killed on TUI exit. Use --no-engine if running it externally."""
        root = Path(__file__).resolve().parent.parent
        cmd = [str(root / "bin" / "realtime_cli"),
               "--live", str(live.CHAIN_FILE), "--level-file", str(live.LEVEL_FILE),
               "--root", str(root)]   # REQ-035 portable：chain 内相对路径按根解析
        if self._dev_in:
            cmd += ["--in", self._dev_in]
        if self._dev_out:
            cmd += ["--out", self._dev_out]
        if self._in_ch:
            cmd += ["--ch", str(self._in_ch)]
        if self._block:
            cmd += ["--block", str(self._block)]
        if self._sr:
            cmd += ["--sr", str(self._sr)]
        try:
            log = open(root / "data" / "engine.log", "w")
            self._engine = subprocess.Popen(cmd, stdout=log, stderr=log,
                                            stdin=subprocess.DEVNULL)
        except FileNotFoundError as e:
            self.notify(f"(engine spawn failed: {e})", severity="error")

    def on_unmount(self) -> None:
        self._device_request_generation += 1
        if self._header_status_timer is not None:
            self._header_status_timer.stop()
            self._header_status_timer = None
        self._kill_engine()

    def _device_request_alive(self, generation: int) -> bool:
        return (generation == self._device_request_generation
                and bool(getattr(self, "is_mounted", False)))

    async def _load_devices(self, generation: int | None = None) -> None:
        """Enumerate audio interfaces via `realtime_cli --list` and fill the DeviceBar.

        Retries once after 12s (the engine may hold the audio device); on failure
        the pickers stay enabled with the CLI defaults so audio is still usable.
        """
        if generation is None:
            self._device_request_generation += 1
            generation = self._device_request_generation
        root = Path(__file__).resolve().parent.parent
        for attempt in range(2):
            try:
                out = await asyncio.to_thread(
                    subprocess.run,
                    [str(root / "bin" / "realtime_cli"), "--list"],
                    capture_output=True, text=True, timeout=12)
                ins, outs = _parse_devices(out.stdout or "")
                if (ins or outs) and self._device_request_alive(generation):
                    self._audio_ins, self._audio_outs = ins, outs
                    for bar in self.query(DeviceBar):
                        bar.set_devices(ins, outs, self._dev_in or "", self._dev_out or "")
                    return
            except Exception:
                pass
            await asyncio.sleep(1)
        if self._device_request_alive(generation):
            self.notify("Device list unavailable — keep IN/OUT defaults", severity="warning")

    def _kill_engine(self) -> None:
        if self._engine:
            self._engine.terminate()
            try:
                self._engine.wait(timeout=3)
            except Exception:
                self._engine.kill()
            self._engine = None

    def on_device_changed(self, event: DeviceChanged) -> None:
        """Interface changes restart only the isolated realtime engine."""
        if event.kind == "mute":
            # chain-level toggle, works regardless of engine ownership
            cfg = live.read_chain()
            if float(cfg.get("master", 1.0)) > 0:
                self._master_backup = float(cfg.get("master", 1.0))
                cfg["master"] = 0.0
                note = "MUTED (click again to restore)"
            else:
                cfg["master"] = getattr(self, "_master_backup", None) or 1.0
                note = f"Unmuted → master {signed_fixed(cfg['master'])}"
            live.write_chain(cfg)
            self.query_one(ChainPanel).chain = cfg
            self.query_one(InterfaceBar).set_muted(cfg["master"] <= 0)
            self.notify(note)
            return
        if event.kind == "buffer":
            self._block = int(event.name)
        elif event.kind == "sr":
            self._sr = int(event.name)
        elif event.kind == "in":
            self._dev_in = event.name
        elif event.kind == "out":
            self._dev_out = event.name
        if not self._spawn_engine:
            self.notify("Audio setting recorded for this session; external engine not restarted")
            return
        self._kill_engine()
        self._start_engine()
        self.notify(f"Engine restarted · IN {self._dev_in or 'default'} · "
                    f"OUT {self._dev_out or 'default'} · block {self._block} · "
                    f"SR {self._sr / 1000:g} kHz")

    def on_interface_bar_settings_requested(self, _event: InterfaceBar.SettingsRequested) -> None:
        self.action_open_audio_settings()

    def action_open_audio_settings(self) -> None:
        self.push_screen(AudioSettingsScreen(
            self._audio_ins, self._audio_outs, self._dev_in, self._dev_out,
            self._block, self._sr))

    def on_pack_install_screen_installed(self, event: PackInstallScreen.Installed) -> None:
        """Pack installed: toast + library refresh + show the tone in the detail pane."""
        self.notify(f"Installed {event.count} file(s) from tone {event.tone_id}")
        panel = self.query_one(LibraryPanel)
        panel._fingerprint = None
        panel.refresh_rows()
        self.on_tone_selected(ToneSelected(event.tone_id))

    def on_local_uninstall_screen_uninstalled(
            self, event: LocalUninstallScreen.Uninstalled) -> None:
        panel = self.query_one(LibraryPanel)
        panel.clear_local_selection()
        panel._fingerprint = None
        panel.refresh_rows()
        self.query_one(DetailPane).clear()
        self.notify(f"Uninstalled {event.count} file(s) · metadata retained")

    def refresh_from_files(self) -> None:
        """0.3s tick: meters + chain panel + library rows follow the current state"""
        # The interval may fire once while Textual is tearing down a test or
        # closing the app. Ignore that final tick instead of querying detached
        # widgets.
        try:
            meter = self.query_one(MeterBar)
            chain = self.query_one(ChainPanel)
            library_panel = self.query_one(LibraryPanel)
            preset_panel = self.query_one(PresetPanel)
            detail = self.query_one(DetailPane)
        except NoMatches:
            return
        self._ensure_engine()   # restart after crash / start after picking a tone
        in_lvl, out_lvl, play_state, play_pos = live.read_levels()
        meter.levels = (in_lvl, out_lvl)
        cfg = live.read_chain()
        self._clear_external_bypass_candidates(cfg)
        chain.chain = cfg
        # 用户刚操作播放控制（写入 chain 后引擎处理有延迟）：窗口内不用
        # level.json 的旧 play_state 覆盖，避免 PLAY/PAUSE 来回跳变
        if time.monotonic() - self._playback_op_ts > 0.4:
            chain.update_playback(play_state, play_pos)  # 引擎实际播放状态（0.1s 回传）
        master = float(cfg.get("master", 1.0))
        if master > 0:
            self._master_backup = master
        self.query_one(InterfaceBar).set_muted(master <= 0)
        detail.refresh_pack_active(cfg)  # pack 视图的 ▶ 标记跟随外部链变更
        library_panel.check_active_tab()
        library_panel.refresh_rows()
        preset_panel.refresh_presets()

    def _clear_external_bypass_candidates(self, cfg: dict) -> None:
        """Drop process-local bypass recovery after an external chain edit.

        ``null`` is intentionally the persisted representation for both an
        empty slot and a bypassed slot, so the value alone cannot identify who
        wrote it.  ``tui.live`` records fingerprints for this process's own
        atomic writes; a different fingerprint means the candidates are no
        longer trustworthy and must not turn an external empty slot into
        BYPASS on the next refresh.
        """
        if not (self._amp_model_backup or self._ir_backup):
            return
        current = live.chain_file_fingerprint()
        expected = live.last_chain_write_fingerprint()
        if expected is None or current == expected:
            return
        self._amp_model_backup = None
        self._ir_backup = None

    def _bump(self, key: str, delta: float) -> None:
        cfg = live.read_chain()
        ranges = {"gain": (0.0, 10.0), "master": (0.0, 10.0)}
        lo, hi = ranges[key]
        previous = float(cfg.get(key, live.CHAIN_PARAMETER_DEFAULTS[key]))
        value = previous + delta
        cfg[key] = round(max(lo, min(hi, value)), 2)
        if key == "master":
            if previous > 0:
                self._master_backup = previous
            if cfg[key] > 0:
                self._master_backup = cfg[key]
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        if key == "master":
            self.query_one(InterfaceBar).set_muted(cfg["master"] <= 0)

    def _set_chain_param(self, key: str, value: float) -> None:
        """手动填写参数（REQ-021）：绝对设置，走与 g·G 同一条写链路径。

        quality 仍按 0..1 钳制（SlimmableContainer 子模型尺寸）。
        """
        cfg = live.read_chain()
        lo, hi = (0.0, 1.0) if key == "quality" else (0.0, 10.0)
        value = max(lo, min(hi, value))
        if key == "master":
            previous = float(cfg.get(key, live.CHAIN_PARAMETER_DEFAULTS[key]))
            if previous > 0:
                self._master_backup = previous
        cfg[key] = round(value, 2)
        if key == "master" and cfg[key] > 0:
            self._master_backup = cfg[key]
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        if key == "master":
            self.query_one(InterfaceBar).set_muted(cfg["master"] <= 0)

    def action_bump_gain(self, delta: float) -> None:
        self._bump("gain", delta)

    def action_bump_master(self, delta: float) -> None:
        self._bump("master", delta)

    def action_bump_quality(self, delta: float) -> None:
        """A2 model quality (SlimmableContainer sub-model size), clamped 0..1.

        1.0 = full precision (default), lower = lighter CPU. A1 models ignore it.
        """
        cfg = live.read_chain()
        q = round(float(cfg.get("quality", live.CHAIN_PARAMETER_DEFAULTS["quality"])) + delta, 2)
        cfg["quality"] = max(0.0, min(1.0, q))
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg

    def action_focus_search(self) -> None:
        self.query_one(LibraryPanel).focus_search()

    # ---- 干声试听：播放控制（space/s/l）与输入源选择器 ----

    def _playback_edit(self, edit) -> None:
        """播放控制公共路径：读链 → 改 input → 写回 → 刷 INPUT 行"""
        cfg = live.read_chain()
        if not cfg:
            return
        inp = live.chain_input(cfg)
        if inp.get("source") != "file":
            self.notify("Instrument input active — click the INPUT row to pick a dry file")
            return
        edit(inp)
        cfg["input"] = inp
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        self._playback_op_ts = time.monotonic()

    def action_playback_toggle(self) -> None:
        self._playback_edit(
            lambda inp: inp.__setitem__(
                "state", live.PLAY_PAUSED if inp.get("state") == live.PLAY_PLAYING
                else live.PLAY_PLAYING))

    def action_playback_stop(self) -> None:
        self._playback_edit(lambda inp: inp.__setitem__("state", live.PLAY_STOPPED))

    def action_playback_loop(self) -> None:
        self._playback_edit(lambda inp: inp.__setitem__("loop", not inp.get("loop", False)))

    def on_input_node_widget_playback_requested(self, event) -> None:
        """INPUT 节点聚焦时 space/s/l → 同全局播放控制"""
        if event.action == "toggle":
            self.action_playback_toggle()
        elif event.action == "stop":
            self.action_playback_stop()
        elif event.action == "loop":
            self.action_playback_loop()

    def on_input_node_widget_source_requested(self, event) -> None:
        """聚焦 INPUT 节点后按 Enter → 打开输入源选择器（干声试听）"""
        self.action_open_input_source()

    def _unload_slot(self, key: str, node_kind: str, note: str) -> None:
        """卸载链槽（model/ir → null）：模块不加载、音频直通。

        watch_chain 无法从链值区分"卸载"与"双击 BYPASS"（都是 null）——
        BYPASS 保留内容显示，卸载必须强制重置节点为 NONE 空态。后续
        0.1s tick 的 watch_chain 因 label 已是 NONE 走空态分支，保持稳定。
        """
        cfg = live.read_chain()
        cfg[key] = None
        live.write_chain(cfg)
        panel = self.query_one(ChainPanel)
        panel.chain = cfg
        node = next((n for n in panel.query(NodeWidget)
                     if n.kind == node_kind), None)
        if node is not None:
            node.set_title(None)
            node.set_label("NONE")
            node.set_bypassed(False)
            node.set_class(True, "chain-node-empty")
        self.notify(note)

    def on_node_widget_delete_requested(self, event) -> None:
        """d 键删除聚焦模块：置 null 空槽（模块不加载、音频直通）。

        与双击 BYPASS 不同：BYPASS 保留内容显示（加载但直通），删除是
        缺位（NONE 灰底）。恢复 BYPASS 备份一并清除。

        链值已为 null（BYPASS 态或 preset 缺位）时不再提前返回：删除
        语义降级为清掉残留的 BYPASS/内容显示（REQ-016 bug②——此前
        BYPASS 节点按 d 被 "already empty" 守卫拒绝，看起来 delete 无效）。
        """
        key = "model" if event.kind == "amp" else "ir"
        panel = self.query_one(ChainPanel)
        node = next((n for n in panel.query(NodeWidget)
                     if n.kind == event.kind), None)
        if not live.read_chain().get(key):
            # 链上已无此模块：仅当节点本就显示空态（NONE）时无需动作；
            # 节点残留内容/BYPASS 标记时必须清掉。
            if node is None or (node.label in {"—", "NONE"} and not node.title):
                self.notify(f"{event.kind.upper()} slot already empty")
                return
        if key == "model":
            self._amp_model_backup = None
            note = "AMP unloaded — empty slot (audio passes through)"
        else:
            self._ir_backup = None
            note = "CAB unloaded — empty slot (audio passes through)"
        self._unload_slot(key, event.kind, note)

    def action_open_input_source(self) -> None:
        self.push_screen(InputSourceScreen())

    def on_input_source_screen_source_changed(self, event) -> None:
        """输入源切换完成：刷新链面板 INPUT 行"""
        self.query_one(ChainPanel).chain = event.chain

    def action_open_preset_picker(self) -> None:
        self.push_screen(PresetPickerScreen())

    def action_save_preset(self) -> None:
        active = library.preset_current()
        if not active:
            self._save_confirm_name = None
            self._save_confirm_chain = None
            self.action_save_preset_as()
            return
        chain_signature = json.dumps(
            library.chain_get(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if not library.preset_is_dirty(active):
            self._save_confirm_name = None
            self._save_confirm_chain = None
            self.notify(f"Preset '{active}' is already up to date")
            return
        now = time.monotonic()
        if (self._save_confirm_name == active and
                self._save_confirm_chain == chain_signature and
                now - self._save_confirm_at < 2.0):
            p = library.preset_save(active)
            self._save_confirm_name = None
            self._save_confirm_at = 0.0
            self._save_confirm_chain = None
            self.query_one(PresetPanel)._fingerprint = None
            self.query_one(PresetPanel).refresh_presets()
            self.notify(f"Preset '{p['name']}' overwritten")
            return
        self._save_confirm_name = active
        self._save_confirm_at = now
        self._save_confirm_chain = chain_signature
        self.notify(f"Press ctrl+s again within 2s to overwrite '{active}'")

    def action_save_preset_as(self) -> None:
        self.push_screen(PresetNameModal())

    def on_click(self, event) -> None:
        """Click routing for the chain panel's clickable rows and switch buttons.

        NodeWidget owns keyboard focus, while the row shell is a larger visual
        hit target. Textual forwards mouse events to the deepest widget under
        the pointer, so route by hit-testing the click coordinates here.
        """
        if event.screen_x is None:
            return
        # Route by coordinates first: hit-testing can land on overlapping
        # siblings, so check the switch-button regions directly (▲ = title
        # line, ▼ = filename line — two rows, no dead space). The node kind
        # (amp | cab) comes from the row's own node — the row class names the
        # position, not the routing kind.
        for col in self.query(".chain-switch-col"):
            if col.region.contains(event.screen_x, event.screen_y):
                row = col.parent
                node = next((n for n in row.query(NodeWidget)), None)
                kind = node.kind if node else "amp"
                event.stop()
                self._focus_node(kind)
                up = next((b for b in col.query(NodeSwitchButton)
                           if b.direction > 0), None)
                down = next((b for b in col.query(NodeSwitchButton)
                             if b.direction < 0), None)
                if up is not None and up.region.contains(event.screen_x, event.screen_y):
                    self._switch_chain_model(kind, -1)
                elif down is not None and down.region.contains(event.screen_x, event.screen_y):
                    self._switch_chain_model(kind, +1)
                return
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        if widget.has_class("chain-node"):
            # Clicking a node focuses it AND opens its Selection (pack list)
            # in the detail pane — mouse focus decides the detail mode
            # (library → description, chain → selection). Double-click
            # additionally toggles bypass.
            event.stop()
            if getattr(widget, "kind", None) == "input":
                # INPUT 节点：单击聚焦，双击打开输入源选择器
                if getattr(event, "chain", 1) >= 2:
                    self.action_open_input_source()
                else:
                    self._focus_node("input")
                return
            self._focus_node(widget.kind)
            if getattr(event, "chain", 1) >= 2:
                # 双击先切换（bypass/恢复）再开 pack：pack 用切换后的链状态
                # 决定去向——否则恢复时槽位仍是 null，_show_node_pack 会把
                # detail 清成"未选中"，违背"detail 永远跟点选一致"。
                self._toggle_node(widget.kind)
            self._show_node_pack(widget.kind)
            return
        # 兜底：链节点框内任意位置（边框、空白、未命中的子区域）→ 聚焦该框。
        for row in self.query(".chain-node-row"):
            if row.region.contains(event.screen_x, event.screen_y):
                node = next((n for n in row.query(NodeWidget)), None)
                if node is None:
                    return
                event.stop()
                self._focus_node(node.kind)
                self._show_node_pack(node.kind)
                return
        # 链面板的其余空白（节点行之间的空隙、effect/params 之外的区域）：
        # 点击聚焦面板本身，←/→ 即可切换 detail 视图。
        panel = self.query_one(ChainPanel)
        if panel.region.contains(event.screen_x, event.screen_y):
            event.stop()
            panel.focus()

    def _focus_node(self, kind: str) -> None:
        node = next((n for n in self.query(NodeWidget) if n.kind == kind), None)
        if node:
            node.focus()

    def _show_node_pack(self, kind: str) -> None:
        """Open the focused node's tone pack (all files) in the detail pane."""
        cfg = live.read_chain()
        path = cfg.get("model" if kind == "amp" else "ir")
        if not path:
            node = next((n for n in self.query(NodeWidget)
                         if n.kind == kind), None)
            if node is not None and node.bypassed:
                # 双击 BYPASS：槽位置 null 但节点仍显示当前内容——detail 保持
                # 现状（该节点的 pack 已在显示），不能清成"未选中"。
                return
            self.query_one(DetailPane).clear()
            return
        models = library.local_models_by_tone(path)
        if not models:
            self.query_one(DetailPane).clear()
            return
        tone = library.get_tone(models[0]["tone_id"]) or {}
        all_models = tone.get("models") or models
        self.query_one(DetailPane).show_pack(tone, all_models, cfg, kind)

    def _toggle_node(self, kind: str) -> None:
        """Double-click on a node: AMP/CAB bypass / restore (engine直通).

        Bypass keeps the current file displayed and flags the node; the engine
        passes the signal through (model=null / ir=null). Amplifier mute via
        gain is still reachable with G-/G+ keys — the double-click now means
        bypass, not silence.
        """
        cfg = live.read_chain()
        if kind == "cab":
            if cfg.get("ir"):
                self._ir_backup = cfg["ir"]
                cfg["ir"] = None  # engine treats null as bypass (pass through)
                note = "CAB bypass (double-click to restore)"
            elif self._ir_backup:
                restored = self._ir_backup
                cfg["ir"] = restored
                self._ir_backup = None
                note = f"CAB restored → {live.short_name(restored)}"
            else:
                self.notify("CAB: nothing to restore")
                return
        else:  # amp bypass: model=null → engine passes input through
            if cfg.get("model"):
                self._amp_model_backup = cfg["model"]
                cfg["model"] = None
                note = "AMP bypass (double-click to restore)"
            elif self._amp_model_backup:
                restored = self._amp_model_backup
                cfg["model"] = restored
                self._amp_model_backup = None
                note = f"AMP restored → {live.short_name(restored)}"
            else:
                self.notify("AMP: nothing to restore")
                return
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        self.notify(note)

    def on_preset_picker_screen_loaded(self, event: PresetPickerScreen.Loaded) -> None:
        self._apply_preset(event.name)

    def on_preset_panel_activated(self, event: PresetPanel.Activated) -> None:
        self._apply_preset(event.name)

    def on_preset_panel_highlighted(self, event: PresetPanel.Highlighted) -> None:
        """Preset row highlighted: mirror its chain summary in the detail pane."""
        # PresetPanel publishes its initial row while the screen is mounting.
        # It must not replace the library detail unless the user is actually
        # navigating the preset list; detail always follows the focused pane.
        focused = self.focused
        if focused is None or not any(
                isinstance(ancestor, PresetPanel)
                for ancestor in focused.ancestors_with_self):
            return
        p = event.preset
        if not p:
            self.query_one(DetailPane).clear()
            return
        try:
            ch = library.preset_resolved_chain(p["name"])
        except ValueError as e:
            self.query_one(DetailPane).show_text(escape(str(e)))
            return
        active = library.preset_current() == p["name"]
        dirty = active and library.preset_is_dirty(p["name"])
        self.query_one(DetailPane).show_preset(
            p, ch, active=active, dirty=dirty)

    def _apply_preset(self, name: str) -> None:
        self._save_confirm_name = None
        self._save_confirm_chain = None
        # preset 是全新链：双击 BYPASS 的备份作废，否则 preset 缺位槽会被
        # _set_node 的备份判断误显示为 BYPASS（REQ-016 bug①）。
        self._amp_model_backup = None
        self._ir_backup = None
        # REQ-017: 应用前快照入 undo 栈（redo 清空）。失败路径不入栈：
        # preset_load 抛错时链未变，快照毫无意义。
        self._push_preset_undo()
        try:
            cfg = library.preset_load(name)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        self.query_one(ChainPanel).chain = cfg
        self.notify(f"Preset '{name}' loaded — ctrl+z undo")

    # ---- REQ-017: preset 链配置撤销/重做 ----------------------------------

    # 快照域 = preset 涉及的链配置（与 preset_save 内容一致）；input 输入源
    # 按既有语义"preset 不存输入源"不入快照——preset 应用保留输入源，undo
    # 恢复的也只是 preset 内容域，input 始终跟随当前链不变。
    _CHAIN_SNAPSHOT_KEYS = ("model", "ir", "gain", "master", "quality")
    _CHAIN_UNDO_LIMIT = 50

    def _chain_snapshot(self) -> dict:
        """当前链的 preset 内容域快照。

        只存链上真实存在的键：链协议里"键缺失 = 默认值"（watch_chain 对
        quality 等做 ``float(chain.get("quality", 1.0))``），恢复时把 None
        写进链文件会让浮点解析崩溃。应用前链上没有的键，preset_load 之后
        也按 preset 语义存在或显式 null（ir）——undo 恢复时保留现状即可，
        与应用前状态等效。"""
        cfg = live.read_chain()
        return {key: cfg[key] for key in self._CHAIN_SNAPSHOT_KEYS if key in cfg}

    def _restore_chain(self, snap: dict) -> None:
        """恢复快照 → 写 live_chain.json（引擎热切换）→ UI 跟随。"""
        cfg = live.read_chain()
        cfg.update(snap)
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg

    def _push_undo(self, snap: dict) -> None:
        self._undo_stack.append(snap)
        if len(self._undo_stack) > self._CHAIN_UNDO_LIMIT:
            self._undo_stack.pop(0)

    def _push_redo(self, snap: dict) -> None:
        self._redo_stack.append(snap)
        if len(self._redo_stack) > self._CHAIN_UNDO_LIMIT:
            self._redo_stack.pop(0)

    def _push_preset_undo(self) -> None:
        """Preset 应用入栈：快照当前链进 undo 栈，redo 栈清空（新动作
        使旧 redo 失效）。与栈顶相同（preset 内容未变）则跳过。"""
        snap = self._chain_snapshot()
        if self._undo_stack and self._undo_stack[-1] == snap:
            return
        self._push_undo(snap)
        self._redo_stack.clear()

    def action_undo_chain(self) -> None:
        if not self._undo_stack:
            self.notify("Nothing to undo")
            return
        snap = self._undo_stack.pop()
        self._push_redo(self._chain_snapshot())
        self._restore_chain(snap)
        self.notify("Undo preset")

    def action_redo_chain(self) -> None:
        if not self._redo_stack:
            self.notify("Nothing to redo")
            return
        snap = self._redo_stack.pop()
        self._push_undo(self._chain_snapshot())
        self._restore_chain(snap)
        self.notify("Redo preset")

    def on_node_widget_switch_requested(self, event) -> None:
        """↑/↓ on a focused AMP/IR node: step through the sibling models of the
        same tone folder, hot-swap the chain and mirror the file in the detail pane."""
        self._switch_chain_model(event.kind, event.direction)

    def _switch_chain_model(self, kind: str, direction: int) -> None:
        cfg = live.read_chain()
        key = "model" if kind == "amp" else "ir"
        path = cfg.get(key)
        if not path:
            self.notify(f"{kind.upper()}: no model loaded")
            return
        siblings = library.local_models_by_tone(path)
        if not siblings:
            self.notify(f"{kind.upper()}: not a library model")
            return
        if kind == "cab":
            siblings = [m for m in siblings if m["architecture"] == "IR"]
        else:
            siblings = [m for m in siblings if m["architecture"] != "IR"]
        if len(siblings) <= 1:
            self.notify(f"{kind.upper()}: only one model in this folder")
            return
        cur = next((i for i, m in enumerate(siblings) if m["local_path"] == path), None)
        if cur is None:
            return
        nxt = siblings[(cur + direction) % len(siblings)]
        cfg[key] = nxt["local_path"]
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        detail = self.query_one(DetailPane)
        if detail._pack_mode:
            # 聚焦打开的是 pack 视图：换模型只移动 ▶ 标记，不替换整个视图
            detail.refresh_pack_active(cfg)
        else:
            tone = library.get_tone(nxt["tone_id"])
            detail.show_model(tone, nxt)
        self.notify(f"{kind.upper()} → {live.short_name(nxt['local_path'])}")

    def on_detail_pane_pack_install_requested(self, event) -> None:
        """selection 视图里 Enter/双击一行 → 二级菜单详情页（与库表 remote
        行 Enter 同一屏：预览文件、勾选安装/卸载）。event.tone 是 tone dict
        （REQ-038 修复：旧实现把 model dict 传进来，model 的 id 被当作
        tone id 使用）。"""
        if not event.tone:
            return
        self.push_screen(PackInstallScreen(event.tone))

    def on_detail_pane_pack_files_installed(self, event) -> None:
        """pack 表 i 键批量安装完成：toast + 库表下载态刷新。"""
        self.notify(f"Installed {event.count} file(s) from tone {event.tone_id}")
        panel = self.query_one(LibraryPanel)
        panel._fingerprint = None
        panel.refresh_rows()

    def on_detail_pane_pack_files_uninstalled(self, event) -> None:
        """pack 表 u 键批量卸载完成：toast + 库表下载态刷新（元数据保留）。"""
        self.notify(f"Uninstalled {event.count} file(s) · metadata retained")
        panel = self.query_one(LibraryPanel)
        panel._fingerprint = None
        panel.refresh_rows()

    def on_pack_install_screen_uninstalled(self, event) -> None:
        """PackInstallScreen 的 u 键卸载完成：库表下载态刷新。"""
        self.notify(f"Uninstalled {event.count} file(s) from tone {event.tone_id} "
                    "· metadata retained")
        panel = self.query_one(LibraryPanel)
        panel._fingerprint = None
        panel.refresh_rows()

    def on_detail_pane_pack_file_picked(self, event) -> None:
        """Pack 列表选中一个文件：热换对应链槽（IR 行换 ir、其余换 model）。

        重复选择链上已加载的项 = 卸载（置 null 空槽，音频直通但模块不再
        加载，与 BYPASS 的"加载但直通"不同）；amp-cab 包选 AMP 行时
        CAB 显式置 null（pop 不会让引擎移除旧 IR）。
        """
        cfg = live.read_chain()
        if not event.path:
            return
        if cfg.get(event.slot) == event.path:
            # 重复选择已加载项 → BYPASS（模块加载但音频直通，保留内容显示；
            # 卸载只在链面板 d 键 delete）
            node_kind = "amp" if event.slot == "model" else "cab"
            self._toggle_node(node_kind)
            self.query_one(DetailPane).refresh_pack_active(live.read_chain())
            return
        backup_attr = "_amp_model_backup" if event.slot == "model" else "_ir_backup"
        node_kind = "amp" if event.slot == "model" else "cab"
        if not cfg.get(event.slot) and getattr(self, backup_attr, None) == event.path:
            # Selecting the recovery candidate while bypassed is the same
            # action as the node double-click restore path.
            self._toggle_node(node_kind)
            self.query_one(DetailPane).refresh_pack_active(live.read_chain())
            return
        cfg[event.slot] = event.path
        # Loading a different file exits bypass and replaces its in-process
        # recovery candidate; retaining the old candidate would make a later
        # delete look like a bypass state.
        setattr(self, backup_attr, None)
        note = f" · {event.slot.upper()} → {live.short_name(event.path)}"
        if event.slot == "model" and event.tone_gear == "amp-cab":
            cfg["ir"] = None
            self._ir_backup = None
            note += " · CAB empty (Amp + Cab model)"
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        self.query_one(DetailPane).refresh_pack_active(cfg)
        self.notify(note.lstrip(" ·"))

    def on_detail_pane_pack_closed(self, event) -> None:
        """Esc 从 pack 文件列表回到链节点（其 ↑/↓ 换模型、双击切换恢复）。"""
        self._focus_node(event.kind)

    def on_preset_name_modal_saved(self, event: PresetNameModal.Saved) -> None:
        self.query_one(PresetPanel)._fingerprint = None
        self.query_one(PresetPanel).refresh_presets()
        self.notify(f"Preset '{event.name}' saved")

    def on_preset_rename_modal_renamed(self, event: PresetRenameModal.Renamed) -> None:
        self.query_one(PresetPanel)._fingerprint = None
        self.query_one(PresetPanel).refresh_presets()
        self.notify(f"Preset '{event.old_name}' renamed to '{event.new_name}'")

    def on_preset_note_modal_updated(self, event: PresetNoteModal.Updated) -> None:
        self.query_one(PresetPanel)._fingerprint = None
        self.query_one(PresetPanel).refresh_presets()
        self.notify(f"Preset '{event.name}' note updated")

    def on_preset_delete_modal_deleted(self, event: PresetDeleteModal.Deleted) -> None:
        panel = self.query_one(PresetPanel)
        panel._selected.difference_update(event.names)
        panel._fingerprint = None
        panel.refresh_presets()
        self.notify(f"Deleted {len(event.names)} preset(s)")

    def on_tone_selected(self, event: ToneSelected) -> None:
        """Enter on a library row: jump straight to that tone's model files.

        The picker lists the exact downloaded filenames; Enter picks one into
        the live chain, Esc backs out — no intermediate action menu.
        """
        t = library.get_tone(event.tone_id)
        if t:
            kind = "ir" if t.get("gear") == "cab" else "amp"
            self.push_screen(TonePickerScreen(
                kind, tone_id=int(t["id"]), tone_type=t.get("gear") or "amp"))

    def on_link_clicked(self, event) -> None:
        """Click a metadata link (author/tag) → TONE3000 search for it."""
        href = getattr(event, "href", "") or ""
        if href.startswith(("https://", "http://")):
            webbrowser.open(href)
            self.notify("Opened link in browser")
            return
        if not href.startswith("search:"):
            return
        _, kind, value = href.split(":", 2)
        panel = self.query_one(LibraryPanel)
        tab = panel.query_one("#--content-tab-pane-tone")
        tab.post_message(tab.Clicked(tab))  # user-path tab switch (no rollback)
        if kind == "author":
            panel.run_worker(partial(panel._show_search, f"@{value}"), name="search",
                             exclusive=True)
        elif kind == "tag":
            panel.run_worker(partial(panel._show_search, f"#{value}"), name="search",
                             exclusive=True)
        self.notify(f"Searched {kind}: {value}")

    def on_tone_highlighted(self, event: ToneHighlighted) -> None:
        detail = self.query_one(DetailPane)
        if event.tone:
            detail.show(event.tone)
        else:
            detail.clear()

    def on_creator_focused(self, event) -> None:
        """TOP CREATORS 行聚焦 → 作者信息 + top 音色列表（REQ-012）。"""
        self.query_one(DetailPane).show_creator(event.username)

    def on_verified_author(self, event: VerifiedAuthor) -> None:
        self.query_one(LibraryPanel).mark_verified_author(event.username)

    def on_tone_picker_screen_picked(self, event: TonePickerScreen.Picked) -> None:
        """Node picked: write chain config → engine hot-swaps.

        path None removes the slot — the key is set to null (not popped), so
        the engine sees the change and passes the signal through; an absent
        key would leave the old model loaded.
        """
        cfg = live.read_chain()
        key = "model" if event.kind == "amp" else "ir"
        if event.path is None:
            cfg[key] = None
            setattr(self, "_amp_model_backup" if key == "model" else "_ir_backup", None)
            note = f"Chain updated: {key} → empty"
        else:
            backup_attr = "_amp_model_backup" if key == "model" else "_ir_backup"
            node_kind = "amp" if key == "model" else "cab"
            if not cfg.get(key) and getattr(self, backup_attr, None) == event.path:
                self._toggle_node(node_kind)
                self.query_one(ChainPanel).chain = live.read_chain()
                return
            if cfg.get(key) == event.path:
                self._toggle_node(node_kind)
                self.query_one(ChainPanel).chain = live.read_chain()
                return
            cfg[key] = event.path
            setattr(self, backup_attr, None)
            note = f"Chain updated: {key} → {live.short_name(event.path)}"
        if event.tone_type == "amp-cab":
            cfg["ir"] = None  # 显式 null（pop 不会让引擎移除旧 IR）
            self._ir_backup = None
            note += " · CAB empty (Amp + Cab model)"
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        self.notify(note)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gigbuddy", description="GigBuddy tone-chain TUI")
    parser.add_argument("--in", dest="dev_in", default="",
                        help="input device name fragment (default: system default)")
    parser.add_argument("--out", dest="dev_out", default="",
                        help="output device name fragment (default: system default)")
    parser.add_argument("--ch", type=int, default=1, help="input channel (default: 1)")
    parser.add_argument("--no-engine", action="store_true",
                        help="engine already running externally (skip spawn)")
    parser.add_argument("--theme", default=None,
                        help="startup color theme (t cycles themes; default: built-in)")
    args = parser.parse_args(argv)
    GigBuddyApp(dev_in=args.dev_in, dev_out=args.dev_out, in_ch=args.ch,
                spawn_engine=not args.no_engine, theme=args.theme).run()


if __name__ == "__main__":
    main()
