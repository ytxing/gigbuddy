"""Regression tests for field-to-column mappings in the TONE3000 tables."""

import library
import tone3000
from tui.library_panel import LibraryPanel
from tui.panels import DetailPane


def test_library_row_cells_follow_the_declared_table_column_order():
    panel = LibraryPanel.__new__(LibraryPanel)
    row = {
        "title": "Space Pack",
        "gear": "space",
        "format": "ir",
        "downloads_count": 12,
        "favorites_count": 3,
        "a1_models_count": 1,
        "a2_models_count": 2,
        "custom_models_count": 0,
        "models_count": 4,
        "published_at": "2026-08-07T00:00:00Z",
        "username": "artist",
    }
    assert panel._row_cells(row) == [
        "Space Pack", "SPACE", "@artist", "12", "3", "2026-08-07", "IR", "A2", "4",
    ]


def test_legacy_a1_is_hidden_but_new_architecture_and_ir_stay_visible():
    panel = LibraryPanel.__new__(LibraryPanel)
    row = {
        "title": "Legacy + modern",
        "gear": "amp-cab",
        "format": "nam",
        "a1_models_count": 3,
        "a2_models_count": 2,
        "custom_models_count": 1,
        "irs_count": 4,
        "models_count": 10,
    }
    cells = panel._row_cells(row)
    assert cells[7] == "A2+CUSTOM"
    assert "A1" not in cells[7]

    ir_row = {"title": "Space", "gear": "space", "format": "ir",
              "irs_count": 1, "models_count": 1}
    assert panel._row_cells(ir_row)[6] == "IR"


def test_ir_is_not_rendered_as_a_model_architecture():
    colors = {"field": "#111111", "value": "#222222", "header": "#333333",
              "warn": "#444444", "section": "#555555"}
    ir_model = {"architecture_version": None, "architecture": "IR"}
    arch_cell = DetailPane._arch_tag(ir_model, colors, {"format": "ir"})
    assert "IR" not in arch_cell
    assert "—" in arch_cell


def test_legacy_a1_is_not_rendered_as_a_model_architecture():
    colors = {"field": "#111111", "value": "#222222", "header": "#333333",
              "warn": "#444444", "section": "#555555"}
    a1_model = {"architecture_version": "1", "architecture": "WaveNet"}
    arch_cell = DetailPane._arch_tag(a1_model, colors)
    assert "A1" not in arch_cell
    assert "—" in arch_cell


def test_legacy_gear_alias_is_normalized_before_badge_selection():
    badge = DetailPane._gear_badge("full-rig")
    assert "AMP" in badge and "CAB" in badge


def test_cli_rows_keep_the_official_format_visible():
    tone = {"id": 36, "gear": "space", "format": "ir",
            "downloads_count": 1, "favorites_count": 2,
            "a2_models_count": 0, "title": "Space", "username": "artist"}
    assert "| space    | ir          |" in tone3000.fmt(tone)
    assert "| space    | ir          |" in library._fmt_table([tone])
