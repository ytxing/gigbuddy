"""Shared semantic metadata tables for the main detail pane and model picker."""
from io import StringIO
import re
import sys
from pathlib import Path
from urllib.parse import quote

from rich import box
from rich.console import Console, Group
from rich.markup import escape as rich_escape
from rich.table import Table
from rich.text import Text
from textual.content import Content
from textual.selection import Selection
from textual.visual import RenderOptions, RichVisual, Visual
from textual.widgets import Static

from .marquee import resolve_rich_style

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import tone3000  # noqa: E402

# Fallback palette for direct/meta rendering outside the app (and for
# theme-less callers). Inside the TUI, callers pass theme_colors(app) so every
# color follows the active theme. "field" is the warm beige of field names;
# "value" is the fixed success color and "warn" follows the active theme.
DEFAULT_COLORS = {
    "header": "#e59a3c",
    "section": "#f5b042",
    "field": "#d3bf9e",
    "value": "#8fb573",
    "warn": "#e0b34a",
}


def signed_fixed(value, digits: int = 2, fallback: str = "?") -> str:
    """Format a number with a reserved sign column for stable UI geometry."""
    try:
        return f"{float(value): .{digits}f}"
    except (TypeError, ValueError):
        return fallback


class _SelectableTableVisual(Visual):
    """Render a Rich table through Textual Content so selections are styled.

    Textual's ``RichVisual`` renders Rich segments directly, but that path does
    not consume ``RenderOptions.selection``. Convert the already-laid-out Rich
    segments to a Textual ``Content`` object, whose renderer applies the
    standard screen selection style.
    """

    def __init__(self, widget: Static, table: Table) -> None:
        self._widget = widget
        self._table = table
        self._dimensions = RichVisual(widget, table)

    def get_optimal_width(self, rules, container_width: int) -> int:
        return self._dimensions.get_optimal_width(rules, container_width)

    def get_minimal_width(self, rules) -> int:
        return self._dimensions.get_minimal_width(rules)

    def get_height(self, rules, width: int) -> int:
        return self._dimensions.get_height(rules, width)

    def render_strips(
        self, width: int, height: int | None, style, options: RenderOptions
    ):
        app = self._widget.app
        console_options = app.console_options.update(
            highlight=False,
            width=width,
            height=height,
        )
        renderable = self._widget.post_render(self._table, style.rich_style)
        segments = app.console.render(
            renderable, console_options.update_width(width)
        )

        text = Text()
        for segment in segments:
            if not segment.control:
                text.append(segment.text, style=segment.style)

        content = Content.from_rich_text(text, console=app.console)
        return content.render_strips(width, height, style, options)


class SelectableStatic(Static):
    """Static content that also supports copying and highlighting Rich tables.

    Textual's default selection extractor handles ``Text`` and ``Content``
    directly, but returns no text for a Rich ``Table``. Metadata panes use
    tables for alignment, so render the same table through Textual ``Content``
    while retaining a plain-text fallback for copying.
    """

    ALLOW_SELECT = True

    def render(self):
        if isinstance(self.content, Table):
            return _SelectableTableVisual(self, self.content)
        return super().render()

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        if isinstance(self.content, Table):
            renderable = self.content
        else:
            visual = self._render()
            renderable = (visual._renderable
                          if isinstance(visual, RichVisual) else visual)
        if isinstance(renderable, (Text, Content)):
            text = str(renderable)
        else:
            width = max(self.size.width, 1)
            output = StringIO()
            Console(
                file=output,
                width=width,
                color_system=None,
                force_terminal=False,
                no_color=True,
            ).print(renderable, end="")
            text = output.getvalue()
        return selection.extract(text), "\n"


