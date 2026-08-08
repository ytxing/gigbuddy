"""Preset picker + save-name modal: manage named chain snapshots from the TUI.

Loading writes data/live_chain.json (engine hot-swap); saving snapshots the
current chain. Both go through src/library.py preset_* functions so the CLI,
TUI and external agents share one code path.
"""
import sys
from pathlib import Path
from typing import Callable

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Leave, MouseEvent, MouseMove
from textual.message import Message
from textual.widgets import DataTable, Input, Static, Tree

from .marquee import MarqueeBar
from .metadata import SelectableStatic, preset_metadata_table, signed_fixed
from .modals import (ClickSelectTable, ClickSelectTree, GigBuddyModal, ModalBox,
                     border_hint_action_token, border_hint_click,
                     border_hint_hit, hint_span, set_border_hint_hover)
from .modals import set_border_hint_layout
from .selection import NonSelectableStatic

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402


def _preset_file_label(chain: dict, key: str, fallback: str) -> str:
    """Show a compact model reference in preset lists and banners."""
    id_key = "model_id" if key == "model" else f"{key}_model_id"
    model_id = chain.get(id_key)
    if model_id is not None:
        return f"#{model_id}"
    return fallback


def _preset_controls(chain: dict) -> str:
    """Keep the three chain controls visible without adding another panel."""
    values = []
    for key, label, default in (
            ("gain", "G", 1.0), ("master", "M", 1.0), ("quality", "Q", 1.0)):
        try:
            value = float(chain.get(key, default))
            values.append(f"{label}{signed_fixed(value)}")
        except (TypeError, ValueError):
            values.append(f"{label}?")
    return " ".join(values)


