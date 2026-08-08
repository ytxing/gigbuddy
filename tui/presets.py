"""Preset picker + save-name modal: manage named chain snapshots from the TUI.

Loading writes data/live_chain.json (engine hot-swap); saving snapshots the
current chain. Both go through src/library.py preset_* functions so the CLI,
TUI and external agents share one code path.
"""
import asyncio
from copy import deepcopy
import shlex
import sys
from pathlib import Path
from typing import Callable

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Leave, MouseEvent, MouseMove
from textual.message import Message
from textual.widgets import DataTable, Input, Select, Static

from .marquee import MarqueeBar
from .metadata import preset_slot_label, signed_fixed
from .modals import (ClickSelectTable, GigBuddyModal, ModalBox,
                     border_hint_action_token, border_hint_click,
                     border_hint_hit, hint_span, set_border_hint_hover)
from .picker import TonePickerScreen
from .modals import set_border_hint_layout
from .selection import NonSelectableStatic
from .mutations import (ViewAnchor, focused_widget_key,
                        view_context)
from .view_controls import SearchBar

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402


def _preset_file_label(chain: dict, key: str, fallback: str) -> str:
    """Show a compact model reference in preset lists and banners."""
    id_key = "model_id" if key == "model" else f"{key}_model_id"
    model_id = chain.get(id_key)
    if model_id is not None:
        return f"#{model_id}"
    return fallback


def _preset_controls(chain: dict) -> str:
    """Keep the three chain controls visible without adding another panel."""
    values = []
    for key, label, default in (
            ("gain", "G", 1.0), ("master", "M", 1.0), ("quality", "Q", 1.0)):
        try:
            value = float(chain.get(key, default))
            values.append(f"{label}{signed_fixed(value)}")
        except (TypeError, ValueError):
            values.append(f"{label}?")
    return " ".join(values)


def _preset_slot_summary(chain: dict | None) -> str:
    """Compact ordered Model ID summary used by the main Presets table."""
    if not isinstance(chain, dict):
        return "INVALID"
    slots = chain.get("slots") or []
    if not slots:
        return "NONE"
    return " > ".join(preset_slot_label(slot) for slot in slots)


PRESET_SORT_CHOICES = [("Updated", "updated"), ("Name", "name")]
_PRESET_SEARCH_FIELDS = ("name", "note", "file", "id")


def _preset_search_tokens(query: str) -> list[tuple[str, str]]:
    """Parse the small local Preset query grammar without raising on typing."""
    try:
        raw_tokens = shlex.split(query)
    except ValueError:
        raw_tokens = query.split()
    tokens = []
    for raw in raw_tokens:
        key, separator, value = raw.partition(":")
        key = key.casefold() if separator else ""
        if key not in _PRESET_SEARCH_FIELDS:
            key = ""
            value = raw
        value = value.strip().casefold()
        if value:
            tokens.append((key, value))
    return tokens


def _preset_search_catalog(presets: list[dict]) -> dict[int, dict[str, set[str]]]:
    """Resolve model/tone IDs and model names for one preset refresh."""
    model_ids = {
        int(slot["model_id"])
        for preset in presets
        for slot in (preset.get("chain") or {}).get("slots", [])
        if isinstance(slot, dict)
        and isinstance(slot.get("model_id"), int)
        and not isinstance(slot.get("model_id"), bool)
    }
    if not model_ids:
        return {}
    marks = ",".join("?" for _ in model_ids)
    with library.connect() as conn:
        rows = conn.execute(
            "SELECT id, tone_id, name FROM models "
            f"WHERE id IN ({marks})", tuple(sorted(model_ids))).fetchall()
    return {
        int(row["id"]): {
            "ids": {str(row["id"]), str(row["tone_id"])},
            "files": {str(row["name"] or "")},
        }
        for row in rows
    }


def _preset_matches(preset: dict, tokens: list[tuple[str, str]],
                    catalog: dict[int, dict[str, set[str]]]) -> bool:
    """Match every query token against the requested local Preset fields."""
    chain = preset.get("chain") or {}
    slots = chain.get("slots") if isinstance(chain, dict) else []
    slots = slots if isinstance(slots, list) else []
    fields: dict[str, set[str]] = {
        "name": {str(preset.get("name") or "").casefold()},
        "note": {str(preset.get("note") or "").casefold()},
        "file": set(),
        "id": set(),
    }
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        path = str(slot.get("path") or "")
        if path:
            fields["file"].add(Path(path).name.casefold())
        model_id = slot.get("model_id")
        if isinstance(model_id, int) and not isinstance(model_id, bool):
            details = catalog.get(model_id, {})
            fields["id"].update(details.get("ids", {str(model_id)}))
            fields["file"].update(
                name.casefold() for name in details.get("files", set()) if name)
    all_text = " ".join(
        value for values in fields.values() for value in values).casefold()
    for key, value in tokens:
        if key == "id":
            if value not in fields["id"]:
                return False
        elif key:
            if not any(value in field for field in fields[key]):
                return False
        elif value not in all_text:
            return False
    return True


def _same_local_path(left: object, right: object) -> bool:
    """Compare draft storage paths and picker paths in one coordinate system."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    def absolute(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (library.ROOT / path)
    return absolute(left).resolve(strict=False) == absolute(right).resolve(strict=False)


def _valid_preset_id(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


class PresetTable(ClickSelectTable):
    """Republish the focused preset when focus returns without cursor movement."""

    def on_focus(self, event) -> None:
        panel = self.query_ancestor(PresetPanel)
        if panel is not None:
            panel._publish_highlight(force=True)

    def on_click(self, event) -> None:
        panel = self.query_ancestor(PresetPanel)
        meta = event.style.meta
        if (panel is not None and meta.get("column") == 0
                and isinstance(meta.get("row"), int) and meta["row"] >= 0):
            table = panel.query_one("#preset-table", DataTable)
            if meta["row"] < len(table.ordered_rows):
                table.move_cursor(row=meta["row"], column=0,
                                  animate=False, scroll=False)
            panel.action_toggle_selected()
            event.stop()
        elif getattr(event, "chain", 1) >= 2:
            self.action_select_cursor()
            event.stop()


class PresetSearchInput(Input):
    """Preset search owns Escape before the panel's selection bindings."""

    BINDINGS = [
        *Input.BINDINGS,
        Binding("escape", "cancel_search", "cancel", show=False),
    ]

    def action_cancel_search(self) -> None:
        panel = self.query_ancestor(PresetPanel)
        if panel is not None:
            panel.action_search_escape()

    def on_blur(self, _event) -> None:
        # Search editing is a focus state, not a sticky panel mode. Without
        # this reset, leaving the input for another pane makes the next Esc
        # behave as if the input were still active.
        panel = self.query_ancestor(PresetPanel)
        if panel is not None:
            panel._search_editing = False


