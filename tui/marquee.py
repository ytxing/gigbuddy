"""Banner-style scrolling title (marquee) for long text that overflows its row.

Textual ships no marquee widget in 8.2, so this is a minimal one: while the
content is wider than the widget, a 0.12s timer advances an offset and the
render shows a paused ping-pong window. When the text fits, it scrolls nothing.
"""
import re

from rich.cells import cell_len
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static


_RICH_ANSI_COLORS = {
    "ansi_black": "black",
    "ansi_red": "red",
    "ansi_green": "green",
    "ansi_yellow": "yellow",
    "ansi_blue": "blue",
    "ansi_magenta": "magenta",
    "ansi_cyan": "cyan",
    "ansi_white": "white",
    "ansi_bright_black": "bright_black",
    "ansi_bright_red": "bright_red",
    "ansi_bright_green": "bright_green",
    "ansi_bright_yellow": "bright_yellow",
    "ansi_bright_blue": "bright_blue",
    "ansi_bright_magenta": "bright_magenta",
    "ansi_bright_cyan": "bright_cyan",
    "ansi_bright_white": "bright_white",
}

MARQUEE_ENDPOINT_PAUSE_TICKS = 6


def resolve_rich_style(style: str, variables: dict | None = None) -> str:
    """Resolve Textual theme names into styles accepted by Rich.

    Textual's built-in themes may expose a semantic color as ``ansi_green``;
    Rich accepts ``green`` but rejects the Textual-prefixed spelling. This
    helper is also used by widgets that build ``Text.from_markup`` directly,
    bypassing MarqueeBar's normal markup resolver.
    """
    variables = variables or {}

    def resolve(match: re.Match[str]) -> str:
        value = variables.get(match.group(1))
        if value is None:
            return match.group(0)
        value = str(value)
        if value.startswith("auto"):
            value = str(variables.get("foreground", "#ffffff"))
        folded = value.casefold()
        return _RICH_ANSI_COLORS.get(
            folded,
            "#ffffff" if folded == "ansi_default" else value,
        )

    resolved = re.sub(r"\$([A-Za-z0-9_-]+)", resolve, style)
    return re.sub(
        r"(?<![A-Za-z0-9_-])(ansi_(?:bright_)?(?:black|red|green|yellow|blue|magenta|cyan|white)|ansi_default)(?![A-Za-z0-9_-])",
        lambda match: _RICH_ANSI_COLORS.get(
            match.group(1).casefold(), "#ffffff"),
        resolved,
    )


def _marquee_target(total: int, width: int, offset: int) -> int:
    """Return the cell offset for a ping-pong marquee phase."""
    travel = total - width
    pause = MARQUEE_ENDPOINT_PAUSE_TICKS
    cycle = (pause + travel) * 2
    phase = offset % cycle
    if phase < pause:
        return 0
    if phase < pause + travel:
        return phase - pause
    if phase < pause + travel + pause:
        return travel
    return travel - (phase - pause - travel - pause)


def marquee_window(text: str, width: int, offset: int = 0) -> str:
    """Return a cell-width-safe ping-pong window into ``text``.

    ``offset`` is the number of timer ticks. The window pauses at both ends
    before moving back, so the visible text never wraps from the tail to the
    head.
    """
    if width <= 0:
        return ""
    total = cell_len(text)
    if total <= width:
        return text

    target = _marquee_target(total, width, offset)

    chars = list(text)
    consumed = 0
    start = 0
    for index, char in enumerate(chars):
        char_width = max(cell_len(char), 1)
        if consumed + char_width > target:
            start = index
            break
        consumed += char_width
    window = Text("".join(chars[start:]))
    window.truncate(width, overflow="crop")
    return str(window)


def marquee_text(text: Text, width: int, offset: int = 0) -> Text:
    """Return a cell-width-safe marquee window while preserving Rich spans."""
    if width <= 0:
        return Text()
    total = text.cell_len
    if total <= width:
        return text.copy()

    target = _marquee_target(total, width, offset)
    consumed = 0
    start = 0
    for index, char in enumerate(text.plain):
        char_width = max(cell_len(char), 1)
        if consumed + char_width > target:
            start = index
            break
        consumed += char_width
    window = text[start:]
    window.truncate(width, overflow="crop")
    return window


