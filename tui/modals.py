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
from rich.cells import cell_len
from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.events import Leave, MouseEvent, MouseMove
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static, Tree

from .marquee import ellipsis_window
from .selection import ShiftSelectableScreenMixin


def border_hint_label(widget) -> str:
    """Return a subtitle's visible text without Rich markup.

    Hover styling stores a styled ``Text`` back in ``border_subtitle``. The
    border geometry and click hit-testing still need the plain cell string.
    """
    value = str(widget.border_subtitle or "")
    try:
        return Text.from_markup(value).plain
    except Exception:
        return value


def border_hint_hit(widget, screen_x: int, screen_y: int) -> tuple[str, int] | None:
    """Return the border subtitle and visual-cell offset under the pointer."""
    if screen_y != widget.region.bottom - 1:
        return None
    label = border_hint_label(widget)
    if not label:
        return None
    label_width = cell_len(label)
    label_start = widget.region.x + max(1, widget.region.width - label_width - 2)
    offset = screen_x - label_start
    if offset < 0 or offset >= label_width:
        return None
    return label, offset


def hint_span(label: str, token: str) -> tuple[int, int] | None:
    """Return a token's visual-cell span in a border subtitle."""
    # Match complete separator-delimited segments first.  A raw substring
    # search would resolve the ``d`` action to the ``d`` in ``add``.
    key = _compact_hint_action(token).casefold()
    if not key:
        return None
    offset = 0
    for segment in label.split("·"):
        stripped = segment.strip()
        leading = len(segment) - len(segment.lstrip())
        visible = stripped.casefold()
        wanted = token.strip().casefold()
        if visible == wanted or visible == key or visible.startswith(key + " "):
            segment_start = offset + leading
            segment_end = offset + len(segment.rstrip())
            return cell_len(label[:segment_start]), cell_len(label[:segment_end])
        offset += len(segment) + 1
    return None


def border_hint_segments(widget) -> tuple[str, ...]:
    """Return the visible ``·``-separated operation segments."""
    return tuple(
        segment.strip()
        for segment in border_hint_label(widget).split("·")
        if segment.strip()
    )


def set_border_hint_layout(widget, state: str, actions: list[str] | tuple[str, ...]) -> str:
    """Render one right-aligned hint block with state before actions.

    Textual right-aligns a border subtitle. Keeping the label unpadded makes
    the whole hint live in the lower-right corner; changing state text grows
    to the left while the stable action tokens remain on the right. Action
    labels are compacted as complete tokens before state text is shortened,
    so a narrow pane never leaves a partial ``l loo…``-style command behind.

    ``state`` is the changing part of the hint; ``actions`` are the stable
    clickable suffix. The raw values are cached so a resize can recompute the
    label after Textual has assigned the widget's real region width.
    """
    width = int(getattr(getattr(widget, "region", None), "width", 0) or 0)
    if not width:
        width = int(getattr(getattr(widget, "size", None), "width", 0) or 0)
    raw_state = str(state or "")
    raw_actions = tuple(str(action) for action in actions if action)
    if not width:
        label = " · ".join(part for part in (raw_state, " · ".join(raw_actions))
                            if part)
        widget.border_subtitle = label
        widget._hint_layout_state = raw_state
        widget._hint_layout_actions = raw_actions
        widget._hint_hover_base = None
        widget._hint_hover_token = None
        return label
    # Textual passes the border edge width minus two to its label renderer,
    # which then reserves one cell for each corner. Reserve all six cells
    # here so the visible subtitle is never truncated by the border renderer.
    inner = max(width - 6, 1)
    display_actions = _fit_hint_actions(raw_actions, inner)
    action_text = " · ".join(display_actions)
    if not action_text:
        label = ellipsis_window(raw_state, inner)
    elif not raw_state:
        label = action_text
    else:
        separator_width = cell_len(" · ")
        state_width = max(inner - cell_len(action_text) - separator_width, 0)
        state_text = ellipsis_window(raw_state, state_width)
        label = f"{state_text} · {action_text}" if state_text else action_text
    widget.border_subtitle = label
    widget._hint_layout_state = raw_state
    widget._hint_layout_actions = raw_actions
    widget._hint_hover_base = None
    widget._hint_hover_token = None
    return label