class PresetPanel(Vertical):
    """Persistent preset list under the library: browse with ↑/↓, load with Enter.

    Highlighted presets mirror their chain summary in the detail pane; selecting
    one writes the chain (engine hot-swap), same code path as the picker.
    """

    class Activated(Message):
        def __init__(self, name: str, preset_id: int | None = None) -> None:
            super().__init__()
            self.name = name
            self.preset_id = _valid_preset_id(preset_id)

    class Highlighted(Message):
        def __init__(self, preset: dict | None) -> None:
            super().__init__()
            self.preset = preset

    BINDINGS = [
        Binding("/", "focus_search", "search", show=False),
        Binding("enter", "activate_cursor", "load", show=False),
        Binding("s", "save_active", "save", show=False),
        Binding("n", "save_as", "new"),
        Binding("r", "rename", "rename"),
        Binding("e", "edit", "edit"),
        Binding("d", "delete", "delete"),
        Binding("space", "toggle_selected", "select"),
        Binding("a", "toggle_all", "all/none"),
        Binding("escape", "search_escape", "clear", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "PRESETS"
        self._preset_names_by_key: dict[str, str] = {}
        self._preset_ids_by_key: dict[str, int] = {}
        self._search_editing = False
        self._filter_anchor: dict[str, object] | None = None
        self._mutation_anchor: ViewAnchor | None = None

    @staticmethod
    def _row_key(preset: dict) -> str:
        preset_id = preset.get("id")
        if preset_id is not None:
            return f"preset:{preset_id}"
        # Keep lightweight test doubles and legacy database rows usable while
        # real rows always use the immutable SQLite id.
        return f"preset-name:{preset.get('name', '')}"

    def _preset_name_for_key(self, key: str | None) -> str | None:
        if not isinstance(key, str):
            return None
        return self._preset_names_by_key.get(key)

    def compose(self) -> ComposeResult:
        yield MarqueeBar(id="preset-marquee")
        yield SearchBar(
            input_id="preset-search",
            sort_options=PRESET_SORT_CHOICES,
            sort_id="preset-sort",
            placeholder="name:clean note:live file:SVT id:101",
            input_cls=PresetSearchInput,
            id="preset-search-bar",
        )
        table = PresetTable(id="preset-table", cursor_type="row")
        table.add_column("Sel", key="pick", width=5)
        table.add_column("Preset", key="name")
        table.add_column("Slots", key="slots")
        table.add_column("NOTE", key="note")
        yield table

    def on_mount(self) -> None:
        self._fingerprint: tuple | None = None
        self._highlighted: str | None = None
        self._selected: set[str] = set()
        self._active: str | None = None
        self._query = ""
        self._sort = "updated"
        self._filter_anchor = None
        self._starter_bootstrap_started = False
        self._bootstrap_starter_if_empty()
        self.refresh_presets()
        self.call_after_refresh(lambda: self._publish_highlight(force=True))

    def _bootstrap_starter_if_empty(self) -> None:
        with library.connect() as conn:
            has_preset = conn.execute(
                "SELECT 1 FROM presets LIMIT 1").fetchone() is not None
        if has_preset or self._starter_bootstrap_started:
            return
        self._starter_bootstrap_started = True
        self.app.notify("Preparing starter presets")
        self.run_worker(self._bootstrap_starter_presets(),
                        name="starter-presets", exclusive=True)

    async def _bootstrap_starter_presets(self) -> None:
        try:
            result = await asyncio.to_thread(
                library.bootstrap_starter_presets, quiet=True)
        except Exception as exc:
            self.app.notify(f"Starter presets unavailable: {exc}",
                            severity="warning")
            return
        self._fingerprint = None
        self.refresh_presets(force=True)
        count = int(result.get("presets") or 0)
        failed = result.get("failed") or []
        if failed:
            self.app.notify(f"Starter presets incomplete: {count} ready",
                            severity="warning")
        elif count:
            self.app.notify(f"Starter presets ready: {count}")

    def focus_search(self) -> None:
        self._search_editing = True
        self.query_one("#preset-search", Input).focus()

    def focus_table(self) -> None:
        self.query_one("#preset-table", DataTable).focus()

    def action_activate_cursor(self) -> None:
        self._activate_selected()

    def action_save_active(self) -> None:
        self.app.action_save_preset()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "preset-search":
            return
        if not self._query.strip() and event.value.strip():
            self._capture_filter_anchor()
        self._search_editing = True
        self._query = event.value
        self._fingerprint = None
        self.refresh_presets()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "preset-search":
            if not self._query.strip() and event.value.strip():
                self._capture_filter_anchor()
            self._query = event.value
            self._fingerprint = None
            self.refresh_presets()
            self.focus_table()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "preset-sort":
            return
        self._sort = str(event.value)
        self._fingerprint = None
        self.refresh_presets()

    def refresh_presets(self, *, force: bool = False,
                        incremental: bool = False) -> None:
        """Reload from the DB (called on tick; skips repaint unless changed)."""
        with library.connect() as conn:
            fp = tuple(conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0), "
                "COALESCE(MAX(updated_at), '') FROM presets").fetchone())
            active_row = conn.execute(
                "SELECT s.value FROM settings s JOIN presets p ON p.name = s.value "
                "WHERE s.key = 'active_preset'").fetchone()
        active = active_row["value"] if active_row else None
        self._active = active
        chain_mtime = (library.CHAIN_FILE.stat().st_mtime_ns
                       if library.CHAIN_FILE.exists() else 0)
        fp += (active, chain_mtime, self._query, self._sort)
        if not force and fp == self._fingerprint:
            return
        self._fingerprint = fp
        self._highlighted = None
        table = self.query_one("#preset-table", DataTable)
        central_anchor = self._mutation_anchor
        self._mutation_anchor = None
        restoring_filter = not self._query.strip() and self._filter_anchor is not None
        view_anchor = self._view_anchor(table)
        if central_anchor is not None:
            view_anchor.update({
                "focused_key": central_anchor.cursor_row_key,
                "cursor_column": central_anchor.cursor_column,
                "first_visible_key": central_anchor.first_visible_row_key,
                "row_offset": central_anchor.row_offset,
                "scroll_x": central_anchor.scroll_x,
                "scroll_y": central_anchor.scroll_y,
            })
        elif restoring_filter:
            view_anchor = self._filter_anchor
        focused_key = view_anchor["focused_key"]
        focused_index = int(view_anchor["focused_index"])
        scroll_x = float(view_anchor["scroll_x"])
        scroll_y = float(view_anchor["scroll_y"])
        first_visible_key = view_anchor["first_visible_key"]
        row_offset = float(view_anchor["row_offset"])
        real_rows = [
            row for row in table.ordered_rows
            if not str(row.key.value).startswith("__")
        ]
        successor_key = view_anchor["successor_key"]
        predecessor_key = view_anchor["predecessor_key"]
        all_presets = library.preset_list()
        self._preset_names_by_key = {
            self._row_key(preset): preset["name"] for preset in all_presets
        }
        self._preset_ids_by_key = {
            self._row_key(preset): int(preset["id"])
            for preset in all_presets
            if isinstance(preset.get("id"), int)
            and not isinstance(preset.get("id"), bool)
        }
        existing_keys = set(self._preset_names_by_key)
        presets = all_presets
        search_tokens = _preset_search_tokens(self._query)
        if search_tokens:
            catalog = _preset_search_catalog(all_presets)
            presets = [p for p in presets
                       if _preset_matches(p, search_tokens, catalog)]
        if self._sort == "name":
            presets = sorted(
                presets, key=lambda p: str(p.get("name") or "").casefold())
        visible_keys = {self._row_key(preset) for preset in presets}
        # Search changes visibility only. Selection is keyed to the complete
        # preset set, so hidden rows return selected when the query is cleared.
        self._selected.intersection_update(existing_keys)
        if incremental:
            self._reconcile_rows_incremental(table, presets, active)
        else:
            table.clear()
            for p in presets:
                key = self._row_key(p)
                ch = p.get("chain")
                is_active = p["name"] == active
                dirty = is_active and library.preset_is_dirty(active)
                table.add_row(
                    "\\[x]" if key in self._selected else "\\[ ]",
                    f"{p['name']}{' *' if dirty else ''}",
                    _preset_slot_summary(ch),
                    p.get("note") or "",
                    key=key)
            if not table.rows:
                table.add_row("", "(no matching presets)", "", "", key="__status__")
        if table.rows and focused_key in visible_keys:
            focused_row = next(
                index for index, row in enumerate(table.ordered_rows)
                if row.key.value == focused_key)
            table.move_cursor(row=focused_row, animate=False, scroll=False)
        else:
            # A deleted current row selects the next visual row, then the
            # previous one. A search filter can hide both, so retain the old
            # visual index as the final fallback.
            fallback_key = next(
                (candidate for candidate in (successor_key, predecessor_key)
                 if candidate in visible_keys), None)
            if fallback_key is not None:
                target = next(
                    index for index, row in enumerate(table.ordered_rows)
                    if row.key.value == fallback_key)
                table.move_cursor(row=target, animate=False, scroll=False)
            else:
                real_rows = [row for row in table.ordered_rows
                             if not str(row.key.value).startswith("__")]
                if real_rows:
                    target = min(max(focused_index, 0), len(real_rows) - 1)
                    table.move_cursor(row=target, animate=False, scroll=False)
        first_row = next(
            (index for index, row in enumerate(table.ordered_rows)
             if row.key.value == first_visible_key), None)
        restored_scroll_y = (
            first_row + row_offset
            if first_row is not None else scroll_y
        )
        def restore_scroll() -> None:
            if not table.is_attached:
                return
            table.scroll_to(
                x=scroll_x,
                y=restored_scroll_y,
                animate=False,
                force=True,
                immediate=True,
            )

        self.call_after_refresh(restore_scroll)
        self._publish_highlight()
        self._update_selection_status()
        if restoring_filter:
            self._filter_anchor = None

    def _view_anchor(self, table: DataTable) -> dict[str, object]:
        rows = table.ordered_rows
        focused_key = None
        focused_index = table.cursor_row
        if rows and 0 <= table.cursor_row < len(rows):
            candidate = rows[table.cursor_row].key.value
            if isinstance(candidate, str) and not candidate.startswith("__"):
                focused_key = candidate

        real_rows = [
            row for row in rows
            if not str(row.key.value).startswith("__")
        ]
        current_position = next(
            (index for index, row in enumerate(real_rows)
             if row.key.value == focused_key), None)
        first_visible_key = None
        row_offset = 0.0
        if rows:
            first_index = min(max(int(table.scroll_y), 0), len(rows) - 1)
            candidate = rows[first_index].key.value
            if isinstance(candidate, str) and not candidate.startswith("__"):
                first_visible_key = candidate
            row_offset = max(0.0, float(table.scroll_y) - first_index)

        return {
            "focused_key": focused_key,
            "focused_index": focused_index,
            "cursor_column": table.cursor_column,
            "successor_key": (
                real_rows[current_position + 1].key.value
                if current_position is not None
                and current_position + 1 < len(real_rows) else None),
            "predecessor_key": (
                real_rows[current_position - 1].key.value
                if current_position is not None and current_position > 0
                else None),
            "scroll_x": float(table.scroll_x),
            "scroll_y": float(table.scroll_y),
            "first_visible_key": first_visible_key,
            "row_offset": row_offset,
        }

    def capture_view_anchor(self) -> ViewAnchor:
        """Capture the preset table, selection and current detail identity."""
        table = self.query_one("#preset-table", DataTable)
        state = self._view_anchor(table)
        screen_id, app_tab = view_context(self)
        return ViewAnchor(
            screen_id=screen_id,
            app_tab=app_tab,
            view_tab_id="presets",
            focused_widget=focused_widget_key(self),
            cursor_row_key=state["focused_key"],
            cursor_column=int(state["cursor_column"]),
            first_visible_row_key=state["first_visible_key"],
            row_offset=float(state["row_offset"]),
            scroll_x=float(state["scroll_x"]),
            scroll_y=float(state["scroll_y"]),
            selection_keys=tuple(sorted(self._selected)),
            confirmation_state={"search_editing": self._search_editing},
            detail_context_key=self._highlighted,
        )

    def set_mutation_anchor(self, anchor: ViewAnchor | None) -> None:
        self._mutation_anchor = anchor

    def restore_view_anchor(self, anchor: ViewAnchor | None) -> None:
        """Restore the stable preset row and viewport after an incremental update."""
        if anchor is None or anchor.view_tab_id != "presets":
            return
        table = self.query_one("#preset-table", DataTable)
        rows = table.ordered_rows
        row_index = next(
            (index for index, row in enumerate(rows)
             if row.key.value == anchor.cursor_row_key), None)
        if row_index is not None:
            table.move_cursor(
                row=row_index, column=anchor.cursor_column,
                animate=False, scroll=False)
        first_index = next(
            (index for index, row in enumerate(rows)
             if row.key.value == anchor.first_visible_row_key), None)
        scroll_y = (
            first_index + max(float(anchor.row_offset), 0.0)
            if first_index is not None else anchor.scroll_y
        )
        table.scroll_to(
            x=anchor.scroll_x, y=scroll_y, animate=False, force=True,
            immediate=True)
        self._selected.intersection_update(self._preset_names_by_key)
        if not anchor.focused_widget:
            return
        focused = getattr(self.app, "focused", None)
        try:
            focus_still_in_panel = focused is None or any(
                item is self for item in focused.ancestors_with_self)
            target = self.query_one(f"#{anchor.focused_widget}")
        except Exception:
            return
        if focus_still_in_panel:
            target.focus()

    def _capture_filter_anchor(self) -> None:
        if self._filter_anchor is None:
            table = self.query_one("#preset-table", DataTable)
            self._filter_anchor = self._view_anchor(table)

    def _reconcile_rows_incremental(self, table: DataTable,
                                    presets: list[dict], active: str | None) -> None:
        """Reconcile preset rows by stable key without clearing the table."""
        desired: dict[str, tuple[str, str, str, str]] = {}
        desired_order: list[str] = []
        for preset in presets:
            key = self._row_key(preset)
            is_active = preset["name"] == active
            dirty = is_active and library.preset_is_dirty(active)
            desired[key] = (
                "\\[x]" if key in self._selected else "\\[ ]",
                f"{preset['name']}{' *' if dirty else ''}",
                _preset_slot_summary(preset.get("chain")),
                preset.get("note") or "",
            )
            desired_order.append(key)

        for row in tuple(table.ordered_rows):
            key = str(row.key.value)
            if key.startswith("__") or key not in desired:
                try:
                    table.remove_row(row.key)
                except Exception:
                    pass

        columns = ("pick", "name", "slots", "note")
        for key in desired_order:
            values = desired[key]
            if key in table.rows:
                for column, value in zip(columns, values):
                    table.update_cell(key, column, value, update_width=False)
            else:
                table.add_row(*values, key=key)

        if not desired_order:
            if "__status__" not in table.rows:
                table.add_row("", "(no matching presets)", "", "", key="__status__")
            return

        try:
            table.remove_row("__status__")
        except Exception:
            pass

        # DataTable exposes keyed updates/removals but no public row insertion.
        # Rebuild only its stable key-to-index map so rows already mounted keep
        # their widgets, render caches aside, and the requested sort order stays
        # correct after a new/renamed preset is committed.
        row_by_value = {
            str(row.key.value): row.key for row in table.rows.values()
        }
        ordered_keys = [row_by_value[key] for key in desired_order
                        if key in row_by_value]
        if [str(row.key.value) for row in table.ordered_rows] != desired_order:
            table._row_locations = type(table._row_locations)(
                {key: index for index, key in enumerate(ordered_keys)})
            table._update_count += 1
            table._clear_caches()
            table._require_update_dimensions = True
            table.refresh(layout=True)

    def reconcile_after_mutation(self, _event) -> None:
        """Re-read presets once while retaining row, selection, and viewport."""
        if not getattr(self, "is_mounted", False):
            return
        self._fingerprint = None
        self.refresh_presets(force=True, incremental=True)

    def _selected_preset(self) -> dict | None:
        table = self.query_one("#preset-table", DataTable)
        if not table.ordered_rows or not 0 <= table.cursor_row < len(table.ordered_rows):
            return None
        key = table.ordered_rows[table.cursor_row].key.value
        name = self._preset_name_for_key(key)
        preset_id = self._preset_ids_by_key.get(str(key))
        if preset_id is not None:
            return library.preset_get_by_id(preset_id)
        return library.preset_get(name) if name else None

    def _request_load(self, preset: dict | None = None) -> None:
        """Route every load entry through the dirty-chain confirmation gate."""
        preset = preset or self._selected_preset()
        if not preset:
            return
        active = library.preset_current()
        if active and library.preset_is_dirty(active):
            self.app.push_screen(PresetLoadConfirm(preset))
            return
        self.post_message(self.Activated(
            str(preset["name"]), _valid_preset_id(preset.get("id"))))

    def action_save_as(self) -> None:
        self.app.push_screen(PresetNameModal())

    def action_rename(self) -> None:
        preset = self._selected_preset()
        if preset:
            self.app.push_screen(PresetRenameModal(preset))

    def action_edit(self) -> None:
        preset = self._selected_preset()
        if preset:
            self.app.push_screen(PresetEditModal(preset))

    def action_delete(self) -> None:
        keys = sorted(self._selected)
        if not keys:
            table = self.query_one("#preset-table", DataTable)
            if table.ordered_rows and 0 <= table.cursor_row < len(table.ordered_rows):
                keys = [str(table.ordered_rows[table.cursor_row].key.value)]
        targets = [
            {"id": self._preset_ids_by_key.get(key),
             "name": self._preset_name_for_key(key)}
            for key in keys
            if self._preset_name_for_key(key) is not None
        ]
        if targets:
            self.app.push_screen(PresetDeleteModal(targets))

    def _hint_action_specs(self) -> list[tuple[str, Callable[[], None]]]:
        """Build the visible operations for the current width and selection.

        The full hint is kept on wide terminals. Narrow panes use familiar
        TUI abbreviations (``^S`` and ``↵``) so the border never clips the
        operation list into an unreadable fragment.
        """
        count = len(getattr(self, "_selected", ()))
        try:
            table = self.query_one("#preset-table", DataTable)
            names = {
                row.key.value for row in table.ordered_rows
                if isinstance(row.key.value, str)
                and not row.key.value.startswith("__")
            }
        except Exception:
            names = set()
        all_label = "a none" if names and names <= self._selected else "a all"
        width = self.region.width or (self.size.width + 4)
        if count:
            if width < 64:
                labels = (all_label, "space", "d", "esc")
            elif width < 80:
                labels = (all_label, "space", "d del", "esc clear")
            else:
                labels = (all_label, "space select", "d delete", "esc clear")
            actions = (
                self.action_toggle_all,
                self.action_toggle_selected,
                self.action_delete,
                self.action_clear_selected,
            )
            return list(zip(labels, actions))

        if width < 64:
            labels = ("n", "s", "r", "e", "a", "d", "enter")
        elif width < 80:
            labels = ("n new", "s save", "r ren", "e edit", all_label,
                      "d del", "enter")
        else:
            labels = ("n new", "s save", "r rename", "e edit", all_label,
                      "d delete", "enter load")
        actions = (
            self.action_save_as,
            self.app.action_save_preset,
            self.action_rename,
            self.action_edit,
            self.action_toggle_all,
            self.action_delete,
            self._activate_selected,
        )
        return list(zip(labels, actions))

    def _update_selection_status(self) -> None:
        count = len(self._selected)
        specs = self._hint_action_specs()
        visible = [label for label, _action in specs]
        set_border_hint_layout(self, f"{count} sel" if count else "", visible)

    def on_resize(self, _event) -> None:
        if hasattr(self, "_selected"):
            self._update_selection_status()

    def action_toggle_selected(self) -> None:
        table = self.query_one("#preset-table", DataTable)
        if not table.ordered_rows or not 0 <= table.cursor_row < len(table.ordered_rows):
            return
        key = table.ordered_rows[table.cursor_row].key.value
        if key not in self._preset_names_by_key:
            return
        if key in self._selected:
            self._selected.remove(key)
        else:
            self._selected.add(key)
        table.update_cell(
            key, "pick", "\\[x]" if key in self._selected else "\\[ ]")
        self._update_selection_status()

    def action_toggle_all(self) -> None:
        table = self.query_one("#preset-table", DataTable)
        keys = {
            row.key.value for row in table.ordered_rows
            if isinstance(row.key.value, str)
            and row.key.value in self._preset_names_by_key
        }
        self._selected = set() if keys and keys <= self._selected else keys
        for key in keys:
            table.update_cell(key, "pick", "\\[x]" if key in self._selected else "\\[ ]")
        self._update_selection_status()

    def action_clear_selected(self) -> None:
        table = self.query_one("#preset-table", DataTable)
        for key in self._selected:
            try:
                table.update_cell(key, "pick", "\\[ ]")
            except Exception:
                pass
        self._selected.clear()
        self._update_selection_status()

    def action_search_escape(self) -> None:
        """Close search editing first; a second escape clears its query."""
        search = self.query_one("#preset-search", Input)
        if self._search_editing or search.has_focus:
            self._search_editing = False
            search.blur()
            self.focus_table()
            return
        if self._query:
            self._query = ""
            search.value = ""
            self._fingerprint = None
            self.refresh_presets()
            self.focus_table()

    def _border_hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        return self._hint_action_specs()

    def _click_border_hint(self, event: MouseEvent) -> bool:
        hit = border_hint_hit(self, event.screen_x, event.screen_y)
        if hit is None:
            return False
        label, offset = hit
        actions = self._border_hint_actions()
        for text, action in actions:
            span = hint_span(label, text)
            if span is None:
                continue
            if span[0] <= offset < span[1]:
                event.stop()
                action()
                return True
        return False

    def on_mouse_move(self, event: MouseMove) -> None:
        tokens = [token for token, _ in self._border_hint_actions()]
        set_border_hint_hover(
            self,
            border_hint_action_token(self, event.screen_x, event.screen_y, tokens),
        )

    def on_leave(self, event: Leave) -> None:
        set_border_hint_hover(self, None)

    def _activate_selected(self) -> None:
        self._request_load()

    def _publish_highlight(self, *, force: bool = False) -> None:
        """Publish the current row even when focus starts on row zero."""
        table = self.query_one("#preset-table", DataTable)
        rows = table.ordered_rows
        key = rows[table.cursor_row].key.value if (
            rows and 0 <= table.cursor_row < len(rows)) else None
        name = self._preset_name_for_key(key)
        if not name:
            return
        if not force and key == self._highlighted:
            return
        self._highlighted = key
        preset_id = self._preset_ids_by_key.get(str(key))
        preset = (library.preset_get_by_id(preset_id)
                  if preset_id is not None else library.preset_get(name))
        banner = self.query_one("#preset-marquee", MarqueeBar)
        if preset:
            chain = preset["chain"]
            amp = _preset_file_label(chain, "model", "external")
            ir = _preset_file_label(chain, "ir", "—")
            dirty = self._active == preset["name"] and library.preset_is_dirty(
                preset["name"])
            state = "ACTIVE" if self._active == preset["name"] else "SAVED"
            if dirty:
                state += " · DIRTY"
            banner.content = (
                f"{state} · {preset['name']} · SLOTS "
                f"{_preset_slot_summary(chain)} · {_preset_controls(chain)}"
            )
        else:
            banner.content = None
        self.post_message(self.Highlighted(preset))

    def on_descendant_focus(self, event) -> None:
        """Re-publish row zero when focus returns without a cursor move."""
        widget_id = getattr(event.widget, "id", None)
        if widget_id == "preset-search":
            self._search_editing = True
        elif widget_id == "preset-table":
            self._search_editing = False
            self._publish_highlight()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._publish_highlight()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        name = self._preset_name_for_key(key)
        preset_id = self._preset_ids_by_key.get(str(key))
        preset = (library.preset_get_by_id(preset_id)
                  if preset_id is not None else library.preset_get(name))
        self._request_load(preset)

    def on_click(self, event) -> None:
        """Single click focuses; double click loads the preset (like Enter)."""
        if self._click_border_hint(event):
            return
        meta = event.style.meta
        if meta.get("column") == 0 and isinstance(meta.get("row"), int) and meta["row"] >= 0:
            table = self.query_one("#preset-table", DataTable)
            rows = table.ordered_rows
            if meta["row"] < len(rows):
                table.cursor_row = meta["row"]
                self.action_toggle_selected()
                event.stop()
                return
        if getattr(event, "chain", 1) >= 2:
            table = self.query_one("#preset-table", DataTable)
            key = (table.ordered_rows[table.cursor_row].key.value
                   if table.ordered_rows else None)
            name = self._preset_name_for_key(key)
            if name:
                event.stop()
                preset_id = self._preset_ids_by_key.get(str(key))
                preset = (library.preset_get_by_id(preset_id)
                          if preset_id is not None else library.preset_get(name))
                self._request_load(preset)


