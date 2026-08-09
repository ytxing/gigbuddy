"""Library browser panel: LOCAL, remote sources, and creator aggregates.

Row keys encode the source ("local:<id>" / "remote:<id>"). Selecting a local row
enters the canonical DetailPane flow; remote rows enter its PACK view.
TONE3000 hits are tagged with their local download state (✓ when anything is
downloaded, blank otherwise) by comparing model ids against the local library.
"""
import asyncio
from functools import partial
import sys
from pathlib import Path
from typing import Callable

from rich.cells import cell_len
from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Leave, MouseEvent, MouseMove
from textual.message import Message
from textual.widgets import (Button, DataTable, Input, Select,
                             TabbedContent, TabPane)
from textual.widgets._tabbed_content import ContentTabs  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402

from .install_screen import PackInstallScreen  # noqa: E402
from .marquee import (MarqueeBar, ellipsis_window, marquee_window,
                      resolve_rich_style)  # noqa: E402
from .metadata import gear_markup, theme_colors, tone_format  # noqa: E402
from .modals import (ClickSelectTable, border_hint_action_token,
                     border_hint_hit, hint_span, set_border_hint_hover,
                     refresh_border_hint_layout,
                     set_border_hint_layout)  # noqa: E402
from .search_query import SearchSpec, SearchSyntaxError, parse_search  # noqa: E402
from .uninstall_screen import LocalUninstallScreen  # noqa: E402
from .mutations import (ViewAnchor, focused_widget_key,
                        view_context)  # noqa: E402
from .view_controls import SearchBar, ViewTabStrip  # noqa: E402


def _arch(t: dict) -> str:
    """Show NAM architectures; IR is a Tone format, not an architecture.

    A1 (WaveNet) 是废弃架构，产品不浏览不展示：即使本地库/远程行带有
    a1_models_count 也不出现在标签里。
    """
    labels = []
    if t.get("a2_models_count"):
        labels.append("A2")
    if t.get("custom_models_count"):
        labels.append("Custom")
    return "/".join(labels) if labels else "—"


def _uploaded(t: dict) -> str:
    s = t.get("published_at") or t.get("created_at") or ""
    return s[:10] if s else ""


def _clip(text: str, n: int = 56) -> str:
    """Keep unfocused table titles bounded; focus replaces this with marquee."""
    return ellipsis_window(text, n)


# Which TabPane id each library table belongs to. Async loads finish after
# the user may have left the pane; Textual's TabbedContent treats any focus
# inside a pane as a tab switch (TabPane.Focused → active = pane id), so a
# stale focus would yank the UI back to the tab the load was started on.
_TABLE_PANE = {
    "lib-table-local": "pane-local",
    "lib-table-tone": "pane-tone",
    "lib-table-creators": "pane-creators",
}

_SEARCH_BAR_ID = {
    "pane-local": "#local-search-bar",
    "pane-tone": "#tone-search-bar",
}


class ToneSelected(Message):
    """A row in the library was selected — app shows the detail pane"""

    def __init__(self, tone_id: int) -> None:
        super().__init__()
        self.tone_id = tone_id


class RemoteToneSelected(Message):
    """A remote row was selected for the canonical DetailPane flow."""

    def __init__(self, tone: dict) -> None:
        super().__init__()
        self.tone = tone


class ToneHighlighted(Message):
    """A highlighted row changed — update detail without opening an action."""

    def __init__(self, tone: dict | None, *, remote: bool = False) -> None:
        super().__init__()
        self.tone = tone
        self.remote = remote


class VerifiedAuthor(Message):
    """A live author verification landed; refresh visible author cells."""

    def __init__(self, username: str) -> None:
        super().__init__()
        self.username = username


class CreatorFocused(Message):
    """TOP CREATORS 行聚焦 → detail 显示作者信息 + 该作者 top 音色列表
    （REQ-012：取代"聚合首 tone"的随机单音色映射）。"""

    def __init__(self, username: str) -> None:
        super().__init__()
        self.username = username


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


class LibraryTable(ClickSelectTable):
    """Row table whose horizontal keys move between the two main columns."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._marquee_row_key: str | None = None
        self._marquee_tone: dict | None = None
        self._marquee_original_cell = None
        self._marquee_offset = 0
        self._marquee_timer = None
        # Title 列可用内容宽（不含两侧 padding）。随表格宽度自适应：
        # 窄表格压缩列宽，让 _clip 的 … 与 marquee 尾部始终可见。
        self._title_cell_limit = 54

    def clear(self, columns: bool = False):
        self._clear_title_marquee()
        return super().clear(columns)

    def on_blur(self, event) -> None:
        self._clear_title_marquee()

    def _fit_title_column(self) -> None:
        """Resize the Title column to the table's usable width (bounded).

        The other columns keep their declared widths and scroll out of view on
        narrow panes; the Title column must not overflow the table, otherwise
        its trailing ellipsis and the marquee tail get cropped by the renderer.
        """
        title = next((c for c in self.ordered_columns
                      if c.key.value == "title"), None)
        if title is None:
            return
        inner = max(self.content_region.width - 2 * self.cell_padding, 1)
        limit = max(min(inner, 54), 20)
        if limit == self._title_cell_limit:
            return
        self._title_cell_limit = limit
        title.width = limit
        self._require_update_dimensions = True
        panel = self.screen.query_one(LibraryPanel)
        for row in self.ordered_rows:
            tone = panel._tone_for_key(row.key.value)
            if tone:
                cells = panel._row_cells(tone, table=self)
                self.update_cell(row.key, "title", cells[0], update_width=False)
        self.refresh()

    def on_resize(self, event) -> None:
        self._fit_title_column()

    def _stop_title_timer(self) -> None:
        if self._marquee_timer is not None:
            self._marquee_timer.stop()
            self._marquee_timer = None

    def _restore_title_cell(self) -> None:
        if self._marquee_row_key is None or self._marquee_original_cell is None:
            return
        try:
            self.update_cell(
                self._marquee_row_key, "title", self._marquee_original_cell,
                update_width=False)
        except Exception:
            # A refresh may have removed the old row before the blur event.
            pass

    def _clear_title_marquee(self) -> None:
        self._stop_title_timer()
        self._restore_title_cell()
        self._marquee_row_key = None
        self._marquee_tone = None
        self._marquee_original_cell = None
        self._marquee_offset = 0

    def _title_content_width(self) -> int:
        column = next(
            (column for column in self.ordered_columns
             if column.key.value == "title"),
            None,
        )
        # 窗口宽 = min(内容宽, 列可用宽)，至少 TITLE_CELL_LIMIT（除非列更窄）：
        # 列随表格缩放变窄时，marquee 窗口同步收缩，尾部不会被渲染裁剪。
        limit = getattr(self, "_title_cell_limit", TITLE_CELL_LIMIT)
        return min(max(column.content_width if column is not None else 0,
                       TITLE_CELL_LIMIT), limit)

    @staticmethod
    def _title_scroll_parts(tone: dict) -> tuple[str, str | None, str]:
        detail = str(tone.get("title") or "")
        matched_model_ids = tone.get("matched_model_ids") or ()
        if matched_model_ids:
            detail += " · model " + ", ".join(
                f"#{model_id}" for model_id in matched_model_ids)
        marker = (
            ("✓ ", "bold $success")
            if tone.get("download_state") in ("all", "partial") else ("", None))
        return detail, marker[1], marker[0]

    def _focused_title_cell(self) -> Text | None:
        if self._marquee_tone is None:
            return None
        detail, marker_style, marker = self._title_scroll_parts(self._marquee_tone)
        available = max(
            self._title_content_width() - cell_len(marker), 1)
        window = marquee_window(detail, available, self._marquee_offset)
        if marker_style:
            marker_style = resolve_rich_style(
                marker_style, getattr(self.app, "theme_variables", {}))
            cell = Text.from_markup(
                f"[{marker_style}]{escape(marker)}[/]{escape(window)}",
                end="",
            )
        else:
            cell = Text(escape(window), no_wrap=True, end="")
        cell.no_wrap = True
        return cell

    def _refresh_title_cell(self) -> None:
        if self._marquee_row_key is None:
            return
        cell = self._focused_title_cell()
        if cell is None:
            return
        try:
            self.update_cell(
                self._marquee_row_key, "title", cell, update_width=False)
        except Exception:
            self._clear_title_marquee()

    def _advance_title_marquee(self) -> None:
        self._marquee_offset += 1
        self._refresh_title_cell()

    def set_focused_tone(self, row_key: str | None, tone: dict | None) -> None:
        """Scroll only the focused title cell; restore the old row on change."""
        if row_key == self._marquee_row_key:
            return
        self._clear_title_marquee()
        if not row_key or not tone or not tone.get("title"):
            return
        try:
            original = self.get_cell(row_key, "title")
        except Exception:
            return
        self._marquee_row_key = row_key
        self._marquee_tone = tone
        self._marquee_original_cell = original
        self._marquee_offset = 0
        self._refresh_title_cell()
        detail, _, marker = self._title_scroll_parts(tone)
        available = max(self._title_content_width() - cell_len(marker), 1)
        if cell_len(detail) > available:
            self._marquee_timer = self.set_interval(
                0.12, self._advance_title_marquee)

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
        Binding("r", "retry_search", "retry", show=False),
        Binding("space", "toggle_selected", "select", show=False),
        Binding("a", "toggle_all", "all/none", show=False),
        Binding("u", "delete_selected", "uninstall", show=False),
    ]

    def on_focus(self, event) -> None:
        panel = self.query_ancestor(LibraryPanel)
        if panel is not None:
            panel._publish_highlight(self)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Let mouse-wheel/scrollbar navigation request the next remote page."""
        super().watch_scroll_y(old_value, new_value)
        panel = self.query_ancestor(LibraryPanel)
        if panel is not None:
            panel._maybe_load_more_from_viewport(self)

    def scroll_end(self, *args, **kwargs):
        """Treat an explicit End/scroll-end action as viewport intent.

        A page that exactly fills the viewport has no scroll offset, so
        ``watch_scroll_y`` cannot observe the user reaching its bottom.
        """
        result = super().scroll_end(*args, **kwargs)
        panel = self.query_ancestor(LibraryPanel)
        if panel is not None and self.has_focus:
            panel._maybe_load_more_from_viewport(self, force=True)
        return result

    def on_click(self, event) -> None:
        """Single click focuses (cursor move, handled by DataTable); double
        click acts like Enter (open picker / pack install screen)."""
        meta = event.style.meta
        if (self.id == "lib-table-local" and meta.get("column") == 0
                and isinstance(meta.get("row"), int) and meta["row"] >= 0):
            rows = self.ordered_rows
            if meta["row"] < len(rows):
                key = rows[meta["row"]].key.value
                if isinstance(key, str) and key.startswith("local:"):
                    self.screen.query_one(LibraryPanel).toggle_local_id(
                        int(key.partition(":")[2]))
                    event.stop()
                    return
        if getattr(event, "chain", 1) >= 2:
            self.action_select_cursor()
            event.stop()
            return
        # A normal row click only moves the table cursor; do not let it bubble
        # into the app-level chain hit-test.
        event.stop()

    def action_select_cursor(self) -> None:
        """Enter/双击：提示行（加载中/失败）→ 重试当前视图；正常行 → 选择。

        REQ-011：提示行用 cursor_type=none，基类 action_select_cursor 对
        "none" 直接 return——Enter/双击在加载/失败窗口彻底失效（用户
        "操作了一下"后进不了二级菜单的窗口）。提示行上改为触发重试。
        """
        if self.row_count and self.ordered_rows[0].key.value == "__status__":
            panel = self.screen.query_one(LibraryPanel)
            if self.id == "lib-table-tone" and panel._tone_auth_required:
                panel._focus_tone_login()
            elif self.id == "lib-table-creators" and panel._creator_auth_required:
                panel._focus_creator_login()
            else:
                panel.retry_active()
            return
        super().action_select_cursor()

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
        self.screen.query_one(LibraryPanel).action_escape()

    def action_retry_search(self) -> None:
        self.screen.query_one(LibraryPanel).retry_active()

    def action_toggle_selected(self) -> None:
        self.screen.query_one(LibraryPanel).toggle_local_selection()

    def action_toggle_all(self) -> None:
        self.screen.query_one(LibraryPanel).toggle_all_local()

    def action_delete_selected(self) -> None:
        self.screen.query_one(LibraryPanel).uninstall_local_selection()


# Local sorting is performed by the SQLite query so paging preserves the
# requested order instead of sorting only the currently loaded page.
LOCAL_SORT_CHOICES = [
    ("Title", "title"),
    ("Newest added", "added-desc"),
    ("Oldest added", "added-asc"),
]
# mirror tone3000.com's sort options; favorites comes from tones_counts
# (the search RPC rejects favorites ordering)
SORT_CHOICES = [("Trending", "trending"), ("Best match", "best-match"),
                ("Most downloaded", "downloads"),
                ("Most favorited", "favorites"), ("Newest", "newest")]
