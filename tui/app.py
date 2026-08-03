"""GigBuddy TUI: tone library browser (left) + tone chain control (right) + meter (bottom)

v2: pure control surface — no embedded agent. The library DB (data/gigbuddy.db)
is open to external agents via the `gigbuddy` CLI; chain edits flow to the engine
through data/live_chain.json as before.

Run: .venv/bin/python -m tui            (spawns the realtime engine automatically)
     .venv/bin/python -m tui --no-engine (engine already running externally)
"""
import argparse
import asyncio
import re
import subprocess
import sys
import time
from pathlib import Path

from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.theme import Theme
from textual.widgets import Footer, Header

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from . import live  # noqa: E402
import library  # noqa: E402
from .install_screen import PackInstallScreen  # noqa: E402
from .library_panel import (LibraryPanel, LibraryTable, ToneHighlighted,
                            ToneSelected)  # noqa: E402
from .panels import (ChainPanel, DetailPane, DeviceBar, DeviceChanged,
                     MeterBar, NodeWidget)  # noqa: E402
from .picker import TonePickerScreen  # noqa: E402
from .presets import PresetNameModal, PresetPanel, PresetPickerScreen  # noqa: E402

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
    },
)


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
    SUB_TITLE = "tone chain control"

    CSS = """
    Screen { layout: vertical; background: $background; }
    Header { background: $panel; color: $primary; text-style: bold; }
    Header .header--title { color: $primary; text-style: bold; }
    Header .header--sub-title { color: $text-muted; text-style: none; }
    HeaderIcon { display: none; }  /* palette stays on ctrl+p only */
    Footer { background: $panel; }
    #top { layout: horizontal; height: 1fr; }

    /* panels: quiet warm border, amber when something inside has focus */
    LibraryPanel, PresetPanel, ChainPanel, DetailPane, MeterBar {
        border: round $surface-lighten-2;
        border-title-color: $text-muted;
        border-subtitle-color: $text-disabled;
        padding: 0 1;
    }
    LibraryPanel:focus-within, PresetPanel:focus-within,
    ChainPanel:focus-within, DetailPane:focus-within {
        border: round $primary;
        border-title-color: $primary;
    }

    #left-col { width: 3fr; layout: vertical; }
    LibraryPanel { height: 3fr; }
    #type-filter-row { height: 3; }
    /* label sits in the middle of the 3-row filter row (Textual's container
       align doesn't position plain Statics; margin does) */
    #type-filter-row .filter-label {
        width: 8; height: 1; margin: 1 0;
        color: $text-muted; text-style: bold;
    }
    /* explicit height:3 keeps the Select's round border inside the row —
       auto height (content+border) grows to 5 rows, clipping the border and
       skewing the TYPE label off-center */
    #type-filter-local, #type-filter-tone { width: 24; height: 3; }
    #sort-filter { width: 26; height: 3; }
    #lib-status { color: $text-muted; padding: 0 1; }
    PresetPanel { height: 1fr; min-height: 6; }

    #right-col { width: 2fr; layout: vertical; }
    ChainPanel { height: 1fr; min-height: 18; }
    ChainPanel .chain-node-row {
        /* 4 = round border 2 + content 2: title line with ▲, filename with ▼ */
        height: 4; width: 100%;
        background: $panel;
        border: round $surface-lighten-2;
    }
    ChainPanel .chain-node {
        width: 1fr; height: 2; padding: 0 1;
        background: transparent; border: none;
    }
    ChainPanel .chain-node:focus {
        background: $panel-lighten-1;
    }
    ChainPanel .chain-node-row:focus-within {
        border: round $accent;
        background: $panel-lighten-1;
    }
    ChainPanel .chain-switch-col {
        width: 10; height: 4;
        layout: vertical;
        padding: 0 1;
        border-left: solid $surface-lighten-2;
    }
    ChainPanel .chain-switch-btn {
        width: 10; height: 1; padding: 0;
        content-align: center middle;
        color: $text; background: transparent; text-style: bold;
    }
    ChainPanel .chain-switch-btn:hover {
        background: $accent; color: $background;
    }
    ChainPanel .chain-effect {
        height: 1; padding: 0 1 0 2; color: $text-muted;
    }
    ChainPanel .chain-params {
        height: 1; margin-top: 1; padding: 0 1;
        background: $panel; color: $text;
    }

    DetailPane { height: 1fr; }
    DetailPane MarqueeBar { height: 1; color: $text; }

    #bottom { layout: vertical; height: 6; }
    #dev-row { layout: horizontal; height: 3; }
    DeviceBar { border: round $surface-lighten-2; padding: 0 1;
                align: center middle; }
    DeviceBar .device-label { color: $text-muted; text-style: bold; margin: 0 1 0 0; }
    DeviceBar Select { width: 15; }
    DeviceBar .device-mute { width: 12; height: 2; content-align: center middle;
                             border: round $surface-lighten-2; text-style: bold;
                             color: $text; margin: 0 0 0 2; }
    DeviceBar .device-mute:hover { background: $accent; color: $background; }
    DeviceBar .device-mute.muted { background: $error; color: $background; }
    #meter-row { layout: horizontal; height: 3; }
    MeterBar { width: 1fr; }

    /* tables: warm header, amber cursor row */
    DataTable > .datatable--header {
        background: $panel; color: $primary; text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: $primary; color: $background; text-style: bold;
    }
    DataTable > .datatable--hover { background: $boost; }
    Tree { background: transparent; }
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
    Input, Select, Select > SelectCurrent {
        border: round $surface-lighten-2;
    }
    Input:focus, Select:focus { border: round $accent; }
    Input > .input--placeholder { color: $text-disabled; }

    /* overlays (select dropdown / toast / command palette) share one raised
       look: lighter surface + accent border, clearly off the main background */
    SelectOverlay {
        background: $panel-lighten-1;
        border: round $accent;
    }
    Toast {
        background: $panel-lighten-1;
        border: round $accent;
        border-title-color: $accent;
    }
    CommandPalette {
        background: $panel-lighten-1;
        border: round $accent;
    }

    /* tabs: Textual's built-in Tabs{height:2} + Tab{height:1} leave zero content
       rows under a round border (labels vanish) and clip the bottom border —
       both overridden here to height:3 (border 2 + label 1) */
    TabbedContent > ContentTabs { height: 3; }
    TabbedContent Tab {
        height: 3;
        padding: 0 2; margin: 0 1 0 0;
        color: $text-muted; text-style: none;
        border: round $surface-lighten-2;
    }
    TabbedContent Tab:hover { color: $foreground; background: $boost; }
    TabbedContent Tab.-active {
        color: $accent; text-style: bold;
        border: round $accent;
    }
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
        Binding("u", "bump_quality(-0.1)", "quality -"),
        Binding("U", "bump_quality(+0.1)", "quality +"),
        # no single-key quit: Ctrl+C twice (from any screen/modal) exits
        Binding("ctrl+c", "request_quit", "quit (×2)", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            with Horizontal(id="top"):
                with Vertical(id="left-col"):
                    yield LibraryPanel()
                    yield PresetPanel()
                with Vertical(id="right-col"):
                    yield ChainPanel()
                    yield DetailPane()
            with Vertical(id="bottom"):
                yield DeviceBar()
                yield MeterBar()
        yield Footer()

    def __init__(self, dev_in: str = "", dev_out: str = "", in_ch: int = 0,
                 spawn_engine: bool = True, theme: str | None = None) -> None:
        super().__init__()
        self._dev_in = dev_in
        self._dev_out = dev_out
        self._in_ch = in_ch
        self._spawn_engine = spawn_engine
        self._engine: subprocess.Popen | None = None
        self._block = 256
        # double-click toggles: remembered values for restoring IR / amp gain
        self._ir_backup: str | None = None
        self._amp_gain_backup: float | None = None
        self._last_quit_at = 0.0  # Ctrl+C twice within QUIT_WINDOW_S exits
        self.register_theme(GIGBUDDY_THEME)
        self.theme = theme or GIGBUDDY_THEME.name

    QUIT_WINDOW_S = 1.5

    def action_request_quit(self) -> None:
        """Ctrl+C: first press warns, second press within 1.5s exits.

        Bound at app level, so it works from every screen and modal; the
        command palette's Quit entry exits immediately.
        """
        now = time.monotonic()
        if now - self._last_quit_at < self.QUIT_WINDOW_S:
            self.exit()
        else:
            self._last_quit_at = now
            self.notify("再按一次 Ctrl+C 退出")

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
        yield SystemCommand(title="Preset…", help="load a named chain preset (also: p)",
                            callback=self.action_open_preset_picker, discover=True)
        yield SystemCommand(title="Save preset…", help="snapshot the current chain (also: ctrl+s)",
                            callback=self.action_save_preset, discover=True)
        yield SystemCommand(title="Next theme", help="cycle color themes (also: t)",
                            callback=self.action_next_theme, discover=True)
        yield SystemCommand(title="Quit", help="quit GigBuddy",
                            callback=self.action_quit, discover=True)

    def _on_notify(self, event) -> None:
        """Right-corner toasts: keep at most two on screen at once.

        Rapid actions (model stepping, preset loads) otherwise pile up a stack
        of toasts; drop the oldest when a new one is enqueued.
        """
        while len(self._notifications) >= 2:
            oldest = next(iter(self._notifications))
            del self._notifications[oldest]
        super()._on_notify(event)

    def action_next_theme(self) -> None:
        themes = list(self.available_themes)
        i = themes.index(self.theme)
        self.theme = themes[(i + 1) % len(themes)]
        self.notify(f"Theme: {self.theme}")

    def on_mount(self) -> None:
        self._ensure_engine()
        self.set_interval(0.1, self.refresh_from_files)
        self.query_one("#lib-table-local").focus()
        self.run_worker(self._load_devices(), name="devices")

    def _ensure_engine(self) -> None:
        """Spawn (or restart) the engine when the chain has a valid model and no
        engine is running. Without this a fresh chain-less boot would spawn the
        engine into an immediate silent exit (realtime_cli --live requires a
        model) — the user gets a hint instead, and picking a tone later starts
        audio. Also recovers from engine crashes via the 0.1s tick."""
        if not self._spawn_engine:
            return
        if self._engine is not None and self._engine.poll() is None:
            return
        cfg = live.read_chain()
        model_path = cfg.get("model") or ""
        if not model_path or not Path(model_path).exists():
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
               "--live", str(live.CHAIN_FILE), "--level-file", str(live.LEVEL_FILE)]
        if self._dev_in:
            cmd += ["--in", self._dev_in]
        if self._dev_out:
            cmd += ["--out", self._dev_out]
        if self._in_ch:
            cmd += ["--ch", str(self._in_ch)]
        if self._block:
            cmd += ["--block", str(self._block)]
        try:
            log = open(root / "data" / "engine.log", "w")
            self._engine = subprocess.Popen(cmd, stdout=log, stderr=log,
                                            stdin=subprocess.DEVNULL)
        except FileNotFoundError as e:
            self.notify(f"(engine spawn failed: {e})", severity="error")

    def on_unmount(self) -> None:
        self._kill_engine()

    async def _load_devices(self) -> None:
        """Enumerate audio interfaces via `realtime_cli --list` and fill the DeviceBar.

        Retries once after 12s (the engine may hold the audio device); on failure
        the pickers stay enabled with the CLI defaults so audio is still usable.
        """
        root = Path(__file__).resolve().parent.parent
        for attempt in range(2):
            try:
                out = await asyncio.to_thread(
                    subprocess.run,
                    [str(root / "bin" / "realtime_cli"), "--list"],
                    capture_output=True, text=True, timeout=12)
                ins, outs = _parse_devices(out.stdout or "")
                if ins or outs:
                    self.query_one(DeviceBar).set_devices(
                        ins, outs, self._dev_in or "", self._dev_out or "")
                    return
            except Exception:
                pass
            await asyncio.sleep(1)
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
        """IN/OUT/buffer changed → restart the engine; mute toggles the chain."""
        if event.kind == "mute":
            # chain-level toggle, works regardless of engine ownership
            cfg = live.read_chain()
            if float(cfg.get("master", 1.0)) > 0:
                self._master_backup = float(cfg.get("master", 1.0))
                cfg["master"] = 0.0
                note = "MUTED (click again to restore)"
            else:
                cfg["master"] = getattr(self, "_master_backup", None) or 1.0
                note = f"Unmuted → master {cfg['master']:.2f}"
            live.write_chain(cfg)
            self.query_one(ChainPanel).chain = cfg
            self.query_one(DeviceBar).set_muted(cfg["master"] <= 0)
            self.notify(note)
            return
        if not self._spawn_engine:
            self.notify("--no-engine mode: engine runs externally, not restarted")
            return
        if event.kind == "buffer":
            self._block = int(event.name)
        elif event.kind == "in":
            self._dev_in = event.name
        elif event.kind == "out":
            self._dev_out = event.name
        self._kill_engine()
        self._start_engine()
        self.notify(f"Engine restarted — IN {self._dev_in} · OUT {self._dev_out} · "
                    f"block {self._block}")

    def on_pack_install_screen_installed(self, event: PackInstallScreen.Installed) -> None:
        """Pack installed: toast + library refresh + show the tone in the detail pane."""
        self.notify(f"Installed {event.count} file(s) from tone {event.tone_id}")
        panel = self.query_one(LibraryPanel)
        panel._fingerprint = None
        panel.refresh_rows()
        self.on_tone_selected(ToneSelected(event.tone_id))

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
        except NoMatches:
            return
        self._ensure_engine()   # restart after crash / start after picking a tone
        meter.levels = live.read_levels()
        chain.chain = live.read_chain()
        library_panel.check_active_tab()
        library_panel.refresh_rows()
        preset_panel.refresh_presets()

    def _bump(self, key: str, delta: float) -> None:
        cfg = live.read_chain()
        cfg[key] = round(float(cfg.get(key, 1.0)) + delta, 2)
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg

    def action_bump_gain(self, delta: float) -> None:
        self._bump("gain", delta)

    def action_bump_master(self, delta: float) -> None:
        self._bump("master", delta)

    def action_bump_quality(self, delta: float) -> None:
        """A2 model quality (SlimmableContainer sub-model size), clamped 0..1.

        1.0 = full precision (default), lower = lighter CPU. A1 models ignore it.
        """
        cfg = live.read_chain()
        q = round(float(cfg.get("quality", 1.0)) + delta, 2)
        cfg["quality"] = max(0.0, min(1.0, q))
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg

    def action_focus_search(self) -> None:
        self.query_one(LibraryPanel).focus_search()

    def action_open_preset_picker(self) -> None:
        self.push_screen(PresetPickerScreen())

    def action_save_preset(self) -> None:
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
        # line, ▼ = filename line — two rows, no dead space).
        for col in self.query(".chain-switch-col"):
            if col.region.contains(event.screen_x, event.screen_y):
                row = col.parent
                kind = "amp" if row.has_class("chain-node-row-amp") else "ir"
                event.stop()
                self._focus_node(kind)
                up = self.query_one(f"#chain-{kind}-up")
                down = self.query_one(f"#chain-{kind}-down")
                if up.region.contains(event.screen_x, event.screen_y):
                    self._switch_chain_model(kind, -1)
                elif down.region.contains(event.screen_x, event.screen_y):
                    self._switch_chain_model(kind, +1)
                return
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        if widget.has_class("chain-node"):
            # clicking node content: focus it and mirror its tone folder detail;
            # double-click toggles the node (IR bypass / amp mute)
            event.stop()
            self._focus_node(widget.kind)
            if getattr(event, "chain", 1) >= 2:
                self._toggle_node(widget.kind)
            else:
                self._show_node_detail(widget.kind)
        elif widget.has_class("chain-node-row"):
            # The border and separator are part of the same visual control.
            kind = "amp" if widget.has_class("chain-node-row-amp") else "ir"
            event.stop()
            self._focus_node(kind)
            self._show_node_detail(kind)

    def _focus_node(self, kind: str) -> None:
        node = next((n for n in self.query(NodeWidget) if n.kind == kind), None)
        if node:
            node.focus()

    def _show_node_detail(self, kind: str) -> None:
        """Mirror the active amp/IR model (with its owning tone) in the detail pane."""
        cfg = live.read_chain()
        path = cfg.get("model" if kind == "amp" else "ir")
        if not path:
            self.query_one(DetailPane).clear()
            return
        siblings = library.local_models_by_tone(path)
        model = next((m for m in (siblings or []) if m["local_path"] == path), None)
        if not model:
            self.query_one(DetailPane).clear()
            return
        tone = library.get_tone(model["tone_id"])
        self.query_one(DetailPane).show_model(tone, model)

    def _toggle_node(self, kind: str) -> None:
        """Double-click on a node: IR bypass / restore, amp mute / unmute."""
        cfg = live.read_chain()
        if kind == "ir":
            if cfg.get("ir"):
                self._ir_backup = cfg["ir"]
                cfg["ir"] = None  # engine treats null as pass-through
                note = "IR bypassed (double-click to restore)"
            elif self._ir_backup:
                cfg["ir"] = self._ir_backup
                note = f"IR restored → {live.short_name(self._ir_backup)}"
            else:
                self.notify("IR: nothing to restore")
                return
        else:  # amp mute via gain (engine has no model-bypass)
            gain = float(cfg.get("gain", 1.0))
            if gain > 0:
                self._amp_gain_backup = gain
                cfg["gain"] = 0.0
                note = "AMP muted (double-click to restore)"
            else:
                cfg["gain"] = self._amp_gain_backup if self._amp_gain_backup else 1.0
                note = f"AMP restored → gain {cfg['gain']:.2f}"
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        self.notify(note)

    def on_preset_picker_screen_loaded(self, event: PresetPickerScreen.Loaded) -> None:
        self._apply_preset(event.name)

    def on_preset_panel_activated(self, event: PresetPanel.Activated) -> None:
        self._apply_preset(event.name)

    def on_preset_panel_highlighted(self, event: PresetPanel.Highlighted) -> None:
        """Preset row highlighted: mirror its chain summary in the detail pane."""
        p = event.preset
        if not p:
            self.query_one(DetailPane).clear()
            return
        ch = p["chain"]
        lines = [
            f"[b]{p['name']}[/b]  " + (f"[dim]{p.get('note')}[/dim]" if p.get("note") else ""),
            f"amp: {ch.get('model_path') or '(external)'}",
        ]
        if ch.get("ir_path"):
            lines.append(f"ir:  {ch['ir_path']}")
        else:
            lines.append("ir:  bypass")
        lines.append(f"gain {ch.get('gain')}  master {ch.get('master')}  ·  "
                     f"updated {p.get('updated_at', '')[:19]}")
        self.query_one(DetailPane).show_text("\n".join(lines))

    def _apply_preset(self, name: str) -> None:
        try:
            cfg = library.preset_load(name)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        self.query_one(ChainPanel).chain = cfg
        self.notify(f"Preset '{name}' loaded")

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
        if kind == "ir":
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
        tone = library.get_tone(nxt["tone_id"])
        self.query_one(DetailPane).show_model(tone, nxt)
        self.notify(f"{kind.upper()} → {live.short_name(nxt['local_path'])}")

    def on_preset_name_modal_saved(self, event: PresetNameModal.Saved) -> None:
        self.notify(f"Preset '{event.name}' saved")

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
        if not href.startswith("search:"):
            return
        _, kind, value = href.split(":", 2)
        panel = self.query_one(LibraryPanel)
        tab = panel.query_one("#--content-tab-pane-tone")
        tab.post_message(tab.Clicked(tab))  # user-path tab switch (no rollback)
        if kind == "author":
            panel.run_worker(panel._show_search(f"@{value}"), name="search",
                             exclusive=True)
        elif kind == "tag":
            panel.run_worker(panel._show_search(f"#{value}"), name="search",
                             exclusive=True)
        self.notify(f"Searched {kind}: {value}")

    def on_tone_highlighted(self, event: ToneHighlighted) -> None:
        detail = self.query_one(DetailPane)
        if event.tone:
            detail.show(event.tone)
        else:
            detail.clear()

    def on_tone_picker_screen_picked(self, event: TonePickerScreen.Picked) -> None:
        """Node picked: write chain config → engine hot-swaps.

        path None (IR bypass) removes the key so the engine passes through.
        """
        cfg = live.read_chain()
        key = "model" if event.kind == "amp" else "ir"
        if event.path is None:
            cfg.pop(key, None)
            note = f"Chain updated: {key} → bypass"
        else:
            cfg[key] = event.path
            note = f"Chain updated: {key} → {live.short_name(event.path)}"
        if event.tone_type == "amp-cab":
            cfg.pop("ir", None)
            note += " · IR bypassed (Amp + Cab model)"
        live.write_chain(cfg)
        self.query_one(ChainPanel).chain = cfg
        self.notify(note)


def main() -> None:
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
    args = parser.parse_args()
    GigBuddyApp(dev_in=args.dev_in, dev_out=args.dev_out, in_ch=args.ch,
                spawn_engine=not args.no_engine, theme=args.theme).run()


if __name__ == "__main__":
    main()
