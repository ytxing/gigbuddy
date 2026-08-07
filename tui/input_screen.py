"""Input-source picker: instrument device or dry-file playback (音色试听用).

选中干声文件 → 写 live_chain.json 的 input 键（source=file, state=playing,
loop=true）并自动开始循环播放；选中乐器 → source=instrument。播放控制
space/s/l 在屏幕内即时生效（引擎 ≤0.1s 响应）；d 下载缺失的常用干声素材，
树内 "download all" 叶子补下其余。
"""
import asyncio
import sys
from pathlib import Path
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.events import Leave, MouseEvent, MouseMove
from textual.message import Message
from textual.widgets import Static, Tree

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import tone3000  # noqa: E402

from . import live  # noqa: E402
from .marquee import MarqueeBar  # noqa: E402
from .modals import (ClickSelectTree, GigBuddyModal, ModalBox,
                     border_hint_action_token, border_hint_hit, hint_span,
                     set_border_hint_hover, set_border_hint_layout)  # noqa: E402

# 常用干声素材（吉他，TONE3000 网页试听源）——d 键下载这些，其余按需补下
DEFAULT_DRY_KEYS = list(tone3000.DRY_INPUT_STARTER_KEYS)


def _rel(path: str) -> str:
    """绝对路径 → 相对项目根（live_chain.json 用相对路径，与 model/ir 一致）"""
    try:
        return str(Path(path).resolve().relative_to(live.ROOT))
    except ValueError:
        return path


class NoArrowTree(ClickSelectTree):
    """无三角箭头的树：层级用缩进表达。

    Textual 的展开/折叠三角是类常量（ICON_NODE/ICON_NODE_EXPANDED），
    覆写为空格以保持相同缩进宽度；配合 show_guides=False 得到纯缩进层级。
    """

    ICON_NODE = "  "
    ICON_NODE_EXPANDED = "  "