def theme_colors(app) -> dict[str, str]:
    """Resolve the metadata palette from the active theme's CSS variables.

    Derived variables like ``text-muted`` resolve to "auto 60%" style strings
    (not hex), which Rich cannot use — so field falls back to the foreground.
    """
    v = app.theme_variables or app.get_css_variables()

    def rich_color(value, fallback: str) -> str:
        resolved = resolve_rich_style(str(value or fallback), v)
        if resolved.casefold().startswith("auto"):
            resolved = resolve_rich_style(
                str(v.get("foreground") or fallback), v)
        return resolved

    return {
        "header": rich_color(v.get("primary"), DEFAULT_COLORS["header"]),
        "section": rich_color(v.get("accent"), DEFAULT_COLORS["section"]),
        "field": rich_color(
            v.get("field") or v.get("foreground"), DEFAULT_COLORS["field"]),
        "value": rich_color(v.get("success"), DEFAULT_COLORS["value"]),
        "warn": rich_color(v.get("warning"), DEFAULT_COLORS["warn"]),
    }


def _value(value, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or fallback
    return str(value)


def _gear_value(value) -> str:
    """Display the native gear token without changing its identity."""
    return _value(value, "SLOT").upper()


def _link_markup(href: str, label: str) -> str:
    """Build a link while keeping external text out of Rich tag syntax."""
    encoded_href = quote(str(href), safe=":/?&=#-_.@%")
    return f"[link={encoded_href}]{rich_escape(str(label))}[/]"


def _compact_path(value: str | None, limit: int = 76) -> str:
    """Keep the useful tail of a path without letting it dominate the table."""
    if not value or len(value) <= limit:
        return _value(value)
    return "…/" + value[-(limit - 2):]


def tone3000_url(tone: dict | None) -> str | None:
    """Return the public TONE3000 page for a tone row.

    Search responses do not expose the web slug, but the public route is
    deterministic: ``/tones/<title-slug>-<tone-id>``.  Keep this helper local
    to the TUI so metadata rendering does not turn into a network request.
    """
    tone = tone or {}
    tone_id = tone.get("tone_id", tone.get("id"))
    if tone_id is None:
        return None
    title = str(tone.get("title") or "tone")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "tone"
    return f"https://www.tone3000.com/tones/{slug}-{tone_id}"


def metadata_copy_text(tone: dict | None = None, model: dict | None = None,
                       *, note: str | None = None) -> str:
    """Build a stable, plain-text snapshot for the global Copy action."""
    tone = tone or {}
    lines: list[str] = []
    if tone:
        lines.append(f"Tone: {_value(tone.get('title'))}")
        lines.append(f"Tone ID: {_value(tone.get('tone_id', tone.get('id')))}")
        if tone3000_url(tone):
            lines.append(f"TONE3000: {tone3000_url(tone)}")
        lines.append(f"Author: @{_value(tone.get('username'), '?').lstrip('@')}")
        lines.append(f"Type: {_gear_value(tone.get('gear'))}")
        if tone.get("tags"):
            lines.append(f"Tags: {_value(tone.get('tags'))}")
        if tone.get("description"):
            lines.append(f"Description: {_value(tone.get('description'))}")
    if model:
        local_path = model.get("local_path")
        lines.extend([
            f"Model ID: {_value(model.get('id'))}",
            f"Model filename: {_value(Path(local_path).name if local_path else model.get('name'))}",
            f"Architecture: {_value(model.get('architecture'))}",
        ])
        if model.get("model_url"):
            lines.append(f"Model source: {model['model_url']}")
        if local_path:
            lines.append(f"Local path: {local_path}")
    if note:
        lines.append(f"Status: {note}")
    return "\n".join(lines)


def metadata_table(tone: dict | None = None, model: dict | None = None,
                   *, note: str | None = None,
                   skip_title: bool = False,
                   condensed: bool = False,
                   colors: dict[str, str] | None = None):
    """Build one consistent, non-focusable metadata table for TUI detail panes.

    skip_title: the DetailPane renders the tone title as its own frozen header,
    so the table omits the duplicate "Tone" row in that case.
    condensed: DetailPane also renders the author/gear/counts line above the
    table, so the table omits those duplicates (Author/Type/Popularity/Total).
    The description is not a table row at all — it is laid out below the table
    at full width, since descriptions are long prose rather than field/value
    pairs. Returns the Table alone, or a Group when prose is present.
    colors: palette resolved from the active theme (theme_colors); falls back
    to DEFAULT_COLORS outside the app.
    """
    tone = tone or {}
    colors = {**DEFAULT_COLORS, **(colors or {})}
    table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
        pad_edge=False,
        padding=(0, 1),
        show_header=False,
        header_style=f"bold {colors['header']}",
    )
    table.add_column("", style="bold", width=18, no_wrap=True)
    table.add_column("", ratio=1, overflow="fold")

    last_section = None

    def row(section: str, field: str, value, *, style: str | None = None,
            markup: bool = False) -> None:
        nonlocal last_section
        if section != last_section:
            if last_section is not None:
                table.add_section()
            table.add_row(Text(section, style=f"bold {colors['section']}"), "")
            last_section = section
        rendered = (Text.from_markup(_value(value), style=style)
                    if markup else Text(_value(value), style=style))
        table.add_row(Text(field, style=f"bold {colors['field']}"),
                      rendered)

    def model_rows() -> None:
        local_path = model.get("local_path")
        row("FILE", "Filename", Path(local_path).name if local_path else None,
            style=f"bold {colors['value']}")
        row("FILE", "Model ID", model.get("id"))
        row("FILE", "Architecture", model.get("architecture"))
        if model.get("model_url"):
            row("SOURCE", "Model source",
                _link_markup(model["model_url"], "Open model source"),
                markup=True)
        row("FILE", "Local path", _compact_path(local_path))

    # The picker is about the focused file, so put its fields first.
    if model:
        model_rows()

    if tone:
        tone_id = tone.get("tone_id", tone.get("id"))
        if not skip_title:
            row("IDENTITY", "Tone", tone.get("title"))
        row("IDENTITY", "Tone ID", tone_id)
        if not condensed:
            author = tone.get("username")
            if author:
                badge = (f" [b {colors['value']}]✓[/]"
                         if tone3000.is_verified(author) else "")
                row("IDENTITY", "Author",
                    _link_markup(f"search:author:{author}", f"@{author}") + badge,
                    markup=True)
            else:
                row("IDENTITY", "Author", "?")
            row("IDENTITY", "Type", _gear_value(tone.get("gear")))

        url = tone3000_url(tone)
        if url:
            row("SOURCE", "TONE3000",
                _link_markup(url, "Open tone page"), markup=True)

        if tone.get("tags") or tone.get("makes"):
            tags = tone.get("tags") or []
            tag_links = ", ".join(
                _link_markup(f"search:tag:{t}", str(t)) for t in tags)
            row("CLASSIFICATION", "Tags", tag_links or "", markup=True)
            row("CLASSIFICATION", "Makes", tone.get("makes"))

        if not condensed and any(
                tone.get(key) is not None
                for key in ("downloads_count", "favorites_count")):
            row("POPULARITY", "Downloads", tone.get("downloads_count", 0))
            row("POPULARITY", "Favorites", tone.get("favorites_count", 0))

        if any(key in tone for key in (
                "a1_models_count", "a2_models_count", "custom_models_count",
                "irs_count", "models_count", "model_name")):
            row("MODEL SET", "A1 / A2",
                f"{tone.get('a1_models_count') or 0} / {tone.get('a2_models_count') or 0}")
            row("MODEL SET", "Custom / IR",
                f"{tone.get('custom_models_count') or 0} / {tone.get('irs_count') or 0}")
            if not condensed:
                row("MODEL SET", "Total", tone.get("models_count", 0))
            if tone.get("model_name"):
                row("MODEL SET", "Default name", tone.get("model_name"))
        if not model and tone.get("models") is not None:
            row("MODEL SET", "Downloaded", len(tone.get("models") or []))
            row("MODEL SET", "Local folder", _compact_path(tone.get("local_dir")))

    if note:
        row("NOTES", "Status", note)
    if tone.get("description"):
        # Prose goes full-width below the table instead of a folded table cell,
        # under the same section-header grammar as the rows above it.
        return Group(table, Text(""),
                     Text("DESCRIPTION", style=f"bold {colors['section']}"),
                     Text(""),
                     Text(_value(tone["description"])))
    return table