def refresh_border_hint_layout(widget) -> str:
    """Recompute a previously configured hint after a resize.

    Compose-time dimensions are often zero. Call this from ``on_resize`` (or
    after mount) so the visible action set matches the actual border width and
    the click hit map never refers to a clipped token.
    """
    if not hasattr(widget, "_hint_layout_state"):
        return border_hint_label(widget)
    return set_border_hint_layout(
        widget,
        getattr(widget, "_hint_layout_state", ""),
        getattr(widget, "_hint_layout_actions", ()),
    )


def _compact_hint_action(action: str) -> str:
    """Return the key portion used when a complete action label will not fit."""
    return action.strip().split(None, 1)[0] if action.strip() else ""


def _fit_hint_actions(actions: tuple[str, ...], width: int) -> tuple[str, ...]:
    """Fit action tokens without clipping a token in the middle.

    Callers provide actions in priority order. First retain their descriptive
    labels, then use key-only labels, and finally keep the rightmost suffix if
    an exceptionally narrow surface cannot display every action. ``hint_span``
    accepts the key-only form, so the compact labels remain clickable through
    the same action mapping.
    """
    if not actions or width <= 0:
        return ()
    if cell_len(" · ".join(actions)) <= width:
        return actions
    compact = tuple(_compact_hint_action(action) for action in actions if action)
    if cell_len(" · ".join(compact)) <= width:
        return compact
    for start in range(1, len(compact)):
        candidate = compact[start:]
        if cell_len(" · ".join(candidate)) <= width:
            return candidate
    return compact[-1:] if compact else ()


def border_hint_action_hit(widget, screen_x: int, screen_y: int,
                           tokens: tuple[str, ...] | list[str]) -> bool:
    """Return whether the pointer is over one of the clickable hint tokens."""
    hit = border_hint_hit(widget, screen_x, screen_y)
    if hit is None:
        return False
    label, offset = hit
    return border_hint_action_token(widget, screen_x, screen_y, tokens) is not None


def border_hint_action_token(widget, screen_x: int, screen_y: int,
                             tokens: tuple[str, ...] | list[str]) -> str | None:
    """Return the specific action token under the pointer, if any."""
    hit = border_hint_hit(widget, screen_x, screen_y)
    if hit is None:
        return None
    label, offset = hit
    for token in tokens:
        span = hint_span(label, token)
        if span is not None and span[0] <= offset < span[1]:
            return token
    return None


def border_hint_click(widget, event, actions: list) -> bool:
    """Run the action whose token was clicked in ``widget``'s border subtitle.

    Shared by every clickable hint (chain panel, library, modals): returns
    True when the click landed on a token and was consumed.
    """
    hit = border_hint_hit(widget, event.screen_x, event.screen_y)
    if hit is None:
        return False
    label, offset = hit
    for token, action in actions:
        span = hint_span(label, token)
        if span is not None and span[0] <= offset < span[1]:
            event.stop()
            action()
            return True
    return False


