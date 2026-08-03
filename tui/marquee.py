"""Banner-style scrolling title (marquee) for long text that overflows its row.

Textual ships no marquee widget in 8.2, so this is a minimal one: while the
content is wider than the widget, a 0.12s timer advances an offset and the
render shows a wrap-around window. When the text fits, it scrolls nothing.
"""
from textual.reactive import reactive
from textual.widgets import Static


class MarqueeBar(Static):
    content: reactive[str | None] = reactive(None, recompose=False)

    def __init__(self, content: str | None = None, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._offset = 0
        self._timer = None
        self.content = content

    def watch_content(self, value: str | None) -> None:
        self._offset = 0
        self._maybe_animate()

    def on_resize(self) -> None:
        self._maybe_animate()

    def _maybe_animate(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None
        text = self.content or ""
        if len(text) > max(self.size.width, 0):
            self._timer = self.set_interval(0.12, self._tick)
        self.refresh()

    def _tick(self) -> None:
        self._offset += 1
        self.refresh()

    def render(self) -> str:
        text = self.content or ""
        w = self.size.width
        if not text or w <= 0:
            return ""
        if len(text) <= w:
            return f"[dim]{text}[/]"
        o = self._offset % len(text)
        return f"[b]{_escape((text + text)[o:o + w])}[/]"


def _escape(text: str) -> str:
    """Escape every '[' so TONE3000 titles can't leak into Rich markup."""
    return text.replace("[", "\\[")