class PresetLoadConfirm(GigBuddyModal):
    """Confirm discarding dirty live-chain changes before loading a Preset."""

    CSS = """
    PresetLoadConfirm > ModalBox {
        width: 64%; height: auto; margin: 7 18;
        border: round $warning; border-title-color: $warning;
    }
    """

    class Confirmed(Message):
        def __init__(self, name: str, preset_id: int | None = None) -> None:
            super().__init__()
            self.name = name
            self.preset_id = _valid_preset_id(preset_id)

    BINDINGS = [
        Binding("enter", "confirm_load", "load", show=False),
    ]

    def __init__(self, preset: dict) -> None:
        super().__init__()
        self._preset_name = str(preset.get("name") or "")
        self._preset_id = _valid_preset_id(preset.get("id"))

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "LOAD PRESET"
        with box:
            yield NonSelectableStatic(
                f"Discard current live changes and load '{self._preset_name}'?\n"
                "The current chain will be available through undo.")

    def on_mount(self) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "dirty live chain",
            [token for token, _action in self._border_hint_actions()])

    def action_confirm_load(self) -> None:
        self._confirm()

    def _confirm(self) -> None:
        self.post_message(self.Confirmed(self._preset_name, self._preset_id))
        self.dismiss()

    def _border_hint_actions(self) -> list:
        return [
            ("enter load", self._confirm),
            ("esc cancel", self.dismiss),
        ]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))