class PresetTable(ClickSelectTable):
    """Republish the focused preset when focus returns without cursor movement."""

    def on_focus(self, event) -> None:
        panel = self.query_ancestor(PresetPanel)
        if panel is not None:
            panel._publish_highlight(force=True)

    def on_click(self, event) -> None:
        panel = self.query_ancestor(PresetPanel)
        meta = event.style.meta
        if (panel is not None and meta.get("column") == 0
                and isinstance(meta.get("row"), int) and meta["row"] >= 0):
            table = panel.query_one("#preset-table", DataTable)
            if meta["row"] < len(table.ordered_rows):
                table.move_cursor(row=meta["row"], column=0,
                                  animate=False, scroll=False)
            panel.action_toggle_selected()
            event.stop()
        elif getattr(event, "chain", 1) >= 2:
            self.action_select_cursor()
            event.stop()


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

    BINDINGS = [
        Binding("n", "save_as", "new"),
        Binding("r", "rename", "rename"),
        Binding("e", "edit_note", "note"),
        Binding("d", "delete", "delete"),
        Binding("space", "toggle_selected", "select"),
        Binding("a", "toggle_all", "all/none"),
        Binding("escape", "clear_selected", "clear", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "PRESETS"

    def compose(self) -> ComposeResult:
        yield MarqueeBar(id="preset-marquee")
        table = PresetTable(id="preset-table", cursor_type="row")
        table.add_column("Sel", key="pick", width=5)
        table.add_column("Preset", key="name")
        table.add_column("AMP", key="amp", width=12)
        table.add_column("CAB", key="ir", width=10)
        table.add_column("NOTE", key="note")
        yield table

    def on_mount(self) -> None:
        self._fingerprint: tuple | None = None
        self._highlighted: str | None = None
        self._selected: set[str] = set()
        self._active: str | None = None
        self.refresh_presets()
        self.call_after_refresh(lambda: self._publish_highlight(force=True))

    def refresh_presets(self) -> None:
        """Reload from the DB (called on tick; skips repaint unless changed)."""
        with library.connect() as conn:
            fp = tuple(conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0), "
                "COALESCE(MAX(updated_at), '') FROM presets").fetchone())
            active_row = conn.execute(
                "SELECT s.value FROM settings s JOIN presets p ON p.name = s.value "
                "WHERE s.key = 'active_preset'").fetchone()
        active = active_row["value"] if active_row else None
        self._active = active
        chain_mtime = (library.CHAIN_FILE.stat().st_mtime_ns
                       if library.CHAIN_FILE.exists() else 0)
        fp += (active, chain_mtime)
        if fp == self._fingerprint:
            return
        self._fingerprint = fp
        self._highlighted = None
        table = self.query_one("#preset-table", DataTable)
        focused_name = None
        if table.ordered_rows and 0 <= table.cursor_row < len(table.ordered_rows):
            focused_name = table.ordered_rows[table.cursor_row].key.value
        table.clear()
        presets = library.preset_list()
        names = {p["name"] for p in presets}
        self._selected &= names
        for p in presets:
            ch = p["chain"]
            amp = _preset_file_label(ch, "model", "external")
            ir = _preset_file_label(ch, "ir", "—")
            is_active = p["name"] == active
            dirty = is_active and library.preset_is_dirty(active)
            table.add_row(
                "\\[x]" if p["name"] in self._selected else "\\[ ]",
                f"{'>' if is_active else ' '} {p['name']}{' *' if dirty else ''}",
                str(amp),
                str(ir),
                p.get("note") or "",
                key=p["name"])
        if not table.rows:
            table.add_row("", "(no presets — ctrl+s saves the current chain)",
                          "", "", "")
        elif focused_name in names:
            focused_row = next(
                index for index, row in enumerate(table.ordered_rows)
                if row.key.value == focused_name)
            table.move_cursor(row=focused_row, animate=False)
        self._publish_highlight()
        self._update_selection_status()

    def selected_name(self) -> str | None:
        table = self.query_one("#preset-table", DataTable)
        if not table.ordered_rows or not 0 <= table.cursor_row < len(table.ordered_rows):
            return None
        name = table.ordered_rows[table.cursor_row].key.value
        return name if isinstance(name, str) and library.preset_get(name) else None

    def action_save_as(self) -> None:
        self.app.push_screen(PresetNameModal())

    def action_rename(self) -> None:
        name = self.selected_name()
        if name:
            self.app.push_screen(PresetRenameModal(name))

    def action_edit_note(self) -> None:
        name = self.selected_name()
        if name:
            preset = library.preset_get(name)
            if preset:
                self.app.push_screen(PresetNoteModal(name, preset.get("note") or ""))

    def action_delete(self) -> None:
        names = sorted(self._selected)
        if not names:
            name = self.selected_name()
            names = [name] if name else []
        if names:
            self.app.push_screen(PresetDeleteModal(names))

    def _hint_action_specs(self) -> list[tuple[str, Callable[[], None]]]:
        """Build the visible operations for the current width and selection.

        The full hint is kept on wide terminals. Narrow panes use familiar
        TUI abbreviations (``^S`` and ``↵``) so the border never clips the
        operation list into an unreadable fragment.
        """
        count = len(getattr(self, "_selected", ()))
        try:
            table = self.query_one("#preset-table", DataTable)
            names = {
                row.key.value for row in table.ordered_rows
                if isinstance(row.key.value, str)
            }
        except Exception:
            names = set()
        all_label = "a none" if names and names <= self._selected else "a all"
        width = self.region.width or (self.size.width + 4)
        if count:
            if width < 64:
                labels = (all_label, "space", "d", "esc")
            elif width < 80:
                labels = (all_label, "space", "d del", "esc clear")
            else:
                labels = (all_label, "space select", "d delete", "esc clear")
            actions = (
                self.action_toggle_all,
                self.action_toggle_selected,
                self.action_delete,
                self.action_clear_selected,
            )
            return list(zip(labels, actions))

        if width < 64:
            labels = ("n", "ctrl+s", "r", "e", "a", "d", "enter")
        elif width < 80:
            labels = ("n new", "ctrl+s save", "r ren", "e note", all_label,
                      "d del", "enter")
        else:
            labels = ("n new", "ctrl+s save", "r rename", "e note", all_label,
                      "d delete", "enter load")
        actions = (
            self.action_save_as,
            self.app.action_save_preset,
            self.action_rename,
            self.action_edit_note,
            self.action_toggle_all,
            self.action_delete,
            self._activate_selected,
        )
        return list(zip(labels, actions))

    def _update_selection_status(self) -> None:
        count = len(self._selected)
        specs = self._hint_action_specs()
        visible = [label for label, _action in specs]
        set_border_hint_layout(self, f"{count} sel" if count else "", visible)

    def on_resize(self, _event) -> None:
        if hasattr(self, "_selected"):
            self._update_selection_status()

    def action_toggle_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        if name in self._selected:
            self._selected.remove(name)
        else:
            self._selected.add(name)
        self.query_one("#preset-table", DataTable).update_cell(
            name, "pick", "\\[x]" if name in self._selected else "\\[ ]")
        self._update_selection_status()

    def action_toggle_all(self) -> None:
        table = self.query_one("#preset-table", DataTable)
        names = {
            row.key.value for row in table.ordered_rows
            if isinstance(row.key.value, str)
        }
        self._selected = set() if names and names <= self._selected else names
        for name in names:
            table.update_cell(name, "pick", "\\[x]" if name in self._selected else "\\[ ]")
        self._update_selection_status()

    def action_clear_selected(self) -> None:
        table = self.query_one("#preset-table", DataTable)
        for name in self._selected:
            try:
                table.update_cell(name, "pick", "\\[ ]")
            except Exception:
                pass
        self._selected.clear()
        self._update_selection_status()

    def _border_hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        return self._hint_action_specs()

    def _click_border_hint(self, event: MouseEvent) -> bool:
        hit = border_hint_hit(self, event.screen_x, event.screen_y)
        if hit is None:
            return False
        label, offset = hit
        actions = self._border_hint_actions()
        for text, action in actions:
            span = hint_span(label, text)
            if span is None:
                continue
            if span[0] <= offset < span[1]:
                event.stop()
                action()
                return True
        return False

    def on_mouse_move(self, event: MouseMove) -> None:
        tokens = [token for token, _ in self._border_hint_actions()]
        set_border_hint_hover(
            self,
            border_hint_action_token(self, event.screen_x, event.screen_y, tokens),
        )

    def on_leave(self, event: Leave) -> None:
        set_border_hint_hover(self, None)

    def _activate_selected(self) -> None:
        table = self.query_one("#preset-table", DataTable)
        rows = table.ordered_rows
        if rows and 0 <= table.cursor_row < len(rows):
            self.post_message(self.Activated(rows[table.cursor_row].key.value))

    def _publish_highlight(self, *, force: bool = False) -> None:
        """Publish the current row even when focus starts on row zero."""
        table = self.query_one("#preset-table", DataTable)
        rows = table.ordered_rows
        key = rows[table.cursor_row].key.value if (
            rows and 0 <= table.cursor_row < len(rows)) else None
        if not key:
            return
        if not force and key == self._highlighted:
            return
        self._highlighted = key
        preset = library.preset_get(key) if key else None
        banner = self.query_one("#preset-marquee", MarqueeBar)
        if preset:
            chain = preset["chain"]
            amp = _preset_file_label(chain, "model", "external")
            ir = _preset_file_label(chain, "ir", "—")
            dirty = self._active == preset["name"] and library.preset_is_dirty(
                preset["name"])
            state = "ACTIVE" if self._active == preset["name"] else "SAVED"
            if dirty:
                state += " · DIRTY"
            banner.content = (
                f"{state} · {preset['name']} · AMP {amp} · CAB {ir} · "
                f"{_preset_controls(chain)}"
            )
        else:
            banner.content = None
        self.post_message(self.Highlighted(preset))

    def on_descendant_focus(self, event) -> None:
        """Re-publish row zero when focus returns without a cursor move."""
        if getattr(event.widget, "id", None) == "preset-table":
            self._publish_highlight()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._publish_highlight()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        if name:
            self.post_message(self.Activated(name))

    def on_click(self, event) -> None:
        """Single click focuses; double click loads the preset (like Enter)."""
        if self._click_border_hint(event):
            return
        meta = event.style.meta
        if meta.get("column") == 0 and isinstance(meta.get("row"), int) and meta["row"] >= 0:
            table = self.query_one("#preset-table", DataTable)
            rows = table.ordered_rows
            if meta["row"] < len(rows):
                table.cursor_row = meta["row"]
                self.action_toggle_selected()
                event.stop()
                return
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
        with box:
            yield NonSelectableStatic(
                "↑↓ browse · enter load · esc cancel · ctrl+s saves the current chain",
                classes="modal-hint")
            yield MarqueeBar(id="preset-picker-marquee")
            tree = ClickSelectTree("Presets", id="preset-tree")
            tree.show_root = False
            yield tree
            yield SelectableStatic("", id="preset-detail")

    def on_mount(self) -> None:
        self._fill()
        self.query_one("#preset-tree", Tree).focus()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def _fill(self) -> None:
        tree = self.query_one("#preset-tree", Tree)
        tree.reset("Presets")
        tree.root.expand()
        groups = {}
        for p in library.preset_list():
            category, instrument = library.preset_group(p["name"])
            category_node = groups.get(category)
            if category_node is None:
                category_node = tree.root.add(category)
                category_node.expand()
                groups[category] = category_node
            group_key = (category, instrument)
            instrument_node = groups.get(group_key)
            if instrument_node is None:
                instrument_node = category_node.add(instrument)
                instrument_node.expand()
                groups[group_key] = instrument_node
            ch = p["chain"]
            note = p.get("note") or ""
            amp = _preset_file_label(ch, "model", "external")
            ir = _preset_file_label(ch, "ir", "—")
            instrument_node.add_leaf(
                f"{escape(p['name'])}  [dim]{escape(note)}[/dim]",
                {"preset": p, "summary": f"AMP {amp} · CAB {ir} · {_preset_controls(ch)}"})
        if not tree.root.children:
            tree.root.add_leaf("(no presets — ctrl+s to save the current chain)", None)
        if tree.root.children:
            # Start on the first loadable preset, not its category heading.
            first = tree.root.children[0]
            while first.children:
                first = first.children[0]
            tree.call_after_refresh(tree.move_cursor, first)

    def _confirm(self) -> None:
        tree = self.query_one("#preset-tree", Tree)
        node = tree.cursor_node
        data = node.data if node else None
        if not data or not data.get("preset"):
            return
        name = data["preset"]["name"]
        self.post_message(self.Loaded(name))
        self.dismiss()

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter load", self._confirm),
            ("esc cancel", self.dismiss),
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

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        data = event.node.data
        banner = self.query_one("#preset-picker-marquee", MarqueeBar)
        detail = self.query_one("#preset-detail", Static)
        if not data or not data.get("preset"):
            banner.content = None
            detail.update("")
            return
        p = data["preset"]
        banner.content = f"{p['name']} · {data.get('summary', '')}"
        try:
            resolved = library.preset_resolved_chain(p["name"])
        except ValueError as e:
            detail.update(f"[$error]{escape(str(e))}[/]")
            return
        active = library.preset_current() == p["name"]
        dirty = active and library.preset_is_dirty(p["name"])
        detail.update(preset_metadata_table(p, resolved, active=active, dirty=dirty))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data and event.node.data.get("preset"):
            self._confirm()