class InputSourceScreen(GigBuddyModal):
    """Choose the chain input source: instrument device or a dry riff file."""

    CSS = """
    InputSourceScreen > ModalBox { width: 72%; height: 70%; margin: 6 14; }
    InputSourceScreen #input-status { height: 1; color: $text-muted; margin-top: 1; }
    InputSourceScreen #input-tree { height: 1fr; }
    """

    class SourceChanged(Message):
        def __init__(self, chain: dict) -> None:
            super().__init__()
            self.chain = chain

    BINDINGS = [
        Binding("escape", "cancel", "close"),
        Binding("enter", "confirm", "confirm"),
        Binding("ctrl+c", "request_quit", "quit (×2)", show=False),
        Binding("space", "playback_toggle", "play/pause", show=False),
        Binding("s", "playback_stop", "stop", show=False),
        Binding("l", "playback_loop", "loop", show=False),
        Binding("d", "download_dry", "download dry inputs", show=False),
    ]

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "INPUT SOURCE"
        with box:
            tree = NoArrowTree("Input source", id="input-tree")
            tree.show_root = False
            # 层级用缩进表达：无三角图标（ICON_NODE 覆写为空格保持宽度）+ 无引导线
            tree.show_guides = False
            yield tree
            yield MarqueeBar(id="input-status")

    def on_mount(self) -> None:
        self._download_generation = 0
        self._refresh_tree()
        self.query_one("#input-tree", Tree).focus()
        box = self.query_one(ModalBox)
        set_border_hint_layout(
            box, "", [token for token, _ in self._border_hint_actions()])
        self.set_interval(0.25, self._update_status)

    def _instrument_label(self) -> str:
        device = getattr(self.app, "_dev_in", "") or "default device"
        return f"Instrument — {device}"

    def _refresh_tree(self) -> None:
        tree = self.query_one("#input-tree", Tree)
        tree.reset("Input source")
        tree.root.expand()
        chain = live.chain_input(live.read_chain())
        current_file = chain.get("file") if chain.get("source") == "file" else None
        current = (f"✓ {self._instrument_label()}"
                   if chain.get("source") != "file" else self._instrument_label())
        tree.root.add_leaf(current, {"type": "instrument"})
        files = sorted(
            p for p in live.DRY_INPUTS_DIR.glob("*.wav")) if live.DRY_INPUTS_DIR.is_dir() else []
        if files:
            # 干声按文件名 " - Guitar"/" - Bass" 后缀分组为一级选项
            # （Guitar / Bass），组内才是 wav 文件。
            groups: dict[str, list] = {}
            for f in files:
                tail = Path(f.name).stem.rsplit(" - ", 1)[-1].strip()
                groups.setdefault(tail if tail in ("Guitar", "Bass") else "Guitar",
                                  []).append(f)
            for group in sorted(groups):
                branch = tree.root.add(group, {"type": "group"})
                branch.expand()
                for f in groups[group]:
                    mark = "✓ " if str(_rel(str(f))) == str(current_file) else "  "
                    branch.add_leaf(f"{mark}{f.name}", {"type": "dry", "path": str(f)})
            missing = tone3000.fetch_dry_inputs_missing(live.DRY_INPUTS_DIR)
            if missing:
                tree.root.add_leaf(
                    f"↓ download all ({len(missing)} missing)",
                    {"type": "download", "keys": None})
        else:
            tree.root.add_leaf(
                "(no dry inputs)", {"type": "status"})
            tree.root.add_leaf(
                f"↓ download all ({len(tone3000.DRY_INPUTS)})",
                {"type": "download", "keys": None})
        if tree.root.children:
            tree.move_cursor(tree.root.children[0])

    def _update_status(self) -> None:
        _, _, play_state, play_pos = live.read_levels()
        chain = live.chain_input(live.read_chain())
        word = {"playing": "playing", "paused": "paused"}.get(play_state, "stopped")
        pos = f" {play_pos:.0f}s" if play_state == "playing" else ""
        loop = " loop" if chain.get("loop") else ""
        if chain.get("source") == "file":
            name = Path(chain["file"]).name if chain.get("file") else "—"
            self.query_one("#input-status", MarqueeBar).content = (
                f"{word}{pos}{loop}  {name}")
        else:
            self.query_one("#input-status", MarqueeBar).content = (
                "Instrument (live) — chain takes the guitar signal")

    # ---- playback control (space/s/l): write the chain, engine responds ≤0.1s ----

    def _commit_input(self, cfg: dict) -> dict | None:
        """Use the App's chain boundary when the modal edits live input."""
        try:
            writer = getattr(self.app, "_commit_external_chain", None)
            persisted = (writer(cfg) if callable(writer)
                         else (live.write_chain(cfg) or live.read_chain()))
            if persisted is None:
                persisted = live.read_chain() or cfg
        except Exception as exc:
            self.app.notify(f"Input unchanged: {exc}", severity="error")
            return None
        return persisted

    def _set_input(self, inp: dict, *, note: str) -> dict | None:
        cfg = live.read_chain()
        cfg["input"] = inp
        persisted = self._commit_input(cfg)
        if persisted is None:
            return None
        self._update_status()
        self._refresh_tree()
        self.app.notify(note)
        return persisted

    def action_playback_toggle(self) -> None:
        cfg = live.read_chain()
        inp = live.chain_input(cfg)
        if inp.get("source") != "file":
            self.app.notify("Instrument input active — pick a dry file first")
            return
        inp["state"] = live.PLAY_PAUSED if inp.get("state") == live.PLAY_PLAYING \
            else live.PLAY_PLAYING
        cfg["input"] = inp
        self._commit_input(cfg)
        self._update_status()

    def action_playback_stop(self) -> None:
        cfg = live.read_chain()
        inp = live.chain_input(cfg)
        if inp.get("source") != "file":
            return
        inp["state"] = live.PLAY_STOPPED
        cfg["input"] = inp
        self._commit_input(cfg)
        self._update_status()

    def action_playback_loop(self) -> None:
        cfg = live.read_chain()
        inp = live.chain_input(cfg)
        if inp.get("source") != "file":
            return
        inp["loop"] = not inp.get("loop", False)
        cfg["input"] = inp
        self._commit_input(cfg)
        self._update_status()

    def action_download_dry(self) -> None:
        self._download_generation += 1
        self.run_worker(
            self._download(DEFAULT_DRY_KEYS, self._download_generation),
            name="dry-download", exclusive=True)

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        return [
            ("space play/pause", self.action_playback_toggle),
            ("s stop", self.action_playback_stop),
            ("l loop", self.action_playback_loop),
            ("d download", self.action_download_dry),
            ("enter select", self._confirm),
            ("esc close", self.dismiss),
        ]

    def on_click(self, event: MouseEvent) -> None:
        """The modal's border subtitle is a real control, one token per key."""
        hit = border_hint_hit(self.query_one(ModalBox),
                              event.screen_x, event.screen_y)
        if hit is None:
            return
        label, offset = hit
        for token, action in self._border_hint_actions():
            span = hint_span(label, token)
            if span is not None and span[0] <= offset < span[1]:
                event.stop()
                action()
                return

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))

    # ---- selection handling ----

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        self._handle_data(event.node.data)

    def _confirm(self) -> None:
        """Enter (when the tree itself isn't focused): confirm the cursor row."""
        tree = self.query_one("#input-tree", Tree)
        if tree.cursor_node is not None:
            self._handle_data(tree.cursor_node.data)

    def _handle_data(self, data: dict | None) -> None:
        if not data:
            return
        kind = data.get("type")
        if kind in ("status", "spacer"):
            return
        if kind == "group":
            # 一级分组选项：选中即展开/折叠内部 wav，不关闭选择器
            tree = self.query_one("#input-tree", Tree)
            if tree.cursor_node is not None:
                tree.cursor_node.expand() if not tree.cursor_node.is_expanded \
                    else tree.cursor_node.collapse()
            return
        if kind == "instrument":
            persisted = self._set_input(
                {"source": "instrument"}, note="Input source → instrument (live)")
            if persisted is None:
                return
            self.post_message(self.SourceChanged(persisted))
            self.dismiss()
            return
        if kind == "dry":
            inp = {"source": "file", "file": _rel(data["path"]),
                   "state": live.PLAY_PLAYING, "loop": True}
            persisted = self._set_input(
                inp, note=f"Dry loop preview → {Path(data['path']).name}")
            if persisted is None:
                return
            self.post_message(self.SourceChanged(persisted))
            self.dismiss()
            return
        if kind == "download":
            self._download_generation += 1
            self.run_worker(
                self._download(data.get("keys"), self._download_generation),
                name="dry-download-all", exclusive=True)

    def _download_ui_alive(self, generation: int) -> bool:
        return (generation == getattr(self, "_download_generation", -1)
                and bool(getattr(self, "is_mounted", False)))

    def on_unmount(self) -> None:
        self._download_generation = getattr(self, "_download_generation", 0) + 1

    async def _download(self, keys: list[str] | None,
                        generation: int | None = None) -> None:
        """下载干声素材（worker 线程），状态行显示进度，完成后刷新列表"""
        if generation is None:
            self._download_generation += 1
            generation = self._download_generation
        if self._download_ui_alive(generation):
            self.query_one("#input-status", MarqueeBar).content = (
                "Downloading dry inputs…")
        missing = tone3000.fetch_dry_inputs_missing(
            live.DRY_INPUTS_DIR, names=keys)
        if not missing:
            if self._download_ui_alive(generation):
                self.query_one("#input-status", MarqueeBar).content = (
                    "All dry inputs present")
                self._refresh_tree()
            return

        def progress(done: int, total: int, fname: str | None) -> None:
            if not self._download_ui_alive(generation):
                return
            if fname:
                self.app.call_from_thread(
                    self._set_status, generation,
                    f"Downloading dry {done}/{total}  {fname}")
            else:
                self.app.call_from_thread(
                    self._set_status, generation, f"Done {done}/{total}")

        try:
            n = await asyncio.to_thread(
                tone3000.fetch_dry_inputs, live.DRY_INPUTS_DIR, missing, progress)
            if self._download_ui_alive(generation):
                self.query_one("#input-status", MarqueeBar).content = (
                    f"Downloaded {n} dry inputs")
        except Exception as e:
            if self._download_ui_alive(generation):
                self.query_one("#input-status", MarqueeBar).content = (
                    f"Download failed: {e}")
        if self._download_ui_alive(generation):
            self._refresh_tree()

    def _set_status(self, generation: int, message: str) -> None:
        if self._download_ui_alive(generation):
            self.query_one("#input-status", MarqueeBar).content = message
