"""Reusable controls for v0.2 pane navigation and list surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Leave, MouseEvent, MouseMove
from textual.message import Message
from textual.widgets import Input, Select, Static

from .selection import NonSelectableStatic


@dataclass(frozen=True)
class ViewTab:
    """Stable identity and display label for one pane-local view."""

    view_tab_id: str
    label: str


class ViewTabStrip(Static):
    """One focus stop for a pane name and its view tags.

    The strip deliberately owns only keyboard focus and hit testing. The host
    decides what changing ``view_tab_id`` means, which keeps this control free
    of Library/Detail/Preset business logic.
    """

    can_focus = True
    ALLOW_SELECT = False

    class Changed(Message):
        def __init__(self, view_tab_id: str) -> None:
            super().__init__()
            self.view_tab_id = view_tab_id

    BINDINGS = [
        Binding("[", "previous_view", "previous view", show=False),
        Binding("]", "next_view", "next view", show=False),
    ]

    def __init__(
        self,
        pane_name: str,
        tabs: list[tuple[str, str] | ViewTab],
        *,
        active: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.pane_name = str(pane_name or "PANE").upper()
        self._tabs = [
            item if isinstance(item, ViewTab)
            else ViewTab(str(item[0]), str(item[1]).upper())
            for item in tabs
        ]
        self._active = active or (self._tabs[0].view_tab_id if self._tabs else "")
        self._hovered: str | None = None
        self._ranges: list[tuple[str, int, int]] = []

    @property
    def active(self) -> str:
        return self._active

    @property
    def view_tab_ids(self) -> tuple[str, ...]:
        return tuple(tab.view_tab_id for tab in self._tabs)

    @property
    def tabs(self) -> tuple[ViewTab, ...]:
        return tuple(self._tabs)

    def set_active(self, view_tab_id: str, *, notify: bool = False) -> bool:
        if view_tab_id not in self.view_tab_ids or view_tab_id == self._active:
            return False
        self._active = view_tab_id
        self.refresh()
        if notify:
            self.post_message(self.Changed(view_tab_id))
        return True

    def _activate_at(self, index: int) -> None:
        if 0 <= index < len(self._tabs):
            self.set_active(self._tabs[index].view_tab_id, notify=True)

    def _active_index(self) -> int:
        return next(
            (index for index, tab in enumerate(self._tabs)
             if tab.view_tab_id == self._active),
            -1,
        )

    def action_previous_view(self) -> None:
        index = self._active_index()
        if index > 0:
            self._activate_at(index - 1)

    def action_next_view(self) -> None:
        index = self._active_index()
        if 0 <= index < len(self._tabs) - 1:
            self._activate_at(index + 1)

    def render(self) -> Text:
        text = Text()
        self._ranges = []
        text.append(self.pane_name, style="bold")
        cursor = cell_len(self.pane_name)
        for tab in self._tabs:
            text.append("  ")
            cursor += 2
            start = cursor
            style = None
            if tab.view_tab_id == self._active:
                style = "bold reverse"
            elif tab.view_tab_id == self._hovered:
                style = "underline"
            text.append(tab.label, style=style)
            cursor += cell_len(tab.label)
            self._ranges.append((tab.view_tab_id, start, cursor))
        return text

    def _hit_tab(self, x: int) -> str | None:
        for view_tab_id, start, end in self._ranges:
            if start <= x < end:
                return view_tab_id
        return None

    def on_click(self, event: MouseEvent) -> None:
        view_tab_id = self._hit_tab(event.x)
        if view_tab_id is not None:
            self.focus()
            self.set_active(view_tab_id, notify=True)
            event.stop()

    def on_mouse_move(self, event: MouseMove) -> None:
        hovered = self._hit_tab(event.x)
        if hovered != self._hovered:
            self._hovered = hovered
            self.refresh()

    def on_leave(self, _event: Leave) -> None:
        if self._hovered is not None:
            self._hovered = None
            self.refresh()


class SearchBar(Horizontal):
    """Fixed-track one-line query and sort surface.

    ``Input`` and ``Select`` remain real Textual controls so normal keyboard
    behavior, cursor scrolling and select overlays work. The surrounding
    surface supplies the stable labels and geometry required by the spec.
    """

    DEFAULT_CSS = """
    SearchBar {
        width: 100%;
        height: 1;
        layout: grid;
        grid-size: 5;
        grid-columns: 7 1fr 1 6 24;
        background: $surface;
        color: $text-muted;
    }
    SearchBar.search-bar--compact {
        grid-columns: 7 1fr 1 6 18;
    }
    SearchBar.search-bar--sort-only {
        grid-size: 2;
        grid-columns: 1fr 24;
    }
    SearchBar.search-bar--sort-only.search-bar--compact {
        grid-columns: 1fr 18;
    }
    SearchBar > .search-label {
        height: 1;
        padding: 0 1;
        content-align: left middle;
        text-style: bold;
    }
    SearchBar > Input {
        width: 100%;
        height: 1;
        min-width: 16;
        padding: 0 1;
        border: none;
        background: $surface;
        color: $text;
    }
    SearchBar.search-bar--compact > Input {
        min-width: 10;
    }
    SearchBar > Input:focus {
        background: $boost;
        border: none;
    }
    SearchBar > .search-separator {
        width: 1;
        height: 1;
        color: $text-muted;
        content-align: center middle;
    }
    SearchBar > Select {
        width: 100%;
        height: 1;
        min-width: 24;
        background: $surface;
        border: none;
        color: $text;
    }
    SearchBar.search-bar--compact > Select {
        min-width: 18;
    }
    SearchBar > Select:focus {
        background: $boost;
        border: none;
    }
    SearchBar:focus-within {
        background: $boost;
    }
    """

    def __init__(
        self,
        *,
        input_id: str | None,
        sort_options: list[tuple[str, str]],
        sort_id: str,
        placeholder: str = "",
        input_cls: type[Input] = Input,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        sort_only = input_id is None
        merged_classes = " ".join(
            item for item in (classes, "search-bar--sort-only" if sort_only else "")
            if item
        ) or None
        super().__init__(id=id, classes=merged_classes)
        self.input_id = input_id
        self.sort_id = sort_id
        self._input_cls = input_cls
        self._placeholder = placeholder
        self._sort_options = list(sort_options)

    def compose(self) -> ComposeResult:
        if self.input_id is not None:
            yield NonSelectableStatic("SEARCH", classes="search-label")
            yield self._input_cls(
                placeholder=self._placeholder,
                id=self.input_id,
            )
            yield NonSelectableStatic("·", classes="search-separator")
            yield NonSelectableStatic("SORT", classes="search-label")
        else:
            # Keep sort-only bars right-aligned in the same fixed final track.
            yield NonSelectableStatic("", classes="search-spacer")
        yield Select(
            self._sort_options,
            value=self._sort_options[0][1] if self._sort_options else Select.NULL,
            allow_blank=not bool(self._sort_options),
            compact=True,
            id=self.sort_id,
        )

    def set_compact(self, compact: bool) -> None:
        self.set_class(compact, "search-bar--compact")


class TypeFilterMenu(Select[str]):
    """Dynamic single-select menu used by a result table's Type header."""

    class Closed(Message):
        pass

    def __init__(self) -> None:
        super().__init__(
            [("ALL", "all")],
            value="all",
            allow_blank=False,
            compact=True,
            id="type-filter-menu",
        )
        self.target_table_id: str | None = None

    def configure_values(self, values: list[str], selected: str) -> None:
        options = [("ALL", "all")]
        options.extend((value.upper(), value) for value in values if value != "all")
        self.set_options(options)
        self.value = selected if selected in {value for _, value in options} else "all"

    def action_close_menu(self) -> None:
        self.expanded = False
        self.display = False
        self.post_message(self.Closed())

    BINDINGS = [
        *Select.BINDINGS,
        Binding("escape", "close_menu", "close", show=False),
    ]
