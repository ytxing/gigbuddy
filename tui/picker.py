"""Tone/IR picker screen: library list + TONE3000 search, pick to hot-swap.

Local rows come from the SQLite library (models with local files); remote hits are
imported through the same library.import_tone path as the CLI (metadata + download),
so any tone picked here is persisted in data/gigbuddy.db.
"""
import asyncio
from functools import partial
import sys
from pathlib import Path
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.events import Leave, MouseEvent, MouseMove
from textual.message import Message
from textual.widgets import Input, Static, Tree

from .marquee import MarqueeBar, resolve_rich_style
from .modals import (ClickSelectTree, GigBuddyModal, ModalBox,
                     border_hint_action_token, border_hint_click,
                     set_border_hint_hover, set_border_hint_layout)
from .metadata import SelectableStatic, description_only, theme_colors
from .selection import NonSelectableStatic  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402


def _escape(text: str) -> str:
    """Escape every left bracket before embedding user text in Rich markup."""
    return str(text).replace("[", "\\[")


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
    .pick-detail-scroll {
        height: 1fr; min-height: 11; border-top: solid $primary;
        padding: 0 1; overflow-y: auto;
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: $secondary;
        scrollbar-color-hover: $accent;
    }
    #pick-status { height: 1; color: $text-muted; }
    """

    BINDINGS = [
        *GigBuddyModal.BINDINGS,
        Binding("r", "retry_search", "retry", show=False),
    ]

    class Picked(Message):
        def __init__(self, kind: str, path: str | None,
                     tone_type: str | None = None) -> None:
            super().__init__()
            self.kind = kind
            self.path = path
            self.tone_type = tone_type

    def __init__(self, kind: str, tone_id: int | None = None,
                 tone_type: str | None = None,
                 on_pick: Callable[[str | None], None] | None = None) -> None:
        super().__init__()
        self.kind = kind  # "amp" | "ir" | "slot"
        self.tone_id = tone_id
        self.tone_type = tone_type
        # Preset Edit uses the same local/remote picker surface but must return
        # the chosen path to its in-memory draft instead of mutating live Chain.
        self._on_pick = on_pick

    def compose(self) -> ComposeResult:
        title = {"amp": "AMP tone", "ir": "CAB", "slot": "SLOT"}.get(
            self.kind, "SLOT")
        box = ModalBox()
        box.border_title = f"PICK {title.upper()}"
        if self.tone_id is not None:
            tone = library.get_tone(self.tone_id) or {}
            if tone.get("title"):
                box.border_title = f"{tone['title']}"
        with box:
            hint = ("↑↓ browse files · enter pick · esc back" if self.tone_id
                    is not None else "← search · ↑↓ browse · enter pick · esc cancel")
            yield NonSelectableStatic(hint, classes="modal-hint")
            yield PickerSearchInput(
                placeholder="Search TONE3000 (e.g. 'marshall plexi' / 'greenback 1960')",
                id="pick-search")
            yield MarqueeBar(id="pick-marquee")
            tree = PickerTree("Library", id="pick-tree")
            tree.show_root = False
            yield tree
            yield VerticalScroll(
                SelectableStatic("", id="pick-detail"),
                classes="pick-detail-scroll")
            yield MarqueeBar(id="pick-status")

    def on_mount(self) -> None:
        self._request_generation = 0
        self._last_query = ""
        if self.tone_id is not None:
            self.query_one("#pick-search", Input).display = False
        self._fill_local(expand_tone_id=self.tone_id)
        self.query_one("#pick-tree", Tree).focus()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _ in self._border_hint_actions()])

    def on_unmount(self) -> None:
        self._request_generation = getattr(self, "_request_generation", 0) + 1

    def _fill_local(self, expand_tone_id: int | None = None) -> None:
        tree = self.query_one("#pick-tree", Tree)
        tree.reset("Library")
        tree.root.expand()
        if self.kind == "ir" and self.tone_id is None:
            tree.root.add_leaf("CAB — (none)", {"type": "bypass"})
        if self.kind == "slot":
            items = (library.list_local_models("amp")
                     + library.list_local_models("ir"))
        else:
            items = library.list_local_models(
                "ir" if self.kind == "ir" else "amp")
        if self.tone_id is not None:
            items = [item for item in items if item["tone_id"] == self.tone_id]
        folders = {}
        for model in items:
            tone_id = model["tone_id"]
            if tone_id not in folders:
                title = _escape(model.get("title") or "Untitled")
                label = f"{title}  {self._author_label(
                    model.get('username'), self.app.theme_variables)}"
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

    def _request_alive(self, generation: int, query: str | None = None) -> bool:
        return (generation == getattr(self, "_request_generation", -1)
                and bool(getattr(self, "is_mounted", False))
                and (query is None or query == getattr(self, "_last_query", "")))

    async def _search_remote(self, query: str,
                             generation: int | None = None) -> None:
        """Search off the UI loop; keep the previous result until success."""
        if generation is None:
            self._request_generation += 1
            generation = self._request_generation
        if self._request_alive(generation, query):
            self.query_one("#pick-status", MarqueeBar).content = (
                "Searching TONE3000…")
        try:
            # IR picker searches both CAB and SPACE tones; both expose IR files.
            rows = await asyncio.to_thread(
                library.tone3000.search,
                query, page_size=50,
                gear_filters=["cab", "space"] if self.kind == "ir" else None)
            rows = await asyncio.to_thread(library.mark_download_state, rows)
        except Exception as e:
            if self._request_alive(generation, query):
                self.query_one("#pick-status", MarqueeBar).content = (
                    f"Search failed: {e} · press r to retry")
            return
        if not self._request_alive(generation, query):
            return
        tree = self.query_one("#pick-tree", Tree)
        tree.reset("TONE3000 results")
        tree.root.expand()
        variables = self.app.theme_variables
        for t in rows:
            state = t.get("download_state")
            mark_style = {"all": "bold $success",
                          "partial": "bold $success",
                          "none": None}.get(state)
            mark = (f"[{resolve_rich_style(mark_style, self.app.theme_variables)}]"
                    f"✓[/] " if mark_style else "")
            title = _escape(t.get("title") or "")
            tree.root.add_leaf(
                f"{mark}{title}  [dim]dl={t.get('downloads_count', 0)} "
                f"gear={_escape(t.get('gear', '?'))}[/dim] "
                f"{self._author_label(t.get('username'), variables)}"
                + (f" [dim]downloaded {t.get('downloaded')}/"
                   f"{t.get('models_count', '?')}[/dim]"
                   if state in {"all", "partial"} else ""),
                {"type": "remote", "tone": t})
        self.query_one("#pick-status", MarqueeBar).content = (
            "no results" if not rows else "")
        if tree.root.children:
            tree.move_cursor(tree.root.children[0])

    @staticmethod
    def _author_label(username: str | None, variables: dict | None = None) -> str:
        """Render picker authors from the shared positive verification cache."""
        name = str(username or "?")
        safe_name = _escape(name)
        badge_style = resolve_rich_style("b $success", variables)
        badge = (f" [{badge_style}]✓[/]"
                 if library.tone3000.is_verified(name) else "")
        return f"[dim]@{safe_name}[/dim]{badge}"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        self._last_query = query
        if query:
            self._request_generation += 1
            self.run_worker(
                partial(self._search_remote, query, self._request_generation),
                name="picker-search", exclusive=True)
        else:
            self._fill_local()
        self.query_one("#pick-tree", Tree).focus()

    def action_retry_search(self) -> None:
        query = getattr(self, "_last_query", "")
        if not query:
            return
        self._request_generation += 1
        self.run_worker(
            partial(self._search_remote, query, self._request_generation),
            name="picker-search", exclusive=True)

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

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        close_token = "esc back" if self.tone_id is not None else "esc cancel"
        return [("enter pick", self._confirm), (close_token, self.dismiss)]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))

    def _handle_data(self, data: dict | None) -> None:
        if not data:
            return
        kind = data.get("type")
        if kind == "status":
            return
        if kind == "bypass":
            if self._on_pick is not None:
                self._on_pick(None)
                self.dismiss()
                return
            self.post_message(self.Picked(self.kind, None, self.tone_type))
            self.dismiss()
            return
        if kind == "model":
            path = data["model"]["local_path"]
            if self._on_pick is not None:
                self._on_pick(path)
                self.dismiss()
                return
            self.post_message(self.Picked(
                self.kind, path, self.tone_type))
            self.dismiss()
            return
        if kind == "remote":
            tone_id = int(data["tone"]["id"])
            self._request_generation += 1
            self.run_worker(
                partial(self._import_remote, tone_id, self._request_generation),
                name="picker-import", exclusive=True)

    async def _import_remote(self, tone_id: int,
                             generation: int | None = None) -> None:
        """Import a remote tone, then open its folder for an explicit model choice."""
        if generation is None:
            self._request_generation += 1
            generation = self._request_generation
        if self._request_alive(generation):
            self.query_one("#pick-status", MarqueeBar).content = (
                f"Importing tone {tone_id}…")
        try:
            t = await asyncio.to_thread(library.import_tone, tone_id, quiet=True)
        except Exception as e:
            if self._request_alive(generation):
                self.query_one("#pick-status", MarqueeBar).content = (
                    f"Import failed: {e}")
            return
        if not t:
            if self._request_alive(generation):
                self.query_one("#pick-status", MarqueeBar).content = (
                    f"TONE3000 has no tone {tone_id}")
            return
        publish = getattr(self.app, "_publish_mutation", None)
        if publish is not None:
            publish("import", (f"tone:{tone_id}",), t.get("revision"))
        if not self._request_alive(generation):
            return
        self.query_one("#pick-search", Input).value = ""
        self._fill_local(expand_tone_id=tone_id)
        self.query_one("#pick-status", MarqueeBar).content = (
            f"Imported {len(t.get('models') or [])} file(s) — choose a specific file")
        self.query_one("#pick-tree", Tree).focus()

    def _show_detail(self, data: dict | None) -> None:
        banner = self.query_one("#pick-marquee", MarqueeBar)
        detail = self.query_one("#pick-detail", Static)
        if not data:
            banner.content = None
            detail.update("")
            return
        colors = theme_colors(self.app)
        kind = data.get("type")
        if kind == "bypass":
            banner.content = "CAB — (none)"
            detail.update(description_only(
                {"description": "No cabinet impulse response will be applied."},
                colors=colors))
            return
        if kind == "remote":
            t = data["tone"]
            banner.content = t.get("title") or ""
            detail.update(description_only(t, colors=colors))
            return
        model = data.get("model")
        if not model:
            banner.content = None
            detail.update("")
            return
        tone = library.get_tone(model.get("tone_id")) or model
        title = tone.get("title") or ""
        filename = Path(model.get("local_path") or model.get("name") or "").name
        banner.content = " · ".join(part for part in (title, filename) if part)
        detail.update(description_only(tone, model, colors=colors))
