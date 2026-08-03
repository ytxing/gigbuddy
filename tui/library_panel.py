"""Library browser panel: two tabs — LOCAL (imported tones) / TONE3000 (search).

Row keys encode the source ("local:<id>" / "remote:<id>"). Selecting a local row
opens the tone action screen; a remote row opens the pack install screen.
TONE3000 hits are tagged with their local download state (✓ all / ◐ partial /
○ none) by comparing model ids against the local library.
"""
import asyncio
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import (DataTable, Input, ProgressBar, Select, Static,
                             TabbedContent, TabPane)

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402

from .install_screen import PackInstallScreen  # noqa: E402
from .marquee import MarqueeBar  # noqa: E402


def _arch(t: dict) -> str:
    """Architecture tag: A2 / A1 / IR / —"""
    if t.get("a2_models_count"):
        return "A2"
    if t.get("a1_models_count"):
        return "A1"
    if t.get("irs_count"):
        return "IR"
    return "—"


def _uploaded(t: dict) -> str:
    s = t.get("published_at") or t.get("created_at") or ""
    return s[:10] if s else ""


def _clip(text: str, n: int = 40) -> str:
    """Truncate with an ellipsis so the table shows 缩写 for overflow, and the
    focused row's full title scrolls in the detail-pane marquee instead."""
    return text if len(text) <= n else text[:n - 1] + "…"


def _parse_query(q: str) -> tuple[str, list[str], list[str]]:
    """Split a search box query: @author → usernames, #tag → tag_names, rest → words."""
    users, tags, words = [], [], []
    for tok in q.split():
        if tok.startswith("@"):
            users.append(tok[1:])
        elif tok.startswith("#"):
            tags.append(tok[1:])
        else:
            words.append(tok)
    return " ".join(words), users, tags


class ToneSelected(Message):
    """A row in the library was selected — app shows the detail pane"""

    def __init__(self, tone_id: int) -> None:
        super().__init__()
        self.tone_id = tone_id


class ToneHighlighted(Message):
    """A highlighted row changed — update detail without opening an action."""

    def __init__(self, tone: dict | None) -> None:
        super().__init__()
        self.tone = tone


class LibrarySearchInput(Input):
    """Search input with the usual selector shortcut: Down enters the results."""

    BINDINGS = [
        *Input.BINDINGS,
        Binding("down", "focus_results", "results", show=False),
        Binding("escape", "cancel_search", "cancel", show=False),
    ]

    def action_focus_results(self) -> None:
        panel = self.screen.query_one(LibraryPanel)
        table = panel.table_for(self.id)
        table.focus()

    def action_cancel_search(self) -> None:
        self.screen.query_one(LibraryPanel).action_reset()


class LibraryTable(DataTable):
    """Row table whose horizontal keys move between the two main columns."""

    BINDINGS = [
        Binding("enter", "select_cursor", "select", show=False),
        Binding("up", "cursor_up", "up", show=False),
        Binding("down", "cursor_down", "down", show=False),
        Binding("left", "focus_search", "search", show=False),
        Binding("right", "cursor_right", "right", show=False),
        Binding("pageup", "page_up", "page up", show=False),
        Binding("pagedown", "page_down", "page down", show=False),
        Binding("home", "scroll_home", "first", show=False),
        Binding("end", "scroll_end", "last", show=False),
        Binding("escape", "reset_library", "back", show=False),
    ]

    def on_click(self, event) -> None:
        """Single click focuses (cursor move, handled by DataTable); double
        click acts like Enter (open picker / pack install screen)."""
        if getattr(event, "chain", 1) >= 2:
            self.action_select_cursor()
            event.stop()

    def action_focus_search(self) -> None:
        panel = self.screen.query_one(LibraryPanel)
        search = panel.search_for(self.id)
        search.focus()

    def action_cursor_up(self) -> None:
        if self.cursor_row == 0:
            self.action_focus_search()
        else:
            super().action_cursor_up()

    def action_reset_library(self) -> None:
        self.screen.query_one(LibraryPanel).action_reset()


TYPE_CHOICES = [("All types", "all"), ("Amp", "amp"), ("Cab", "cab"),
                ("Amp + Cab", "amp-cab")]