# TOP CREATORS 排行榜排序（REQ-029，参考 tone3000.com/top-creators 的
# sort 下拉：Most Tones 默认 + 数字列可排序）。
CREATOR_SORT_CHOICES = [("Most Tones", "tones"), ("Most Downloads", "downloads"),
                        ("Most Favorites", "favorites"), ("Most Models", "models")]
REMOTE_PAGE_SIZE = 40
LOCAL_PAGE_SIZE = 200
CREATOR_PAGE_SIZE = 100
LOAD_AHEAD_ROWS = 5
TITLE_CELL_LIMIT = 40
# TONE3000 结果按 (query, TYPE, SORT) 组合缓存（REQ-010）：
# 切换筛选命中缓存直接显示，不发网络请求；超限按最早写入淘汰。
_TONE_CACHE_MAX = 20
# Keep the SearchBar's standard and compact tracks deterministic at the
# terminal widths used by the TUI; content never participates in track sizing.
FILTER_BAR_COMPACT_WIDTH = 118


class LibraryContentTabs(TabbedContent):
    """Content switcher owned by the custom view-tab strip.

    Textual normally infers the active tab from any focused descendant. That
    lets a hidden worker completion or a table repaint yank the Library back
    to another view. The visible strip is the only tab activation authority in
    v0.2, so descendant focus must not mutate ``active``.
    """

    def _on_tab_pane_focused(self, event) -> None:
        event.stop()
        event.prevent_default()

    def on_mount(self) -> None:
        # The visible ViewTabStrip owns navigation in v0.2. Keep Textual's
        # compatibility ContentTabs available for ``active`` updates, but
        # remove it and its child tabs from the global focus cycle.
        tabs = self.query_one(ContentTabs)
        tabs.can_focus = False
        tabs.can_focus_children = False


