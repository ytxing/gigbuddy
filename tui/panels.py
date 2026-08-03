"""GigBuddy TUI panels: chain (focusable nodes) / tone detail pane / level meter."""
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Select, Static

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402

from . import live  # noqa: E402
from .marquee import MarqueeBar  # noqa: E402
from .metadata import metadata_table  # noqa: E402


def _escape(text: str) -> str:
    """Escape EVERY '[' for Textual markup.

    rich.markup.escape only escapes brackets that look like style tags, so a
    TONE3000 title like "VOX AC30 CH [Hyper Accuracy+]" — or one clipped
    mid-bracket — leaks through and swallows the following closing tag.
    """
    return text.replace("[", "\\[")


class NodeWidget(Static):
    """Chain node (AMP / IR): click to focus, then ↑/↓ steps through the sibling
    models in the same tone folder (engine hot-swaps via live_chain.json).

    Two-line pedal readout: tone title on top, the exact model file below.
    """

    can_focus = True

    class SwitchRequested(Message):
        def __init__(self, kind: str, direction: int) -> None:
            super().__init__()
            self.kind = kind  # "amp" | "ir"
            self.direction = direction  # +1 next / -1 prev

    BINDINGS = [
        Binding("up", "switch_prev", "prev model", show=False),
        Binding("down", "switch_next", "next model", show=False),
    ]

    def __init__(self, kind: str, label: str = "—") -> None:
        self.kind = kind.lower()
        self.muted = False
        self.title: str | None = None
        super().__init__(classes=f"chain-node chain-node-{self.kind}")
        self.label = label

    def render(self) -> str:
        limit = max(12, (self.size.width or 24) - 8)

        def clip(text: str) -> str:
            return text if len(text) <= limit else text[:limit - 1] + "…"

        label = self.label or "—"
        active = label not in {"—", "bypass"}
        state = "[bold $error]◼[/]" if self.muted else (
            "[bold $success]●[/]" if active else "[dim]○[/]")
        mute_tag = " [dim $error]MUTED[/]" if self.muted else ""
        head = f"[b $text-muted]{self.kind.upper():<5}[/]{state} "
        # Escape user-provided names so brackets in a TONE3000 title/filename
        # cannot be interpreted as Rich markup.
        if self.title:
            return (
                f"{head}[b]{_escape(clip(self.title))}[/]{mute_tag}\n"
                f"       [dim]{_escape(clip(label))}[/]"
            )
        value_style = "bold" if active else "dim"
        return f"{head}[{value_style}]{_escape(clip(label))}[/]{mute_tag}"

    def set_label(self, label: str) -> None:
        self.label = label
        self.refresh()

    def set_title(self, title: str | None) -> None:
        self.title = title
        self.refresh()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.refresh()

    def action_switch_next(self) -> None:
        self.post_message(self.SwitchRequested(self.kind, +1))

    def action_switch_prev(self) -> None:
        self.post_message(self.SwitchRequested(self.kind, -1))


class NodeSwitchButton(Static):
    """Wide flat click target beside a chain node for stepping sibling models.

    ▲ steps to the previous model in the tone folder, ▼ to the next. The pair
    sits in a .chain-switch-col next to the pedal-style node, vertically
    centered; clicks are routed by App.on_click via the widget id.
    """

    def __init__(self, kind: str, direction: int) -> None:
        self.kind = kind.lower()
        self.direction = direction
        arrow = "▲" if direction > 0 else "▼"
        arrow_name = "up" if direction > 0 else "down"
        super().__init__(f"[bold]{arrow}[/]",
                         id=f"chain-{self.kind}-{arrow_name}",
                         classes="chain-switch-btn")


