"""Theme-following colors: metadata tables read their palette from the active
theme, and the DetailPane re-renders when the theme switches."""
import asyncio

from rich.console import Group
from rich.style import Style
from rich.table import Table
from rich.text import Text

from tui.app import GigBuddyApp
from tui.metadata import (DEFAULT_COLORS, metadata_table,
                          preset_metadata_table, signed_fixed, theme_colors)
from tui.panels import DetailPane


def run(coro):
    return asyncio.run(coro)


def _cell_color(table, column: int, text: str) -> str | None:
    """Color of the first cell whose plain text equals `text` (Rich Column.cells
    is a bare value iterator, so locate by content)."""
    for cell in table.columns[column].cells:
        if isinstance(cell, Text) and cell.plain == text:
            style = cell.style
            if isinstance(style, str):
                style = Style.parse(style)
            if style is not None and style.color is not None:
                t = style.color.triplet
                return f"#{t.red:02x}{t.green:02x}{t.blue:02x}"
            return None
    raise AssertionError(f"no cell with text {text!r} in column {column}")


def test_metadata_table_colors_parameter():
    """colors= drives every styled cell; DEFAULT_COLORS keeps hex fallbacks."""
    colors = {"header": "#111111", "section": "#222222", "field": "#333333",
              "value": "#444444", "warn": "#555555"}
    tone = {"id": 1, "title": "Plexi", "gear": "amp", "username": "alice",
            "downloads_count": 1, "favorites_count": 0}
    model = {"local_path": "/x/amp.nam", "id": 1, "architecture": "wave"}
    table = metadata_table(tone, model, colors=colors)
    assert "111111" in table.header_style
    assert _cell_color(table, 0, "FILE") == "#222222"    # section header
    assert _cell_color(table, 0, "Filename") == "#333333"  # field name
    assert _cell_color(table, 1, "amp.nam") == "#444444"  # file value → success
    # default palette still renders without colors=
    plain = metadata_table(tone, model)
    assert DEFAULT_COLORS["section"] in str(plain.columns[0].cells.__next__().style)


def test_signed_fixed_reserves_minus_column():
    assert signed_fixed(0.8) == " 0.80"
    assert signed_fixed(-0.1) == "-0.10"
    assert len(signed_fixed(0.8)) == len(signed_fixed(-0.1))


def test_metadata_table_description_has_section_label():
    """Prose descriptions carry the section-header grammar: a bold DESCRIPTION
    title above the full-width text, table first and prose last (the picker
    tests rely on both positions)."""
    colors = {"section": "#222222"}
    tone = {"id": 1, "title": "Plexi", "description": "Bright channel",
            "models": []}
    group = metadata_table(tone, colors=colors)
    assert isinstance(group, Group)
    assert isinstance(group.renderables[0], Table)   # table stays first
    texts = [r for r in group.renderables if isinstance(r, Text)]
    label = next(r for r in texts if r.plain == "DESCRIPTION")
    assert "#222222" in str(label.style)              # section color
    assert group.renderables[-1].plain == "Bright channel"  # prose stays last

    # no description → plain Table, no label
    bare = metadata_table({"id": 1, "title": "Plexi", "models": []}, colors=colors)
    assert isinstance(bare, Table)


def test_metadata_missing_gear_uses_slot_label():
    table = metadata_table({"id": 1, "title": "Unknown", "models": []})
    values = [cell.plain if isinstance(cell, Text) else str(cell)
              for cell in table.columns[1].cells]
    assert "SLOT" in values


def test_preset_metadata_table_colors_parameter():
    """Dirty status uses warn, canonical Slot values use value."""
    colors = {"section": "#222222", "field": "#333333", "value": "#444444",
              "warn": "#555555"}
    preset = {"name": "p", "chain": {"slots": [
        {"path": "/x/amp.nam"}, {"path": "/y/cab.wav"},
    ]}, "updated_at": "2026-01-01T00:00:00"}
    resolved = {"slots": [{"path": "/x/amp.nam"}, {"path": "/y/cab.wav"}],
                "gain": 0.8, "master": 1.0, "quality": 1.0}
    table = preset_metadata_table(preset, resolved, dirty=True, colors=colors)
    assert _cell_color(table, 1, "SAVED · DIRTY") == "#555555"  # dirty → warn
    assert _cell_color(table, 1, "amp.nam") == "#444444"         # slot → value
    assert _cell_color(table, 1, "cab.wav") == "#444444"         # slot → value
    assert _cell_color(table, 0, "CONTROLS") == "#222222"
    clean = preset_metadata_table(preset, resolved, colors=colors)
    assert _cell_color(clean, 1, "SAVED") == "#444444"          # saved → value


def test_theme_colors_reads_app_variables():
    """theme_colors maps the theme's CSS variables onto the palette keys."""
    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            # pilot.pause waits for the message loop (mount completes), unlike
            # a bare asyncio.sleep which can race the initial screen push
            await pilot.pause(0.1)
            colors = theme_colors(app)
            lower = {k: v.lower() for k, v in colors.items()}
            # top-level theme colors go through Textual's Color normalization
            # (#e59a3c → #e49a3c); variables values pass through verbatim
            assert lower["header"] == "#e49a3c"   # gigbuddy primary
            assert lower["section"] == "#f4b042"  # gigbuddy accent
            assert lower["field"] == "#d3bf9e"    # gigbuddy custom field
            assert lower["value"] == "#8fb573"    # fixed success
            assert lower["warn"] == app.theme_variables["warning"].lower()

    run(scenario())


def test_theme_colors_normalizes_textual_ansi_names_for_rich():
    class App:
        theme_variables = {
            "primary": "ansi_blue",
            "accent": "ansi_bright_yellow",
            "field": "auto 60%",
            "foreground": "ansi_white",
            "success": "ansi_green",
            "warning": "ansi_red",
        }

        @staticmethod
        def get_css_variables():
            return App.theme_variables

    colors = theme_colors(App())

    assert colors == {
        "header": "blue",
        "section": "bright_yellow",
        "field": "white",
        "value": "green",
        "warn": "red",
    }

    table = metadata_table(
        {"id": 1, "title": "Plexi", "username": "alice"},
        colors=colors,
    )
    assert "ansi_" not in str(table.header_style)


def test_detail_pane_rerenders_on_theme_change():
    """Switching theme rebuilds the metadata table with new colors."""
    model = {"id": 1, "name": "amp.nam", "local_path": "/x/amp.nam",
             "architecture": "wave", "tone_id": 1}

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            pane = app.query_one(DetailPane)
            pane.show_model({"title": "Tone"}, model)
            before = pane._body.content
            before_hex = _cell_color(before, 0, "FILE")
            assert before_hex.startswith("#")

            app.theme = "textual-dark"
            await pilot.pause(0.5)
            after = pane._body.content
            after_hex = _cell_color(after, 0, "FILE")
            # gigbuddy accent (#f5b042) vs textual-dark accent (#ffa62b)
            assert after_hex != before_hex, "table must re-render with new theme"
            # semantic colors stay pinned: success cell identical in both themes
            assert _cell_color(after, 1, "amp.nam") == _cell_color(before, 1, "amp.nam")

    run(scenario())