class LibraryPanel(Vertical):
    """Left panel: LOCAL tab (imported tones) / TONE3000 tab (search + install)."""

    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def _make_table(table_id: str, *, rank: bool = False,
                    selectable: bool = False) -> LibraryTable:
        table = LibraryTable(id=table_id, cursor_type="row")
        if selectable:
            table.add_column("Sel", key="pick", width=5)
        if rank:
            table.add_column("Rank", key="rank", width=5)
        # 54 = 表格可用宽 - 2 侧 padding：单元格内容（含 marker）不超过它，
        # 尾部 … 与 marquee 窗口才不会被列渲染裁剪。
        table.add_column("Title", key="title", width=54)
        table.add_column("Type", key="type")
        table.add_column("Author", key="author")
        table.add_column("DL", key="downloads")
        table.add_column("Fav", key="favorites")
        table.add_column("Up", key="uploaded")
        table.add_column("Format", key="format", width=8)
        table.add_column("Files", key="files")
        table.add_column("Arch", key="arch")
        return table

    @staticmethod
    def _make_creators_table() -> LibraryTable:
        """Creators are aggregates, not tones: give them their own schema.

        排行榜 6 列对齐 tone3000.com/top-creators（REQ-012 权威参考）：
        Rank / Creator / Tones / Downloads / Favorites / Models，Most Tones 降序。
        """
        table = LibraryTable(id="lib-table-creators", cursor_type="row")
        table.add_column("Rank", key="rank", width=6)
        table.add_column("Creator", key="creator")
        table.add_column("Tones", key="tones", width=9)
        table.add_column("Downloads", key="downloads", width=12)
        table.add_column("Fav", key="favorites", width=8)
        table.add_column("Models", key="models", width=8)
        return table

    def compose(self) -> ComposeResult:
        yield ViewTabStrip(
            "LIBRARY",
            [("pane-local", "LOCAL"),
             ("pane-tone", "TONE3000"),
             ("pane-creators", "TOP CREATORS")],
            active="pane-local",
            id="library-view-tabs",
            classes="view-tabs--border",
        )
        with LibraryContentTabs(initial="pane-local"):
            with TabPane("LOCAL", id="pane-local"):
                yield SearchBar(
                    input_id="local-search",
                    sort_options=LOCAL_SORT_CHOICES,
                    sort_id="sort-filter-local",
                    type_id="type-filter-local-search",
                    type_options=[("ALL", "all")],
                    placeholder='@tone3000 #clean author:tone3000 tag:clean make:"Fender Reverb"',
                    input_cls=LibrarySearchInput,
                    id="local-search-bar",
                )
                yield self._make_table("lib-table-local", selectable=True)
            with TabPane("TONE3000", id="pane-tone"):
                yield SearchBar(
                    input_id="tone-search",
                    sort_options=SORT_CHOICES,
                    sort_id="sort-filter",
                    type_id="type-filter-tone-search",
                    type_options=[("ALL", "all")],
                    placeholder='@tone3000 #clean author:tone3000 tag:clean make:"Fender Reverb"',
                    input_cls=LibrarySearchInput,
                    id="tone-search-bar",
                )
                yield self._make_table("lib-table-tone")
                yield Button("Log in to TONE3000", id="tone-login-button",
                             variant="primary")
                yield MarqueeBar(id="tone-status")
            with TabPane("TOP CREATORS", id="pane-creators"):
                yield SearchBar(
                    input_id=None,
                    sort_options=CREATOR_SORT_CHOICES,
                    sort_id="sort-filter-creators",
                    id="creators-search-bar",
                )
                yield self._make_creators_table()
                yield Button("Log in to TONE3000", id="creators-login-button",
                             variant="primary")

    def on_mount(self) -> None:
        self._mode = "local"
        self._active_pane = "pane-local"
        self._sort = "trending"
        self._last_active = "pane-local"  # initial state: first tick is a no-op
        self._type_filter = "all"
        self._query = ""
        self._search_spec = SearchSpec()
        self._view_states = {
            pane: {
                "query": "", "sort": sort, "type_filter": "all",
                "view_tab_id": pane, "cursor_key": None, "cursor_index": 0,
                "cursor_column": 0, "first_visible_row_key": None,
                "row_offset": 0, "scroll_x": 0, "scroll_y": 0,
                "selection_keys": (), "detail_context_key": None,
            }
            for pane, sort in (
                ("pane-local", "title"),
                ("pane-tone", "trending"),
                ("pane-creators", "tones"),
            )
        }
        self._type_filters = {
            "pane-local": "all", "pane-tone": "all",
        }
        self._type_values_by_pane: dict[str, set[str]] = {
            "pane-local": set(), "pane-tone": set(),
        }
        self._type_value_context_by_pane: dict[str, tuple] = {}
        self._users: list[str] = []
        self._tags: list[str] = []
        self._makes: list[str] = []
        self._fingerprint: tuple | None = None
        self._db_token: tuple | None = None
        self._remote_tones: dict[int, dict] = {}
        self._highlighted_key: str | None = None
        self._tone_page = 0
        self._tone_total: int | None = None
        self._tone_has_more = False
        self._tone_loading = False
        self._tone_request_id = 0
        self._tone_error = False
        self._tone_auth_required = False
        self.query_one("#tone-login-button", Button).display = False
        self.query_one("#creators-login-button", Button).display = False
        self._local_page = 0
        self._local_has_more = False
        self._local_loading = False
        self._local_request_id = 0
        self._creator_page = 0
        self._creator_has_more = False
        self._creator_loading = False
        self._creator_total: int | None = None
        self._creator_tones: dict[str, list[dict]] = {}
        self._creator_request_id = 0
        self._creator_error = False
        self._creator_auth_required = False
        self._local_selected: set[int] = set()
        self._mutation_anchor: ViewAnchor | None = None
        self._screen_generation = 1
        self._mutation_request_id = 0
        self._tone_request_view: tuple | None = None
        self._creator_request_view: tuple | None = None
        # REQ-010: 搜索缓存 —— TONE3000 按 (query, TYPE, SORT, author) 组合
        # 缓存（值含 tones/total/page/has_more），TOP CREATORS 单一视图缓存。
        self._tone_cache: dict[tuple, dict] = {}
        self._tone_cache_key: tuple | None = None
        self._creator_cache: dict | None = None
        # 排行榜排序键（REQ-029）：tones / downloads / favorites / models。
        self._creator_sort = "tones"
        # 点击 tab 后焦点落在 tab 条上，Textual 默认 left/right 会切换
        # LOCAL/TONE3000/TOP CREATORS —— 禁掉这个键位，左右只能在表格/
        # 搜索框里操作，防止误操作跳到别的视图。
        self._disable_tab_arrow_keys()
        self._sync_search_bars()
        self.refresh_rows()
        # REQ-010: 启动即预取默认 TONE3000（trending）与 TOP CREATORS。
        # silent 只填缓存与隐藏表格、不碰状态栏/副标题，用户首次进入对应
        # tab 时缓存命中立即显示；之后按需 load more / 手动 r refresh /
        # 搜索新词才发起新请求。
        # 不用 exclusive：Textual 的 exclusive 取消同 group（default）的
        # 全部 worker，两个预取会互相取消。用户进入 tab 时 check_active_tab
        # 的 reload worker 自带 exclusive，会取消仍在跑的预取。
        self.run_worker(partial(self._reload_tone_table, silent=True),
                        name="search")
        self.run_worker(partial(self._show_top_creators, silent=True),
                        name="creators")

    def on_unmount(self) -> None:
        """Invalidate every worker before Textual releases this panel."""
        self._invalidate_async_requests()

    def _invalidate_async_requests(self) -> None:
        """Make every in-flight result stale before a shared-data reconcile."""
        self._screen_generation += 1
        self._tone_request_id += 1
        self._local_request_id += 1
        self._creator_request_id += 1
        self._mutation_request_id += 1
        self._tone_loading = False
        self._local_loading = False
        self._creator_loading = False
        self._tone_request_view = None
        self._creator_request_view = None

    def _screen_alive(self, generation: int) -> bool:
        return (bool(getattr(self, "is_mounted", False))
                and generation == self._screen_generation)

    def _view_identity(self, pane_id: str) -> tuple:
        state = self._view_states.get(pane_id, {})
        return (
            pane_id,
            str(state.get("query", "")),
            str(state.get("sort", "")),
            str(state.get("type_filter", "all")),
        )

    def _request_view_alive(self, pane_id: str, expected: tuple | None) -> bool:
        return (
            getattr(self, "_active_pane", None) == pane_id
            and expected is not None
            and self._view_identity(pane_id) == expected
        )

    def _tone_alive(self, generation: int, request_id: int) -> bool:
        if not (self._screen_alive(generation)
                and request_id == self._tone_request_id):
            return False
        return (self._tone_request_view is None
                or self._request_view_alive("pane-tone", self._tone_request_view))

    def _creator_alive(self, generation: int, request_id: int) -> bool:
        if not (self._screen_alive(generation)
                and request_id == self._creator_request_id):
            return False
        return (self._creator_request_view is None
                or self._request_view_alive(
                    "pane-creators", self._creator_request_view))

    def _local_alive(self, generation: int, request_id: int) -> bool:
        return (self._screen_alive(generation)
                and request_id == self._local_request_id)

    def _disable_tab_arrow_keys(self) -> None:
        """Make the tab strip's left/right dead (priority bindings win over
        Tabs' built-in previous_tab/next_tab)."""
        tabs = self.query_one(ContentTabs)
        tabs.action_noop_tab = lambda: None
        for key in ("left", "right"):
            tabs._bindings.bind(key, "noop_tab", show=False, priority=True)

    def on_resize(self, event) -> None:
        """布局尺寸确定后立即同步 SearchBar。

        on_mount 时 content_region 尚未计算（宽度为 0），compact 判断会误判，
        导致首帧轨道与后续 tick 修正后不一致。resize 在首次布局时触发，让
        首帧布局直接稳定。
        """
        if hasattr(self, "_active_pane"):
            self._sync_search_bars()
            # REQ-040：副标题的宽度自适应 token（space/Esc/d 的宽窄写法）
            # 在 mount 时按宽度 0 算过——resize 落定后必须重算，否则提示词
            # 与实际可点 token 脱节（点 "space" 找不到 "space select"）。
            if self._active_pane == "pane-local":
                self._update_local_selection_status()
            elif self._active_pane == "pane-tone":
                self._update_tone_subtitle(
                    loading=self._tone_loading, error=self._tone_error)
            elif self._active_pane == "pane-creators":
                self._update_creator_subtitle(
                    loading=self._creator_loading, error=self._creator_error)
            else:
                refresh_border_hint_layout(self)

    def _sync_search_bars(self) -> None:
        """Apply fixed SearchBar tracks.

        The parent TabPane owns visibility. Keeping child SearchBars displayed
        avoids a stale child ``display=False`` from surviving a tab switch and
        collapsing the active pane's search row.
        """
        compact = ((self.content_region.width
                    or self.app.size.width * 3 // 5)
                   < FILTER_BAR_COMPACT_WIDTH)
        pane_by_bar = {
            "#local-search-bar": "pane-local",
            "#tone-search-bar": "pane-tone",
            "#creators-search-bar": "pane-creators",
        }
        for bar_id in pane_by_bar:
            try:
                bar = self.query_one(bar_id, SearchBar)
                bar.set_compact(compact)
                bar.display = True
            except Exception:
                pass

    def _table_for_pane(self, pane_id: str) -> DataTable | None:
        table_id = {
            "pane-local": "lib-table-local",
            "pane-tone": "lib-table-tone",
            "pane-creators": "lib-table-creators",
        }.get(pane_id)
        if table_id is None:
            return None
        try:
            return self.query_one(f"#{table_id}", DataTable)
        except Exception:
            return None

    def _capture_view_state(self, pane_id: str | None = None) -> None:
        pane_id = pane_id or getattr(self, "_active_pane", "pane-local")
        state = getattr(self, "_view_states", {}).get(pane_id)
        if state is None:
            return
        try:
            search_id = {
                "pane-local": "local-search",
                "pane-tone": "tone-search",
            }.get(pane_id)
            if search_id:
                state["query"] = self.query_one(
                    f"#{search_id}", Input).value
            if pane_id == "pane-tone":
                state["sort"] = self._sort
            elif pane_id == "pane-creators":
                state["sort"] = self._creator_sort
            state["type_filter"] = self._type_filters.get(pane_id, "all")
            table = self._table_for_pane(pane_id)
            if table is not None:
                rows = table.ordered_rows
                real_rows = [
                    row for row in rows
                    if not str(row.key.value).startswith("__")
                ]
                state["cursor_key"] = (
                    rows[table.cursor_row].key.value
                    if 0 <= table.cursor_row < len(rows) else None)
                state["cursor_index"] = table.cursor_row
                state["cursor_column"] = table.cursor_column
                state["scroll_y"] = table.scroll_y
                state["scroll_x"] = table.scroll_x
                current_key = state["cursor_key"]
                current_position = next(
                    (index for index, row in enumerate(real_rows)
                     if row.key.value == current_key), None)
                if current_position is None:
                    state["cursor_successor_key"] = None
                    state["cursor_predecessor_key"] = None
                else:
                    state["cursor_successor_key"] = (
                        real_rows[current_position + 1].key.value
                        if current_position + 1 < len(real_rows) else None)
                    state["cursor_predecessor_key"] = (
                        real_rows[current_position - 1].key.value
                        if current_position > 0 else None)
                if rows:
                    first_index = min(
                        max(int(table.scroll_y), 0), len(rows) - 1)
                    state["first_visible_row_key"] = rows[
                        first_index].key.value
                    state["first_visible_successor_key"] = (
                        rows[first_index + 1].key.value
                        if first_index + 1 < len(rows) else None)
                    state["first_visible_predecessor_key"] = (
                        rows[first_index - 1].key.value
                        if first_index > 0 else None)
                    state["row_offset"] = max(
                        0.0, float(table.scroll_y) - first_index)
                else:
                    state["first_visible_row_key"] = None
                    state["first_visible_successor_key"] = None
                    state["first_visible_predecessor_key"] = None
                    state["row_offset"] = 0
            state["selection_keys"] = tuple(
                f"local:{tone_id}" for tone_id in sorted(self._local_selected)
            ) if pane_id == "pane-local" else ()
            state["detail_context_key"] = self._highlighted_key
        except Exception:
            pass

    def _restore_view_state(self, pane_id: str) -> None:
        state = self._view_states[pane_id]
        self._type_filters[pane_id] = state.get("type_filter", "all")
        self._type_filter = self._type_filters[pane_id]
        if pane_id == "pane-local":
            self._local_selected = {
                int(key.partition(":")[2])
                for key in state.get("selection_keys", ())
                if isinstance(key, str) and key.startswith("local:")
                and key.partition(":")[2].isdigit()
            }
        if pane_id == "pane-tone":
            self._sort = state.get("sort", "trending")
            self._query = state.get("query", "")
            self._search_spec = self._parse_or_notify(self._query) or SearchSpec()
            self.query_one("#tone-search", Input).value = self._query
            self.query_one("#sort-filter", Select).value = self._sort
        elif pane_id == "pane-local":
            self._query = state.get("query", "")
            self._search_spec = self._parse_or_notify(self._query) or SearchSpec()
            self.query_one("#local-search", Input).value = self._query
            self.query_one("#sort-filter-local", Select).value = state.get(
                "sort", "title")
        else:
            self._creator_sort = state.get("sort", "tones")
            self.query_one("#sort-filter-creators", Select).value = self._creator_sort

    def _restore_view_anchor(self, pane_id: str) -> None:
        """Restore a tab-local row key and viewport after a table reconcile."""
        state = self._view_states.get(pane_id, {})
        table = self._table_for_pane(pane_id)
        if table is None:
            return
        key = state.get("cursor_key")
        rows = table.ordered_rows
        row_index = next(
            (index for index, row in enumerate(rows) if row.key.value == key),
            None,
        )
        if row_index is None:
            for fallback_key in (
                    state.get("cursor_successor_key"),
                    state.get("cursor_predecessor_key")):
                if fallback_key is None:
                    continue
                row_index = next(
                    (index for index, row in enumerate(rows)
                     if row.key.value == fallback_key),
                    None,
                )
                if row_index is not None:
                    break
        if row_index is not None:
            table.move_cursor(
                row=row_index,
                column=state.get("cursor_column", table.cursor_column),
                animate=False,
                scroll=False,
            )
        elif rows:
            # If the anchored row was deleted, keep its visual position when
            # possible: the next row takes over, otherwise the previous row.
            real_rows = [
                index for index, row in enumerate(rows)
                if not str(row.key.value).startswith("__")
            ]
            if real_rows:
                position = min(
                    max(int(state.get("cursor_index", 0)), 0),
                    len(real_rows) - 1,
                )
                table.move_cursor(
                    row=real_rows[position],
                    column=state.get("cursor_column", table.cursor_column),
                    animate=False,
                    scroll=False,
                )
        first_key = state.get("first_visible_row_key")
        first_index = next(
            (index for index, row in enumerate(rows)
             if row.key.value == first_key), None)
        if first_index is None:
            for fallback_key in (
                    state.get("first_visible_successor_key"),
                    state.get("first_visible_predecessor_key")):
                if fallback_key is None:
                    continue
                first_index = next(
                    (index for index, row in enumerate(rows)
                     if row.key.value == fallback_key),
                    None,
                )
                if first_index is not None:
                    break
        scroll_y = (
            first_index + max(float(state.get("row_offset", 0)), 0.0)
            if first_index is not None
            else state.get("scroll_y", table.scroll_y)
        )
        table.scroll_to(
            x=state.get("scroll_x", table.scroll_x),
            y=scroll_y,
            animate=False,
        )

    def capture_view_anchor(self) -> ViewAnchor:
        """Capture the active library tab using stable row identities."""
        for pane_id in self._view_states:
            self._capture_view_state(pane_id)
        pane_id = getattr(self, "_active_pane", "pane-local")
        state = self._view_states.get(pane_id, {})
        screen_id, app_tab = view_context(self)
        return ViewAnchor(
            screen_id=screen_id,
            app_tab=app_tab,
            view_tab_id=pane_id,
            focused_widget=focused_widget_key(self),
            cursor_row_key=state.get("cursor_key"),
            cursor_column=int(state.get("cursor_column", 0)),
            first_visible_row_key=state.get("first_visible_row_key"),
            row_offset=float(state.get("row_offset", 0.0)),
            scroll_x=float(state.get("scroll_x", 0.0)),
            scroll_y=float(state.get("scroll_y", 0.0)),
            selection_keys=tuple(state.get("selection_keys", ())),
            confirmation_state=None,
            detail_context_key=state.get("detail_context_key"),
        )

    def set_mutation_anchor(self, anchor: ViewAnchor | None) -> None:
        """Make the coordinator snapshot available to async table refreshes."""
        self._mutation_anchor = anchor
        if anchor is None or anchor.view_tab_id not in self._view_states:
            return
        self._view_states[anchor.view_tab_id].update({
            "cursor_key": anchor.cursor_row_key,
            "cursor_column": anchor.cursor_column,
            "first_visible_row_key": anchor.first_visible_row_key,
            "row_offset": anchor.row_offset,
            "scroll_x": anchor.scroll_x,
            "scroll_y": anchor.scroll_y,
            "selection_keys": anchor.selection_keys,
            "detail_context_key": anchor.detail_context_key,
        })

    def restore_view_anchor(self, anchor: ViewAnchor | None) -> None:
        """Restore the captured table position without changing the active tab."""
        if anchor is None or anchor.view_tab_id not in self._view_states:
            return
        self._restore_view_anchor(anchor.view_tab_id)
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

    def on_view_tab_strip_changed(self, event: ViewTabStrip.Changed) -> None:
        self.activate_view_tab(event.view_tab_id)

    def activate_view_tab(self, pane_id: str) -> None:
        if pane_id not in self._view_states:
            return
        old = getattr(self, "_active_pane", pane_id)
        if old == pane_id:
            return
        strip = self.query_one("#library-view-tabs", ViewTabStrip)
        keep_strip_focus = strip.has_focus
        self._capture_view_state(old)
        self.query_one(TabbedContent).active = pane_id
        strip.set_active(pane_id)
        # Keep the existing worker routing in one place. The application tick
        # will see the same transition, but immediate reconcile prevents a
        # visible click from waiting for that tick.
        self._last_active = old
        self.check_active_tab()
        if keep_strip_focus:
            strip.focus()

    def _type_values_for_table(self, table: DataTable) -> list[str]:
        pane_id = {
            "lib-table-local": "pane-local",
            "lib-table-tone": "pane-tone",
        }.get(table.id or "")
        if pane_id is not None:
            state = self._view_states.get(pane_id, {})
            context = (state.get("query", ""), state.get("sort", ""))
            if self._type_value_context_by_pane.get(pane_id) != context:
                self._type_value_context_by_pane[pane_id] = context
                self._type_values_by_pane[pane_id] = set()
        # Keep the type universe for the current query/sort context while
        # discarding values from older contexts. This lets AMP -> CAB work
        # without leaking a type from an unrelated search into the menu.
        values = (set(self._type_values_by_pane.get(pane_id, set()))
                  if pane_id else set())
        for row in table.ordered_rows:
            key = row.key.value
            tone = self._tone_for_key(key)
            if tone and tone.get("gear"):
                value = str(tone["gear"]).strip()
                if value:
                    values.add(value)
        if pane_id is not None:
            self._type_values_by_pane[pane_id] = values
        return sorted(values, key=str.casefold)

    def _sync_type_search_options(self, table: DataTable) -> None:
        """Keep the inline Type select in sync with the visible data set."""
        pane_id = {
            "lib-table-local": "pane-local",
            "lib-table-tone": "pane-tone",
        }.get(table.id or "")
        if pane_id is None:
            return
        options = [("ALL", "all")]
        options.extend((value.upper(), value)
                       for value in self._type_values_for_table(table))
        selected = self._type_filters.get(pane_id, "all")
        if selected != "all" and selected not in {value for _, value in options}:
            options.append((selected.upper(), selected))
        try:
            self.query_one(_SEARCH_BAR_ID[pane_id], SearchBar).set_type_options(
                options, selected)
        except Exception:
            pass

    # ---- tab / table routing ---------------------------------------------

    def _table(self) -> DataTable:
        active = getattr(self, "_active_pane", None) or self.query_one(TabbedContent).active
        if active == "pane-creators":
            return self.query_one("#lib-table-creators", DataTable)
        return self.query_one("#lib-table-local" if active == "pane-local"
                              else "#lib-table-tone", DataTable)

    def _focus_if_pane_active(self, table: DataTable) -> None:
        """Focus a table only when its pane is still the active one.

        Load workers complete after the user may have switched tabs, and
        TabbedContent reads any focus inside a pane as a tab switch request
        (TabPane.Focused → active = pane id). Focusing a stale table would
        therefore force the UI back to the tab that started the load. This
        guard is the only safe way to focus from a worker completion.
        """
        pane = _TABLE_PANE.get(table.id or "")
        if pane is None:
            return
        try:
            if self.query_one(TabbedContent).active == pane:
                strip = self.query_one("#library-view-tabs", ViewTabStrip)
                if strip.has_focus:
                    return
                table.focus()
        except Exception:
            pass

    def table_for(self, input_id: str | None) -> DataTable:
        table_id = {
            "local-search": "lib-table-local",
            "tone-search": "lib-table-tone",
        }.get(input_id, "lib-table-tone")
        return self.query_one(f"#{table_id}", DataTable)

    def search_for(self, table_id: str | None) -> Input:
        input_id = {
            "lib-table-local": "local-search",
            "lib-table-tone": "tone-search",
        }.get(table_id, "tone-search")
        return self.query_one(f"#{input_id}", Input)

    def _search_creator(self, name: str) -> None:
        """REQ-033：作者行 Enter/双击 → 跳 TONE3000 tab，搜索栏填
        @author 并触发真实搜索（显示该作者全部音色结果）。"""
        self.activate_view_tab("pane-tone")
        query = f"@{name}"
        self.query_one("#tone-search", Input).value = query
        # _query 同步设置：tab 切换后 check_active_tab 的加载（exclusive）
        # 会取消本 worker 并用 _query 重新加载——同一 query，结果一致。
        self._query = query
        self.run_worker(partial(self._show_search, query), name="search",
                        exclusive=True)

    def _set_search_spec(self, query: str, spec: SearchSpec) -> None:
        """Keep the raw input and its normalized form together."""
        self._query = query
        self._search_spec = spec
        state = getattr(self, "_view_states", {}).get(
            getattr(self, "_active_pane", "pane-local"))
        if state is not None:
            state["query"] = query
        self._users = list(spec.authors)
        self._tags = list(spec.tags)
        self._makes = list(spec.makes)

    def _parse_or_notify(self, query: str) -> SearchSpec | None:
        try:
            return parse_search(query)
        except SearchSyntaxError as exc:
            self.notify(f"Search syntax: {exc}", severity="error")
            return None

    def _effective_authors(self, spec: SearchSpec) -> list[str]:
        return list(spec.authors)

    def _selected_order(self) -> str:
        return {
            "trending": "trending",
            "best-match": "best-match",
            "downloads": "downloads-all-time",
            "newest": "newest",
        }.get(self._sort, "trending")

    @staticmethod
    def _status_row(table: DataTable, message: str) -> None:
        """Put transient network state inside the list, without a fake cursor."""
        table.cursor_type = "none"
        # Never put the banner in a fixed-width column: fixed columns crop
        # their cells at column width without an ellipsis, so "Loading top
        # creators…" rendered as "Loadin" on a narrow pane (TOP CREATORS
        # puts its message in the Rank column otherwise). The first
        # auto-width column grows to fit the message and stays on the left
        # side of the viewport crop.
        cols = list(table.ordered_columns)
        target = 0 if table.id == "lib-table-tone" else next(
            (i for i, col in enumerate(cols) if col.auto_width), 0)
        cells = [""] * len(cols)
        cells[target] = f"[dim]{message}[/dim]"
        table.add_row(*cells, key="__status__")

    @staticmethod
    def _has_real_rows(table: DataTable) -> bool:
        return any(row.key.value != "__status__" for row in table.ordered_rows)

    def _show_status_if_empty(self, table: DataTable, message: str) -> None:
        """Show a loading/error row only when no valid rows can be retained."""
        if self._has_real_rows(table):
            return
        table.clear()
        self._status_row(table, message)

    def _set_tone_login_visible(self, visible: bool) -> None:
        try:
            button = self.query_one("#tone-login-button", Button)
        except Exception:
            return
        button.display = visible
        if not visible:
            button.disabled = False

    def _focus_tone_login(self) -> None:
        if not getattr(self, "_tone_auth_required", False):
            return
        try:
            button = self.query_one("#tone-login-button", Button)
        except Exception:
            return
        if button.display and not button.disabled:
            button.focus()

    def _show_tone_auth_required(self, table: DataTable, *,
                                 silent: bool = False,
                                 message: str | None = None) -> None:
        """Show the user-facing login action for a TONE3000 auth failure."""
        self._tone_loading = False
        if silent:
            return
        self._tone_auth_required = True
        self._tone_error = False
        self._set_tone_login_visible(True)
        self._show_status_if_empty(
            table, message or "TONE3000 login required — select Log in.")
        try:
            self.query_one("#tone-status", MarqueeBar).content = (
                "login required · select Log in")
        except Exception:
            pass
        self._update_tone_subtitle()
        self._focus_tone_login()

    def _clear_tone_auth_required(self) -> None:
        self._tone_auth_required = False
        self._set_tone_login_visible(False)

    def _set_creator_login_visible(self, visible: bool) -> None:
        try:
            button = self.query_one("#creators-login-button", Button)
        except Exception:
            return
        button.display = visible
        if not visible:
            button.disabled = False

    def _focus_creator_login(self) -> None:
        if not getattr(self, "_creator_auth_required", False):
            return
        try:
            button = self.query_one("#creators-login-button", Button)
        except Exception:
            return
        if button.display and not button.disabled:
            button.focus()

    def _show_creator_auth_required(self, table: DataTable, *,
                                    silent: bool = False,
                                    message: str | None = None) -> None:
        """Show the user-facing login action for the creator leaderboard."""
        self._creator_loading = False
        if silent:
            return
        self._creator_auth_required = True
        self._creator_error = False
        self._set_creator_login_visible(True)
        self._show_status_if_empty(
            table, message or "TONE3000 login required — select Log in.")
        self._update_creator_subtitle()
        self._focus_creator_login()

    def _clear_creator_auth_required(self) -> None:
        self._creator_auth_required = False
        self._set_creator_login_visible(False)

    @staticmethod
    def _network_error(action: str, error: Exception) -> str:
        """Keep volatile transport diagnostics out of the main list surface."""
        text = str(error).lower()
        if "ssl" in text or "tls" in text:
            return f"{action} unavailable — secure connection closed. Press r to retry."
        if "timeout" in text or "timed out" in text:
            return f"{action} timed out. Press r to retry."
        return f"{action} unavailable. Check your connection, then press r to retry."

    def retry_active(self) -> None:
        """Refresh the active remote view (r key): drop its cache entry and
        re-fetch. Doubles as the retry path after a failed load — the error
        banners say "Press r to retry", which this satisfies."""
        active = getattr(self, "_active_pane", "pane-local")
        if active == "pane-tone":
            if self._tone_auth_required:
                self._focus_tone_login()
                return
            if self._tone_cache_key is not None:
                self._tone_cache.pop(self._tone_cache_key, None)
            self.run_worker(partial(self._reload_tone_table, refresh=True),
                            name="search", exclusive=True)
        elif active == "pane-creators":
            if self._creator_auth_required:
                self._focus_creator_login()
                return
            self._creator_cache = None
            self.run_worker(partial(self._show_top_creators, refresh=True),
                            name="creators", exclusive=True)

    def _update_tone_subtitle(self, *, loading: bool = False,
                              error: bool = False) -> None:
        if loading:
            # The banner is its own short complete line: a narrow pane can
            # only truncate it to an ellipsis-suffixed "Loa…", never to a
            # bare partial word like the "loadin" that cropping the old
            # "N · loading… · Enter install" string produced.
            self._tone_error = False
            set_border_hint_layout(
                self, "loading…",
                [token for token, _action in self._border_hint_actions()])
            return
        loaded = len(self._remote_tones)
        count = f"{loaded}/{self._tone_total}" if self._tone_total else str(loaded)
        if self._tone_auth_required:
            self._tone_error = False
            state = "login required"
        elif error:
            self._tone_error = True
            state = f"{count} · load failed"
        elif self._tone_has_more:
            self._tone_error = False
            state = count
        else:
            self._tone_error = False
            state = f"{count} · all loaded"
        # REQ-038：Enter 打开二级菜单详情页（不是直连 install）
        set_border_hint_layout(
            self, state,
            [token for token, _action in self._border_hint_actions()])

    def _load_more_from_hint(self) -> None:
        """Load the active table's next page from its clickable hint token."""
        self._maybe_load_more_from_viewport(self._table(), force=True)

    def _maybe_load_more_from_viewport(self, table: LibraryTable, *, force: bool = False) -> None:
        """Load another page when the cursor or viewport reaches the tail."""
        if table.id == "lib-table-tone":
            has_more, loading = self._tone_has_more, self._tone_loading
        elif table.id == "lib-table-local":
            has_more, loading = self._local_has_more, self._local_loading
        elif table.id == "lib-table-creators":
            has_more, loading = self._creator_has_more, self._creator_loading
        else:
            return
        if not has_more or loading:
            return
        near_cursor = table.cursor_row >= max(
            0, table.row_count - LOAD_AHEAD_ROWS)
        at_bottom = force or (
            table.max_scroll_y > 0 and table.scroll_y >= table.max_scroll_y - 1)
        if not (near_cursor or at_bottom):
            return
        if table.id == "lib-table-tone":
            worker, name = self._load_more_tones, "search-more"
        elif table.id == "lib-table-local":
            worker, name = self._load_more_local, "local-more"
        elif table.id == "lib-table-creators":
            self.run_worker(
                partial(self._load_more_creators),
                name="creators-more", exclusive=True)
            return
        else:
            return
        self.run_worker(partial(worker), name=name, exclusive=True)

    def check_active_tab(self) -> None:
        """Tick-driven tab detection (0.1s): the reactive `active` value is
        always current here, unlike TabActivated events whose pane/active can
        lag during a switch. Drives trending/creators loading and routing."""
        try:
            active = self.query_one(TabbedContent).active
        except Exception:
            return
        self._sync_search_bars()
        if active == getattr(self, "_last_active", None):
            return
        strip = self.query_one("#library-view-tabs", ViewTabStrip)
        keep_strip_focus = strip.has_focus
        previous = getattr(self, "_active_pane", None)
        view_changed = bool(previous and previous != active)
        if view_changed:
            self._invalidate_async_requests()
            self._capture_view_state(previous)
        self._last_active = active
        self._active_pane = active
        self._restore_view_state(active)
        self.query_one("#library-view-tabs", ViewTabStrip).set_active(active)
        self._sync_search_bars()
        self._mode = "local" if active == "pane-local" else "tone"
        if view_changed:
            # The saved key belongs to this tab, but DetailPane may still show
            # the previous tab. Force one highlight publication below.
            self._highlighted_key = None
        if active == "pane-tone":
            if not self.query_one("#lib-table-tone", DataTable).row_count:
                self.run_worker(partial(self._reload_tone_table),
                                name="search", exclusive=True)
            else:
                # 启动预取/缓存已填好行：直接刷新副标题——否则残留 LOCAL 的
                # 提示词（a all/space select…）挂在 TONE3000 tab 上，既误导
                # 又让提示与可点 token 脱节（REQ-040 点击等效审计发现）。
                self._update_tone_subtitle()
        elif active == "pane-creators":
            # TOP CREATORS is a tab-local paged view. Re-entering it must show
            # the retained page set instead of replacing it with page one.
            if self._creator_cache is not None:
                self._creator_loading = False
                self._restore_creator_entry()
            elif not self.query_one("#lib-table-creators", DataTable).row_count:
                self.run_worker(partial(self._show_top_creators),
                                name="creators", exclusive=True)
            else:
                self._update_creator_subtitle()
        elif active == "pane-local":
            self._update_local_selection_status()
            self._fingerprint = None
            self.refresh_rows()
        table = self._table()
        if keep_strip_focus:
            strip.focus()
        else:
            table.focus()
        self._publish_highlight(table)

    def focus_search(self) -> None:
        self.search_for(self._table().id).focus()

    def action_reset(self) -> None:
        """Escape: clear the active tab's input and stay on LOCAL."""
        search = self.search_for(self._table().id)
        search.value = ""
        self.query_one("#local-search", Input).value = ""
        self.query_one("#tone-search", Input).value = ""
        self._set_search_spec("", SearchSpec())
        if self._mode != "local":
            self.activate_view_tab("pane-local")
            self._mode = "local"
            self._fingerprint = None
            self.query_one("#tone-status", MarqueeBar).content = ""
            self.refresh_rows()
        self._table().focus()

    def action_escape(self) -> None:
        if self._active_pane == "pane-local" and self._local_selected:
            self.clear_local_selection()
            return
        self.action_reset()

    # ---- local tab --------------------------------------------------------

    def refresh_rows(self, *, force: bool = False, publish: bool = True,
                     preserve_pages: bool = False) -> None:
        """Reload local rows from the DB (called on tick so external imports appear).

        Skips repaint unless the DB or local install state changed, so the
        user's browsing position and the TONE3000 tab are not clobbered.
        """
        # _mode can lag behind inside tab-activation handlers; _active_pane is
        # set first and is the authoritative "which tab is showing" signal.
        if (not force
                and getattr(self, "_active_pane", "pane-local") != "pane-local"):
            return
        db_token = library.database_change_token()
        if not force and self._fingerprint is not None and db_token == self._db_token:
            return
        with library.connect() as conn:
            fp = tuple(conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0), "
                "COALESCE(SUM(local_dir IS NOT NULL), 0), "
                "(SELECT COUNT(*) FROM models WHERE local_path IS NOT NULL), "
                "(SELECT COALESCE(GROUP_CONCAT(id || ':' || local_path, '|'), '') "
                "FROM models WHERE local_path IS NOT NULL) FROM tones").fetchone())
            all_local_ids = {
                int(row[0]) for row in conn.execute(
                    "SELECT id FROM tones WHERE local_dir IS NOT NULL "
                    "OR id IN (SELECT tone_id FROM models "
                    "WHERE local_path IS NOT NULL)").fetchall()
            }
        if not force and fp == self._fingerprint:
            self._db_token = db_token
            return
        self._fingerprint = fp
        self._db_token = db_token
        # 注意：此处不能清 _remote_tones——TONE3000 表的行还在，Enter 依赖
        # 这张查找表（remote:<id> → tone）；本地 DB 变化与远程表无关。
        loaded_page = max(int(self._local_page), 0) if preserve_pages else 0
        page_limit = LOCAL_PAGE_SIZE * (loaded_page + 1)
        self._local_page = loaded_page
        self._local_has_more = False
        self._local_loading = False
        self._local_request_id += 1
        table = self.query_one("#lib-table-local", DataTable)
        table.clear()
        spec = getattr(self, "_search_spec", SearchSpec())
        tones = library.list_tones(
            gear=None if self._type_filter == "all" else self._type_filter,
            limit=page_limit,
            authors=self._effective_authors(spec) or None,
            tags=spec.tags or None,
            makes=spec.makes or None,
            model_ids=spec.model_ids or None,
            query=spec.text or None,
            has_files=True,
            offset=0,
            sort_by=self._view_states["pane-local"].get("sort", "title"))
        self._local_has_more = len(tones) == page_limit
        if preserve_pages and tones:
            self._local_page = min(
                loaded_page, (len(tones) - 1) // LOCAL_PAGE_SIZE)
        self._local_selected.intersection_update(all_local_ids)
        self._update_local_selection_status()
        for t in tones:
            checked = "\\[x]" if t["id"] in self._local_selected else "\\[ ]"
            table.add_row(checked, *self._row_cells(t, table), key=f"local:{t['id']}")
        self._sync_type_search_options(table)
        if not tones:
            self._status_row(
                table,
                "no local tones — switch to TONE3000 "
                "to search and import")
        self._update_local_selection_status()
        self._restore_view_anchor("pane-local")
        if publish:
            self._publish_highlight(table)

    @staticmethod
    def _update_remote_row(table: DataTable, row_key: str, cells: list[str]) -> None:
        columns = ("title", "type", "author", "downloads", "favorites",
                   "uploaded", "format", "arch", "files")
        for column, value in zip(columns, cells):
            try:
                table.update_cell(row_key, column, value, update_width=False)
            except Exception:
                pass

    def _update_local_row(self, table: DataTable, tone: dict) -> None:
        columns = ("pick", "title", "type", "author", "downloads", "favorites",
                   "uploaded", "format", "arch", "files")
        cells = [
            "\\[x]" if int(tone["id"]) in self._local_selected else "\\[ ]",
            *self._row_cells(tone),
        ]
        for column, value in zip(columns, cells):
            try:
                table.update_cell(
                    f"local:{tone['id']}", column, value, update_width=False)
            except Exception:
                pass

    @staticmethod
    def _local_tone_has_files(tone: dict | None) -> bool:
        if not tone:
            return False
        if tone.get("local_dir"):
            return True
        return any(model.get("local_path") for model in (tone.get("models") or [])
                   if isinstance(model, dict))

    def _local_tone_matches(self, tone: dict, spec: SearchSpec,
                            type_filter: str) -> bool:
        if not self._local_tone_has_files(tone):
            return False
        if type_filter != "all" and str(tone.get("gear") or "") != type_filter:
            return False
        if spec.authors and str(tone.get("username") or "") not in spec.authors:
            return False

        def tokens(value) -> set[str]:
            if isinstance(value, str):
                return {value}
            return {str(item) for item in (value or ())}

        if spec.tags and not tokens(tone.get("tags")).intersection(spec.tags):
            return False
        if spec.makes and not tokens(tone.get("makes")).intersection(spec.makes):
            return False
        if spec.model_ids:
            model_ids = {
                int(model.get("id")) for model in (tone.get("models") or [])
                if isinstance(model, dict) and str(model.get("id") or "").isdigit()
            }
            if not model_ids.intersection(spec.model_ids):
                return False
        if spec.text:
            needle = spec.text.casefold()
            haystack = " ".join(
                str(tone.get(key) or "")
                for key in ("title", "username", "description")
            ).casefold()
            if needle not in haystack:
                return False
        return True

    def _mutation_tone_ids(self, event) -> set[int]:
        """Resolve committed tone/model keys without scanning the library."""
        keys = getattr(event, "keys", ()) or getattr(event, "object_keys", ())
        tone_ids: set[int] = set()
        model_ids: set[int] = set()
        for raw_key in keys:
            kind, separator, value = str(raw_key).partition(":")
            if not separator or not value.isdigit():
                continue
            if kind == "tone":
                tone_ids.add(int(value))
            elif kind == "model":
                model_ids.add(int(value))
        if not model_ids:
            return tone_ids

        try:
            rows = library.list_tones(model_ids=sorted(model_ids), limit=None)
        except Exception:
            rows = []
        for tone in rows:
            try:
                tone_ids.add(int(tone["id"]))
            except (KeyError, TypeError, ValueError):
                pass

        if tone_ids:
            return tone_ids
        # Narrow test doubles and remote-only caches may not expose the SQL
        # model lookup. Use already loaded model metadata as a local fallback.
        for tone_map in (self._remote_tones,):
            for tone_id, tone in tone_map.items():
                for model in tone.get("models") or ():
                    try:
                        if int(model.get("id")) in model_ids:
                            tone_ids.add(int(tone_id))
                            break
                    except (AttributeError, TypeError, ValueError):
                        continue
        return tone_ids

    def _local_fingerprint(self) -> tuple:
        with library.connect() as conn:
            return tuple(conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0), "
                "COALESCE(SUM(local_dir IS NOT NULL), 0), "
                "(SELECT COUNT(*) FROM models WHERE local_path IS NOT NULL), "
                "(SELECT COALESCE(GROUP_CONCAT(id || ':' || local_path, '|'), '') "
                "FROM models WHERE local_path IS NOT NULL) FROM tones"
            ).fetchone())

    def _reconcile_local_rows(self, tone_ids: set[int]) -> None:
        table = self.query_one("#lib-table-local", DataTable)
        state = self._view_states["pane-local"]
        query = str(state.get("query", ""))
        spec = self._parse_or_notify(query) or SearchSpec()
        type_filter = state.get("type_filter", "all")

        def has_row(row_key: str) -> bool:
            return any(row.key.value == row_key for row in table.ordered_rows)

        for tone_id in sorted(tone_ids):
            row_key = f"local:{tone_id}"
            try:
                tone = library.get_tone(tone_id)
            except Exception:
                continue
            visible = bool(tone and self._local_tone_matches(
                tone, spec, type_filter))
            if visible:
                if has_row("__status__"):
                    table.remove_row("__status__")
                table.cursor_type = "row"
                if has_row(row_key):
                    self._update_local_row(table, tone)
                else:
                    checked = "\\[x]" if tone_id in self._local_selected else "\\[ ]"
                    table.add_row(checked, *self._row_cells(tone), key=row_key)
            else:
                if has_row(row_key):
                    table.remove_row(row_key)
                if not self._local_tone_has_files(tone):
                    self._local_selected.discard(tone_id)

        if any(row.key.value != "__status__" for row in table.ordered_rows):
            if has_row("__status__"):
                table.remove_row("__status__")
            table.cursor_type = "row"
        elif not has_row("__status__"):
            self._status_row(
                table,
                "no local tones — switch to TONE3000 "
                "to search and import")
        state["selection_keys"] = tuple(
            f"local:{tone_id}" for tone_id in sorted(self._local_selected)
        )
        self._sync_type_search_options(table)
        try:
            self._fingerprint = self._local_fingerprint()
        except Exception:
            self._fingerprint = None

    def _mutation_download_candidates(self,
                                      affected_ids: set[int]) -> list[dict]:
        """Snapshot visible remote rows before leaving the UI thread.

        ``mark_download_state`` may query TONE3000 once per locally known tone,
        so it must receive detached dictionaries and run outside Textual's
        event loop. The current and cached views are deduplicated here; the
        returned rows are applied back to every matching cache after the
        worker completes.
        """
        maps: list[dict[int, dict]] = [self._remote_tones]
        maps.extend(entry.get("tones", {})
                    for entry in self._tone_cache.values())
        known: dict[int, dict] = {}
        for tone_map in maps:
            for raw_id, tone in tone_map.items():
                try:
                    tone_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if tone_id in affected_ids and isinstance(tone, dict):
                    known.setdefault(tone_id, dict(tone))
        return list(known.values())

    def _apply_download_state_updates(self, updated: list[dict]) -> None:
        """Apply worker results on the UI loop and repaint loaded rows."""
        by_id: dict[int, dict] = {}
        for tone in updated:
            try:
                by_id[int(tone["id"])] = tone
            except (KeyError, TypeError, ValueError):
                continue
        if not by_id:
            return
        maps: list[dict[int, dict]] = [self._remote_tones]
        maps.extend(entry.get("tones", {})
                    for entry in self._tone_cache.values())
        for tone_map in maps:
            for tone_id, tone in by_id.items():
                if tone_id in tone_map:
                    tone_map[tone_id] = tone
        try:
            tone_table = self.query_one("#lib-table-tone", DataTable)
        except Exception:
            tone_table = None
        for tone_id, tone in by_id.items():
            if tone_id in self._remote_tones and tone_table is not None:
                self._update_remote_row(
                    tone_table, f"remote:{tone_id}", self._row_cells(tone))

    async def _reconcile_mutation_worker(self, tone_ids: set[int],
                                         generation: int,
                                         request_id: int) -> None:
        """Refresh mutation-dependent remote state without blocking Textual."""
        if not (self._screen_alive(generation)
                and request_id == self._mutation_request_id):
            return
        candidates = self._mutation_download_candidates(tone_ids)
        try:
            updated = (await asyncio.to_thread(
                library.mark_download_state, candidates)
                       if candidates else [])
        except Exception:
            # The committed filesystem/DB mutation remains valid even when a
            # remote state probe fails. Continue with the local reconciliation;
            # the next normal search can retry the remote probe.
            updated = []
        if not (self._screen_alive(generation)
                and request_id == self._mutation_request_id):
            return

        # Everything below this point is UI/cache work and therefore runs
        # after the blocking state probe has returned to the event loop.
        self._reconcile_local_rows(tone_ids)
        self._apply_download_state_updates(updated)
        # The mutation coordinator captures every retained Library view. The
        # async worker must restore every tab-local anchor after its incremental
        # update, not only LOCAL; remote rows and TOP CREATORS may be inactive
        # when the committed install/uninstall lands.
        for pane_id in self._view_states:
            self._restore_view_anchor(pane_id)

        active = self._active_pane
        if active == "pane-local":
            self._update_local_selection_status()
            self._publish_highlight(self._table())
        elif active == "pane-tone":
            self._update_tone_subtitle()
            self._publish_highlight(self._table())
        else:
            self._update_creator_subtitle()
            self._publish_highlight(self._table())

    def reconcile_after_mutation(self, event) -> None:
        """Reconcile only library rows named by a successful mutation."""
        if not getattr(self, "is_mounted", False):
            return
        operations = set(getattr(event, "operations", ()) or ())
        operation = getattr(event, "operation", "")
        library_operations = {"install", "uninstall", "import", "batch"}
        if operation not in library_operations \
                and not operations.intersection(library_operations):
            return

        tone_ids = self._mutation_tone_ids(event)
        if not tone_ids:
            return
        for pane_id in self._view_states:
            self._capture_view_state(pane_id)
        self._mutation_request_id += 1
        generation = self._screen_generation
        request_id = self._mutation_request_id
        self.run_worker(
            partial(self._reconcile_mutation_worker, tone_ids,
                    generation, request_id),
            name="mutation-reconcile", exclusive=False)

    async def _load_more_local(self) -> None:
        """Append the next local SQLite page without resetting the cursor."""
        if not self._local_has_more or self._local_loading:
            return
        self._local_request_id += 1
        request_id = self._local_request_id
        generation = self._screen_generation
        self._local_loading = True
        page = self._local_page + 1
        spec = self._search_spec
        try:
            tones = await asyncio.to_thread(
                library.list_tones,
                gear=None if self._type_filter == "all" else self._type_filter,
                limit=LOCAL_PAGE_SIZE,
                offset=page * LOCAL_PAGE_SIZE,
                authors=self._effective_authors(spec) or None,
                tags=spec.tags or None,
                makes=spec.makes or None,
                model_ids=spec.model_ids or None,
                query=spec.text or None,
                has_files=True,
                sort_by=self._view_states["pane-local"].get("sort", "title"))
        except Exception as exc:
            if self._local_alive(generation, request_id):
                self.notify(f"More local tones unavailable: {exc}", severity="warning")
            return
        finally:
            if self._local_alive(generation, request_id):
                self._local_loading = False
        if not self._local_alive(generation, request_id):
            return
        table = self.query_one("#lib-table-local", DataTable)
        for tone in tones:
            checked = "\\[x]" if tone["id"] in self._local_selected else "\\[ ]"
            table.add_row(checked, *self._row_cells(tone), key=f"local:{tone['id']}")
        self._sync_type_search_options(table)
        self._local_page = page
        self._local_has_more = len(tones) == LOCAL_PAGE_SIZE
        self._update_local_selection_status()

    def _local_cursor_id(self) -> int | None:
        if self._active_pane != "pane-local":
            return None
        table = self.query_one("#lib-table-local", DataTable)
        rows = table.ordered_rows
        if not 0 <= table.cursor_row < len(rows):
            return None
        key = rows[table.cursor_row].key.value
        if not isinstance(key, str) or not key.startswith("local:"):
            return None
        return int(key.partition(":")[2])

    def _update_local_selection_status(self) -> None:
        count = len(self._local_selected)
        state = f"{count} sel"
        set_border_hint_layout(
            self, state,
            [label for label, _action in self._border_hint_actions()])

    def _visible_local_ids(self) -> set[int]:
        try:
            table = self.query_one("#lib-table-local", DataTable)
        except Exception:
            return set()
        return {
            int(row.key.value.partition(":")[2])
            for row in table.ordered_rows
            if isinstance(row.key.value, str) and row.key.value.startswith("local:")
        }

    def toggle_local_selection(self) -> None:
        tone_id = self._local_cursor_id()
        if tone_id is None:
            return
        self.toggle_local_id(tone_id)

    def toggle_local_id(self, tone_id: int) -> None:
        if tone_id in self._local_selected:
            self._local_selected.remove(tone_id)
        else:
            self._local_selected.add(tone_id)
        self.query_one("#lib-table-local", DataTable).update_cell(
            f"local:{tone_id}", "pick", "\\[x]" if tone_id in self._local_selected else "\\[ ]")
        self._update_local_selection_status()

    def toggle_all_local(self) -> None:
        if self._active_pane != "pane-local":
            return
        table = self.query_one("#lib-table-local", DataTable)
        visible = self._visible_local_ids()
        self._local_selected = set() if visible and visible <= self._local_selected else visible
        for tone_id in visible:
            table.update_cell(f"local:{tone_id}", "pick",
                              "\\[x]" if tone_id in self._local_selected else "\\[ ]")
        self._update_local_selection_status()

    def clear_local_selection(self) -> None:
        selected = getattr(self, "_local_selected", set())
        self._local_selected = set()
        try:
            table = self.query_one("#lib-table-local", DataTable)
            for tone_id in selected:
                try:
                    table.update_cell(f"local:{tone_id}", "pick", "\\[ ]")
                except Exception:
                    pass
        except Exception:
            pass
        if hasattr(self, "_active_pane") and self._active_pane == "pane-local":
            self._update_local_selection_status()

    def remove_local_selection(self, tone_ids) -> None:
        """Drop only rows whose uninstall actually changed local storage."""
        removed = {int(tone_id) for tone_id in tone_ids}
        if not removed:
            return
        selected = getattr(self, "_local_selected", set())
        affected = selected.intersection(removed)
        if not affected:
            return
        selected.difference_update(removed)
        try:
            table = self.query_one("#lib-table-local", DataTable)
            for tone_id in affected:
                table.update_cell(f"local:{tone_id}", "pick", "\\[ ]")
        except Exception:
            pass
        if getattr(self, "_active_pane", None) == "pane-local":
            self._update_local_selection_status()

    def uninstall_local_selection(self) -> None:
        if getattr(self, "_active_pane", None) != "pane-local":
            return
        tone_ids = sorted(self._local_selected)
        if not tone_ids:
            cursor_id = self._local_cursor_id()
            tone_ids = [cursor_id] if cursor_id is not None else []
        if tone_ids:
            self.app.push_screen(LocalUninstallScreen(tone_ids))

    def _show_local_filter(self, query: str, spec: SearchSpec | None = None) -> None:
        spec = spec or self._parse_or_notify(query)
        if spec is None:
            return
        self._capture_view_state("pane-local")
        self._set_search_spec(query, spec)
        self._fingerprint = None
        self.refresh_rows()
        self.query_one("#lib-table-local", DataTable).focus()

    # ---- TONE3000 tab -----------------------------------------------------

    def _tone_status_hint(self, spec: SearchSpec) -> str:
        """The hint shown in the #tone-status line for the current view."""
        extra = []
        if spec.authors:
            extra.append("@" + ", @".join(spec.authors))
        if spec.tags:
            extra.append("#" + ", #".join(spec.tags))
        if spec.makes:
            extra.append("make:" + ", ".join(spec.makes))
        if spec.model_ids:
            extra.append("model:" + ", ".join(str(model_id) for model_id in spec.model_ids))
        return " ".join(extra) or "TONE3000"

    def _save_tone_cache(self, key: tuple) -> None:
        """Snapshot the current TONE3000 page set under `key` (FIFO bound)."""
        if len(self._tone_cache) >= _TONE_CACHE_MAX and key not in self._tone_cache:
            self._tone_cache.pop(next(iter(self._tone_cache)))
        self._tone_cache[key] = {
            "tones": dict(self._remote_tones),
            "total": self._tone_total,
            "page": self._tone_page,
            "has_more": self._tone_has_more,
        }

    def _restore_tone_entry(self, key: tuple, *, silent: bool = False) -> None:
        """Render a cached page set without touching the network."""
        entry = self._tone_cache[key]
        self._remote_tones = dict(entry["tones"])
        self._tone_page = entry["page"]
        self._tone_total = entry["total"]
        self._tone_has_more = entry["has_more"]
        table = self._table_for_pane("pane-tone")
        if table is None:
            return
        table.clear()
        table.cursor_type = "row"
        for tone_id, t in self._remote_tones.items():
            table.add_row(*self._row_cells(t, table), key=f"remote:{tone_id}")
        self._sync_type_search_options(table)
        if not silent:
            try:
                status = self.query_one("#tone-status", MarqueeBar)
            except Exception:
                return
            if key[2] == "favorites":
                status.content = "most favorited · enter detail"
            else:
                status.content = self._tone_status_hint(self._search_spec)
        if silent:
            return
        self._update_tone_subtitle()
        self._restore_view_anchor("pane-tone")
        self._publish_highlight(table)
        self._focus_if_pane_active(table)

    async def _show_search(self, query: str, order_by: str | None = None,
                           *, append: bool = False, spec: SearchSpec | None = None,
                           refresh: bool = False, silent: bool = False) -> None:
        """Load one 40-row TONE3000 page, preserving prior rows on append.

        Results are cached per (query, TYPE filter, SORT); a
        cache hit renders the page set without a network request. `refresh`
        bypasses the cache (manual reload); `silent` (startup prefetch) only
        fills the cache and the hidden table without touching status chrome.
        """
        spec = spec or self._parse_or_notify(query)
        if spec is None:
            return
        if not silent and self._active_pane != "pane-tone":
            return
        generation = self._screen_generation
        if not append:
            self._capture_view_state("pane-tone")
        if append:
            request_id = self._tone_request_id
            key = self._tone_cache_key
        else:
            self._tone_request_id += 1
            request_id = self._tone_request_id
            key = (query, self._type_filter, self._sort)
            self._tone_cache_key = key
            self._tone_error = False
            self._clear_tone_auth_required()
        self._set_search_spec(query, spec)
        self._tone_request_view = (
            None if silent else self._view_identity("pane-tone"))
        if not self._screen_alive(generation):
            return
        table = self._table_for_pane("pane-tone")
        if table is None:
            return
        try:
            status = self.query_one("#tone-status", MarqueeBar)
        except Exception:
            return
        # A page request owns this flag for its complete lifetime.  Cursor
        # events only ask the worker to start a request; they do not mutate
        # loading state themselves.
        if append and (not self._tone_has_more or self._tone_loading):
            return
        if not append:
            if not refresh and key in self._tone_cache:
                # Cache hit: render the cached page set, no network request.
                if self._tone_alive(generation, request_id):
                    self._restore_tone_entry(key, silent=silent)
                return
            self._tone_page = 0
            self._tone_total = None
            self._tone_has_more = False
            if not silent:
                self._show_status_if_empty(table, "loading…")
                status.content = "loading…"
                # 不在此清 detail：搜索/换排序/刷新期间保留上一条选中内容，
                # 落定后由 _publish_highlight 替换（REQ-011：搜索瞬间空态
                # 一闪、失败后永久空态）。空结果在收尾处显式清空。
        self._tone_loading = True
        if not silent:
            self._update_tone_subtitle(loading=True)
        page = self._tone_page + 1
        try:
            if spec.model_ids:
                hits = await asyncio.to_thread(
                    library.tone3000.tones_for_model_ids, spec.model_ids)
            else:
                hits = await asyncio.to_thread(
                    library.tone3000.search,
                    spec.text, page_size=REMOTE_PAGE_SIZE, page_number=page,
                    order_by=order_by or self._selected_order(),
                    gear_filters=None if self._type_filter == "all" else [self._type_filter],
                    usernames=self._effective_authors(spec) or None,
                    tag_names=list(spec.tags) or None,
                    make_names=list(spec.makes) or None)
        except library.tone3000.AuthenticationRequiredError:
            if not self._tone_alive(generation, request_id):
                return
            self._show_tone_auth_required(table, silent=silent)
            return
        except Exception as e:
            if not self._tone_alive(generation, request_id):
                return
            self._tone_loading = False
            if silent:
                return  # startup prefetch failed; the tab visit reloads normally
            if append and self._remote_tones:
                self._update_tone_subtitle(error=True)
                self.notify(self._network_error("More results", e), severity="warning")
            else:
                self._update_tone_subtitle(error=True)
                self._show_status_if_empty(
                    table, self._network_error("TONE3000 search", e))
            return
        if not self._tone_alive(generation, request_id):
            return
        # Exact model lookups already identify the requested files. Resolve
        # their local state from SQLite directly instead of issuing another
        # remote model-list request before rendering the result.
        # A normal search must render its remote rows before probing each
        # locally known tone's model list.  The latter is an optional status
        # enrichment and can take several seconds when many visible tones are
        # installed.  Keep exact model lookup and silent prefetch unchanged so
        # their cache/state semantics remain deterministic.
        if spec.model_ids or silent:
            try:
                if spec.model_ids:
                    local_by_tone = await asyncio.to_thread(
                        library.downloaded_model_ids_by_tone)
                    for hit in hits:
                        matched = set(
                            hit.get("matched_model_ids") or spec.model_ids)
                        downloaded = matched & local_by_tone.get(
                            int(hit["id"]), set())
                        hit["downloaded"] = len(downloaded)
                        hit["download_state"] = (
                            "all" if matched and downloaded >= matched else
                            "partial" if downloaded else "none")
                else:
                    hits = await asyncio.to_thread(
                        library.mark_download_state, hits)
            except library.tone3000.AuthenticationRequiredError:
                if not self._tone_alive(generation, request_id):
                    return
                self._show_tone_auth_required(table, silent=silent)
                return
            except Exception as e:
                if not self._tone_alive(generation, request_id):
                    return
                self._tone_loading = False
                if not silent:
                    self._update_tone_subtitle(error=True)
                    self._show_status_if_empty(
                        table, self._network_error("TONE3000 search", e))
                return
        if not self._tone_alive(generation, request_id):
            return
        self._tone_loading = False
        self._clear_tone_auth_required()
        self._tone_page = page
        total = next((hit.get("total_count") for hit in hits
                      if hit.get("total_count") is not None), None)
        try:
            self._tone_total = int(total) if total is not None else self._tone_total
        except (TypeError, ValueError):
            pass
        if not append:
            self._remote_tones = {}
            table.clear()
        table.cursor_type = "row"
        for t in hits:
            tone_id = int(t["id"])
            if tone_id not in self._remote_tones:
                self._remote_tones[tone_id] = t
                table.add_row(*self._row_cells(t, table), key=f"remote:{tone_id}")
        self._sync_type_search_options(table)
        self._tone_has_more = not spec.model_ids and (
            len(self._remote_tones) < self._tone_total
            if self._tone_total is not None else len(hits) == REMOTE_PAGE_SIZE)
        if key is not None:
            self._save_tone_cache(key)
        if silent:
            return  # prefetch done: cache filled, UI chrome untouched
        status.content = self._tone_status_hint(spec)
        self._update_tone_subtitle()
        if not append and not table.row_count:
            # 空结果：没有可显示的 tone，此时才清 detail（不再是搜索瞬间）
            self._highlighted_key = None
            self.post_message(ToneHighlighted(None))
            self._focus_if_pane_active(table)
            return
        if not append:
            # A first-page/filter reload rebuilds the table, so restore its
            # stable row and viewport anchor. An append only adds rows; using
            # an anchor captured before the request would overwrite any cursor
            # movement the user made while the network request was in flight.
            self._restore_view_anchor("pane-tone")
        self._publish_highlight(table)
        if not append:
            self._focus_if_pane_active(table)

        if not spec.model_ids:
            # Keep the list usable while the optional download-state probe
            # checks the remote model sets for locally known tones.
            try:
                updated = await asyncio.to_thread(
                    library.mark_download_state,
                    [dict(tone) for tone in hits])
            except Exception:
                return
            if not self._tone_alive(generation, request_id):
                return
            self._apply_download_state_updates(updated)

    async def _load_more_tones(self) -> None:
        """Fetch the next remote page without moving the cursor or clearing rows."""
        await self._show_search(self._query, order_by=self._selected_order(), append=True)

    async def _login_tone3000(self) -> None:
        """Open OAuth in the browser, then reload the active remote view."""
        creator_view = self._active_pane == "pane-creators"
        button_id = ("#creators-login-button" if creator_view
                     else "#tone-login-button")
        try:
            button = self.query_one(button_id, Button)
            table = (self._creator_table() if creator_view
                     else self._table_for_pane("pane-tone"))
        except Exception:
            return
        if table is None:
            return
        button.disabled = True
        try:
            await asyncio.to_thread(library.tone3000.login)
        except library.tone3000.AuthenticationRequiredError:
            show_auth = (self._show_creator_auth_required if creator_view
                         else self._show_tone_auth_required)
            show_auth(
                table,
                message="TONE3000 login cancelled — select Log in to retry.")
            return
        except Exception:
            show_auth = (self._show_creator_auth_required if creator_view
                         else self._show_tone_auth_required)
            show_auth(
                table,
                message="TONE3000 login unavailable — select Log in to retry.")
            return
        finally:
            button.disabled = False
        if creator_view:
            self._clear_creator_auth_required()
            self._creator_cache = None
            self.run_worker(partial(self._show_top_creators, refresh=True),
                            name="creators", exclusive=True)
        else:
            self._clear_tone_auth_required()
            if self._tone_cache_key is not None:
                self._tone_cache.pop(self._tone_cache_key, None)
            self.run_worker(partial(self._reload_tone_table, refresh=True),
                            name="search", exclusive=True)

    # ---- recommended views (TONE3000 tab) ---------------------------------

    async def _reload_tone_table(self, *, refresh: bool = False,
                                 silent: bool = False) -> None:
        """Reload the TONE3000 tab per the SORT picker: trending / most
        downloaded / most favorited / newest (mirrors tone3000.com's sort
        options; favorites reads the tones_counts table since the search RPC
        has no favorites ordering)."""
        sort = self._sort
        generation = self._screen_generation
        if not silent:
            self._capture_view_state("pane-tone")
        if not self._screen_alive(generation):
            return
        table = self._table_for_pane("pane-tone")
        if table is None:
            return
        try:
            status = self.query_one("#tone-status", MarqueeBar)
        except Exception:
            return
        if sort == "favorites":
            self._tone_request_id += 1
            request_id = self._tone_request_id
            key = (self._query, self._type_filter, "favorites")
            self._tone_cache_key = key
            # 与 _show_search 对齐：请求前保存视图快照，否则 _tone_alive
            # 拿着上一次 sort 的残留 identity 判定本请求失效，加载完成后
            # 静默 return，loading 永久残留（MOST FAVORITED 卡 "loading…"）。
            self._tone_request_view = (
                None if silent else self._view_identity("pane-tone"))
            if not refresh and key in self._tone_cache:
                if self._tone_alive(generation, request_id):
                    self._tone_loading = False
                    self._restore_tone_entry(key, silent=silent)
                return
            self._tone_loading = True
            if not silent:
                self._show_status_if_empty(table, "loading…")
                status.content = "loading…"
                self._update_tone_subtitle(loading=True)
                # 同 _show_search：加载期间不清 detail（REQ-011）
            try:
                # 解析当前搜索框输入并应用到收藏榜：text → title/description
                # ilike，authors → users 表反查 user_id 过滤（_show_search 的
                # 同款语义）。解析失败（已 notify 语法错误）时降级复用上次
                # 成功解析的 _search_spec（与 _query 由 _set_search_spec
                # 同步），初始为空 SearchSpec() 即无过滤全局榜，行为与
                # 修复前一致。tag:/make: 无 tones_counts 字段支持，忽略。
                spec = self._parse_or_notify(self._query) or self._search_spec
                hits = await asyncio.to_thread(
                    library.tone3000.top_favorites, 50,
                    text=spec.text or None,
                    usernames=self._effective_authors(spec) or None)
            except library.tone3000.AuthenticationRequiredError:
                if not self._tone_alive(generation, request_id) or silent:
                    return
                self._show_tone_auth_required(table)
                return
            except Exception as e:
                if not self._tone_alive(generation, request_id) or silent:
                    return
                self._tone_loading = False
                self._update_tone_subtitle(error=True)
                self._show_status_if_empty(
                    table, self._network_error("Favorites", e))
                return
            if not self._tone_alive(generation, request_id):
                return
            try:
                hits = await asyncio.to_thread(library.mark_download_state, hits)
            except library.tone3000.AuthenticationRequiredError:
                if not self._tone_alive(generation, request_id) or silent:
                    return
                self._show_tone_auth_required(table)
                return
            except Exception as e:
                if not self._tone_alive(generation, request_id) or silent:
                    return
                self._tone_loading = False
                self._update_tone_subtitle(error=True)
                self._show_status_if_empty(
                    table, self._network_error("Favorites", e))
                return
            if not self._tone_alive(generation, request_id):
                return
            self._tone_loading = False
            self._clear_tone_auth_required()
            self._remote_tones = {}
            table.clear()
            table.cursor_type = "row"
            for t in hits:
                self._remote_tones[int(t["id"])] = t
                table.add_row(*self._row_cells(t, table), key=f"remote:{t['id']}")
            self._tone_total = None
            self._tone_page = 1
            self._tone_has_more = False
            self._save_tone_cache(key)
            if silent:
                return
            status.content = "most favorited · enter detail"
            if not table.row_count:
                # 空结果才清 detail（REQ-011）
                self._highlighted_key = None
                self.post_message(ToneHighlighted(None))
                self._focus_if_pane_active(table)
                return
            self._restore_view_anchor("pane-tone")
            self._publish_highlight(table)
            self._focus_if_pane_active(table)
        else:
            order = self._selected_order()
            await self._show_search(self._query or "", order_by=order,
                                    refresh=refresh, silent=silent)

    def _update_creator_subtitle(self, *, loading: bool = False,
                                 error: bool = False) -> None:
        if loading:
            self._creator_error = False
            set_border_hint_layout(
                self, "loading…",
                [token for token, _action in self._border_hint_actions()])
            return
        count = len(self._creator_tones)
        if self._creator_auth_required:
            self._creator_error = False
            state = "login required"
        elif error:
            self._creator_error = True
            state = f"{count} · load failed"
        else:
            self._creator_error = False
            state = str(count) if self._creator_has_more else f"{count} · all loaded"
        set_border_hint_layout(
            self, state,
            [token for token, _action in self._border_hint_actions()])

    def _save_creator_cache(self) -> None:
        """Snapshot the TOP CREATORS view (single-view cache)."""
        self._creator_cache = {
            "tones": dict(self._creator_tones),
            "total": self._creator_total,
            "page": self._creator_page,
            "has_more": self._creator_has_more,
        }

    @staticmethod
    def _creator_row_count(rows: list[dict]) -> int:
        """Exact public tone count from TONE3000's leaderboard view."""
        return int(rows[0].get("public_tones_count") or 0) if rows else 0

    def _creator_sort_key(self, name: str, tones_: list[dict]) -> int:
        """Exact leaderboard sort value from ``user_public_counts``."""
        if not tones_:
            return 0
        column = {
            "tones": "public_tones_count",
            "downloads": "downloads_count",
            "favorites": "favorites_count",
            "models": "public_models_count",
        }.get(self._creator_sort, "public_tones_count")
        return int(tones_[0].get(column) or 0)

    def _creator_table(self) -> DataTable | None:
        """Return the creators table while it is still mounted.

        Startup prefetch workers can finish during app shutdown, after the
        TabPane has been detached but before the worker observes its generation
        change. Treat that teardown window as a cancelled view instead of
        raising ``NoMatches`` from a background task.
        """
        try:
            return self.query_one("#lib-table-creators", DataTable)
        except Exception:
            return None

    @staticmethod
    def _author_label(username: str | None) -> str:
        """Render an author consistently from the shared positive cache."""
        name = str(username or "?")
        badge = " ✓" if library.tone3000.is_verified(name) else ""
        return f"@{name}{badge}"

    def mark_verified_author(self, username: str) -> None:
        """Update loaded author cells after a verification cache write."""
        name = str(username or "").lower()
        if not name or not library.tone3000.is_verified(name):
            return
        for table_id in ("lib-table-local", "lib-table-tone"):
            try:
                table = self.query_one(f"#{table_id}", DataTable)
            except Exception:
                continue
            for row in table.ordered_rows:
                tone = self._tone_for_key(row.key.value)
                if tone and str(tone.get("username") or "").lower() == name:
                    table.update_cell(
                        row.key, "author",
                        self._author_label(tone.get("username")),
                        update_width=False)
        table = self._creator_table()
        if table is not None:
            for row in table.ordered_rows:
                key = row.key.value
                if (isinstance(key, str) and key.startswith("creator:")
                        and key.partition(":")[2].lower() == name):
                    creator_name = key.partition(":")[2]
                    table.update_cell(
                        row.key, "creator", self._author_label(creator_name),
                        update_width=False)

    def _restore_creator_entry(self, *, silent: bool = False) -> None:
        """Render the cached TOP CREATORS view without a network request."""
        entry = self._creator_cache
        self._creator_tones = dict(entry["tones"])
        self._creator_total = entry["total"]
        self._creator_page = entry["page"]
        self._creator_has_more = entry["has_more"]
        # 不清 _remote_tones：TONE3000 表行仍显示，Enter 需要这张查找表
        # （REQ-009 语义）。
        table = self._creator_table()
        if table is None:
            return
        ranked = sorted(self._creator_tones.items(),
                        key=lambda kv: -self._creator_sort_key(kv[0], kv[1]))
        if not self._render_creator_rows(ranked, table):
            return
        if not ranked:
            self._status_row(table, "No creator data")
        if silent:
            return
        self._update_creator_subtitle()
        self._restore_view_anchor("pane-creators")
        self._publish_highlight(table)
        self._focus_if_pane_active(table)

    async def _show_top_creators(self, limit: int = 0, *, append: bool = False,
                                 refresh: bool = False, silent: bool = False) -> None:
        """Render TONE3000's official paged creator leaderboard.

        Each row comes from ``user_public_counts`` with stable aggregate values;
        no provisional tone-page aggregation or background count rewrite occurs.
        The view is cached: a cache hit renders without a network request;
        `refresh` bypasses the cache, and `silent` fills the startup cache.
        """
        if not silent and self._active_pane != "pane-creators":
            return
        generation = self._screen_generation
        if not append and not silent:
            self._capture_view_state("pane-creators")
        if append:
            request_id = self._creator_request_id
        else:
            request_id = self._creator_request_id
            if not refresh and self._creator_cache is not None:
                if self._creator_alive(generation, request_id):
                    self._creator_loading = False
                    self._restore_creator_entry(silent=silent)
                return
            self._creator_request_id += 1
            request_id = self._creator_request_id
            self._creator_error = False
            self._clear_creator_auth_required()
        self._creator_request_view = (
            None if silent else self._view_identity("pane-creators"))
        table = self._creator_table()
        if table is None:
            return
        if append and (not self._creator_has_more or self._creator_loading):
            return
        if not append:
            # 不清 _remote_tones：TONE3000 表行仍显示，Enter 需要这张查找表
            # （REQ-009 根因：此处的清空让 remote 行 Enter/双击静默无效）。
            if not silent:
                self._show_status_if_empty(table, "loading…")
        self._creator_loading = True
        if not silent:
            self._update_creator_subtitle(loading=True)
        page = self._creator_page + 1 if append else 1
        try:
            hits = await asyncio.to_thread(
                library.tone3000.top_creators,
                sort_by=self._creator_sort,
                page_size=CREATOR_PAGE_SIZE,
                page_number=page)
        except library.tone3000.AuthenticationRequiredError:
            if not self._creator_alive(generation, request_id):
                return
            table = self._creator_table()
            if table is None:
                return
            self._show_creator_auth_required(table, silent=silent)
            return
        except Exception as e:
            if not self._creator_alive(generation, request_id):
                return
            table = self._creator_table()
            if table is None:
                return
            self._creator_loading = False
            if silent:
                return  # startup prefetch failed; the tab visit reloads normally
            if append and self._creator_tones:
                self.notify(self._network_error("More creators", e), severity="warning")
                self._update_creator_subtitle(error=True)
            else:
                self._update_creator_subtitle(error=True)
                self._show_status_if_empty(
                    table, self._network_error("Top creators", e))
            return
        if not self._creator_alive(generation, request_id):
            return
        # The table may have been detached during app shutdown even if the
        # worker generation has not been invalidated yet.
        table = self._creator_table()
        if table is None:
            return
        if not append:
            self._creator_page = 0
            self._creator_total = None
            self._creator_has_more = False
            self._creator_tones = {}
            table.clear()
        self._clear_creator_auth_required()
        self._creator_loading = False
        self._creator_page = page
        existing_count = len(self._creator_tones)
        new_creators: list[tuple[str, list[dict]]] = []
        for creator in hits:
            username = creator.get("username")
            if username and username not in self._creator_tones:
                self._creator_tones[username] = [creator]
                new_creators.append((username, [creator]))
        self._creator_has_more = len(hits) == CREATOR_PAGE_SIZE
        if append:
            start_rank = existing_count + 1
            for offset, (name, tones_) in enumerate(new_creators):
                self._add_creator_row(table, start_rank + offset, name, tones_)
            shown = list(self._creator_tones.items())
        else:
            shown = list(self._creator_tones.items())
            if limit > 0:
                shown = shown[:limit]
            if not self._render_creator_rows(shown, table):
                return
        if not shown:
            self._status_row(table, "No creator data")
        self._save_creator_cache()
        if not silent:
            if self._active_pane == "pane-creators":
                self._update_creator_subtitle()
            # Appending is intentionally left to DataTable's natural row
            # insertion. Restoring an anchor here would actively scroll the
            # table and can overwrite a position changed while the request ran.
            if not append:
                self._restore_view_anchor("pane-creators")
                self._publish_highlight(table)
                self._focus_if_pane_active(table)

    def _render_creator_rows(
            self, ranked: list[tuple[str, list[dict]]],
            table: DataTable | None = None) -> bool:
        """排行榜行渲染（Most X 降序的 ranked 列表 → 6 列）。"""
        table = table or self._creator_table()
        if table is None:
            return False
        table.clear()
        table.cursor_type = "row"
        for rank, (name, tones_) in enumerate(ranked, 1):
            self._add_creator_row(table, rank, name, tones_)
        return True

    def _add_creator_row(
            self, table: DataTable, rank: int, name: str,
            tones_: list[dict]) -> None:
        creator = tones_[0] if tones_ else {}
        table.add_row(str(rank), self._author_label(name),
                      str(self._creator_row_count(tones_)),
                      str(creator.get("downloads_count") or 0),
                      str(creator.get("favorites_count") or 0),
                      str(creator.get("public_models_count") or 0),
                      key=f"creator:{name}")

    async def _load_more_creators(self) -> None:
        await self._show_top_creators(append=True)

    # ---- shared row rendering ---------------------------------------------

    def _row_cells(self, t: dict, table: DataTable | None = None) -> list[str]:
        title = str(t.get("title") or "")
        matched_model_ids = t.get("matched_model_ids") or ()
        if matched_model_ids:
            title = f"{title} · model #{', #'.join(str(i) for i in matched_model_ids)}"
        state = t.get("download_state")
        marker_plain = ("✓ " if state in ("all", "partial") else "").ljust(2)
        # 先拼完整标题（含 model 后缀）再截断，marker 宽度一并计入：
        # 总宽不超过列宽，尾部 … 与后缀不会被表格渲染裁剪掉。列宽随表格
        # 缩放自适应（LibraryTable._title_cell_limit），此处同步使用。
        limit = getattr(table or self._table(), "_title_cell_limit", 54)
        title = _clip(title, max(limit - cell_len(marker_plain), 1))
        marker_markup = (
            "[bold $success]✓[/] "
            if state in ("all", "partial") else "")
        title = f"{marker_markup}{escape(title)}"
        # Files 只数可用模型（A2 + Custom + IR）；A1（WaveNet）已从产品
        # 过滤，算进总数会让下载状态永远显示 partial。
        total = ((t.get("a2_models_count") or 0)
                 + (t.get("custom_models_count") or 0)
                 + (t.get("irs_count") or 0))
        files = str(total)
        if t.get("downloaded") is not None and total:
            files = f"{t['downloaded']}/{total}"
        return [
            title, gear_markup(t.get("gear"), colors=theme_colors(self.app)),
            self._author_label(t.get("username")),
            str(t.get("downloads_count") or 0), str(t.get("favorites_count") or 0),
            _uploaded(t), tone_format(t) or "—", files, _arch(t),
        ]

    def _tone_for_key(self, key: str | None) -> dict | None:
        if not key:
            return None
        kind, _, rest = key.partition(":")
        if kind == "creator":
            # A TOP CREATORS row is an aggregate; follow the cursor with the
            # first tone of that creator so the detail pane never blanks.
            tones = self._creator_tones.get(rest)
            return tones[0] if tones else None
        try:
            tone_num = int(rest)
        except ValueError:
            return None
        if kind == "remote":
            return self._remote_tones.get(tone_num)
        if kind == "local":
            return library.get_tone(tone_num)
        return None

    def _publish_highlight(self, table: DataTable) -> None:
        """Send the current row's metadata to the detail pane without side effects."""
        pane_id = _TABLE_PANE.get(table.id or "")
        if pane_id is not None and pane_id != self._active_pane:
            return
        rows = table.ordered_rows
        key_value = None
        if 0 <= table.cursor_row < len(rows):
            key_value = rows[table.cursor_row].key.value
        if key_value == "__clear__":
            if isinstance(table, LibraryTable):
                table.set_focused_tone(None, None)
            # explicit clear (search failed); reset so the next search can
            # publish again
            self._highlighted_key = None
            self.post_message(ToneHighlighted(None))
            return
        if key_value == "__status__":
            return  # 加载/失败提示行不驱动 detail（REQ-011）
        if key_value and key_value.startswith("creator:"):
            # REQ-012：creators 行 → 作者信息 + top 音色列表视图（取代
            # "聚合首 tone"映射——用户实测 detail 显示无关单音色）。
            if key_value == self._highlighted_key:
                return
            self._highlighted_key = key_value
            self.post_message(CreatorFocused(key_value.partition(":")[2]))
            return
        tone = self._tone_for_key(key_value)
        remote = bool(key_value and key_value.startswith(("remote:", "favorite:")))
        if isinstance(table, LibraryTable):
            table.set_focused_tone(key_value, tone)
        if key_value == self._highlighted_key:
            return
        self._highlighted_key = key_value

        if key_value:
            self.post_message(ToneHighlighted(tone, remote=remote))

    # ---- widget events ------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        spec = self._parse_or_notify(query)
        if spec is None:
            return
        if event.input.id == "local-search":
            if query:
                self._show_local_filter(query, spec)
            else:
                self._capture_view_state("pane-local")
                self._set_search_spec("", SearchSpec())
                self._fingerprint = None
                self.refresh_rows()
        elif event.input.id == "tone-search":
            self._set_search_spec(query, spec)
            if query:
                self.run_worker(
                    partial(self._show_search, query,
                            order_by=self._selected_order(), spec=spec),
                    name="search", exclusive=True)
            else:
                # An empty TONE3000 query is the public Trending feed; do not
                # jump back to LOCAL merely because the input was cleared.
                self.run_worker(partial(self._reload_tone_table), name="search", exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id not in {"tone-login-button", "creators-login-button"}:
            return
        self.run_worker(self._login_tone3000, name="tone-login",
                        group="tone-login", exclusive=True)

    def _border_hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        view_action = (
            ViewTabStrip.NAVIGATION_HINT,
            lambda: self.query_one(
                "#library-view-tabs", ViewTabStrip).action_next_view(),
        )
        active = getattr(self, "_active_pane", "pane-local")
        if active == "pane-local":
            visible = self._visible_local_ids()
            all_label = "a none" if visible and visible <= self._local_selected else "a all"
            width = self.region.width or (self.size.width + 4)
            short = width < 56
            actions: list[tuple[str, Callable[[], None]]] = []
            if self._local_has_more:
                actions.append(("↓ more", self._load_more_from_hint))
            actions.extend([
                (all_label, self.toggle_all_local),
                (("space" if short else "space select"),
                 self.toggle_local_selection),
                (("u del" if short else "u uninstall"),
                 self.uninstall_local_selection),
            ])
            if self._local_selected:
                actions.append((("esc" if short else "esc clear"),
                                self.clear_local_selection))
            else:
                actions.append(("enter open",
                                lambda: self._table().action_select_cursor()))
            return [*actions, view_action]
        if active == "pane-tone":
            actions: list[tuple[str, Callable[[], None]]] = []
            if self._tone_auth_required:
                actions.append(("log in", self._focus_tone_login))
            elif self._tone_error:
                actions.append(("r retry", self.retry_active))
            elif self._tone_has_more:
                actions.append(("↓ more", self._load_more_from_hint))
            actions.append(("enter detail", lambda: self._table().action_select_cursor()))
            return [*actions, view_action]
        actions = []
        if self._creator_auth_required:
            actions.append(("log in", self._focus_creator_login))
        elif self._creator_error:
            actions.append(("r retry", self.retry_active))
        elif self._creator_has_more:
            actions.append(("↓ more", self._load_more_from_hint))
        actions.append(("enter search", lambda: self._table().action_select_cursor()))
        return [*actions, view_action]

    def _click_border_hint(self, event: MouseEvent) -> bool:
        hit = border_hint_hit(self, event.screen_x, event.screen_y)
        if hit is None:
            return False
        label, offset = hit
        for token, action in self._border_hint_actions():
            span = hint_span(label, token)
            if span is not None and span[0] <= offset < span[1]:
                event.stop()
                action()
                return True
        return False

    def on_click(self, event: MouseEvent) -> None:
        if self.query_one("#library-view-tabs", ViewTabStrip).activate_from_border(event):
            return
        self._click_border_hint(event)

    def on_mouse_move(self, event: MouseMove) -> None:
        strip = self.query_one("#library-view-tabs", ViewTabStrip)
        strip.hover_from_border(event)
        tokens = [token for token, _ in self._border_hint_actions()]
        set_border_hint_hover(
            self,
            border_hint_action_token(self, event.screen_x, event.screen_y, tokens),
        )

    def on_leave(self, event: Leave) -> None:
        self.query_one("#library-view-tabs", ViewTabStrip).clear_border_hover()
        set_border_hint_hover(self, None)

    def on_select_changed(self, event: Select.Changed) -> None:
        # Select emits its initial value while the panel is composing. Do not
        # start a remote worker until on_mount has established the active tab.
        if not hasattr(self, "_active_pane"):
            return
        if event.select.id == "sort-filter":
            self._sort = str(event.value)
            self._view_states["pane-tone"]["sort"] = self._sort
            if self._active_pane == "pane-tone":
                self.run_worker(partial(self._reload_tone_table), name="search",
                                exclusive=True)
            return
        if event.select.id == "sort-filter-local":
            self._view_states["pane-local"]["sort"] = str(event.value)
            if self._active_pane == "pane-local":
                self._fingerprint = None
                self.refresh_rows()
            return
        if event.select.id == "sort-filter-creators":
            self._creator_sort = str(event.value)
            self._view_states["pane-creators"]["sort"] = self._creator_sort
            if self._active_pane == "pane-creators":
                # Each sort is a distinct official leaderboard query; the
                # currently loaded page is not a complete local data set.
                self._creator_cache = None
                self.run_worker(partial(self._show_top_creators, refresh=True),
                                name="creators", exclusive=True)
            return
        type_search_panes = {
            "type-filter-local-search": "pane-local",
            "type-filter-tone-search": "pane-tone",
        }
        pane_id = type_search_panes.get(event.select.id or "")
        if pane_id is not None:
            # set_options() posts a transient value before the selected value
            # is restored. Ignore that stale event instead of starting a
            # second reload and feeding the selector back into itself.
            if event.value != event.select.value:
                return
            value = str(event.value)
            if value == self._type_filters.get(pane_id, "all"):
                return
            self._capture_view_state(pane_id)
            self._type_filters[pane_id] = value
            self._view_states[pane_id]["type_filter"] = value
            if pane_id == "pane-local":
                self._type_filter = value
                self._fingerprint = None
                if self._active_pane == pane_id:
                    self.refresh_rows()
            elif pane_id == "pane-tone":
                self._type_filter = value
                if self._active_pane == pane_id:
                    self.run_worker(partial(self._reload_tone_table), name="search",
                                    exclusive=True)
            return
        return

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Table headers are informational; filtering lives in SearchBar."""
        return

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
        if not key or key == "__status__" or key == self._highlighted_key:
            return
        self._publish_highlight(event.data_table)
        self._maybe_load_more_from_viewport(event.data_table)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        if not key:
            return
        if key == "__status__":
            # 加载/失败提示行：Enter/双击 = 重试当前视图（REQ-011：此前静默
            # 吞掉，用户"操作了一下"后在加载/失败窗口 Enter 全部无效）。
            if self._active_pane == "pane-tone" and self._tone_auth_required:
                self._focus_tone_login()
            elif self._active_pane == "pane-creators" and self._creator_auth_required:
                self._focus_creator_login()
            else:
                self.retry_active()
            return
        kind, _, tid = key.partition(":")
        if kind == "local":
            self.post_message(ToneSelected(int(tid)))
        elif kind == "creator":
            # REQ-033：作者行 Enter/双击 → 跳 TONE3000 搜索 @author——
            # 搜索栏填上 @名并触发真实搜索（作者信息聚焦联动保留）。
            self._search_creator(tid)
        elif kind == "remote":
            # Canonical v0.2 enters the Remote PACK in DetailPane. The PACK
            # owns target loading and routes uninstalled rows to the larger
            # install screen.
            tone = self._remote_tones.get(int(tid))
            if tone is None:
                # 查找表与音色表失配（旧版本在 TOP CREATORS/本地刷新时清过
                # 它）：行还在但数据没了——重载音色表而不是静默无响应。
                self.notify("Tone list expired — reloading", severity="warning")
                self.run_worker(partial(self._reload_tone_table),
                                name="search",
                                exclusive=True)
                return
            try:
                canonical = not self.app.query_one("ChainPanel")._legacy_mode
            except Exception:
                canonical = False
            if canonical:
                self.post_message(RemoteToneSelected(tone))
            else:
                self.app.push_screen(PackInstallScreen(tone))