class ChainPanel(Vertical):
    """Read-only view of the live tone chain."""

    chain: reactive[dict] = reactive({}, recompose=False)

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "TONE CHAIN"
        self.border_subtitle = "LIVE ROUTE · MONITOR"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="chain-node-row chain-node-row-amp"):
            self.amp = NodeWidget("AMP")
            yield self.amp
            with Horizontal(classes="chain-switch-col"):
                yield NodeSwitchButton("amp", +1)  # ▲ prev
                yield NodeSwitchButton("amp", -1)  # ▼ next
        yield Static("┊", classes="chain-connector")
        with Horizontal(classes="chain-node-row chain-node-row-ir"):
            self.ir = NodeWidget("IR", "bypass")
            yield self.ir
            with Horizontal(classes="chain-switch-col"):
                yield NodeSwitchButton("ir", +1)
                yield NodeSwitchButton("ir", -1)
        yield Static("┊", classes="chain-connector")
        for _key, label, hint in live.CHAIN_ORDER[2:]:
            yield Static(
                f" [dim]○[/] [bold]{label:6s}[/] [dim]{hint}[/]",
                classes="chain-effect")
        self.params = Static("", classes="chain-params")
        yield self.params

    def watch_chain(self, chain: dict) -> None:
        self._set_node(self.amp, chain.get("model"), empty="—")
        self._set_node(self.ir, chain.get("ir"), empty="bypass")
        gain = float(chain.get("gain", 1.0))
        master = float(chain.get("master", 1.0))
        quality = float(chain.get("quality", 1.0))
        self.amp.set_muted(gain <= 0)  # double-click mute shows on the node row
        self.params.update(
            f"[b]GAIN[/]  [b $accent]{gain:.2f}[/] [dim]g/G[/]   "
            f"[b]MASTER[/]  [b $accent]{master:.2f}[/] [dim]m/M[/]   "
            f"[b]QUALITY[/]  [b $accent]{quality:.2f}[/] [dim]u/U[/]")

    def _set_node(self, node: NodeWidget, path: str | None, *, empty: str) -> None:
        """Node readout: tone title (line 1) + model filename (line 2).

        External files (not in the library DB) fall back to the filename only.
        Titles are cached by path — the 0.3s tick must not hit SQLite twice
        per refresh for an unchanged chain.
        """
        if not path:
            node.set_title(None)
            node.set_label(empty)
            return
        if not hasattr(self, "_title_cache"):
            self._title_cache: dict[str, str | None] = {}
        if path not in self._title_cache:
            self._title_cache[path] = library.tone_title_for_path(path)
        node.set_title(self._title_cache[path])
        node.set_label(live.short_name(path))


class DetailPane(VerticalScroll):
    """Full metadata of the tone selected in the library (from the SQLite DB)."""

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "TONE DETAIL"
        self._body: Static = Static(
            "[dim]Select a tone in the library to see its full metadata here.[/dim]")

    def compose(self) -> ComposeResult:
        self._marquee = MarqueeBar(id="detail-marquee")
        yield self._marquee
        yield self._body

    def show(self, t: dict) -> None:
        """Render a library.get_tone() row as a semantic metadata table."""
        self._marquee.content = t.get("title")
        self._body.update(metadata_table(t))

    def show_model(self, tone: dict | None, model: dict) -> None:
        """Render a chain node's current model: FILE section (name/id/arch/path)
        on top of its owning tone, matching the chain-folder stepping view."""
        self._marquee.content = (tone or {}).get("title")
        self._body.update(metadata_table(tone, model))

    def show_text(self, text: str) -> None:
        """Render plain (rich-markup) text, e.g. a preset chain summary."""
        self._marquee.content = None
        self._body.update(text)

    def clear(self) -> None:
        """Clear stale metadata when the table has no highlighted tone."""
        self._marquee.content = None
        self._body.update(
            "[dim]Move the library cursor onto a tone to see its metadata here.[/dim]")


class MeterBar(Static):
    """Bottom level meter: color-graded (green/yellow/red) + peak-hold marker.

    Refresh 0.3s from the engine level file; peaks hold ~1s then follow.
    """

    levels: reactive[tuple[float, float]] = reactive((0.0, 0.0), recompose=False)
    HOLD_S = 1.0

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.border_title = "LEVEL"
        self._pk_in = self._pk_out = 0.0
        self._pk_in_at = self._pk_out_at = 0.0

    def watch_levels(self, levels: tuple[float, float]) -> None:
        import time

        now = time.monotonic()
        if levels[0] > self._pk_in:
            self._pk_in, self._pk_in_at = levels[0], now
        if levels[1] > self._pk_out:
            self._pk_out, self._pk_out_at = levels[1], now
        self.refresh()

    @staticmethod
    def _db(v: float) -> float:
        import math

        return -99.0 if v < 1e-5 else max(-60.0, 20.0 * math.log10(v))

    @classmethod
    def _bar(cls, v: float, pk: float, w: int = 24) -> str:
        """Color-graded bar with a peak-hold marker (▍). -60..-24 green, -24..-12
        yellow, -12..0 red; dim fill below level."""
        import math

        n = int(max(0.0, min(1.0, (cls._db(v) + 60.0) / 60.0)) * w)
        pp = int(max(0.0, min(1.0, (cls._db(pk) + 60.0) / 60.0)) * w)
        s = "█" * n + "░" * (w - n)
        if pk >= 1e-5:
            s = s[: min(pp, w - 1)] + "▍" + s[min(pp, w - 1) + 1:]
        out = []
        for i, ch in enumerate(s):
            if ch == "█":
                d = -60.0 + (i + 0.5) / w * 60.0
                color = "$success" if d < -24 else ("$warning" if d < -12 else "$error")
                out.append(f"[{color}]{ch}[/]")
            elif ch == "▍":
                out.append("[bold $text]▍[/]")
            else:
                out.append("[dim]░[/]")
        return "".join(out)

    def render(self) -> str:
        import time

        now = time.monotonic()
        inl, outl = self.levels
        # decay peaks after HOLD_S without new signal
        if now - self._pk_in_at > self.HOLD_S:
            self._pk_in = inl
        if now - self._pk_out_at > self.HOLD_S:
            self._pk_out = outl
        return (
            f"[b]IN[/]  {self._bar(inl, self._pk_in)}  {self._db(inl):5.1f} dBFS"
            f"   [dim]│[/]   [b]OUT[/]  {self._bar(outl, self._pk_out)}  "
            f"{self._db(outl):5.1f} dBFS"
        )