class PresetNameModal(GigBuddyModal):
    """Save As dialog; overwriting an existing name requires two submits."""

    CSS = """
    PresetNameModal > ModalBox { width: 60%; height: auto; margin: 6 20; }
    #preset-save-input { height: 3; }
    #preset-save-hint { color: $text-muted; }
    """

    class Saved(Message):
        def __init__(self, name: str, preset_id: int | None = None) -> None:
            super().__init__()
            self.name = name
            self.preset_id = _valid_preset_id(preset_id)

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "SAVE PRESET AS"
        with box:
            yield Input(placeholder="preset name (e.g. band-guitar-rhcp)", id="preset-save-input")

    def on_mount(self) -> None:
        self._pending_overwrite: str | None = None
        self.query_one("#preset-save-input", Input).focus()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if not name:
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, "name required",
                [token for token, _action in self._border_hint_actions()])
            return
        if library.preset_get(name) and self._pending_overwrite != name:
            self._pending_overwrite = name
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, f"'{name}' exists · enter again to overwrite",
                [token for token, _action in self._border_hint_actions()])
            return
        p = library.preset_save(name)
        self.post_message(self.Saved(p["name"], p.get("id")))
        self.dismiss()

    def _confirm(self) -> None:
        inp = self.query_one("#preset-save-input", Input)
        self.on_input_submitted(Input.Submitted(inp, inp.value))

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter save", self._confirm),
            ("esc cancel", self.dismiss),
        ]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))


