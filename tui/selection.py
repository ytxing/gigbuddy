"""Text-selection policy shared by the main screen and modal screens.

Normal mouse drags should only select the metadata/description widgets that
explicitly opt in. Holding Shift is an intentional escape hatch: temporarily
make every visible widget selectable for that drag, then restore the normal
control-only policy when the mouse is released.
"""

from __future__ import annotations

from textual.events import MouseDown, MouseUp
from textual.screen import Screen
from textual.widgets import Static
from textual.widgets._progress_bar import Bar, PercentageStatus

# ProgressBar renders these as separate leaf widgets. They are numeric status,
# not metadata, so keep them outside the normal copy surface as well.
Bar.ALLOW_SELECT = False
PercentageStatus.ALLOW_SELECT = False


class NonSelectableStatic(Static):
    """Plain UI copy that must not start a screen text selection."""

    ALLOW_SELECT = False


class ShiftSelectableScreenMixin:
    """Add Shift+drag selection without making every control selectable."""

    _shift_selection_overrides: list[tuple[object, bool, object]] | None = None

    def _enable_shift_selection(self) -> None:
        if self._shift_selection_overrides is not None:
            return
        overrides: list[tuple[object, bool, object]] = []
        for widget in self.walk_children(with_self=True):
            if getattr(widget, "allow_select", False):
                continue
            if "ALLOW_SELECT" in getattr(widget, "__dict__", {}):
                overrides.append((widget, True, widget.__dict__["ALLOW_SELECT"]))
            else:
                overrides.append((widget, False, None))
            widget.ALLOW_SELECT = True
        self._shift_selection_overrides = overrides

    def _disable_shift_selection(self) -> None:
        overrides = self._shift_selection_overrides
        self._shift_selection_overrides = None
        if not overrides:
            return
        for widget, had_instance_value, previous in overrides:
            if had_instance_value:
                widget.ALLOW_SELECT = previous
            else:
                try:
                    del widget.ALLOW_SELECT
                except AttributeError:
                    pass

    def _forward_event(self, event):
        if isinstance(event, MouseDown) and event.shift:
            self._enable_shift_selection()
        try:
            return super()._forward_event(event)
        finally:
            if isinstance(event, MouseUp):
                self._disable_shift_selection()


class ShiftSelectableScreen(ShiftSelectableScreenMixin, Screen):
    """Default app screen with opt-in selection and Shift override."""


__all__ = [
    "NonSelectableStatic",
    "ShiftSelectableScreen",
    "ShiftSelectableScreenMixin",
]