def ellipsis_window(text: str, width: int) -> str:
    """Return a cell-width-safe static window for an unfocused value."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    ellipsis = "…"
    available = max(width - cell_len(ellipsis), 0)
    prefix: list[str] = []
    consumed = 0
    for char in text:
        char_width = max(cell_len(char), 1)
        if consumed + char_width > available:
            break
        prefix.append(char)
        consumed += char_width
    return "".join(prefix) + ellipsis


class MarqueeBar(Static):
    # Banners and transient status readouts are controls/status, not copyable
    # metadata. DetailPane opts its own title/summary bars back in explicitly.
    ALLOW_SELECT = False

    content: reactive[str | None] = reactive(None, recompose=False)

    def __init__(self, content: str | None = None, *, id: str | None = None,
                 style: str | None = None, markup: bool = False) -> None:
        """``style`` is a Rich markup tag body (e.g. "b $primary") applied to
        the whole banner, both when it fits and while it scrolls; the default
        keeps the historical dim-fit / bold-scroll look. ``markup=True`` keeps
        Rich spans in ``content`` while scrolling a single-line summary."""
        super().__init__(id=id)
        self._style = style
        self._markup = markup
        self._offset = 0
        self._timer = None
        self.content = content

    def watch_content(self, value: str | None) -> None:
        self._offset = 0
        self._maybe_animate()

    def set_markup(self, markup: bool) -> None:
        """Switch the content parser before updating a reused title bar."""
        if self._markup == markup:
            return
        self._markup = markup
        self._offset = 0
        self._maybe_animate()
        self.refresh()

    def on_resize(self) -> None:
        self._maybe_animate()

    def _maybe_animate(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None
        text = self._content_text()
        if text.cell_len > max(self.size.width, 0):
            self._timer = self.set_interval(0.12, self._tick)
        self.refresh()

    def _content_text(self) -> Text:
        text = self.content or ""
        if not self._markup:
            return Text(text)
        text = self._resolve_theme_markup(text)
        try:
            return Text.from_markup(text)
        except Exception:
            # A late error/status may contain arbitrary brackets. Keep the
            # control usable and show the literal value rather than treating
            # it as a Rich tag.
            return Text(text)

    def _resolve_theme_markup(self, text: str) -> str:
        """Resolve Textual ``$theme`` variables before Rich parses markup."""
        return re.sub(
            r"(?<!\\)\[([^\]]*)\]",
            lambda match: f"[{self._resolve_style(match.group(1))}]",
            text,
        )

    def _resolve_style(self, style: str) -> str:
        """Resolve theme variables in one Rich style tag."""
        try:
            variables = self.app.theme_variables
        except Exception:
            variables = {}

        return resolve_rich_style(style, variables)

    def update(self, content: str | None = None, *args, **kwargs):
        """Keep Static.update callers compatible with the reactive content."""
        if args or kwargs:
            return super().update(content, *args, **kwargs)
        self.content = content
        return self

    def _tick(self) -> None:
        self._offset += 1
        self.refresh()

    def render(self) -> str | Text:
        text = self.content or ""
        w = self.size.width
        if not text or w <= 0:
            return ""
        if self._markup:
            rich_text = self._content_text()
            if rich_text.cell_len <= w:
                if self._style:
                    rich_text.stylize(self._resolve_style(self._style))
                return rich_text
            window = marquee_text(rich_text, w, self._offset)
            if self._style:
                window.stylize(self._resolve_style(self._style))
            return window
        if cell_len(text) <= w:
            tag = self._style or "dim"
            return f"[{tag}]{_escape(text)}[/]"
        window = marquee_window(text, w, self._offset)
        tag = self._style or "b"
        return f"[{tag}]{_escape(window)}[/]"


def _escape(text: str) -> str:
    """Escape every '[' so TONE3000 titles can't leak into Rich markup."""
    return text.replace("[", "\\[")