class _ChainSaveRow(Static):
    """SAVE 弹窗的一行：聚焦时通知 owner 切换选中（▶ 跟随焦点）。"""

    def __init__(self, row: str, owner, **kwargs) -> None:
        super().__init__(**kwargs)
        self.can_focus = True
        self._row = row
        self._owner = owner

    def on_focus(self, event) -> None:
        self._owner._select(self._row)


class _ChainSaveNameInput(Input):
    """名字输入框：获得焦点时选中 SAVE AS 行（▶ 跟随焦点）。"""

    def __init__(self, owner, **kwargs) -> None:
        super().__init__(**kwargs)
        self._owner = owner

    def on_focus(self, event) -> None:
        self._owner._select("as")


class ChainSaveModal(GigBuddyModal):
    """Choose whether to overwrite the active Preset or save a new one."""

    CSS = """
    ChainSaveModal > ModalBox {
        width: 72%; height: auto; margin: 8 14;
    }
    #chain-save-here {
        height: 1; padding: 0 1;
        background: transparent;
    }
    #chain-save-as {
        width: auto; height: 1; padding: 0 3 0 1;
        background: transparent;
    }
    #chain-save-here:focus, #chain-save-as:focus,
    #chain-save-here:hover, #chain-save-as:hover {
        background: $panel-lighten-1;
    }
    #chain-save-here.chain-save-row--selected,
    #chain-save-as.chain-save-row--selected {
        background: $panel-lighten-1;
    }
    #chain-save-as-line {
        height: 1; width: 100%; align: left middle;
    }
    #chain-save-name {
        width: 1fr; height: 1; padding: 0 1;
        background: $boost;
        border-top: none; border-right: none;
        border-bottom: none; border-left: none;
    }
    #chain-save-name:focus {
        background: $surface-lighten-1;
        border-top: none; border-right: none;
        border-bottom: none; border-left: none;
    }
    #chain-save-status { height: 1; color: $text-muted; }
    """

    class Saved(Message):
        def __init__(self, name: str, preset_id: int | None = None) -> None:
            super().__init__()
            self.name = name
            self.preset_id = _valid_preset_id(preset_id)

    def __init__(self) -> None:
        super().__init__()
        active = library.preset_current()
        self._selected = "here" if active else "as"
        self._active_name = active
        self._pending_overwrite: str | None = None
        self._last_click: tuple[float, str] | None = None

    BINDINGS = [Binding("enter", "run_selected", "save", show=False)]

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "SAVE"
        with box:
            yield _ChainSaveRow(
                "here", self, id="chain-save-here", classes="chain-save-row")
            with Horizontal(id="chain-save-as-line"):
                yield _ChainSaveRow(
                    "as", self, id="chain-save-as", classes="chain-save-row")
                yield _ChainSaveNameInput(
                    self, placeholder=self._active_name or "new preset name",
                    id="chain-save-name",
                )
            yield Static("", id="chain-save-status")

    def on_mount(self) -> None:
        self._render_rows()
        self._update_hint()
        if self._selected == "here":
            self.query_one("#chain-save-here", Static).focus()
        else:
            self.query_one("#chain-save-name", Input).focus()

    def _set_status(self, text: str) -> None:
        self.query_one("#chain-save-status", Static).update(text)
        self._update_hint(text)

    def _update_hint(self, state: str = "") -> None:
        box = self.query_one(ModalBox)
        action = "enter overwrite" if self._pending_overwrite else "enter save"
        set_border_hint_layout(box, state, ["cancel", action])

    def _finish_save(self, name: str) -> None:
        try:
            preset = library.preset_save(name)
        except (OSError, ValueError, TypeError) as exc:
            self._pending_overwrite = None
            self._set_status(f"Save failed: {exc}")
            return
        self.post_message(self.Saved(preset["name"], preset.get("id")))
        self.dismiss()

    def _render_rows(self) -> None:
        """刷新两行文本与选中指示符（▶ = 选中，未选中行留空格对齐）。

        SAVE HERE 行尾附当前 preset 名（dim 只读——提示覆盖目标，不可改）；
        SAVE AS 行的名字写在右侧可写输入框里。两行名字起始列对齐：
        SAVE HERE 标签 9 字符 + 3 空格，SAVE AS 标签补 1 空格 + padding 右 3。
        """
        here = self.query_one("#chain-save-here", Static)
        as_row = self.query_one("#chain-save-as", Static)
        marker_here = "▶ " if self._selected == "here" else "  "
        marker_as = "▶ " if self._selected == "as" else "  "
        target = self._active_name
        if target:
            here.update(
                f"{marker_here}SAVE HERE   [dim]{escape(target)} (will be overwritten)[/]")
        else:
            here.update(f"{marker_here}SAVE HERE   [dim]no active preset[/]")
        as_row.update(f"{marker_as}SAVE AS ")
        here.set_class(self._selected == "here", "chain-save-row--selected")
        as_row.set_class(self._selected == "as", "chain-save-row--selected")

    def _select(self, row: str) -> None:
        """切换选中行（▶ 指示符跟随），不执行动作。"""
        if self._selected != row:
            self._selected = row
            self._pending_overwrite = None
            self._set_status("")
        self._render_rows()

    def _row_click(self, row: str) -> None:
        """单击选中；350ms 内对同一行再次单击 = 双击，直接执行该行。"""
        import time as _time
        now = _time.monotonic()
        if (self._last_click is not None
                and now - self._last_click[0] < 0.35
                and self._last_click[1] == row):
            self._last_click = None
            self._submit()
            return
        self._last_click = (now, row)
        self._select(row)

    def action_run_selected(self) -> None:
        """Enter：执行当前选中行（焦点在行上时冒泡至此）。"""
        self._submit()

    def _submit(self) -> None:
        if self._pending_overwrite:
            name = self._pending_overwrite
            self._finish_save(name)
            return

        if self._selected == "here":
            self._save_here()
        else:
            self._save_as()

    def _save_here(self) -> None:
        active = library.preset_current()
        if not active:
            self._select("as")
            self.query_one("#chain-save-name", Input).focus()
            self._set_status("没有当前 preset，请使用 SAVE AS 并输入名字")
            return
        self._pending_overwrite = active
        self._set_status(f'"{active}" already exists. Overwrite it?')

    def _save_as(self) -> None:
        name = self.query_one("#chain-save-name", Input).value.strip()
        if not name:
            self._set_status("Preset name required")
            return
        if library.preset_get(name):
            self._pending_overwrite = name
            self._set_status(f'"{name}" already exists. Overwrite it?')
            return
        self._finish_save(name)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "chain-save-name":
            return
        if self._pending_overwrite and event.value.strip() != self._pending_overwrite:
            self._pending_overwrite = None
            self._set_status("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chain-save-name":
            # Enter = 执行选中行（聚焦输入框时 ▶ 已跟随 SAVE AS）
            self._submit()

    def _confirm(self) -> None:
        self._submit()

    def _border_hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        return [("cancel", self.dismiss),
                ("enter overwrite" if self._pending_overwrite else "enter save",
                 self._confirm)]

    def on_click(self, event: MouseEvent) -> None:
        # 行点击优先于 border hint：单击选中、双击执行
        control = event.control
        if control is not None and control.id in ("chain-save-here", "chain-save-as"):
            self._row_click("here" if control.id == "chain-save-here" else "as")
            event.stop()
            return
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))


