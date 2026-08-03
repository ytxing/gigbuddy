"""Tone/IR picker screen: library list + TONE3000 search, pick to hot-swap.

Local rows come from the SQLite library (models with local files); remote hits are
imported through the same library.import_tone path as the CLI (metadata + download),
so any tone picked here is persisted in data/gigbuddy.db.
"""
import asyncio
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Input, Static, Tree

from .modals import ClickSelectTree, GigBuddyModal, ModalBox
from .metadata import metadata_table

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402


class PickerSearchInput(Input):
    BINDINGS = [
        *Input.BINDINGS,
        Binding("down", "focus_results", "results", show=False),
    ]

    def action_focus_results(self) -> None:
        self.screen.query_one("#pick-tree", Tree).focus()


class PickerTree(ClickSelectTree):
    BINDINGS = [
        *ClickSelectTree.BINDINGS,
        Binding("left", "collapse_or_parent", "back", show=False, priority=True),
        Binding("right", "expand_or_child", "open", show=False, priority=True),
    ]

    def action_collapse_or_parent(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.children and node.is_expanded:
            node.collapse()
        elif node.parent is not None and not node.parent.is_root:
            self.move_cursor(node.parent)
        else:
            self.screen.query_one("#pick-search", Input).focus()

    def action_expand_or_child(self) -> None:
        node = self.cursor_node
        if node is None or not node.children:
            return
        if node.is_collapsed:
            node.expand()
        else:
            self.move_cursor(node.children[0])


class TonePickerScreen(GigBuddyModal):
    """Pick an amp (.nam) or IR (.wav): library list first, search box switches to TONE3000"""

    CSS = """
    TonePickerScreen > ModalBox { width: 92%; height: 92%; margin: 2 4; }
    #pick-search { height: 3; }
    #pick-tree { height: 2fr; }
    #pick-detail {
        height: 1fr; min-height: 11; border-top: solid $primary;
        padding: 0 1; overflow-y: auto;
    }
    #pick-status { height: 2; color: $text-muted; }
    """

    class Picked(Message):
        def __init__(self, kind: str, path: str | None,
                     tone_type: str | None = None) -> None:
            super().__init__()
            self.kind = kind
            self.path = path
            self.tone_type = tone_type

    def __init__(self, kind: str, tone_id: int | None = None,
                 tone_type: str | None = None) -> None:
        super().__init__()
        self.kind = kind  # "amp" | "ir"
        self.tone_id = tone_id
        self.tone_type = tone_type

    def compose(self) -> ComposeResult:
        title = "AMP tone" if self.kind == "amp" else "IR"
        box = ModalBox()
        box.border_title = f"PICK {title.upper()}"
        if self.tone_id is not None:
            tone = library.get_tone(self.tone_id) or {}
            if tone.get("title"):
                box.border_title = f"{tone['title']}"
                box.border_subtitle = f"pick {title} file · Enter · Esc back"
        with box:
            hint = ("↑↓ browse files · Enter pick · Esc back" if self.tone_id
                    is not None else "← search · ↑↓ browse · Enter pick · Esc cancel")
            yield Static(hint, classes="modal-hint")
            yield PickerSearchInput(
                placeholder="Search TONE3000 (e.g. 'marshall plexi' / 'greenback 1960')",
                id="pick-search")
            tree = PickerTree("Library", id="pick-tree")
            tree.show_root = False
            yield tree
            yield Static("", id="pick-detail")
            yield Static("", id="pick-status")

    def on_mount(self) -> None:
        if self.tone_id is not None:
            self.query_one("#pick-search", Input).display = False
        self._fill_local(expand_tone_id=self.tone_id)
        self.query_one("#pick-tree", Tree).focus()

    def _fill_local(self, expand_tone_id: int | None = None) -> None:
        tree = self.query_one("#pick-tree", Tree)
        tree.reset("Library")
        tree.root.expand()
        if self.kind == "ir" and self.tone_id is None:
            tree.root.add_leaf("— no IR (bypass) —", {"type": "bypass"})
        items = library.list_local_models("ir" if self.kind == "ir" else "amp")
        if self.tone_id is not None:
            items = [item for item in items if item["tone_id"] == self.tone_id]
        folders = {}
        for model in items:
            tone_id = model["tone_id"]
            if tone_id not in folders:
                label = f"{model.get('title') or 'Untitled'}  [dim]@{model.get('username') or '?'}[/dim]"
                folders[tone_id] = tree.root.add(
                    label, {"type": "tone", "tone_id": tone_id, "model": model},
                    expand=tone_id == expand_tone_id)
            folders[tone_id].add_leaf(
                Path(model["local_path"]).name, {"type": "model", "model": model})
        if self.kind != "ir" and not items:
            tree.root.add_leaf("(no local tones — search above)", None)
        if tree.root.children:
            first = tree.root.children[0]
            if expand_tone_id is not None and len(first.children) > 0:
                first.expand()
                tree.call_after_refresh(tree.move_cursor, first.children[0])
            else:
                tree.move_cursor(first)

    def _fill_search(self, query: str) -> None:
        tree = self.query_one("#pick-tree", Tree)
        tree.reset("TONE3000 results")
        tree.root.expand()
        try:
            # IR picker only searches cabs — an amp search hit has no IR models
            rows = library.tone3000.search(
                query, page_size=50,
                gear_filters=["cab"] if self.kind == "ir" else None)
        except Exception as e:
            tree.root.add_leaf(f"(search failed: {e})", None)
            return
        rows = library.mark_download_state(rows)
        for t in rows:
            detail = (f"dl={t.get('downloads_count', 0)} gear={t.get('gear', '?')} "
                      f"@{t.get('username', '')}")
            state = t.get("download_state")
            mark = {"all": "[bold $success]✓[/] ",
                    "partial": "[bold $warning]◐[/] ",
                    "none": "[dim]○[/] "}.get(state, "")
            if state == "all" or state == "partial":
                detail += f" [dim]已下载 {t.get('downloaded')}/{t.get('models_count', '?')}[/dim]"
            tree.root.add_leaf(
                f"{mark}{t.get('title', '')[:46]}  [dim]{detail}[/dim]",
                {"type": "remote", "tone": t})
        if tree.root.children:
            tree.move_cursor(tree.root.children[0])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._fill_search(query)
        else:
            self._fill_local()
        self.query_one("#pick-tree", Tree).focus()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self._show_detail(event.node.data)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if data and data.get("type") == "tone":
            event.node.toggle()
            return
        self._handle_data(data)

    def _confirm(self) -> None:
        """Enter (when the table itself isn't focused): confirm the cursor row."""
        tree = self.query_one("#pick-tree", Tree)
        if tree.cursor_node is not None:
            self._handle_data(tree.cursor_node.data)

    def _handle_data(self, data: dict | None) -> None:
        if not data:
            return
        kind = data.get("type")
        if kind == "bypass":
            self.post_message(self.Picked(self.kind, None, self.tone_type))
            self.dismiss()
            return
        if kind == "model":
            self.post_message(self.Picked(
                self.kind, data["model"]["local_path"], self.tone_type))
            self.dismiss()
            return
        if kind == "remote":
            tone_id = int(data["tone"]["id"])
            self.run_worker(
                self._import_remote(tone_id), name="picker-import", exclusive=True)

    async def _import_remote(self, tone_id: int) -> None:
        """Import a remote tone, then open its folder for an explicit model choice."""
        status = self.query_one("#pick-status", Static)
        status.update(f"Importing tone {tone_id}…")
        try:
            t = await asyncio.to_thread(library.import_tone, tone_id, quiet=True)
        except Exception as e:
            status.update(f"Import failed: {e}")
            return
        if not t:
            status.update(f"TONE3000 has no tone {tone_id}")
            return
        self.query_one("#pick-search", Input).value = ""
        self._fill_local(expand_tone_id=tone_id)
        status.update(
            f"Imported {len(t.get('models') or [])} file(s) — choose a specific file")
        self.query_one("#pick-tree", Tree).focus()

    def _show_detail(self, data: dict | None) -> None:
        detail = self.query_one("#pick-detail", Static)
        if not data:
            detail.update("")
            return
        kind = data.get("type")
        if kind == "bypass":
            detail.update(metadata_table(
                {"title": "IR bypass", "gear": "cab"},
                note="No cabinet impulse response will be applied."))
            return
        if kind == "remote":
            t = data["tone"]
            detail.update(metadata_table(
                t, note="Enter to import, then choose a specific downloaded file."))
            return
        model = data.get("model")
        if not model:
            detail.update("")
            return
        detail.update(metadata_table(model, model))