class LibraryPanel(Vertical):
    """Left panel: LOCAL tab (imported tones) / TONE3000 tab (search + install)."""

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "LIBRARY"
        self.border_subtitle = "LOCAL · TONE3000 — ↑↓ browse · Enter open"

    @staticmethod
    def _make_table(table_id: str) -> LibraryTable:
        table = LibraryTable(id=table_id, cursor_type="row")
        table.add_column("Title", key="title")
        table.add_column("Type", key="type")
        table.add_column("DL", key="downloads")
        table.add_column("Fav", key="favorites")
        table.add_column("Arch", key="arch")
        table.add_column("Files", key="files")
        table.add_column("Up", key="uploaded")
        table.add_column("Author", key="author")
        return table

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="pane-local"):
            with TabPane("LOCAL", id="pane-local"):
                yield LibrarySearchInput(
                    placeholder="Filter title, author, description (local library)",
                    id="local-search")
                with Horizontal(id="type-filter-row"):
                    yield Static("TYPE", classes="filter-label")
                    yield Select(TYPE_CHOICES, value="all", allow_blank=False,
                                 compact=True, id="type-filter-local")
                yield MarqueeBar(id="lib-marquee")
                yield self._make_table("lib-table-local")
            with TabPane("TONE3000", id="pane-tone"):
                yield LibrarySearchInput(
                    placeholder="Search TONE3000 (title, author, description)",
                    id="tone-search")
                with Horizontal(id="type-filter-row"):
                    yield Static("TYPE", classes="filter-label")
                    yield Select(TYPE_CHOICES, value="all", allow_blank=False,
                                 compact=True, id="type-filter-tone")
                yield self._make_table("lib-table-tone")
                yield Static("", id="tone-status")
                yield ProgressBar(total=1, show_eta=False, id="import-progress")
            with TabPane("TOP CREATORS", id="pane-creators"):
                yield Static("", id="creators-status")
                yield self._make_table("lib-table-creators")

    def on_mount(self) -> None:
        self._mode = "local"
        self._active_pane = "pane-local"
        self._last_active = "pane-local"  # initial state: first tick is a no-op
        self._type_filter = "all"
        self._author_filter: str | None = None
        self._query = ""
        self._users: list[str] = []
        self._tags: list[str] = []
        self._fingerprint: tuple | None = None
        self._remote_tones: dict[int, dict] = {}
        self._highlighted_key: str | None = None
        self.query_one("#import-progress", ProgressBar).display = False
        self.refresh_rows()

    # ---- tab / table routing ---------------------------------------------

    def _table(self) -> DataTable:
        active = getattr(self, "_active_pane", None) or self.query_one(TabbedContent).active
        if active == "pane-creators":
            return self.query_one("#lib-table-creators", DataTable)
        return self.query_one("#lib-table-local" if active == "pane-local"
                              else "#lib-table-tone", DataTable)

    def table_for(self, input_id: str | None) -> DataTable:
        return self.query_one("#lib-table-local" if input_id == "local-search"
                              else "#lib-table-tone", DataTable)

    def search_for(self, table_id: str | None) -> Input:
        return self.query_one("#local-search" if table_id == "lib-table-local"
                              else "#tone-search", Input)

    def check_active_tab(self) -> None:
        """Tick-driven tab detection (0.1s): the reactive `active` value is
        always current here, unlike TabActivated events whose pane/active can
        lag during a switch. Drives trending/creators loading and routing."""
        try:
            active = self.query_one(TabbedContent).active
        except Exception:
            return
        open("/tmp/check.log", "a").write(f"CHECK active={active!r} last={getattr(self, '_last_active', None)!r}\n")
        if active == getattr(self, "_last_active", None):
            return
        self._last_active = active
        self._active_pane = active
        self._mode = "local" if active == "pane-local" else "tone"
        self._highlighted_key = None
        if active == "pane-tone" and not self.query_one("#lib-table-tone", DataTable).row_count:
            self.run_worker(self._show_trending(), name="trending", exclusive=True)
        elif active == "pane-creators" and not self.query_one("#lib-table-creators", DataTable).row_count:
            self.run_worker(self._show_top_creators(), name="creators", exclusive=True)
        elif active == "pane-local":
            self._fingerprint = None
            self.refresh_rows()
        table = self._table()
        table.focus()
        self._publish_highlight(table)

    def focus_search(self) -> None:
        self.search_for(self._table().id).focus()

    def action_reset(self) -> None:
        """Escape: clear the active tab's input and stay on LOCAL."""
        search = self.search_for(self._table().id)
        search.value = ""
        if self._mode != "local":
            # Programmatic `tabs.active = ...` rolls back in Textual (the Tabs
            # watcher re-posts a stale TabActivated), so activate the tab the
            # same way a user click does: post Tab.Clicked.
            tab = self.query_one("#--content-tab-pane-local")
            tab.post_message(tab.Clicked(tab))
            self._mode = "local"
            self._fingerprint = None
            self.query_one("#tone-status", Static).update("")
            self.refresh_rows()
        self._table().focus()

    # ---- local tab --------------------------------------------------------

    def refresh_rows(self) -> None:
        """Reload local rows from the DB (called on tick so external imports appear).

        Skips repaint unless the DB actually changed (row count / max id), so the
        user's browsing position and the TONE3000 tab are not clobbered.
        """
        # _mode can lag behind inside tab-activation handlers; _active_pane is
        # set first and is the authoritative "which tab is showing" signal.
        if getattr(self, "_active_pane", "pane-local") != "pane-local":
            return
        with library.connect() as conn:
            fp = tuple(conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM tones").fetchone())
        if fp == self._fingerprint:
            return
        self._fingerprint = fp
        self._remote_tones = {}
        table = self.query_one("#lib-table-local", DataTable)
        table.clear()
        tones = library.list_tones(
            gear=None if self._type_filter == "all" else self._type_filter,
            limit=200,
            author=self._author_filter,
            tag=self._tags[0] if self._tags else None,
            query=self._query or None,
            has_files=True)
        for t in tones:
            table.add_row(*self._row_cells(t), key=f"local:{t['id']}")
        if not tones:
            table.add_row("(empty library — switch to TONE3000 to search and import)",
                          "", "", "", "", "", "", "", key=None)
        self._publish_highlight(table)

    def _show_local_filter(self, query: str) -> None:
        self._query = query
        self._fingerprint = None
        self.refresh_rows()
        self.query_one("#lib-table-local", DataTable).focus()

    # ---- TONE3000 tab -----------------------------------------------------

    async def _show_search(self, query: str) -> None:
        self._query = query
        words, users, tags = _parse_query(query)
        self._users, self._tags = users, tags
        table = self.query_one("#lib-table-tone", DataTable)
        status = self.query_one("#tone-status", Static)
        table.clear()
        self._remote_tones = {}
        status.update("Searching TONE3000…")
        try:
            hits = await asyncio.to_thread(
                library.tone3000.search,
                words, page_size=50,
                gear_filters=None if self._type_filter == "all" else [self._type_filter],
                usernames=(users or ([self._author_filter] if self._author_filter else None)),
                tag_names=tags or None)
        except Exception as e:
            status.update(f"Search failed: {e}")
            self._highlighted_key = None
            self.post_message(ToneHighlighted(None))  # explicit clear
            return
        # 按 model id 对比本地库：标记 ✓ 全部下载 / ◐ 部分 / ○ 未下载
        hits = await asyncio.to_thread(library.mark_download_state, hits)
        for t in hits:
            self._remote_tones[int(t["id"])] = t
            table.add_row(*self._row_cells(t), key=f"remote:{t['id']}")
        extra = []
        if self._author_filter:
            extra.append(f"author {self._author_filter}")
        if users:
            extra.append("@" + ", @".join(users))
        if tags:
            extra.append("#" + ", #".join(tags))
        hint = f" ({' '.join(extra)})" if extra else ""
        status.update(f"(TONE3000{hint} — ✓ 已下载全部 ◐ 部分 ○ 未下载 · Enter 安装)")
        self._publish_highlight(table)
        table.focus()

    # ---- recommended views (TONE3000 tab) ---------------------------------

    async def _show_trending(self) -> None:
        """TONE3000's default order IS downloads-all-time — an empty query is the
        trending feed."""
        await self._show_search("")
        self.query_one("#tone-status", Static).update(
            "(TRENDING — TONE3000 all-time downloads · Enter 安装)")

    async def _show_top_creators(self, limit: int = 5) -> None:
        """Aggregate a large page of hits by username → top creators; clicking a
        creator row searches only that author's tones. Shows the first `limit`
        rows (default 5) plus a MORE row; clicking MORE lists everyone."""
        from collections import Counter

        status = self.query_one("#creators-status", Static)
        table = self.query_one("#lib-table-creators", DataTable)
        table.clear()
        self._remote_tones = {}
        status.update("Loading top creators…")
        try:
            hits = await asyncio.to_thread(
                library.tone3000.search, "", page_size=100)
        except Exception as e:
            status.update(f"Failed to load creators: {e}")
            return
        by_user: dict[str, list[dict]] = {}
        for t in hits:
            u = t.get("username")
            if u:
                by_user.setdefault(u, []).append(t)
        ranked = sorted(by_user.items(), key=lambda kv: -len(kv[1]))
        shown = ranked if limit <= 0 else ranked[:limit]
        for name, tones_ in shown:
            total_dl = sum(t.get("downloads_count") or 0 for t in tones_)
            table.add_row(name, "CREATOR", str(len(tones_)), str(total_dl),
                          "—", "—", "—", f"@{name}", key=f"creator:{name}")
        if limit > 0 and len(ranked) > limit:
            table.add_row(f"＋ MORE ({len(ranked) - limit} more creators)",
                          "", "", "", "", "", "", "", key="creators-more")
        if not shown:
            table.add_row("(no creator data)", "", "", "", "", "", "", "", key=None)
        status.update("(TOP CREATORS · 点击查看其音色 · MORE 展开全部)")
        self._publish_highlight(table)
        table.focus()

    # ---- shared row rendering ---------------------------------------------

    @staticmethod
    def _row_cells(t: dict) -> list[str]:
        title = _clip(t.get("title") or "")
        state = t.get("download_state")
        if state == "all":
            title = f"[bold $success]✓[/] {title}"
        elif state == "partial":
            title = f"[bold $warning]◐[/] {title}"
        elif state == "none":
            title = f"[dim]○[/] {title}"
        # A2 下载目标：amp 用 a2_models_count，cab 无 A2 用 models_count
        total = t.get("a2_models_count") or t.get("models_count") or 0
        files = str(total)
        if t.get("downloaded") is not None and total:
            files = f"{t['downloaded']}/{total}"
        return [
            title, t.get("gear") or "?",
            str(t.get("downloads_count") or 0), str(t.get("favorites_count") or 0),
            _arch(t), files, _uploaded(t),
            f"@{t.get('username') or '?'}",
        ]

    def _tone_for_key(self, key: str | None) -> dict | None:
        if not key:
            return None
        kind, _, tone_id = key.partition(":")
        try:
            tone_num = int(tone_id)
        except ValueError:
            return None
        if kind == "remote":
            return self._remote_tones.get(tone_num)
        if kind == "local":
            return library.get_tone(tone_num)
        return None

    def _publish_highlight(self, table: DataTable) -> None:
        """Send the current row's metadata to the detail pane without side effects."""
        rows = table.ordered_rows
        key_value = None
        if 0 <= table.cursor_row < len(rows):
            key_value = rows[table.cursor_row].key.value
        if key_value == "__clear__":
            # explicit clear (search failed); reset so the next search can
            # publish again
            self._highlighted_key = None
            self.post_message(ToneHighlighted(None))
            return
        if key_value == self._highlighted_key:
            return
        self._highlighted_key = key_value

        tone = self._tone_for_key(key_value)
        # Repaint races can resolve a key to no tone (e.g. _remote_tones cleared
        # mid-flight) — never blank the detail pane for those.
        if key_value and tone:
            self.post_message(ToneHighlighted(tone))
            title = (tone or {}).get("title") or ""
            self.query_one("#lib-marquee", MarqueeBar).content = title or None

    # ---- import (TONE3000 tab) ----------------------------------------------

    async def _import_and_select(self, tone_id: int) -> None:
        """Import a remote tone (metadata + models), then surface it in the UI."""
        status = self.query_one("#tone-status", Static)
        bar = self.query_one("#import-progress", ProgressBar)
        bar.update(total=1, progress=0)
        bar.display = True
        status.update(f"Importing tone {tone_id}…")

        def progress(done: int, total: int, filename: str) -> None:
            self.app.call_from_thread(
                self._show_import_progress, tone_id, done, total, filename)

        try:
            # Downloads and SQLite work are blocking; keep Textual's event loop
            # free so focus, arrows, and repaint continue during an import.
            t = await asyncio.to_thread(
                library.import_tone, tone_id, progress, quiet=True)
        except Exception as e:
            status.update(f"Import failed: {e}")
            bar.display = False
            return
        if not t:
            status.update(f"TONE3000 has no tone {tone_id}")
            bar.display = False
            return
        count = len(t.get("models") or [])
        bar.update(total=max(count, 1), progress=max(count, 1))
        status.update(f"Imported tone {tone_id}: {count} file(s) — 在 LOCAL 标签页查看")
        self._fingerprint = None  # force repaint with the new row
        self.refresh_rows()
        self.post_message(ToneSelected(tone_id))

    def _show_import_progress(self, tone_id: int, done: int, total: int,
                              filename: str) -> None:
        self.query_one("#import-progress", ProgressBar).update(
            total=max(total, 1), progress=done)
        self.query_one("#tone-status", Static).update(
            f"Importing tone {tone_id}: {done}/{total}  {filename}")

    # ---- widget events ------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if event.input.id == "local-search":
            if query:
                self._show_local_filter(query)
            else:
                self._query = ""
                self._fingerprint = None
                self.refresh_rows()
        elif event.input.id == "tone-search":
            if query:
                self.run_worker(self._show_search(query), name="search", exclusive=True)
            else:
                self.action_reset()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id not in ("type-filter-local", "type-filter-tone"):
            return
        self._type_filter = str(event.value)
        if event.select.id == "type-filter-tone":
            if self._query:
                self._show_search(self._query)
            else:
                self.query_one("#lib-table-tone", DataTable).clear()
                self.query_one("#tone-status", Static).update(
                    "(TONE3000 — type a query to search)")
        else:
            self._fingerprint = None
            self.refresh_rows()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        col = event.column_key.value
        is_tone = event.control.id == "lib-table-tone"
        if col == "type":
            values = ["all", "amp", "cab", "amp-cab"]
            current = values.index(self._type_filter)
            self.query_one("#type-filter-tone" if is_tone else "#type-filter-local",
                           Select).value = values[(current + 1) % len(values)]
            return
        if col == "author":
            # cycle: no filter → cursor row's author → clear
            if self._author_filter:
                self._author_filter = None
            else:
                table = event.control
                rows = table.ordered_rows
                if 0 <= table.cursor_row < len(rows):
                    key = rows[table.cursor_row].key.value
                    t = self._tone_for_key(key)
                    if t and t.get("username"):
                        self._author_filter = t["username"]
            if is_tone:
                if self._query:
                    self._show_search(self._query)
            else:
                self._fingerprint = None
                self.refresh_rows()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # Only the active tab's table drives the detail pane. The hidden tab's
        # table fires highlight events during tab switches (cursor resets,
        # clears) that would clobber the visible pane; TabPane display state
        # lags behind the switch, so match the pane id against _active_pane.
        pane = next((a for a in event.data_table.ancestors
                     if isinstance(a, TabPane)), None)
        if pane is None or pane.id != self._active_pane:
            return
        # table.clear() fires a highlight with no row key — that's a repaint
        # artifact, not a user action; ignore it so the detail pane isn't blanked
        # behind a freshly filled table (row_key can be None here).
        key = event.row_key.value if event.row_key else None
        if not key or key == self._highlighted_key:
            return
        self._highlighted_key = key
        self.post_message(ToneHighlighted(self._tone_for_key(key)))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        if not key:
            return
        if key == "creators-more":
            # MORE row on the creators tab → list everyone
            self.run_worker(self._show_top_creators(limit=0), name="creators",
                            exclusive=True)
            return
        kind, _, tid = key.partition(":")
        if kind == "local":
            self.post_message(ToneSelected(int(tid)))
        elif kind == "creator":
            # top-creators row → jump to the TONE3000 tab searching that author
            tab = self.query_one("#--content-tab-pane-tone")
            tab.post_message(tab.Clicked(tab))
            self.run_worker(self._show_search(f"@{tid}"), name="search", exclusive=True)
        else:
            # Enter on a remote hit → pack install screen (preview files, pick subset)
            tone = self._remote_tones.get(int(tid))
            if tone:
                self.app.push_screen(PackInstallScreen(tone))