class DeviceChanged(Message):
    """The user picked a different audio interface — app restarts the engine."""

    def __init__(self, kind: str, name: str) -> None:
        super().__init__()
        self.kind = kind  # "in" | "out"
        self.name = name


BUFFER_CHOICES = [128, 256, 512, 1024]
SR_HZ = 48000  # engine default sample rate (TUI spawns without --sr)


class DeviceBar(Horizontal):
    """Bottom audio-interface pickers (IN/OUT) + engine buffer (with its latency).

    Device/buffer changes are forwarded to the app, which restarts the engine.
    """

    def compose(self) -> ComposeResult:
        yield Static("IN ", classes="device-label")
        yield Select([("(none)", "")], value="", allow_blank=False, compact=True,
                     id="dev-in", disabled=True)
        yield Static("OUT ", classes="device-label")
        yield Select([("(none)", "")], value="", allow_blank=False, compact=True,
                     id="dev-out", disabled=True)
        yield Static("BUFFER ", classes="device-label")
        yield Select(self._buffer_options(256), value=256, allow_blank=False,
                     compact=True, id="dev-buffer")
        self.latency = Static("", classes="device-latency")
        yield self.latency
        self.mute = Static("[MUTE]", classes="device-mute", id="dev-mute")
        yield self.mute

    def set_muted(self, muted: bool) -> None:
        self.mute.set_classes("device-mute muted" if muted else "device-mute")
        self.mute.update("[MUTED]" if muted else "[MUTE]")

    def on_click(self, event) -> None:
        if getattr(event, "widget", None) is self.mute or (
                event.screen_x is not None and self.mute.region.contains(
                    event.screen_x, event.screen_y)):
            event.stop()
            self.post_message(DeviceChanged("mute", ""))

    @staticmethod
    def _buffer_options(block: int) -> list[tuple[str, int]]:
        out = []
        for b in BUFFER_CHOICES:
            ms = b * 1000.0 / SR_HZ
            tag = " ◀" if b == block else ""
            out.append((f"{b}·{ms:.1f}ms{tag}", b))
        return out

    def _refresh_latency(self) -> None:
        try:
            block = int(self.query_one("#dev-buffer", Select).value)
        except (TypeError, ValueError):
            block = 256
        ms = block * 1000.0 / SR_HZ
        self.latency.update(f"[dim]block {block} ≈ {ms:.1f} ms @ {SR_HZ} Hz[/dim]")

    def set_devices(self, ins: list[str], outs: list[str],
                    cur_in: str = "", cur_out: str = "") -> None:
        """Fill the pickers from the engine's device list; keep the current pick."""

        def fill(sel_id: str, names: list[str], cur: str) -> None:
            sel = self.query_one(f"#{sel_id}", Select)
            options = [(n, n) for n in names] or [("(none)", "")]
            value = cur if cur in names else (names[0] if names else "")
            sel.set_options(options)
            sel.value = value
            sel.disabled = False
            self._last[sel_id.removeprefix("dev-")] = value

        fill("dev-in", ins, cur_in)
        fill("dev-out", outs, cur_out)

    def __init__(self) -> None:
        super().__init__()
        self._last = {"in": "", "out": "", "buffer": 256}

    def on_mount(self) -> None:
        self._refresh_latency()

    def on_select_changed(self, event: Select.Changed) -> None:
        if not event.value:
            return
        if event.select.id in ("dev-in", "dev-out", "dev-buffer"):
            kind = {"dev-in": "in", "dev-out": "out", "dev-buffer": "buffer"}[event.select.id]
            val = str(event.value)
            if val == self._last[kind]:
                return  # programmatic fill (set_devices), not a user change
            self._last[kind] = val
            self.post_message(DeviceChanged(kind, val))
            if event.select.id == "dev-buffer":
                self._refresh_latency()