def set_border_hint_hover(widget, token: str | None) -> None:
    """Highlight only one action token in a border subtitle.

    Textual exposes one CSS color for a border subtitle, so coloring the whole
    subtitle cannot distinguish adjacent actions. Rich ``Text`` spans let the
    hovered token use the active theme's accent/background while all other
    tokens keep the muted subtitle style.
    """
    label = border_hint_label(widget)
    previous = (getattr(widget, "_hint_hover_base", None),
                getattr(widget, "_hint_hover_token", None))
    if previous == (label, token):
        return
    widget._hint_hover_base = label
    widget._hint_hover_token = token
    if token is None:
        widget.border_subtitle = label
        return
    span = hint_span(label, token)
    if span is None:
        widget.border_subtitle = label
        widget._hint_hover_token = None
        return
    variables = getattr(getattr(widget, "app", None), "theme_variables", {}) or {}
    accent = str(variables.get("accent") or "#e59a3c")
    background = str(variables.get("background") or "#1b1512")
    styled = Text(label)
    styled.stylize(f"bold {background} on {accent}", *span)
    widget.border_subtitle = styled


def _is_blank_static_click(widget: Static, event: MouseEvent) -> bool:
    """Recognize empty/trailing space without treating selectable text as blank."""
    content = getattr(widget, "content", None)
    if content is None or (isinstance(content, str) and not content.strip()):
        return True
    try:
        content_region = widget.content_region
        optimal_width = widget.get_content_width(content_region.size, widget.size)
    except Exception:
        return False
    content_left = content_region.x - widget.region.x
    return event.x < content_left or event.x >= content_left + min(
        optimal_width, content_region.width)


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
                # 单击移光标也跟随滚动：选中项若在可视区外，滚动到可见位置
                self.scroll_to_line(cursor_line)
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


class GigBuddyModal(ShiftSelectableScreenMixin, ModalScreen):
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

    def on_resize(self, _event) -> None:
        """Re-fit every modal hint after the screen gets a real width."""
        for box in self.query(ModalBox):
            refresh_border_hint_layout(box)

    def dismiss(self, *args, **kwargs) -> None:
        """Idempotent close.

        A second dismiss (double Enter/Esc, a click landing while a previous
        dismiss is mid-flight, a late async answer) must never pop the last
        screen — Textual raises ScreenStackError when the stack has only the
        default screen left.
        """
        if getattr(self, "_gigbuddy_dismissed", False):
            return
        self._gigbuddy_dismissed = True
        super().dismiss(*args, **kwargs)

    def _click_border_hint(self, event: MouseEvent) -> bool:
        """Make the existing bottom-right border hint mouse accessible."""
        for box in self.query(ModalBox):
            tokens = getattr(box, "_hint_layout_actions", ())
            if not tokens:
                tokens = border_hint_segments(box)
            segment = border_hint_action_token(
                box, event.screen_x, event.screen_y, tokens)
            if segment is None:
                continue
            key = segment.split(maxsplit=1)[0].casefold()
            if key == "esc":
                event.stop()
                self.dismiss()
                return True
            if key == "enter":
                event.stop()
                self._confirm()
                return True
            if key == "a" and hasattr(self, "action_toggle_all"):
                event.stop()
                self.action_toggle_all()
                return True
            if key == "space" and hasattr(self, "action_toggle_row"):
                event.stop()
                self.action_toggle_row()
                return True
            action_name = {
                "space": "action_playback_toggle",
                "s": "action_playback_stop",
                "l": "action_playback_loop",
                "d": "action_download_dry",
                "change": "_confirm",
            }.get(key)
            action = getattr(self, action_name, None) if action_name else None
            if action is not None:
                event.stop()
                action()
                return True
        return False

    def on_mouse_move(self, event: MouseMove) -> None:
        for box in self.query(ModalBox):
            tokens = getattr(box, "_hint_layout_actions", ())
            if not tokens:
                tokens = border_hint_segments(box)
            set_border_hint_hover(
                box,
                border_hint_action_token(
                    box, event.screen_x, event.screen_y, tokens),
            )

    def on_leave(self, event: Leave) -> None:
        for box in self.query(ModalBox):
            set_border_hint_hover(box, None)

    def on_click(self, event: MouseEvent) -> None:
        """Handle the explicit border action without dismissing the modal."""
        if self._click_border_hint(event):
            return

    def _confirm(self) -> None:
        raise NotImplementedError
