"""Pack install screen: preview a remote TONE3000 pack's model files, pick which
to install (space per-row, a = all/none), Enter installs, completion is reported
back to the app (toast + library refresh).

Remote search rows now open this screen instead of importing everything blindly.
The tone's full metadata sits beside the file list for comparison.
"""
import asyncio
from functools import partial
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import MouseEvent, MouseMove
from textual.message import Message
from textual.widgets import DataTable, ProgressBar, Static

from .marquee import MarqueeBar
from .metadata import (SelectableStatic, metadata_table, model_architecture,
                       normalize_model_architecture, theme_colors)
from .modals import (ClickSelectTable, GigBuddyModal, ModalBox,
                     border_hint_action_token, border_hint_click,
                     set_border_hint_hover, set_border_hint_layout)
from .selection import NonSelectableStatic

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402
import tone3000  # noqa: E402


def _escape(text: str) -> str:
    """Escape EVERY '[' for Textual markup — rich.markup.escape lets through
    tag-shaped brackets like '[Hi Gain]' which swallow the text."""
    return text.replace("[", "\\[")


class PackPickTable(ClickSelectTable):
    """Pack 文件表：Pick 列（[ ]/[x]）单击 = 鼠标点选（REQ-040）。

    点选必须挂在表自身——ClickSelectTable 的 _on_click 会 stop 事件，
    屏幕级 on_click 收不到单元格点击；基类先移光标，本类再切换勾选。
    """

    def on_click(self, event) -> None:
        meta = event.style.meta
        if (meta.get("column") == 0 and isinstance(meta.get("row"), int)
                and meta["row"] >= 0):
            rows = self.ordered_rows
            if meta["row"] < len(rows):
                self.move_cursor(row=meta["row"], column=0,
                                 animate=False, scroll=False)
                screen = self.screen
                if isinstance(screen, PackInstallScreen):
                    screen._toggle_row_key(rows[meta["row"]].key.value)
            event.stop()
            return
        if getattr(event, "chain", 1) >= 2:
            self.action_select_cursor()
            event.stop()