class PresetNameModal(GigBuddyModal):
    """Save As dialog; overwriting an existing name requires two submits."""

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
        box.border_title = "SAVE PRESET AS"
        with box:
            yield Input(placeholder="preset name (e.g. band-guitar-rhcp)", id="preset-save-input")

    def on_mount(self) -> None:
        self._pending_overwrite: str | None = None
        self.query_one("#preset-save-input", Input).focus()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if not name:
            return
        if library.preset_get(name) and self._pending_overwrite != name:
            self._pending_overwrite = name
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, f"'{name}' exists · enter again to overwrite",
                [token for token, _action in self._border_hint_actions()])
            return
        p = library.preset_save(name)
        self.post_message(self.Saved(p["name"]))
        self.dismiss()

    def _confirm(self) -> None:
        inp = self.query_one("#preset-save-input", Input)
        if inp.value.strip():
            self.on_input_submitted(Input.Submitted(inp, inp.value))

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter save", self._confirm),
            ("esc cancel", self.dismiss),
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


class PresetRenameModal(GigBuddyModal):
    CSS = "PresetRenameModal > ModalBox { width: 60%; height: auto; margin: 6 20; }"

    class Renamed(Message):
        def __init__(self, old_name: str, new_name: str) -> None:
            super().__init__()
            self.old_name = old_name
            self.new_name = new_name

    def __init__(self, name: str) -> None:
        super().__init__()
        self._preset_name = name

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "RENAME PRESET"
        with box:
            yield Input(value=self._preset_name, id="preset-rename-input")

    def on_mount(self) -> None:
        inp = self.query_one("#preset-rename-input", Input)
        inp.focus()
        inp.action_select_all()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def _confirm(self) -> None:
        new_name = self.query_one("#preset-rename-input", Input).value.strip()
        if not new_name:
            return
        try:
            library.preset_rename(self._preset_name, new_name)
        except ValueError as e:
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, str(e), [token for token, _action in self._border_hint_actions()])
            return
        self.post_message(self.Renamed(self._preset_name, new_name))
        self.dismiss()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._confirm()

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter rename", self._confirm),
            ("esc cancel", self.dismiss),
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