class ClearSlotsConfirm(GigBuddyModal):
    """Confirm clearing the current chain without deleting Presets or files."""

    CSS = """
    ClearSlotsConfirm > ModalBox {
        width: 58%; height: auto; margin: 10 21;
        border: round $warning; border-title-color: $warning;
    }
    """

    class Confirmed(Message):
        pass

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "CLEAR ALL SLOTS"
        with box:
            yield NonSelectableStatic(
                "Are you sure you want to clear all Slots?\n"
                "Local files and Presets will not be deleted.")

    def on_mount(self) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", ["cancel", "enter clear all"])

    def _confirm(self) -> None:
        self.post_message(self.Confirmed())
        self.dismiss()

    def _border_hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        return [("cancel", self.dismiss), ("enter clear all", self._confirm)]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))


class PresetEditModal(GigBuddyModal):
    """Edit a Preset snapshot in memory, then commit it explicitly."""

    CSS = """
    PresetEditModal > ModalBox { width: 86%; height: auto; margin: 3 7; }
    #preset-edit-slots { height: 8; }
    #preset-edit-fields { height: auto; }
    #preset-edit-fields Input { width: 1fr; }
    #preset-edit-note { height: 3; }
    #preset-edit-status { height: 1; color: $text-muted; }
    """

    class Saved(Message):
        def __init__(self, name: str, *, load: bool = False,
                     bypassed: int = 0, preset_id: int | None = None) -> None:
            super().__init__()
            self.name = name
            self.load = load
            self.bypassed = bypassed
            self.preset_id = _valid_preset_id(preset_id)

    BINDINGS = [
        Binding("+", "add_slot", "add slot", show=False),
        Binding("d", "delete_slot", "delete", show=False),
        Binding("r", "replace_file", "choose file", show=False),
        Binding("alt+up", "move_up", "move up", show=False),
        Binding("alt+down", "move_down", "move down", show=False),
        Binding("ctrl+enter", "save_and_load", "save/load", show=False),
    ]

    def __init__(self, preset: dict) -> None:
        super().__init__()
        self._preset_name = str(preset.get("name") or "")
        self._preset_id = _valid_preset_id(preset.get("id"))
        chain = preset.get("chain") if isinstance(preset, dict) else None
        chain = chain if isinstance(chain, dict) else {}
        self._draft = deepcopy(chain)
        self._draft.setdefault("slots", [])
        self._draft.setdefault("gain", 1.0)
        self._draft.setdefault("master", 1.0)
        self._draft.setdefault("quality", 1.0)
        self._draft["slots"] = [
            deepcopy(slot) if isinstance(slot, dict)
            else {"model_id": None, "path": None}
            for slot in self._draft.get("slots", [])
        ][:6]
        self._note = str(preset.get("note") or "")
        self._cursor = 0
        self._syncing = False
        # Bypass is draft-only state.  It never enters the persisted preset
        # shape; saving a bypassed Slot writes its path as null (Empty).
        self._draft_candidates: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = f"EDIT PRESET · {self._preset_name}"
        with box:
            yield NonSelectableStatic(
                "Draft only · Enter saves · ctrl+enter saves and loads · Esc cancels")
            table = DataTable(id="preset-edit-slots", cursor_type="row")
            table.add_column("Slot", key="index", width=8)
            table.add_column("Model ID", key="path")
            yield table
            with Horizontal(id="preset-edit-fields"):
                yield Input(id="preset-edit-gain", placeholder="gain")
                yield Input(id="preset-edit-master", placeholder="master")
                yield Input(id="preset-edit-quality", placeholder="quality")
            yield Static("", id="preset-edit-path")
            yield Input(id="preset-edit-note", value=self._note,
                        placeholder="optional note")
            yield MarqueeBar("", id="preset-edit-status")

    def on_mount(self) -> None:
        self._refresh_table()
        self._set_controls()
        self._update_hint("draft")
        self.query_one("#preset-edit-slots", DataTable).focus()

    def _update_hint(self, state: str) -> None:
        try:
            box = self.query_one(ModalBox)
        except Exception:
            return
        set_border_hint_layout(
            box, state,
            [token for token, _action in self._border_hint_actions()])

    def _set_status(self, text: str) -> None:
        if getattr(self, "is_mounted", False):
            self.query_one("#preset-edit-status", MarqueeBar).content = text
        self._update_hint(text)

    def _set_controls(self) -> None:
        self._syncing = True
        try:
            for widget_id, key in (
                    ("#preset-edit-gain", "gain"),
                    ("#preset-edit-master", "master"),
                    ("#preset-edit-quality", "quality")):
                self.query_one(widget_id, Input).value = f"{float(self._draft[key]):.2f}"
            self.query_one("#preset-edit-note", Input).value = self._note
            self._sync_path_input()
        finally:
            self._syncing = False

    def _sync_path_input(self) -> None:
        slots = self._draft.get("slots", [])
        path = (slots[self._cursor].get("path")
                if slots and self._cursor < len(slots) else "")
        if not path and self._cursor in self._draft_candidates:
            label = (f"BYPASS · {self._draft_candidates[self._cursor]}"
                     " · save becomes EMPTY")
        else:
            label = str(path or "NONE · press r to choose")
        self.query_one("#preset-edit-path", Static).update(escape(label))

    def _refresh_table(self) -> None:
        table = self.query_one("#preset-edit-slots", DataTable)
        table.clear()
        slots = self._draft.get("slots", [])
        if slots:
            self._cursor = min(max(self._cursor, 0), len(slots) - 1)
        else:
            self._cursor = 0
        for index, slot in enumerate(slots):
            path = slot.get("path") if isinstance(slot, dict) else None
            if path:
                label = preset_slot_label(slot)
            elif index in self._draft_candidates:
                label = "BYPASS"
            else:
                label = preset_slot_label(slot)
            table.add_row(f"{index + 1:02d}", escape(label), key=f"slot:{index}")
        if table.ordered_rows:
            table.move_cursor(row=self._cursor, animate=False, scroll=False)
        if getattr(self, "is_mounted", False):
            self._sync_path_input()

    def _commit_input_values(self) -> None:
        for widget_id, key, lower, upper in (
                ("#preset-edit-gain", "gain", 0.0, 10.0),
                ("#preset-edit-master", "master", 0.0, 10.0),
                ("#preset-edit-quality", "quality", 0.0, 1.0)):
            raw = self.query_one(widget_id, Input).value.strip()
            value = float(raw)
            if not lower <= value <= upper:
                raise ValueError(f"{key} must be between {lower} and {upper}")
            self._draft[key] = round(value, 2)
        self._note = self.query_one("#preset-edit-note", Input).value

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._syncing:
            return
        if event.input.id == "preset-edit-note":
            self._note = event.value

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value)
        if key.startswith("slot:"):
            self._cursor = int(key.partition(":")[2])
            if getattr(self, "is_mounted", False):
                self._sync_path_input()

    def on_data_table_row_selected(self, _event: DataTable.RowSelected) -> None:
        self._confirm()

    def action_add_slot(self) -> None:
        slots = self._draft.setdefault("slots", [])
        if len(slots) >= 6:
            self._set_status("6/6 slots")
            return
        slots.append({"model_id": None, "path": None})
        self._cursor = len(slots) - 1
        self._refresh_table()
        self._set_status("draft Slot added")

    def action_delete_slot(self) -> None:
        slots = self._draft.setdefault("slots", [])
        if not slots:
            self._set_status("no slots")
            return
        slots.pop(self._cursor)
        self._draft_candidates = {
            (index if index < self._cursor else index - 1): path
            for index, path in self._draft_candidates.items()
            if index != self._cursor
        }
        self._cursor = min(self._cursor, max(len(slots) - 1, 0))
        self._refresh_table()
        self._set_status("draft Slot deleted")

    def action_replace_file(self) -> None:
        """Open Pack Selection for the focused draft Slot.

        The picker receives a callback instead of the normal app message, so
        choosing a file only changes this modal's in-memory draft. The live
        chain and the preset row remain untouched until the user saves.
        """
        slots = self._draft.setdefault("slots", [])
        if not slots or not 0 <= self._cursor < len(slots):
            self._set_status("add or select a draft Slot first")
            return
        current = (slots[self._cursor].get("path")
                   or self._draft_candidates.get(self._cursor))
        kind = "slot"
        tone_id = None
        tone_type = None
        if current:
            suffix = Path(str(current)).suffix.lower()
            kind = "ir" if suffix == ".wav" else "amp" if suffix == ".nam" else "slot"
            try:
                siblings = library.local_models_by_tone(str(current)) or []
            except Exception:
                siblings = []
            if siblings:
                tone_id = siblings[0].get("tone_id")
                try:
                    tone_type = (library.get_tone(tone_id) or {}).get("gear")
                except Exception:
                    tone_type = None
        self.app.push_screen(TonePickerScreen(
            kind, tone_id=tone_id, tone_type=tone_type,
            on_pick=self._apply_picker_path))

    def _apply_picker_path(self, path: str | None) -> None:
        slots = self._draft.setdefault("slots", [])
        if not slots or not 0 <= self._cursor < len(slots):
            return
        slot = slots[self._cursor]
        current = slot.get("path")
        candidate = self._draft_candidates.get(self._cursor)
        if path and _same_local_path(current, path):
            self._draft_candidates[self._cursor] = path
            slot["path"] = None
            slot["model_id"] = None
            self._refresh_table()
            self._set_status("draft BYPASS · save becomes EMPTY")
            return
        if path and current is None and candidate == path:
            self._draft_candidates.pop(self._cursor, None)
        else:
            self._draft_candidates.pop(self._cursor, None)
        slot["path"] = path
        slot["model_id"] = None
        if path:
            try:
                siblings = library.local_models_by_tone(path) or []
            except Exception:
                siblings = []
            match = next((model for model in siblings
                          if model.get("local_path") == path), None)
            if match is not None:
                slot["model_id"] = match.get("id")
        self._refresh_table()
        self._set_status("draft file selected")

    def action_move_up(self) -> None:
        self._move_slot(-1)

    def action_move_down(self) -> None:
        self._move_slot(1)

    def _move_slot(self, direction: int) -> None:
        slots = self._draft.setdefault("slots", [])
        other = self._cursor + direction
        if not 0 <= other < len(slots):
            self._set_status("already at Slot boundary")
            return
        candidate = self._draft_candidates.pop(self._cursor, None)
        other_candidate = self._draft_candidates.pop(other, None)
        slots[self._cursor], slots[other] = slots[other], slots[self._cursor]
        if candidate is not None:
            self._draft_candidates[other] = candidate
        if other_candidate is not None:
            self._draft_candidates[self._cursor] = other_candidate
        self._cursor = other
        self._refresh_table()
        self._set_status("draft Slot moved")

    def _confirm(self, *, load: bool = False) -> None:
        try:
            self._commit_input_values()
            bypassed = len(self._draft_candidates)
            if self._preset_id is not None:
                updated = library.preset_update_draft_by_id(
                    self._preset_id, self._draft, self._note)
            else:
                updated = library.preset_update_draft(
                    self._preset_name, self._draft, self._note)
        except (TypeError, ValueError) as exc:
            self._set_status(str(exc))
            return
        if not updated:
            self._set_status("preset no longer exists")
            return
        self.post_message(self.Saved(
            self._preset_name, load=load, bypassed=bypassed,
            preset_id=updated.get("id")))
        self.dismiss()

    def action_save_and_load(self) -> None:
        self._confirm(load=True)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._confirm()

    def _border_hint_actions(self) -> list:
        return [
            ("enter save", self._confirm),
            ("ctrl+enter save/load", self.action_save_and_load),
            ("r choose file", self.action_replace_file),
            ("esc cancel", self.dismiss),
        ]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _action in self._border_hint_actions()]))


