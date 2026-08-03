"""Pack install screen: preview a remote TONE3000 pack's model files, pick which
to install (space per-row, a = all/none), Enter installs, completion is reported
back to the app (toast + library refresh).

Remote search rows now open this screen instead of importing everything blindly.
The tone's full metadata sits beside the file list for comparison.
"""
import asyncio
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, ProgressBar, Static

from .marquee import MarqueeBar
from .metadata import metadata_table
from .modals import ClickSelectTable, GigBuddyModal, ModalBox

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402
import tone3000  # noqa: E402


def _escape(text: str) -> str:
    """Escape EVERY '[' for Textual markup — rich.markup.escape lets through
    tag-shaped brackets like '[Hi Gain]' which swallow the text."""
    return text.replace("[", "\\[")


class PackInstallScreen(GigBuddyModal):
    """Pick model files of a pack to install; Enter installs the selection."""

    BINDINGS = [
        Binding("space", "toggle_row", "select", show=False),
        Binding("a", "toggle_all", "all/none", show=False),
    ]

    CSS = """
    PackInstallScreen > ModalBox { width: 96%; height: 92%; margin: 1 2; }
    #pack-split { height: 1fr; }
    #pack-left { width: 3fr; layout: vertical; }
    #pack-right { width: 2fr; border-left: solid $primary; }
    #pack-header { height: 3; padding: 0 1; color: $text; }
    #pack-table { height: 1fr; }
    #pack-status { height: 2; padding: 0 1; color: $text-muted; }
    #pack-progress { height: 1; }
    """

    class Installed(Message):
        def __init__(self, tone_id: int, count: int) -> None:
            super().__init__()
            self.tone_id = tone_id
            self.count = count

    def __init__(self, tone: dict) -> None:
        super().__init__()
        self._tone = tone
        self._models: list[dict] = []
        self._selected: set[int] = set()

    def compose(self) -> ComposeResult:
        t = self._tone
        box = ModalBox()
        box.border_title = "INSTALL PACK"
        box.border_subtitle = "space select · a all/none · Enter install · Esc cancel"
        with box:
            with Horizontal(id="pack-split"):
                with Vertical(id="pack-left"):
                    yield Static(
                        f"[b]{_escape(t.get('title') or '')}[/b]  [dim]@{t.get('username')} · "
                        f"{t.get('gear')} · dl {t.get('downloads_count')}[/dim]",
                        id="pack-header")
                    yield MarqueeBar(id="pack-marquee")
                    table = ClickSelectTable(id="pack-table", cursor_type="row")
                    table.add_column("✓", key="pick", width=3)
                    table.add_column("Model file", key="name")
                    table.add_column("Architecture", key="arch", width=16)
                    yield table
                    yield Static("Loading pack contents…", id="pack-status")
                    yield ProgressBar(total=1, show_eta=False, id="pack-progress")
                with VerticalScroll(id="pack-right"):
                    # tone metadata side-by-side for comparison while picking
                    yield Static(metadata_table(self._tone), id="pack-detail")

    def on_mount(self) -> None:
        self.query_one("#pack-progress", ProgressBar).display = False
        t = self._tone
        self.run_worker(self._load_models(t["id"], t.get("gear") == "cab"),
                        name="pack-load")

    async def _load_models(self, tone_id: int, is_ir: bool) -> None:
        status = self.query_one("#pack-status", Static)
        try:
            ms = await asyncio.to_thread(
                tone3000.models, tone_id, a2_only=not is_ir)
        except Exception as e:
            status.update(f"Failed to load pack contents: {e}")
            return
        self._models = ms
        self._selected = {m["id"] for m in ms}
        table = self.query_one("#pack-table", DataTable)
        table.clear()
        for m in sorted(ms, key=lambda x: x["id"]):
            arch = m.get("architecture") or "IR"
            name = self._model_name(m)
            clipped = name if len(name) <= 56 else name[:55] + "…"
            table.add_row("[✓]", clipped, arch, key=str(m["id"]))
        status.update(f"{len(ms)} model file(s) — select which to install "
                      f"(all selected by default)")
        table.focus()
        self._publish_focus_marquee()

    @staticmethod
    def _model_name(m: dict) -> str:
        """Semantic name from the models.name column (web zip naming); fall back
        to the storage basename for legacy responses."""
        name = m.get("name")
        if name:
            return name
        url = m.get("model_url") or ""
        return url.rstrip("/").rsplit("/", 1)[-1] or f"model {m['id']}"

    # ---- key handling -----------------------------------------------------

    def action_toggle_row(self) -> None:
        table = self.query_one("#pack-table", DataTable)
        if table.row_count == 0:
            return
        row_key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        mid = int(row_key)
        checked = mid not in self._selected
        if checked:
            self._selected.add(mid)
        else:
            self._selected.discard(mid)
        table.update_cell(row_key, "pick", "[✓]" if checked else "[ ]")
        self._update_status()

    def action_toggle_all(self) -> None:
        table = self.query_one("#pack-table", DataTable)
        if table.row_count == 0:
            return
        all_selected = len(self._selected) == len(self._models)
        if all_selected:
            self._selected.clear()
        else:
            self._selected = {m["id"] for m in self._models}
        for i in range(table.row_count):
            row_key = table.coordinate_to_cell_key((i, 0)).row_key.value
            table.update_cell(row_key, "pick", "[✓]" if int(row_key) in self._selected else "[ ]")
        self._update_status()

    def _publish_focus_marquee(self) -> None:
        rows = self.query_one("#pack-table", DataTable).ordered_rows
        if rows:
            self.on_data_table_row_highlighted(
                type("E", (), {"row_key": type("K", (), {"value": rows[0].key.value})})())

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Focused row's full model name scrolls in the pack marquee."""
        table = self.query_one("#pack-table", DataTable)
        rows = table.ordered_rows
        name = ""
        if 0 <= table.cursor_row < len(rows):
            mid = int(rows[table.cursor_row].key.value)
            m = next((x for x in self._models if x["id"] == mid), None)
            if m:
                name = self._model_name(m)
        self.query_one("#pack-marquee", MarqueeBar).content = name

    def _update_status(self) -> None:
        n = len(self._selected)
        self.query_one("#pack-status", Static).update(
            f"{n}/{len(self._models)} selected — Enter to install")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter (table focused) = confirm, same path as the modal-level binding."""
        self._confirm()

    def on_click(self, event) -> None:
        """Single click focuses; double click on the file table confirms the
        selection (like Enter). Clicks outside the table never install."""
        if getattr(event, "chain", 1) >= 2 and event.screen_x is not None:
            table = self.query_one("#pack-table", ClickSelectTable)
            if table.region.contains(event.screen_x, event.screen_y):
                event.stop()
                self._confirm()

    def _confirm(self) -> None:
        """Enter: install the selected model files."""
        t = self._tone
        if not self._models:
            return
        sel = sorted(self._selected)
        if not sel:
            self.query_one("#pack-status", Static).update(
                "Nothing selected — space to pick files, a to select all")
            return
        self.run_worker(self._install(t["id"], sel), name="pack-install", exclusive=True)

    async def _install(self, tone_id: int, model_ids: list[int]) -> None:
        status = self.query_one("#pack-status", Static)
        bar = self.query_one("#pack-progress", ProgressBar)
        bar.update(total=max(len(model_ids), 1), progress=0)
        bar.display = True
        status.update(f"Installing {len(model_ids)} file(s)…")

        def progress(done: int, total: int, filename: str) -> None:
            self.app.call_from_thread(
                self._show_progress, done, total, filename)

        try:
            t = await asyncio.to_thread(
                library.import_tone, tone_id, progress, quiet=True,
                model_ids=model_ids)
        except Exception as e:
            status.update(f"Install failed: {e}")
            bar.display = False
            return
        if not t:
            status.update(f"TONE3000 has no tone {tone_id}")
            bar.display = False
            return
        bar.display = False
        self.post_message(self.Installed(tone_id, len(model_ids)))
        self.dismiss()

    def _show_progress(self, done: int, total: int, filename: str) -> None:
        self.query_one("#pack-progress", ProgressBar).update(
            total=max(total, 1), progress=done)
        self.query_one("#pack-status", Static).update(
            f"Installing {done}/{total}  {filename}")
