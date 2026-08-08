"""Confirmation screen for uninstalling downloaded local tone packs."""
import asyncio
from functools import partial
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.events import MouseEvent, MouseMove
from textual.message import Message
from textual.widgets import Static

from .modals import (GigBuddyModal, ModalBox, border_hint_action_token,
                     border_hint_click, set_border_hint_hover,
                     set_border_hint_layout)
from .marquee import MarqueeBar
from .selection import NonSelectableStatic

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import library  # noqa: E402


def _size_label(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


class LocalUninstallScreen(GigBuddyModal):
    # destructive action: fixed error red border (pinned across themes)
    CSS = """
    LocalUninstallScreen > ModalBox {
        width: 72%; height: auto; margin: 5 14;
        border: round $error; border-title-color: $error;
    }
    #uninstall-summary { margin-bottom: 1; }
    #uninstall-warning { color: $warning; margin-bottom: 1; }
    #uninstall-status { height: 1; color: $text-muted; }
    """

    class Uninstalled(Message):
        def __init__(self, tone_ids: list[int], count: int, trash_dir: str | None) -> None:
            super().__init__()
            self.tone_ids = tone_ids
            self.count = count
            self.trash_dir = trash_dir

    def __init__(self, tone_ids: list[int]) -> None:
        super().__init__()
        self._tone_ids = sorted(set(tone_ids))
        self._plan = library.local_uninstall_plan(self._tone_ids)
        self._dependency_confirmed = False
        self._dependency_target: tuple[int, ...] = ()
        self._operation_generation = 0
        self._busy = False

    BINDINGS = [
        # REQ-025: 卸载 = u 快捷键（Enter 也确认——GigBuddyModal 基类绑定）
        Binding("u", "confirm", "uninstall", show=False),
    ]

    def compose(self) -> ComposeResult:
        plan = self._plan
        box = ModalBox()
        box.border_title = "UNINSTALL LOCAL PACKS"
        with box:
            yield NonSelectableStatic(
                f"{len(plan['tone_ids'])} pack(s) · {len(plan['models'])} file(s) · "
                f"{_size_label(plan['bytes'])}", id="uninstall-summary")
            yield NonSelectableStatic(self._plan_warnings(), id="uninstall-warning")
            yield MarqueeBar(
                "Files move to data/.trash; metadata and presets are retained.",
                id="uninstall-status")

    def on_mount(self) -> None:
        self._update_hint("plan ready")

    def on_unmount(self) -> None:
        self._operation_generation += 1

    def _ui_alive(self, generation: int) -> bool:
        return (bool(getattr(self, "is_mounted", False))
                and generation == self._operation_generation)

    def _plan_warnings(self) -> str:
        warnings = []
        if self._plan["active_paths"]:
            warnings.append("blocked: selected files are used by the active chain.")
        if self._plan["outside_paths"]:
            warnings.append("blocked: selection contains files outside the managed library.")
        if self._plan["preset_names"]:
            warnings.append("referenced by presets: "
                            + ", ".join(self._plan["preset_names"]))
        return "\n".join(warnings)

    def _refresh_plan(self) -> None:
        """Re-read live references immediately before a destructive action."""
        self._plan = library.local_uninstall_plan(list(self._tone_ids))
        if not getattr(self, "is_mounted", False):
            return
        self.query_one("#uninstall-summary", NonSelectableStatic).update(
            f"{len(self._plan['tone_ids'])} pack(s) · "
            f"{len(self._plan['models'])} file(s) · "
            f"{_size_label(self._plan['bytes'])}")
        self.query_one("#uninstall-warning", NonSelectableStatic).update(
            self._plan_warnings())

    def _update_hint(self, state: str) -> None:
        try:
            box = self.query_one(ModalBox)
        except Exception:
            return
        actions = [("esc cancel", self.dismiss)] if self._busy else [
            ("u uninstall", self._confirm),
            ("esc cancel", self.dismiss),
        ]
        set_border_hint_layout(
            box, state,
            [token for token, _action in actions])

    def _set_status(self, message: str, *, hint: str | None = None) -> None:
        if not getattr(self, "is_mounted", False):
            return
        self.query_one("#uninstall-status", MarqueeBar).content = message
        self._update_hint(hint or message)

    # ---- clickable border hints --------------------------------------------

    def _border_hint_actions(self) -> list:
        if self._busy:
            return [("esc cancel", self.dismiss)]
        return [
            ("u uninstall", self._confirm),
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

    def _confirm(self) -> None:
        if self._busy:
            return
        self._refresh_plan()
        if not self._tone_ids:
            self._set_status("select local packs first")
            return
        target = tuple(self._tone_ids)
        if self._plan["active_paths"] or self._plan["outside_paths"]:
            self._dependency_confirmed = False
            self._dependency_target = ()
            self._set_status(
                "switch the active model or remove unmanaged paths before uninstalling.",
                hint="uninstall blocked")
            return
        if self._plan["preset_names"]:
            if target != self._dependency_target:
                self._dependency_confirmed = False
                self._dependency_target = target
            if not self._dependency_confirmed:
                self._dependency_confirmed = True
                self._set_status(
                    "presets keep their references and may not load · "
                    "press u again to continue", hint="uninstall confirm")
                return
        elif self._dependency_target != target:
            self._dependency_target = target
            self._dependency_confirmed = False
        if not self._plan["models"]:
            self._set_status("no managed files to uninstall")
            return
        self._operation_generation += 1
        generation = self._operation_generation
        frozen_ids = list(target)
        allow_references = bool(self._plan["preset_names"])
        self._busy = True
        self._set_status("uninstalling…", hint="uninstalling…")
        self.run_worker(
            partial(self._uninstall, frozen_ids, allow_references, generation),
            name="local-uninstall", exclusive=True)

    async def _uninstall(self, tone_ids: list[int],
                         allow_preset_references: bool,
                         generation: int) -> None:
        try:
            result = await asyncio.to_thread(
                library.local_uninstall_tones, tone_ids,
                allow_preset_references=allow_preset_references)
        except Exception as e:
            if self._ui_alive(generation):
                self._busy = False
                self._set_status(f"uninstall failed: {e}", hint="uninstall failed")
            return
        if not self._ui_alive(generation):
            return
        self._busy = False
        self.post_message(self.Uninstalled(
            tone_ids, result["removed"], result.get("trash_dir")))
        self.dismiss()