class PresetRenameModal(GigBuddyModal):
    CSS = "PresetRenameModal > ModalBox { width: 60%; height: auto; margin: 6 20; }"

    class Renamed(Message):
        def __init__(self, old_name: str, new_name: str,
                     preset_id: int | None = None) -> None:
            super().__init__()
            self.old_name = old_name
            self.new_name = new_name
            self.preset_id = _valid_preset_id(preset_id)

    def __init__(self, preset: dict | str) -> None:
        super().__init__()
        if isinstance(preset, dict):
            self._preset_name = str(preset.get("name") or "")
            self._preset_id = _valid_preset_id(preset.get("id"))
        else:
            self._preset_name = str(preset)
            current = library.preset_get(self._preset_name)
            self._preset_id = _valid_preset_id(current.get("id")) if current else None

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "RENAME PRESET"
        with box:
            yield Input(value=self._preset_name, id="preset-rename-input")

    def on_mount(self) -> None:
        inp = self.query_one("#preset-rename-input", Input)
        inp.focus()
        inp.action_select_all()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def _confirm(self) -> None:
        new_name = self.query_one("#preset-rename-input", Input).value.strip()
        if not new_name:
            return
        try:
            if self._preset_id is not None:
                updated = library.preset_rename_by_id(self._preset_id, new_name)
            else:
                updated = library.preset_rename(self._preset_name, new_name)
        except ValueError as e:
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, str(e), [token for token, _action in self._border_hint_actions()])
            return
        self.post_message(self.Renamed(
            self._preset_name, new_name, updated.get("id", self._preset_id)))
        self.dismiss()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._confirm()

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter rename", self._confirm),
            ("esc cancel", self.dismiss),
        ]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))