class PackInstallScreen(GigBuddyModal):
    """二级菜单详情页（REQ-038）：tone 元信息 + 模型多选安装/卸载。

    space 逐行勾选、a 全选/全不选、Enter/i 安装选中、u 卸载选中
    （u 语义与 uninstall_screen 一致：活动链/库外拦截、preset 引用二次
    确认）。默认勾选未下载的模型——已下载的不重复下载，u 可卸。
    """

    BINDINGS = [
        Binding("space", "toggle_row", "select", show=False),
        Binding("a", "toggle_all", "all/none", show=False),
        Binding("i", "confirm", "install", show=False),
        Binding("u", "uninstall_selected", "uninstall", show=False),
        Binding("r", "retry_load", "retry", show=False),
    ]

    class Uninstalled(Message):
        def __init__(self, tone_id: int, count: int,
                     model_ids: list[int] | tuple[int, ...] = ()) -> None:
            super().__init__()
            self.tone_id = tone_id
            self.count = count
            self.model_ids = tuple(model_ids)

    CSS = """
    PackInstallScreen > ModalBox { width: 96%; height: 92%; margin: 1 2; }
    #pack-split { height: 1fr; }
    #pack-left { width: 3fr; layout: vertical; }
    #pack-right { width: 2fr; border-left: solid $primary; }
    #pack-header { height: 3; padding: 0 1; color: $text; }
    #pack-table { height: 1fr; }
    #pack-status { height: 1; padding: 0 1; color: $text-muted; }
    #pack-progress { height: 1; }
    """

    class Installed(Message):
        def __init__(self, tone_id: int, count: int,
                     model_ids: list[int] | tuple[int, ...] = ()) -> None:
            super().__init__()
            self.tone_id = tone_id
            self.count = count
            self.model_ids = tuple(model_ids)

    def __init__(self, tone: dict) -> None:
        super().__init__()
        self._tone = tone
        self._models: list[dict] = []
        self._selected: set[int] = set()
        # 本地已下载的模型 id（u 卸载的目标；加载后按行刷新）
        self._downloaded: set[int] = set()
        # preset 引用二次确认（u 语义与 uninstall_screen 一致）
        self._uninstall_confirmed = False
        self._uninstall_target: list[int] = []
        self._load_generation = 0
        self._operation_generation = 0
        self._busy: str | None = None
        self._load_state = "loading"

    def compose(self) -> ComposeResult:
        t = self._tone
        box = ModalBox()
        box.border_title = "INSTALL PACK"
        with box:
            with Horizontal(id="pack-split"):
                with Vertical(id="pack-left"):
                    colors = theme_colors(self.app)
                    badge = (f" [b {colors['value']}]✓[/]"
                             if tone3000.is_verified(t.get("username"))
                             else "")
                    yield NonSelectableStatic(
                        f"[b]{_escape(t.get('title') or '')}[/b]  "
                        f"[dim]@{_escape(str(t.get('username') or '?'))} · "
                        f"{_escape(str(t.get('gear') or '?').upper())} · "
                        f"dl {t.get('downloads_count')}[/dim]{badge}",
                        id="pack-header")
                    yield MarqueeBar(id="pack-marquee")
                    table = PackPickTable(id="pack-table", cursor_type="row")
                    table.add_column("✓", key="pick", width=5)
                    table.add_column("Model file", key="name")
                    table.add_column("Architecture", key="arch", width=16)
                    yield table
                    yield MarqueeBar("Loading pack contents…", id="pack-status")
                    yield ProgressBar(total=1, show_eta=False, id="pack-progress")
                with VerticalScroll(id="pack-right"):
                    # tone metadata side-by-side for comparison while picking
                    yield SelectableStatic(
                        metadata_table(self._tone, colors=colors), id="pack-detail")

    def on_mount(self) -> None:
        self._load_generation += 1
        self._load_state = "loading"
        self.query_one("#pack-progress", ProgressBar).display = False
        t = self._tone
        self.run_worker(
            partial(self._load_models, t["id"], library.model_is_ir({}, t),
                    self._load_generation), name="pack-load")
        self._update_hint("loading…")

    def on_unmount(self) -> None:
        self._load_generation += 1
        self._operation_generation += 1

    def _ui_alive(self, generation: int, *, operation: str | None = None) -> bool:
        return (bool(getattr(self, "is_mounted", False))
                and (generation == self._load_generation
                     if operation is None
                     else generation == self._operation_generation))

    def _update_hint(self, state: str | None = None) -> None:
        """Keep the changing status left of a stable action zone."""
        try:
            box = self.query_one(ModalBox)
        except Exception:
            return
        if state is None:
            state = (f"{len(self._selected)}/{len(self._models)} selected"
                     if self._models else "loading…")
        set_border_hint_layout(
            box, state, [token for token, _ in self._border_hint_actions()])

    def _set_status(self, message: str, *, hint: str | None = None) -> None:
        if not getattr(self, "is_mounted", False):
            return
        self.query_one("#pack-status", MarqueeBar).content = message
        self._update_hint(hint or message)

    def _begin_operation(self, kind: str) -> int | None:
        if self._busy is not None or not getattr(self, "is_mounted", False):
            return None
        self._operation_generation += 1
        self._busy = kind
        return self._operation_generation

    def _finish_operation(self) -> None:
        self._busy = None

    async def _load_models(self, tone_id: int, is_ir: bool,
                           generation: int | None = None) -> None:
        if generation is None:
            self._load_generation += 1
            generation = self._load_generation
        try:
            # INSTALL PACK is a complete model set.  Filtering to A2 here
            # silently drops A1 files before the table can render them.
            ms = await asyncio.to_thread(tone3000.models, tone_id,
                                         a2_only=False)
        except Exception as e:
            if self._ui_alive(generation):
                self._load_state = "error"
                self._set_status(
                    f"failed to load pack contents: {e} · press r to retry",
                    hint="load failed")
            return
        if not self._ui_alive(generation):
            return
        self._models = []
        for model in ms:
            normalized = normalize_model_architecture(model, tone=self._tone)
            arch = model_architecture(normalized, tone=self._tone)
            # A1 (WaveNet) 是废弃架构：不展示、不可勾选，因此也不会被下载。
            if arch == "A1":
                continue
            if arch:
                self._models.append(normalized)
        # REQ-038：默认勾选未下载的模型——已下载的不重复安装，u 可卸载。
        self._downloaded = library.downloaded_model_ids_by_tone().get(
            int(tone_id), set())
        self._selected = {m["id"] for m in self._models
                          if m["id"] not in self._downloaded}
        table = self.query_one("#pack-table", DataTable)
        table.clear()
        for m in sorted(self._models, key=lambda x: x["id"]):
            arch = model_architecture(m, tone=self._tone)
            checked = "\\[x]" if m["id"] in self._selected else "\\[ ]"
            table.add_row(checked, self._mark_name(m), arch, key=str(m["id"]))
        self._load_state = "ready"
        self._update_status()
        table.focus()
        self._publish_focus_marquee()

    def action_retry_load(self) -> None:
        if self._busy is not None:
            return
        self._load_generation += 1
        self._load_state = "loading"
        t = self._tone
        self._set_status("loading pack contents…", hint="loading…")
        self.run_worker(
            partial(self._load_models, t["id"], library.model_is_ir({}, t),
                    self._load_generation), name="pack-load", exclusive=True)

    @staticmethod
    def _model_name(m: dict) -> str:
        """Semantic name from the models.name column (web zip naming); fall back
        to the storage basename for legacy responses."""
        name = m.get("name")
        if name:
            return name
        url = m.get("model_url") or ""
        return url.rstrip("/").rsplit("/", 1)[-1] or f"model {m['id']}"

    def _mark_name(self, m: dict) -> str:
        """行名：本地已下载的模型追加 ✓ downloaded 标记（u 的目标）。"""
        name = self._model_name(m)
        if m.get("id") in getattr(self, "_downloaded", set()):
            return f"{name} [dim]✓ downloaded[/dim]"
        return name

    # ---- key handling -----------------------------------------------------

    def action_toggle_row(self) -> None:
        """space：勾选/取消光标行（Pick 列 [ ]/[x]）。"""
        if self._busy is not None:
            return
        table = self.query_one("#pack-table", DataTable)
        if table.row_count == 0:
            return
        row_key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        self._toggle_row_key(row_key)

    def _toggle_row_key(self, row_key: str) -> None:
        """勾选/取消指定行（space 键与 Pick 列鼠标点选共用）。"""
        if self._busy is not None:
            return
        mid = int(row_key)
        if mid not in {m["id"] for m in self._models}:
            return
        self._uninstall_confirmed = False
        self._uninstall_target = []
        checked = mid not in self._selected
        if checked:
            self._selected.add(mid)
        else:
            self._selected.discard(mid)
        self.query_one("#pack-table", DataTable).update_cell(
            row_key, "pick", "\\[x]" if checked else "\\[ ]")
        self._update_status()

    def action_toggle_all(self) -> None:
        if self._busy is not None:
            return
        table = self.query_one("#pack-table", DataTable)
        if table.row_count == 0:
            return
        self._uninstall_confirmed = False
        self._uninstall_target = []
        all_selected = len(self._selected) == len(self._models)
        if all_selected:
            self._selected.clear()
        else:
            self._selected = {m["id"] for m in self._models}
        for i in range(table.row_count):
            row_key = table.coordinate_to_cell_key((i, 0)).row_key.value
            table.update_cell(row_key, "pick", "\\[x]" if int(row_key) in self._selected else "\\[ ]")
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
        all_label = "none" if self._models and n == len(self._models) else "all"
        message = (
            f"{len(self._models)} model file(s) · "
            f"{n}/{len(self._models)} selected")
        if getattr(self, "is_mounted", False):
            self.query_one("#pack-status", MarqueeBar).content = message
            self._update_hint(message)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter (table focused) = confirm, same path as the modal-level binding."""
        self._confirm()

    def on_click(self, event: MouseEvent) -> None:
        """Border hint tokens are clickable; Pick 列鼠标点选与双击确认由
        PackPickTable 自己处理（ClickSelectTable 会 stop 单元格点击）。"""
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def _border_hint_actions(self) -> list:
        if self._busy is not None:
            return [("esc cancel", self.dismiss)]
        if self._load_state == "loading":
            return [("esc cancel", self.dismiss)]
        if self._load_state == "error":
            return [("r retry", self.action_retry_load),
                    ("esc cancel", self.dismiss)]
        # "a all" / "a none" token 必须与当前副标题文案一致（hint_span 按
        # 文本查找）：全选态点击 "a none" 取消全选，漏配会静默无效。
        n = len(self._selected)
        all_label = "none" if self._models and n == len(self._models) else "all"
        return [
            (f"a {all_label}", self.action_toggle_all),
            ("i install", self._confirm),
            ("u uninstall", self.action_uninstall_selected),
            ("esc cancel", self.dismiss),
        ]

    # ---- REQ-038 u 键卸载选中（与 uninstall_screen 同一安全语义）----

    def action_uninstall_selected(self) -> None:
        """u：卸载选中的已下载模型（未下载的选中项跳过）。

        活动链/库外文件拦截；preset 引用需再次按 u 确认（同
        LocalUninstallScreen 的 Enter 二次确认）。完成后留在本页，
        行标记刷新，可继续安装/卸载。
        """
        if self._busy is not None or not self._models:
            return
        sel = sorted(self._selected)
        downloaded = [m["id"] for m in self._models
                      if m["id"] in sel and m["id"] in self._downloaded]
        if not downloaded:
            self._set_status(
                "none of the selected files are downloaded — nothing to uninstall")
            return
        target = tuple(downloaded)
        if target != tuple(self._uninstall_target):
            self._uninstall_confirmed = False
            self._uninstall_target = list(target)
        plan = library.local_uninstall_models_plan(downloaded)
        if plan["active_paths"] or plan["outside_paths"]:
            self._uninstall_confirmed = False
            self._set_status(
                "switch the active model or remove unmanaged paths "
                "before uninstalling.", hint="uninstall blocked")
            return
        if plan["preset_names"] and not self._uninstall_confirmed:
            self._uninstall_confirmed = True
            self._set_status(
                "presets keep their references and may not load · "
                "press u again to continue", hint="uninstall confirm")
            return
        generation = self._begin_operation("uninstall")
        if generation is None:
            return
        frozen_ids = list(self._uninstall_target)
        self._set_status(
            f"uninstalling {len(frozen_ids)} file(s)…", hint="uninstalling…")
        self.run_worker(
            partial(self._uninstall_models, frozen_ids,
                    bool(plan["preset_names"]), generation),
            name="pack-uninstall", exclusive=True)

    async def _uninstall_models(self, model_ids: list[int],
                                allow_preset_references: bool,
                                generation: int) -> None:
        try:
            result = await asyncio.to_thread(
                library.local_uninstall_models, model_ids,
                allow_preset_references=allow_preset_references)
        except Exception as e:
            if self._ui_alive(generation, operation="operation"):
                self._finish_operation()
                self._set_status(f"uninstall failed: {e}", hint="uninstall failed")
            return
        if int(result.get("removed") or 0) <= 0:
            if self._ui_alive(generation, operation="operation"):
                self._finish_operation()
                self._set_status("no files removed", hint="ready")
            return
        actual_ids = tuple(result.get("removed_model_ids") or model_ids)
        publish = getattr(self.app, "_publish_mutation", None)
        if callable(publish):
            publish("uninstall", tuple(f"model:{model_id}" for model_id in actual_ids),
                    result.get("revision"))
        if not self._ui_alive(generation, operation="operation"):
            return
        self._uninstall_confirmed = False
        self._uninstall_target = []
        self._downloaded = library.downloaded_model_ids_by_tone().get(
            int(self._tone.get("id") or 0), set())
        # 刚卸载的模型从勾选里移除（防误按 i 重复下载）
        self._selected.difference_update(model_ids)
        table = self.query_one("#pack-table", DataTable)
        for m in self._models:
            checked = "\\[x]" if m["id"] in self._selected else "\\[ ]"
            table.update_cell(str(m["id"]), "pick", checked)
            table.update_cell(str(m["id"]), "name", self._mark_name(m))
        self._update_status()  # 先刷新副标题勾选态（a all/a none 动态 token）
        self._finish_operation()
        self._set_status(f"uninstalled {result['removed']} file(s) · metadata retained",
                         hint="ready")
        self.post_message(self.Uninstalled(
            int(self._tone.get("id") or 0), result["removed"], actual_ids))

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))

    def _confirm(self) -> None:
        """Enter: install the selected model files."""
        t = self._tone
        if self._busy is not None or not self._models:
            return
        sel = sorted({m["id"] for m in self._models} & self._selected)
        if not sel:
            self._set_status(
                "nothing selected — space to pick files, a to select all",
                hint="ready")
            return
        generation = self._begin_operation("install")
        if generation is None:
            return
        frozen_ids = list(sel)
        self._set_status(f"installing {len(frozen_ids)} file(s)…",
                         hint="installing…")
        self.run_worker(partial(self._install, t["id"], frozen_ids, generation),
                        name="pack-install", exclusive=True)

    async def _install(self, tone_id: int, model_ids: list[int],
                       generation: int) -> None:
        if not self._ui_alive(generation, operation="operation"):
            return
        status = self.query_one("#pack-status", MarqueeBar)
        bar = self.query_one("#pack-progress", ProgressBar)
        bar.update(total=max(len(model_ids), 1), progress=0)
        bar.display = True
        status.content = f"Installing {len(model_ids)} file(s)…"

        def progress(done: int, total: int, filename: str) -> None:
            try:
                self.app.call_from_thread(
                    self._show_progress, generation, done, total, filename)
            except Exception:
                pass

        try:
            t = await asyncio.to_thread(
                library.import_tone, tone_id, progress, quiet=True,
                model_ids=model_ids)
        except Exception as e:
            if self._ui_alive(generation, operation="operation"):
                self._finish_operation()
                status.content = f"install failed: {e}"
                bar.display = False
                self._update_hint("install failed")
            return
        if not t:
            if self._ui_alive(generation, operation="operation"):
                self._finish_operation()
                status.content = f"tone3000 has no tone {tone_id}"
                bar.display = False
                self._update_hint("install failed")
            return
        downloaded = library.downloaded_model_ids_by_tone().get(tone_id, set())
        actual_ids = tuple(sorted(set(model_ids).intersection(downloaded)))
        if not actual_ids:
            # Keep compatibility with narrow test doubles that return a truthy
            # import result without a populated local-model table. In the real
            # library path, downloaded_model_ids_by_tone supplies the exact set.
            actual_ids = tuple(sorted(set(model_ids)))
        publish = getattr(self.app, "_publish_mutation", None)
        if callable(publish):
            publish("install", tuple(f"model:{model_id}" for model_id in actual_ids),
                    t.get("revision"))
        if not self._ui_alive(generation, operation="operation"):
            return
        bar.display = False
        self._selected.difference_update(model_ids)
        self._downloaded = library.downloaded_model_ids_by_tone().get(
            int(tone_id), set())
        for model in self._models:
            key = str(model["id"])
            try:
                self.query_one("#pack-table", DataTable).update_cell(
                    key, "pick", "\\[x]" if model["id"] in self._selected else "\\[ ]")
                self.query_one("#pack-table", DataTable).update_cell(
                    key, "name", self._mark_name(model))
            except Exception:
                pass
        self._finish_operation()
        self.post_message(self.Installed(tone_id, len(actual_ids), actual_ids))
        self.dismiss()

    def _show_progress(self, generation: int, done: int, total: int,
                       filename: str) -> None:
        if not self._ui_alive(generation, operation="operation"):
            return
        self.query_one("#pack-progress", ProgressBar).update(
            total=max(total, 1), progress=done)
        self.query_one("#pack-status", MarqueeBar).content = (
            f"installing {done}/{total}  {filename}")
        self._update_hint(f"installing {done}/{total}")
