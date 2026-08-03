"""Shared modal-window behavior for the GigBuddy TUI.

Every window opened on top of the main screen gets consistent keyboard handling:
- Esc closes it (cancel, no side effects)
- Enter confirms the current selection (subclasses implement _confirm)

Note: when a DataTable is focused, Enter is consumed by the table itself
(action_select_cursor) — this screen-level binding is the fallback for other
focused widgets (Static, empty areas), so behavior is identical either way.

Styling note: Textual scopes screen CSS to the concrete screen class, so the
shared warm-modal look lives on the ModalBox container (exact-class selector),
not on GigBuddyModal — subclasses just compose `with ModalBox():`.
"""
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import DataTable, Tree


class ClickSelectTable(DataTable):
    """DataTable where a single click only moves the cursor; selection comes
    from Enter or double-click. Textual's DataTable posts RowSelected on a
    single click when the clicked row is already the cursor row (highlight
    click), which both breaks single-click-focus and double-fires on a real
    double-click (base RowSelected + our chain>=2 action)."""

    async def _on_click(self, event) -> None:
        self._set_hover_cursor(True)
        meta = event.style.meta
        if "row" not in meta or "column" not in meta:
            return
        if self.cursor_type != "row" and meta.get("out_of_bounds", False):
            return
        row_index = meta["row"]
        column_index = meta["column"]
        is_header_click = self.show_header and row_index == -1
        is_row_label_click = self.show_row_labels and column_index == -1
        if is_header_click:
            column = self.ordered_columns[column_index]
            self.post_message(DataTable.HeaderSelected(
                self, column.key, column_index, label=column.label))
        elif is_row_label_click:
            row = self.ordered_rows[row_index]
            self.post_message(DataTable.RowLabelSelected(
                self, row.key, row_index, label=row.label))
        elif self.show_cursor and self.cursor_type != "none":
            # move the cursor only — no RowSelected on a highlight click.
            # prevent_default() is required: Textual dispatches handlers down
            # the whole MRO, so without it the base DataTable._on_click still
            # posts RowSelected for a highlight click (and double-fires on a
            # real double-click).
            self.cursor_coordinate = Coordinate(row_index, column_index)
            self._scroll_cursor_into_view(animate=True)
            event.prevent_default()
            event.stop()


class ClickSelectTree(Tree):
    """Tree where a single click only moves the cursor (focus) and a double
    click selects — Textual's Tree fires NodeSelected on a single click, which
    clashes with the app-wide single-click-focus / double-click-act rule."""

    async def _on_click(self, event) -> None:
        async with self.lock:
            meta = event.style.meta
            if "line" not in meta:
                return
            cursor_line = meta["line"]
            if meta.get("toggle", False):
                node = self.get_node_at_line(cursor_line)
                if node is not None:
                    self._toggle_node(node)
            else:
                self.cursor_line = cursor_line
                # prevent base Tree._on_click from selecting on single click
                # (MRO dispatch runs every handler, not just the most derived)
                event.prevent_default()
                if getattr(event, "chain", 1) >= 2:
                    await self.run_action("select_cursor")


class ModalBox(Vertical):
    """Shared modal container: warm panel, amber rounded border, padded.

    "Floating" look: the screen behind is dimmed (GigBuddyModal) and the box
    uses a lighter surface + bright accent border so it reads as raised.
    """

    DEFAULT_CSS = """
    ModalBox {
        background: $panel;
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $text-muted;
        padding: 1 2;
    }
    ModalBox .modal-hint {
        height: 1; margin-bottom: 1;
        color: $text-muted;
    }
    """


class GigBuddyModal(ModalScreen):
    """Base modal: dims the background so the box appears to float above it."""

    DEFAULT_CSS = """
    GigBuddyModal {
        background: rgba(0, 0, 0, 0.55);
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "close"),
        Binding("enter", "confirm", "confirm"),
        # override ModalScreen's ctrl+c "copy selected text" so the two-press
        # quit works from every modal too (forwards to the app)
        Binding("ctrl+c", "request_quit", "quit (×2)", show=False),
    ]

    def action_request_quit(self) -> None:
        self.app.action_request_quit()

    def action_cancel(self) -> None:
        """Esc: close without doing anything."""
        self.dismiss()

    def action_confirm(self) -> None:
        """Enter: act on the current selection (subclass hook)."""
        self._confirm()

    def _confirm(self) -> None:
        raise NotImplementedError
