"""Reusable controls for v0.2 pane navigation and list surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len
from rich.style import Style
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
    # Rich markup treats an unpaired closing tag as invalid; the backslash is
    # consumed by Textual and keeps the visible token as ``[/]``.
    NAVIGATION_HINT = r"\[ / ] select tab"

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
        self.sync_border_title()
        if notify:
            self.post_message(self.Changed(view_tab_id))
        return True

    def on_mount(self) -> None:
        self.sync_border_title()

    def sync_border_title(self) -> None:
        """Mirror the interactive strip into its pane's top border."""
        if self.has_class("view-tabs--border") and self.display and self.parent:
            self.parent.border_title = self.render()

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
        if not self._tabs:
            return
        index = self._active_index()
        self._activate_at((index - 1) % len(self._tabs))

    def action_next_view(self) -> None:
        if not self._tabs:
            return
        index = self._active_index()
        self._activate_at((index + 1) % len(self._tabs))

    def render(self) -> Text:
        text = Text()
        self._ranges = []
        text.append(self.pane_name, style="bold")
        cursor = cell_len(self.pane_name)
        text.append("  ──")
        cursor += 4
        for index, tab in enumerate(self._tabs):
            if index == 0:
                text.append("  ")
                cursor += 2
            else:
                text.append(" / ")
                cursor += 3
            start = cursor
            style = Style(meta={
                "view_tab_id": tab.view_tab_id,
                "view_tab_strip_id": self.id or "",
            })
            if tab.view_tab_id == self._active:
                style += Style(underline=True)
            elif tab.view_tab_id == self._hovered:
                style += Style(underline=True)
            else:
                style += Style(dim=True)
            text.append(tab.label, style=style)
            cursor += cell_len(tab.label)
            self._ranges.append((tab.view_tab_id, start, cursor))
        return text

    def activate_from_border(self, event: MouseEvent) -> bool:
        """Activate a tab represented by this strip's border-title span."""
        meta = event.style.meta
        if meta.get("view_tab_strip_id") != (self.id or ""):
            return False
        view_tab_id = meta.get("view_tab_id")
        if view_tab_id not in self.view_tab_ids:
            return False
        self.focus()
        self.set_active(str(view_tab_id), notify=True)
        event.stop()
        return True

    def hover_from_border(self, event: MouseMove) -> bool:
        """Keep border-title hover feedback aligned with the pointer."""
        meta = event.style.meta
        hovered = (str(meta.get("view_tab_id"))
                   if meta.get("view_tab_strip_id") == (self.id or "")
                   else None)
        if hovered not in self.view_tab_ids:
            hovered = None
        if hovered != self._hovered:
            self._hovered = hovered
            self.refresh()
            self.sync_border_title()
        return hovered is not None

    def clear_border_hover(self) -> None:
        if self._hovered is not None:
            self._hovered = None
            self.refresh()
            self.sync_border_title()

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
    """Fixed-track one-line query, sort, and optional type surface.

    ``Input`` and ``Select`` remain real Textual controls so normal keyboard
    behavior, cursor scrolling and select overlays work. The surrounding
    surface supplies the stable labels and geometry required by the spec.
    """

    DEFAULT_CSS = """
    SearchBar {
        width: 100%;
        height: 1;
        layout: grid;
        grid-size: 4;
        grid-columns: 9 1fr 7 24;
        background: $surface-lighten-1;
        color: $text-muted;
    }
    SearchBar.search-bar--compact {
        grid-columns: 9 1fr 7 18;
    }
    SearchBar.search-bar--with-type {
        grid-size: 6;
        /* SEARCH:/SORT:/TYPE: 标签统一深色块（$background），
           与 $boost 的 Input/Select 亮棕块区分。 */
        grid-columns: 9 1fr 7 24 7 24;
    }
    SearchBar.search-bar--with-type.search-bar--compact {
        grid-columns: 9 1fr 7 18 7 18;
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
        /* SEARCH:/SORT:/TYPE: 统一深色块（$background = #1b1512），
           与 $boost 的 Input/Select 亮棕背景形成对比。 */
        background: $background;
    }
    SearchBar > Input {
        width: 100%;
        height: 1;
        min-width: 16;
        padding: 0 1;
        border-top: none !important;
        border-right: none !important;
        border-left: none !important;
        border-bottom: none !important;
        background: $boost;
        color: $text;
    }
    SearchBar.search-bar--compact > Input {
        min-width: 10;
    }
    SearchBar > Input:focus {
        background: $boost;
        border-top: none !important;
        border-right: none !important;
        border-left: none !important;
    }
    SearchBar > Select {
        width: 100%;
        height: 1;
        min-width: 24;
        background: $boost;
        border-top: none !important;
        border-right: none !important;
        border-left: none !important;
        border-bottom: none !important;
        color: $text;
    }
    SearchBar.search-bar--compact > Select {
        min-width: 18;
    }
    SearchBar > Select:focus {
        background: $boost;
        border-top: none !important;
        border-right: none !important;
        border-left: none !important;
    }
    SearchBar > Select > SelectCurrent {
        border-top: none !important;
        border-right: none !important;
        border-left: none !important;
        border-bottom: none !important;
        background: $boost;
    }
    SearchBar > Input:hover,
    SearchBar > Select:hover {
        border-top: none !important;
        border-right: none !important;
        border-left: none !important;
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
        type_id: str | None = None,
        type_options: list[tuple[str, str]] | None = None,
        placeholder: str = "",
        input_cls: type[Input] = Input,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        sort_only = input_id is None
        merged_classes = " ".join(
            item for item in (
                classes,
                "search-bar--sort-only" if sort_only else "",
                "search-bar--with-type" if type_id is not None else "",
            )
            if item
        ) or None
        super().__init__(id=id, classes=merged_classes)
        self.input_id = input_id
        self.sort_id = sort_id
        self.type_id = type_id
        self._input_cls = input_cls
        self._placeholder = placeholder
        self._sort_options = list(sort_options)
        self._type_options = list(type_options or [("ALL", "all")])

    def compose(self) -> ComposeResult:
        if self.input_id is not None:
            yield NonSelectableStatic("SEARCH:", classes="search-label")
            yield self._input_cls(
                placeholder=self._placeholder or '@author #tag author:name tag:clean make:"Brand Model"',
                id=self.input_id,
                compact=True,
            )
            yield NonSelectableStatic("SORT:", classes="search-label")
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
        if self.type_id is not None:
            yield NonSelectableStatic("TYPE:", classes="search-label")
            yield Select(
                self._type_options,
                value=self._type_options[0][1]
                if self._type_options else Select.NULL,
                allow_blank=False,
                compact=True,
                id=self.type_id,
            )

    def on_click(self, event: MouseEvent) -> None:
        """Keep search-control clicks out of the app-level chain router."""
        event.stop()

    def set_compact(self, compact: bool) -> None:
        self.set_class(compact, "search-bar--compact")

    def set_type_options(self, options: list[tuple[str, str]],
                         selected: str = "all") -> None:
        """Refresh the inline Type select without changing its track."""
        if self.type_id is None:
            return
        select = self.query_one(f"#{self.type_id}", Select)
        normalized = [(str(label), str(value)) for label, value in options]
        if not normalized:
            normalized = [("ALL", "all")]
        values = {value for _, value in normalized}
        select.set_options(normalized)
        select.value = selected if selected in values else normalized[0][1]