def description_only(tone: dict | None = None, model: dict | None = None,
                     *, colors: dict[str, str] | None = None):
    """Render only a tone/model description for compact picker details."""
    tone = tone or {}
    model = model or {}
    colors = {**DEFAULT_COLORS, **(colors or {})}
    description = tone.get("description") or model.get("description")
    return Group(
        Text("DESCRIPTION", style=f"bold {colors['section']}"),
        Text(""),
        Text(_value(description, "No description available.")),
    )


def preset_metadata_table(preset: dict, resolved: dict, *, active: bool = False,
                          dirty: bool = False,
                          colors: dict[str, str] | None = None) -> Table:
    """Render a preset with the same visual grammar as tone metadata.

    Presets are chain snapshots rather than tones, so the sections emphasize
    identity, ordered Slots, and the three live controls. Resolved paths are
    reduced to filenames here; the full path remains available through the
    CLI and the copied metadata view.
    colors: palette resolved from the active theme (theme_colors).
    """
    preset = preset or {}
    chain = preset.get("chain") or {}
    resolved = resolved or {}
    colors = {**DEFAULT_COLORS, **(colors or {})}
    table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
        pad_edge=False,
        padding=(0, 1),
        show_header=False,
    )
    table.add_column("", style="bold", width=18, no_wrap=True)
    table.add_column("", ratio=1, overflow="fold")

    last_section = None

    def row(section: str, field: str, value, *, style: str | None = None) -> None:
        nonlocal last_section
        if section != last_section:
            if last_section is not None:
                table.add_section()
            table.add_row(Text(section, style=f"bold {colors['section']}"), "")
            last_section = section
        table.add_row(Text(field, style=f"bold {colors['field']}"),
                      Text(_value(value), style=style))

    def control(key: str) -> str:
        value = resolved.get(key, chain.get(key, 1.0))
        return signed_fixed(value, fallback=_value(value))

    state = "ACTIVE" if active else "SAVED"
    if dirty:
        state += " · DIRTY"
    row("PRESET", "Name", preset.get("name"), style=f"bold {colors['section']}")
    row("PRESET", "Status", state,
        style=f"bold {colors['warn']}" if dirty else f"bold {colors['value']}")
    if preset.get("note"):
        row("PRESET", "Note", preset["note"])
    updated = (preset.get("updated_at") or "").replace("T", " ")[:19]
    if updated:
        row("PRESET", "Updated", updated)

    slots = chain.get("slots") if isinstance(chain.get("slots"), list) else []
    resolved_slots = (resolved.get("slots")
                      if isinstance(resolved.get("slots"), list) else [])
    if not slots:
        row("SLOTS", "01–06", "NONE", style=f"bold {colors['value']}")
    else:
        for index, slot in enumerate(slots):
            slot = slot if isinstance(slot, dict) else {}
            resolved_slot = (resolved_slots[index]
                             if index < len(resolved_slots)
                             and isinstance(resolved_slots[index], dict) else {})
            path = resolved_slot.get("path") or slot.get("path")
            model_id = slot.get("model_id")
            if path:
                value = f"#{model_id} {Path(str(path)).name}" \
                    if model_id is not None else Path(str(path)).name
            else:
                value = "NONE"
            row("SLOTS", f"{index + 1:02d}", value,
                style=f"bold {colors['value']}")

    row("CONTROLS", "gain", control("gain"), style=f"bold {colors['section']}")
    row("CONTROLS", "master", control("master"), style=f"bold {colors['section']}")
    row("CONTROLS", "quality", control("quality"), style=f"bold {colors['section']}")
    return table
