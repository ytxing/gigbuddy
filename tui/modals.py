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
from textual.screen import ModalScreen


class ModalBox(Vertical):
    """Shared modal container: warm surface, amber rounded border, padded."""

    DEFAULT_CSS = """
    ModalBox {
        background: $surface;
        border: round $primary;
        border-title-color: $primary;
        border-subtitle-color: $text-muted;
        padding: 1 2;
    }
    ModalBox .modal-hint {
        height: 1; margin-bottom: 1;
        color: $text-muted;
    }
    """


class GigBuddyModal(ModalScreen):
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