class PresetNoteModal(GigBuddyModal):
    CSS = "PresetNoteModal > ModalBox { width: 70%; height: auto; margin: 6 15; }"

    class Updated(Message):
        def __init__(self, name: str, preset_id: int | None = None) -> None:
            super().__init__()
            self.name = name
            self.preset_id = _valid_preset_id(preset_id)

    def __init__(self, name: str | dict, note: str = "") -> None:
        super().__init__()
        if isinstance(name, dict):
            self._preset_name = str(name.get("name") or "")
            self._preset_id = _valid_preset_id(name.get("id"))
            self._note = str(name.get("note") or note)
        else:
            self._preset_name = name
            current = library.preset_get(name)
            self._preset_id = _valid_preset_id(current.get("id")) if current else None
            self._note = note

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = f"EDIT NOTE · {self._preset_name}"
        with box:
            yield Input(value=self._note, placeholder="optional note", id="preset-note-input")

    def on_mount(self) -> None:
        inp = self.query_one("#preset-note-input", Input)
        inp.focus()
        inp.action_select_all()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def _confirm(self) -> None:
        note = self.query_one("#preset-note-input", Input).value.strip()
        try:
            if self._preset_id is not None:
                updated = library.preset_update_note_by_id(
                    self._preset_id, note or None)
            else:
                updated = library.preset_update_note(
                    self._preset_name, note or None)
        except ValueError as e:
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, str(e), [token for token, _action in self._border_hint_actions()])
            return
        self.post_message(self.Updated(
            self._preset_name, updated.get("id", self._preset_id)))
        self.dismiss()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._confirm()

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter save", self._confirm),
            ("esc cancel", self.dismiss),
        ]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))


class PresetDeleteModal(GigBuddyModal):
    # destructive action: fixed error red border (pinned across themes)
    CSS = """
    PresetDeleteModal > ModalBox {
        width: 55%; height: auto; margin: 7 22;
        border: round $error; border-title-color: $error;
    }
    """

    class Deleted(Message):
        def __init__(self, names: list[str], stale: list[str] | None = None,
                     preset_ids: list[int] | None = None) -> None:
            super().__init__()
            self.names = names
            self.stale = stale or []
            self.preset_ids = [preset_id for preset_id in (preset_ids or [])
                               if _valid_preset_id(preset_id) is not None]
            self.ids = self.preset_ids

    def __init__(self, targets: list[dict | str]) -> None:
        super().__init__()
        self._preset_targets = []
        for target in targets:
            if isinstance(target, dict):
                self._preset_targets.append({
                    "id": target.get("id"),
                    "name": target.get("name"),
                })
            else:
                # Compatibility for lightweight callers; real TUI rows always
                # provide an immutable id.
                preset = library.preset_get(str(target))
                self._preset_targets.append({
                    "id": preset.get("id") if preset else None,
                    "name": str(target),
                })
        self._preset_names = [str(target["name"])
                              for target in self._preset_targets]

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "DELETE PRESET"
        with box:
            shown = ", ".join(self._preset_names[:5])
            if len(self._preset_names) > 5:
                shown += f" +{len(self._preset_names) - 5} more"
            yield NonSelectableStatic(
                f"Delete {len(self._preset_names)} preset(s)?\n{shown}\n"
                "[b $error]This cannot be undone.[/]",
                id="preset-delete-body")

    def on_mount(self) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _action in self._border_hint_actions()])

    def _confirm(self) -> None:
        deleted: list[str] = []
        stale: list[str] = []
        deleted_ids: list[int] = []
        for target in self._preset_targets:
            preset_id = target.get("id")
            if isinstance(preset_id, int) and not isinstance(preset_id, bool):
                result = library.preset_delete_by_id(preset_id)
                if result.get("deleted"):
                    deleted.append(str(result.get("name") or target["name"]))
                    deleted_ids.append(preset_id)
                elif result.get("stale"):
                    stale.append(str(target["name"]))
            else:
                current = library.preset_get(str(target["name"]))
                if library.preset_delete(str(target["name"])):
                    deleted.append(str(target["name"]))
                    current_id = current.get("id") if current else None
                    if _valid_preset_id(current_id) is not None:
                        deleted_ids.append(current_id)
                else:
                    stale.append(str(target["name"]))
        if not deleted and stale:
            box = self.query_one(ModalBox)
            set_border_hint_layout(
                box, "stale: " + ", ".join(stale),
                [token for token, _action in self._border_hint_actions()])
            return
        self.post_message(self.Deleted(deleted, stale, deleted_ids))
        self.dismiss()

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        return [
            ("enter delete", self._confirm),
            ("esc cancel", self.dismiss),
        ]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))