class PresetNoteModal(GigBuddyModal):
    CSS = "PresetNoteModal > ModalBox { width: 70%; height: auto; margin: 6 15; }"

    class Updated(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, name: str, note: str) -> None:
        super().__init__()
        self._preset_name = name
        self._note = note

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = f"EDIT NOTE · {self._preset_name}"
        with box:
            yield Input(value=self._note, placeholder="optional note", id="preset-note-input")

    def on_mount(self) -> None:
        inp = self.query_one("#preset-note-input", Input)
        inp.focus()
        inp.action_select_all()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def _confirm(self) -> None:
        note = self.query_one("#preset-note-input", Input).value.strip()
        try:
            library.preset_update_note(self._preset_name, note or None)
        except ValueError as e:
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, str(e), [token for token, _action in self._border_hint_actions()])
            return
        self.post_message(self.Updated(self._preset_name))
        self.dismiss()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._confirm()

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter save", self._confirm),
            ("esc cancel", self.dismiss),
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


class PresetDeleteModal(GigBuddyModal):
    # destructive action: fixed error red border (pinned across themes)
    CSS = """
    PresetDeleteModal > ModalBox {
        width: 55%; height: auto; margin: 7 22;
        border: round $error; border-title-color: $error;
    }
    """

    class Deleted(Message):
        def __init__(self, names: list[str]) -> None:
            super().__init__()
            self.names = names

    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self._preset_names = names

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "DELETE PRESET"
        with box:
            shown = ", ".join(self._preset_names[:5])
            if len(self._preset_names) > 5:
                shown += f" +{len(self._preset_names) - 5} more"
            yield NonSelectableStatic(
                f"Delete {len(self._preset_names)} preset(s)?\n{shown}\n"
                "[b $error]This cannot be undone.[/]",
                id="preset-delete-body")

    def on_mount(self) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def _confirm(self) -> None:
        deleted = [name for name in self._preset_names if library.preset_delete(name)]
        if not deleted:
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, "presets no longer exist",
                [token for token, _action in self._border_hint_actions()])
            return
        self.post_message(self.Deleted(deleted))
        self.dismiss()

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter delete", self._confirm),
            ("esc cancel", self.dismiss),
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
