"""Shared semantic metadata tables for the main detail pane and model picker."""
from pathlib import Path

from rich import box
from rich.table import Table
from rich.text import Text


def _value(value, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or fallback
    return str(value)


def _compact_path(value: str | None, limit: int = 76) -> str:
    """Keep the useful tail of a path without letting it dominate the table."""
    if not value or len(value) <= limit:
        return _value(value)
    return "…/" + value[-(limit - 2):]


def metadata_table(tone: dict | None = None, model: dict | None = None,
                   *, note: str | None = None) -> Table:
    """Build one consistent, non-focusable metadata table for TUI detail panes."""
    tone = tone or {}
    table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
        pad_edge=False,
        header_style="bold #e59a3c",
    )
    table.add_column("Field", style="bold", width=18, no_wrap=True)
    table.add_column("Value", ratio=1, overflow="fold")

    last_section = None

    def row(section: str, field: str, value, *, style: str | None = None) -> None:
        nonlocal last_section
        if section != last_section:
            if last_section is not None:
                table.add_section()
            table.add_row(Text(section, style="bold #f5b042"), "")
            last_section = section
        table.add_row(field, Text(_value(value), style=style))

    def model_rows() -> None:
        local_path = model.get("local_path")
        row("FILE", "Filename", Path(local_path).name if local_path else None,
            style="bold #8fb573")
        row("FILE", "Model ID", model.get("id"))
        row("FILE", "Architecture", model.get("architecture"))
        row("FILE", "Local path", _compact_path(local_path))

    # The picker is about the focused file, so put its fields first.
    if model:
        model_rows()

    if tone:
        tone_id = tone.get("tone_id", tone.get("id"))
        row("IDENTITY", "Tone", tone.get("title"))
        row("IDENTITY", "Tone ID", tone_id)
        author = tone.get("username")
        if author:
            row("IDENTITY", "Author",
                f"[link=search:author:{author}]@{author}[/]")
        else:
            row("IDENTITY", "Author", "?")
        row("IDENTITY", "Type", tone.get("gear"))

        if tone.get("tags") or tone.get("makes"):
            tags = tone.get("tags") or []
            tag_links = ", ".join(
                f"[link=search:tag:{t}]{t}[/]" for t in tags)
            row("CLASSIFICATION", "Tags", tag_links or "")
            row("CLASSIFICATION", "Makes", tone.get("makes"))

        if any(tone.get(key) is not None for key in ("downloads_count", "favorites_count")):
            row("POPULARITY", "Downloads", tone.get("downloads_count", 0))
            row("POPULARITY", "Favorites", tone.get("favorites_count", 0))

        if any(key in tone for key in (
                "a1_models_count", "a2_models_count", "custom_models_count",
                "irs_count", "models_count", "model_name")):
            row("MODEL SET", "A1 / A2",
                f"{tone.get('a1_models_count') or 0} / {tone.get('a2_models_count') or 0}")
            row("MODEL SET", "Custom / IR",
                f"{tone.get('custom_models_count') or 0} / {tone.get('irs_count') or 0}")
            row("MODEL SET", "Total", tone.get("models_count", 0))
            if tone.get("model_name"):
                row("MODEL SET", "Default name", tone.get("model_name"))
        if not model and tone.get("models") is not None:
            row("MODEL SET", "Downloaded", len(tone.get("models") or []))
            row("MODEL SET", "Local folder", _compact_path(tone.get("local_dir")))

    if tone.get("description"):
        row("NOTES", "Description", tone["description"])
    if note:
        row("NOTES", "Status", note)
    return table
