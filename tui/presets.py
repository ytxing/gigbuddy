"""Preset picker + save-name modal: manage named chain snapshots from the TUI.

Loading writes data/live_chain.json (engine hot-swap); saving snapshots the
current chain. Both go through src/library.py preset_* functions so the CLI,
TUI and external agents share one code path.
"""
import asyncio
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Input, Static, Tree

from .modals import ClickSelectTable, ClickSelectTree, GigBuddyModal, ModalBox

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402


class PresetPanel(Vertical):
    """Persistent preset list under the library: browse with ↑/↓, load with Enter.

    Highlighted presets mirror their chain summary in the detail pane; selecting
    one writes the chain (engine hot-swap), same code path as the picker.
    """

    class Activated(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    class Highlighted(Message):
        def __init__(self, preset: dict | None) -> None:
            super().__init__()
            self.preset = preset

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "PRESETS"
        self.border_subtitle = "↑↓ browse · Enter load · ctrl+s save"

    def compose(self) -> ComposeResult:
        table = ClickSelectTable(id="preset-table", cursor_type="row")
        table.add_column("Name", key="name")
        table.add_column("Amp / IR", key="chain")
        yield table

    def on_mount(self) -> None:
        self._fingerprint: tuple | None = None
        self._highlighted: str | None = None
        self.refresh_presets()

    def refresh_presets(self) -> None:
        """Reload from the DB (called on tick; skips repaint unless changed)."""
        with library.connect() as conn:
            fp = tuple(conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM presets").fetchone())
        if fp == self._fingerprint:
            return
        self._fingerprint = fp
        table = self.query_one("#preset-table", DataTable)
        table.clear()
        for p in library.preset_list():
            ch = p["chain"]
            amp = ch.get("model_id") or "external"
            ir = ch.get("ir_model_id") or "bypass"
            note = f"  {p['note']}" if p.get("note") else ""
            table.add_row(
                p["name"],
                f"amp {amp} · ir {ir}{note}",
                key=p["name"])
        if not table.rows:
            table.add_row("(no presets — ctrl+s saves the current chain)", "")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value
        if key == self._highlighted:
            return
        self._highlighted = key
        self.post_message(self.Highlighted(library.preset_get(key) if key else None))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        if name:
            self.post_message(self.Activated(name))

    def on_click(self, event) -> None:
        """Single click focuses; double click loads the preset (like Enter)."""
        if getattr(event, "chain", 1) >= 2:
            table = self.query_one("#preset-table", DataTable)
            name = table.ordered_rows[table.cursor_row].key.value if table.ordered_rows else None
            if name:
                event.stop()
                self.post_message(self.Activated(name))


class PresetPickerScreen(GigBuddyModal):
    """List presets; Enter loads the highlighted one into the live chain."""

    CSS = """
    PresetPickerScreen > ModalBox { width: 80%; height: 80%; margin: 4 10; }
    #preset-tree { height: 2fr; }
    #preset-detail {
        height: 1fr; min-height: 9; border-top: solid $primary;
        padding: 0 1; overflow-y: auto;
    }
    """

    class Loaded(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "PRESETS"
        box.border_subtitle = "Enter load · Esc cancel"
        with box:
            yield Static(
                "↑↓ browse · Enter load · Esc cancel · ctrl+s saves the current chain",
                classes="modal-hint")
            tree = ClickSelectTree("Presets", id="preset-tree")
            tree.show_root = False
            yield tree
            yield Static("", id="preset-detail")

    def on_mount(self) -> None:
        self._fill()
        self.query_one("#preset-tree", Tree).focus()

    def _fill(self) -> None:
        tree = self.query_one("#preset-tree", Tree)
        tree.reset("Presets")
        tree.root.expand()
        for p in library.preset_list():
            ch = p["chain"]
            note = p.get("note") or ""
            amp = ch.get("model_id") or "external"
            ir = ch.get("ir_model_id") or "bypass"
            tree.root.add_leaf(
                f"{p['name']}  [dim]{note}[/dim]",
                {"preset": p, "summary": f"amp {amp} · ir {ir} · gain {ch.get('gain')} "
                                          f"master {ch.get('master')}"})
        if not tree.root.children:
            tree.root.add_leaf("(no presets — ctrl+s to save the current chain)", None)
        if tree.root.children:
            tree.move_cursor(tree.root.children[0])

    def _confirm(self) -> None:
        tree = self.query_one("#preset-tree", Tree)
        node = tree.cursor_node
        data = node.data if node else None
        if not data or not data.get("preset"):
            return
        name = data["preset"]["name"]
        try:
            cfg = library.preset_load(name)
        except ValueError as e:
            self.query_one("#preset-detail", Static).update(f"[$error]{e}[/]")
            return
        self.post_message(self.Loaded(name))
        self.dismiss()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        data = event.node.data
        detail = self.query_one("#preset-detail", Static)
        if not data or not data.get("preset"):
            detail.update("")
            return
        p = data["preset"]
        ch = p["chain"]
        note = f"\n[dim]{p.get('note')}[/dim]" if p.get("note") else ""
        amp = ch.get("model_path") or "(external)"
        ir = ch.get("ir_path") or "bypass"
        lines = [
            f"[b $accent]{p['name']}[/]{note}",
            "",
            f"[b $text-muted]AMP[/]     {amp}",
            f"[b $text-muted]IR[/]      [dim]{ir}[/dim]",
            f"[b $text-muted]GAIN[/]    [b]{ch.get('gain')}[/b]   "
            f"[b $text-muted]MASTER[/]  [b]{ch.get('master')}[/b]",
            "",
            f"[dim]updated {p.get('updated_at', '')[:19]}[/dim]",
        ]
        detail.update("\n".join(lines))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data and event.node.data.get("preset"):
            self._confirm()


class PresetNameModal(GigBuddyModal):
    """Ask for a preset name, then snapshot the current chain."""

    CSS = """
    PresetNameModal > ModalBox { width: 60%; height: auto; margin: 6 20; }
    #preset-save-input { height: 3; }
    #preset-save-hint { color: $text-muted; }
    """

    class Saved(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "SAVE PRESET"
        with box:
            yield Input(placeholder="preset name (e.g. mayer-clean)", id="preset-save-input")
            yield Static("Enter to save · Esc cancel", id="preset-save-hint",
                         classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#preset-save-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if not name:
            return
        p = library.preset_save(name)
        self.post_message(self.Saved(p["name"]))
        self.dismiss()

    def _confirm(self) -> None:
        inp = self.query_one("#preset-save-input", Input)
        if inp.value.strip():
            self.on_input_submitted(Input.Submitted(inp, inp.value))
