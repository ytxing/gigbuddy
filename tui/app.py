"""GigBuddy TUI: tone library browser (left) + NAM tone management (right) + meter (bottom)

v2: pure control surface — no embedded agent. The library DB (data/gigbuddy.db)
is open to external agents via the `gigbuddy` CLI; chain edits flow to the engine
through data/live_chain.json as before.

Run: .venv/bin/python -m tui            (spawns the realtime engine automatically)
     .venv/bin/python -m tui --no-engine (engine already running externally)
"""
import argparse
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
import hashlib
import json
from queue import Empty, Queue
import re
import subprocess
import sys
import threading
import time
from typing import Callable
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import unquote

from rich.cells import cell_len
from rich.markup import escape
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import MouseEvent, MouseMove
from textual.theme import Theme
from textual.widgets import Button, Footer, Header, Static
from textual.widgets._header import (HeaderClock, HeaderClockSpace, HeaderIcon,
                                     HeaderTitle)

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from . import live  # noqa: E402
from .chain_state import (ChainState, ChainStateError, SLOT_GAIN_MAX_DB,
                          SLOT_GAIN_MIN_DB, SlotStatus, CommitReceipt,
                          PreparedCommit, chain_fingerprint)  # noqa: E402
import library  # noqa: E402
import chain_protocol  # noqa: E402
from .input_screen import InputSourceScreen  # noqa: E402
from .install_screen import PackInstallScreen  # noqa: E402
from .library_panel import (LibraryPanel, LibraryTable, RemoteToneSelected,
                            ToneHighlighted, ToneSelected,
                            VerifiedAuthor)  # noqa: E402
from .marquee import MarqueeBar  # noqa: E402
from .metadata import signed_fixed  # noqa: E402
from .mutations import (ModelsDownloaded, MutationCommitted,
                        MutationRefreshCoordinator, ViewAnchor)  # noqa: E402
from .panels import (AudioActionButton, AudioSettingsScreen, ChainPanel,
                     DetailPane, DeviceBar, AddSlotButton, ChainSlotWidget,
                     DeviceChanged, InterfaceBar, MeterBar, NodeSwitchButton,
                     NodeWidget)  # noqa: E402
from .picker import TonePickerScreen  # noqa: E402
from .presets import (ChainSaveModal, ClearSlotsConfirm, PresetDeleteModal,
                      PresetEditModal, PresetLoadConfirm, PresetNameModal,
                      PresetNoteModal, PresetPanel, PresetRenameModal)  # noqa: E402
from .modals import (GigBuddyModal, ModalBox, border_hint_action_token,
                     border_hint_click, set_border_hint_hover,
                     set_border_hint_layout)  # noqa: E402
from .selection import NonSelectableStatic, ShiftSelectableScreen  # noqa: E402
from .uninstall_screen import LocalUninstallScreen  # noqa: E402

# Success, error and idle state colors are fixed across every theme. Warning is
# intentionally theme-provided: it is a semantic attention color, not one of
# the three cross-theme state colors frozen by the UI spec.
FIXED_SEMANTIC_COLORS = {
    "error": "#d96a55",
    "success": "#8fb573",
    "state-idle": "#8a817a",
}


def _preset_mutation_key(preset_id: object, name: object = None) -> str | None:
    """Build the stable row key used by all preset mutation events."""
    if isinstance(preset_id, int) and not isinstance(preset_id, bool):
        return f"preset:{preset_id}"
    if isinstance(name, str) and name:
        # Lightweight test doubles and legacy callers may not expose ids. Real
        # database rows always take the stable-id branch above.
        return f"preset-name:{name}"
    return None


class _ManagedNoOp(Exception):
    """Internal signal for a transactional mutation that made no change."""


@dataclass(frozen=True)
class _ManagedChainJob:
    """One ordered managed mutation executed away from the Textual loop."""

    mutation: Callable[[ChainState], object]
    note: str
    failure_note: str | None = None
    on_success: Callable[[dict, int | None], None] | None = None
    target_index: int | None = None
    target_identity: int | None = None
    slot_paths: tuple[str | None, ...] | None = None
    slot_identities: tuple[int, ...] | None = None
    # The UI snapshot this job was created from. A queued job may follow a
    # known local transition, but must not silently rebase onto an external
    # whole-chain replacement with the same Slot paths.
    base_fingerprint: str | None = None
    base_revision: int | None = None


@dataclass(frozen=True)
class _ManagedWriteTarget:
    """Process-local Slot identity transition for one committed file."""

    before_target_identity: int | None
    after_target_identity: int | None
    before_paths: tuple[str | None, ...]
    after_paths: tuple[str | None, ...]
    before_identities: tuple[int, ...]
    after_identities: tuple[int, ...]


@dataclass(frozen=True)
class _ManagedParamCommit:
    """One parameter commit plus the process-local Slot order it used."""

    chain: dict
    slot_identities: tuple[int, ...] | None = None


class _ManagedChainAdapter:
    """Bridge ChainState's transaction seam to the managed file/runtime pair."""

    def __init__(self, app: "GigBuddyApp", *, expected_chain: dict | None = None) -> None:
        self._app = app
        path = live.CHAIN_FILE
        self._previous_payload = path.read_bytes() if path.exists() else None
        self._base_fingerprint = (
            hashlib.sha256(self._previous_payload).hexdigest()
            if self._previous_payload is not None else None)
        if self._previous_payload is None:
            self._base_chain = {}
        else:
            raw = json.loads(self._previous_payload.decode("utf-8"))
            self._base_chain = chain_protocol.normalize_chain(
                raw, root=live.ROOT)
        if expected_chain is not None:
            try:
                expected_fingerprint = chain_protocol.serialized_chain_fingerprint(
                    expected_chain, root=live.ROOT)
                base_fingerprint = chain_protocol.serialized_chain_fingerprint(
                    self._base_chain, root=live.ROOT)
            except chain_protocol.ChainProtocolError as exc:
                raise chain_protocol.ChainFileConflict(
                    "managed UI state is no longer a valid chain base") from exc
            if expected_fingerprint != base_fingerprint:
                raise chain_protocol.ChainFileConflict(
                    "chain changed before the managed transaction started")
        base_revision = self._base_chain.get("revision", 0)
        if (isinstance(base_revision, bool)
                or not isinstance(base_revision, int) or base_revision < 0):
            raise ChainStateError("managed chain has an invalid base revision")
        self._base_revision = base_revision
        self._runtime_before: tuple[dict[str, object], str | None] | None = None
        self._runtime_session_id: str | None = None
        self._transaction_id: str | None = None
        self._candidate_fingerprint: str | None = None
        self._candidate_revision: int | None = None
        self._file_write_succeeded = False
        self._file_restore_succeeded = False
        self._restore_transaction_id: str | None = None
        self._restore_file_was_absent = False
        self._restore_file_fingerprint: str | None = None
        self._timeout = 2.0

    def snapshot_runtime(self):
        self._runtime_before = (
            live.read_runtime_report(), live.level_file_fingerprint())
        return self._runtime_before

    def prepare(self, chain: dict) -> PreparedCommit:
        current_fingerprint = live.chain_file_fingerprint()
        if current_fingerprint != self._base_fingerprint:
            raise chain_protocol.ChainFileConflict(
                "chain changed while the managed transaction was preparing")
        current_revision = self._base_revision
        revision = current_revision + 1
        self._transaction_id = uuid.uuid4().hex
        candidate = deepcopy(chain)
        candidate["_transaction_id"] = self._transaction_id
        normalized = chain_protocol.normalize_chain(
            candidate, root=live.ROOT, revision=revision)
        self._timeout = min(10.0, 2.0 + 0.5 * len(normalized["slots"]))
        self._runtime_session_id = live.request_runtime_prepare(
            normalized, self._transaction_id, timeout=self._timeout)
        return PreparedCommit(
            normalized,
            {"revision": revision, "transaction_id": self._transaction_id},
            revision)

    def write_file(self, chain: dict) -> CommitReceipt:
        self._candidate_fingerprint = chain_protocol.serialized_chain_fingerprint(
            chain, root=live.ROOT)
        self._candidate_revision = chain.get("revision")
        self._file_write_succeeded = False
        self._file_restore_succeeded = False
        self._restore_transaction_id = None
        self._restore_file_was_absent = False
        self._restore_file_fingerprint = None
        try:
            live.write_chain(
                chain,
                expected_fingerprint=self._base_fingerprint,
                expected_revision=self._base_revision,
                revision=chain.get("revision"),
            )
        except Exception:
            # The protocol can fail after rename (for example, while
            # re-reading the just-written file). Detect that case without
            # mistaking a CAS rejection for a successful write.
            self._file_write_succeeded = (
                live.chain_file_fingerprint() == self._candidate_fingerprint)
            raise
        self._file_write_succeeded = True
        persisted = live.read_chain()
        revision = persisted.get("revision")
        if (isinstance(revision, bool) or not isinstance(revision, int)
                or revision < 0):
            raise ChainStateError("managed write did not return a revision")
        return CommitReceipt(
            self._candidate_fingerprint or live.last_chain_write_fingerprint()
            or live.chain_file_fingerprint(),
            revision,
        )

    def apply_runtime(self, prepared: PreparedCommit) -> None:
        wait_kwargs = {
            "transaction_id": prepared.runtime.get("transaction_id"),
            "previous": self._runtime_before,
            "timeout": self._timeout,
        }
        if self._runtime_session_id is not None:
            wait_kwargs["expected_session_id"] = self._runtime_session_id
        live.wait_for_runtime_revision(prepared.revision, **wait_kwargs)

    def restore_file(self, _chain: dict) -> None:
        if not self._file_write_succeeded:
            return
        restore_payload = self._previous_payload
        if restore_payload is None:
            # A missing chain file still has a runtime state: the canonical
            # zero-slot/default chain. Publish that state through a temporary
            # file so the engine can acknowledge rollback before the file is
            # removed again.
            try:
                previous = chain_protocol.normalize_chain(
                    self._base_chain, root=live.ROOT, revision=self._base_revision)
            except chain_protocol.ChainProtocolError as exc:
                raise ChainStateError(
                    "managed rollback base chain is invalid") from exc
        else:
            try:
                previous = json.loads(restore_payload.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ChainStateError(
                    "managed rollback payload is not valid JSON") from exc
            if not isinstance(previous, dict):
                raise ChainStateError("managed rollback payload is not an object")
        self._restore_transaction_id = uuid.uuid4().hex
        previous["_transaction_id"] = self._restore_transaction_id
        # Rollback is a new managed transaction, even though it restores the
        # old chain content. Keep the base revision explicit so the prepare
        # request and the file written below describe the same candidate.
        previous["revision"] = self._base_revision
        self._runtime_session_id = live.request_runtime_prepare(
            previous, self._restore_transaction_id, timeout=self._timeout)
        if self._previous_payload is None:
            self._restore_file_was_absent = True
            restore_payload = (
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
        else:
            restore_payload = (
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
        live.restore_chain_bytes(
            restore_payload,
            expected_fingerprint=self._candidate_fingerprint,
        )
        self._restore_file_fingerprint = live.chain_file_fingerprint()
        self._file_restore_succeeded = True
        remember_rollback = getattr(
            self._app, "_remember_managed_rollback_target", None)
        if callable(remember_rollback):
            remember_rollback(
                (self._candidate_fingerprint, self._candidate_revision))

    def restore_runtime(self, _snapshot) -> None:
        if not self._file_restore_succeeded:
            return
        if not self._app._managed_engine_active():
            raise ChainStateError(
                "managed engine became inactive during rollback")
        if not self._restore_transaction_id:
            raise ChainStateError("managed rollback has no transaction id")
        wait_kwargs = {
            "transaction_id": self._restore_transaction_id,
            "previous": self._runtime_before,
            "timeout": self._timeout,
        }
        if self._runtime_session_id is not None:
            wait_kwargs["expected_session_id"] = self._runtime_session_id
        # The rollback candidate carries the file's base revision. Telemetry
        # may still lag behind that revision when the failed commit began, so
        # it is not a valid acknowledgement target.
        live.wait_for_runtime_revision(self._base_revision, **wait_kwargs)
        if self._restore_file_fingerprint is None:
            raise ChainStateError(
                "managed rollback temporary chain disappeared")
        # Only restore the caller-visible file after the new rollback
        # acknowledgement proves that runtime no longer uses the temporary
        # transaction candidate. Existing files must regain their exact
        # original bytes, including formatting and unknown compatibility data.
        live.restore_chain_bytes(
            None if self._restore_file_was_absent else self._previous_payload,
            expected_fingerprint=self._restore_file_fingerprint,
        )
        remember_rollback = getattr(
            self._app, "_remember_managed_rollback_target", None)
        if callable(remember_rollback):
            remember_rollback(
                (self._candidate_fingerprint, self._candidate_revision))

# Guitar-amp inspired palettes. Keep the source tokens in one table so each
# theme has the same semantic surface and only its visual material changes.
_GUITAR_AMP_THEME_SPECS = (
    ("orange-tolex", "#17110E", "#241912", "#312015", "#492A18",
     "#F4E5D0", "#F07820", "#A8774B", "#FFB04A", "#E0A33A"),
    ("tweed-brass", "#181510", "#282118", "#392C20", "#4B3A27",
     "#F4E5C4", "#D2A65A", "#9A7549", "#EBC878", "#D7923F"),
    ("diamond-noir", "#101315", "#181C1F", "#23292C", "#31383B",
     "#EFE9DC", "#D7B65E", "#789A9C", "#B95F78", "#D89A4A"),
    ("blackface-silver", "#111416", "#1A1E21", "#252A2D", "#343B3E",
     "#EFF0EB", "#D4D8D4", "#90999A", "#9CC2C4", "#D8A248"),
    ("british-green-oxblood", "#101612", "#18221A", "#253126", "#334333",
     "#EDE4D3", "#D0AD68", "#789176", "#B85D5C", "#D69A46"),
    ("surf-cream-coral", "#111719", "#1C2927", "#293A35", "#385047",
     "#F5EAD8", "#95C3B1", "#B9A98D", "#E3795B", "#D9AA52"),
)


def _guitar_amp_theme(spec: tuple[str, ...]) -> Theme:
    (name, background, surface, panel, boost, foreground, primary,
     secondary, accent, warning) = spec
    return Theme(
        name=name,
        dark=True,
        background=background,
        surface=surface,
        panel=panel,
        boost=boost,
        foreground=foreground,
        primary=primary,
        secondary=secondary,
        accent=accent,
        success=FIXED_SEMANTIC_COLORS["success"],
        warning=warning,
        error=FIXED_SEMANTIC_COLORS["error"],
        variables={
            "block-cursor-background": accent,
            "block-cursor-foreground": background,
            "block-cursor-text-style": "bold",
            "input-selection-background": f"{primary} 35%",
            # Rich metadata tables need a concrete field color. Keep it tied
            # to the theme foreground rather than inventing a second palette.
            "field": foreground,
        },
    )


# Keep the existing GigBuddy palette as the product default. The restored
# themes are additional choices, not a replacement for the established name.
GIGBUDDY_THEME = Theme(
    name="gigbuddy",
    dark=True,
    background="#1b1512",
    surface="#261d16",
    panel="#31251a",
    boost="#3d2e1f",
    foreground="#f0e2cc",
    primary="#e59a3c",
    secondary="#8f6b46",
    accent="#f5b042",
    success=FIXED_SEMANTIC_COLORS["success"],
    warning="#e0b34a",
    error=FIXED_SEMANTIC_COLORS["error"],
    variables={
        "block-cursor-background": "#f5b042",
        "block-cursor-foreground": "#1b1512",
        "block-cursor-text-style": "bold",
        "input-selection-background": "#e59a3c 35%",
        "field": "#d3bf9e",
    },
)

# 16 色安全深色主题：给不支持 truecolor 的终端使用（16 色命名色，
# 任何终端都能正确渲染；金色用 yellow 近似）。与 textual-dark 一起
# 构成受限终端的唯二可选主题。
COMPAT_DARK_THEME = Theme(
    name="compat-dark",
    dark=True,
    background="#000000",
    surface="#141414",
    panel="#1a1a1a",
    boost="#242424",
    foreground="#ffffff",
    primary="#ffff00",
    secondary="#b3b3b3",
    accent="#ffff00",
    success="#00ff00",
    warning="#ffff00",
    error="#ff0000",
    variables={
        "block-cursor-background": "#ffff00",
        "block-cursor-foreground": "#000000",
        "block-cursor-text-style": "bold",
        "input-selection-background": "#ffff00",
        "field": "#ffff00",
    },
)

class QuitConfirmModal(GigBuddyModal):
    """Second-stage quit confirmation: the QUIT button asks before exiting."""

    CSS = """
    QuitConfirmModal > ModalBox {
        width: 50%; height: auto; margin: 12 25;
        border: round $error; border-title-color: $error;
    }
    """

    def compose(self) -> ComposeResult:
        box = ModalBox()
        box.border_title = "QUIT"
        with box:
            yield NonSelectableStatic(
                "Are you sure you want to quit GigBuddy?")

    def on_mount(self) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_layout(box, "", ["cancel", "enter quit"])

    def on_unmount(self) -> None:
        # 弹窗移除后焦点会被 Textual 恢复给 Quit 按钮——移走它，避免
        # 空格键（按钮激活键）立即重开弹窗。
        self.app.set_focus(None)

    def action_cancel(self) -> None:
        """Esc / border-hint cancel: close without quitting."""
        self.dismiss()

    def _confirm(self) -> None:
        """Enter / border-hint confirm: exit the app."""
        self.app.exit()

    def _border_hint_actions(self) -> list[tuple[str, Callable[[], None]]]:
        return [("cancel", self.dismiss), ("enter quit", self._confirm)]

    def on_click(self, event: MouseEvent) -> None:
        border_hint_click(self.query_one(ModalBox), event,
                          self._border_hint_actions())

    def on_mouse_move(self, event: MouseMove) -> None:
        box = self.query_one(ModalBox)
        set_border_hint_hover(
            box, border_hint_action_token(
                box, event.screen_x, event.screen_y,
                [token for token, _ in self._border_hint_actions()]))


GUITAR_AMP_THEMES = (GIGBUDDY_THEME,
                     *(_guitar_amp_theme(spec)
                       for spec in _GUITAR_AMP_THEME_SPECS))
GUITAR_AMP_THEME_NAMES = tuple(theme.name for theme in GUITAR_AMP_THEMES)


class HeaderStatus(MarqueeBar):
    """Single-line status message overlaid in the header's top-left corner."""

    def __init__(self, *, id: str = "header-status") -> None:
        super().__init__("", id=id)

    def show_status(self, message: str, severity: str) -> None:
        self.content = message
        self.remove_class(
            "header-status--information",
            "header-status--warning",
            "header-status--error",
        )
        self.add_class(f"header-status--{severity}", "header-status--visible")
        self._cap_width()

    def _cap_width(self) -> None:
        """Cap the strip below the centered title's left edge.

        The title sits centered in the header; the notification strip must
        never cover it (REQ-015/REQ-018), so its max width is the title's
        left edge minus one column. The title's left edge depends on the
        terminal width, so the cap is computed from the live layout instead
        of a fixed CSS constant.
        """
        header = self.app.query_one(GigBuddyHeader)
        title = header.query_one(HeaderTitle)
        content = title.content_region
        title_width = cell_len(str(title.render()))  # 终端列宽（em-dash 占 2 列）
        left_edge = content.x + (content.width - title_width) // 2
        cap = max(left_edge - 1, 16)
        # MarqueeBar renders from its own width, so the strip width must be
        # set explicitly (an auto width would measure a marquee of width 0).
        self.styles.width = cap
        self.styles.max_width = cap

    def clear_status(self) -> None:
        self.content = ""
        self.remove_class(
            "header-status--visible",
            "header-status--information",
            "header-status--warning",
            "header-status--error",
        )


class GigBuddyHeader(Header):
    """Single-line application header; the title is always centered.

    Notifications live inside the header row as an overlay strip in the
    top-left corner (REQ-015/REQ-018): overlay keeps them out of the flow (the
    title never moves) and their width is capped below the title's left edge
    (they never cover the centered title nor the library panel below).
    """

    def compose(self) -> ComposeResult:
        icon = HeaderIcon().data_bind(Header.icon)
        icon.ALLOW_SELECT = False
        yield icon
        yield HeaderStatus()
        title = HeaderTitle()
        title.ALLOW_SELECT = False
        yield title
        yield Button("log in", id="header-auth", compact=True)
        clock = (HeaderClock().data_bind(Header.time_format)
                 if self._show_clock else HeaderClockSpace())
        clock.ALLOW_SELECT = False
        yield clock

    def _on_click(self, event) -> None:
        """Keep the application header single-line.

        Textual's default Header click handler toggles ``-tall`` (height 3),
        which shifts every panel when the title is clicked. GigBuddy uses the
        header as a fixed status bar, so clicking it must have no layout effect.
        """
        event.prevent_default()
        event.stop()


def _parse_devices(output: str) -> tuple[list[str], list[str]]:
    """Parse `realtime_cli --list` lines into (input devices, output devices)."""
    ins, outs = [], []
    for line in output.splitlines():
        m = re.match(r"\[\d+\] (.*?) \(in=(\d+) out=(\d+)", line)
        if not m:
            continue
        name, ni, no = m.group(1), int(m.group(2)), int(m.group(3))
        if ni > 0:
            ins.append(name)
        if no > 0:
            outs.append(name)
    return ins, outs


class GigBuddyApp(App):
    TITLE = "GigBuddy"
    DEFAULT_SUB_TITLE = "Your one-stop NAM tone manager"
    SUB_TITLE = DEFAULT_SUB_TITLE

    CSS = """
    Screen { layout: vertical; background: $background; }
    Header { background: $panel; color: $primary; text-style: bold; }
    Header .header--title { color: $primary; text-style: bold; }
    Header .header--sub-title { color: $text-muted; text-style: none; }
    HeaderIcon { display: none; }  /* palette stays on ctrl+p only */
    GigBuddyHeader { layout: horizontal; }
    GigBuddyHeader HeaderIcon, GigBuddyHeader HeaderTitle,
    GigBuddyHeader HeaderClock, GigBuddyHeader HeaderClockSpace {
        dock: none;
    }
    GigBuddyHeader HeaderTitle {
        width: 1fr; content-align: center middle;  /* 标题居中 */
        padding-left: 22;  /* 与右侧 auth + HeaderClock 对称，内容精确居中 */
    }
    GigBuddyHeader #header-auth {
        dock: none; width: 12; min-width: 12; height: 1;
        padding: 0 1; content-align: center middle;
        color: $text-muted; background: $panel; border: none;
    }
    GigBuddyHeader #header-auth:hover,
    GigBuddyHeader #header-auth:focus {
        color: $accent; background: $surface-lighten-1;
    }
    GigBuddyHeader HeaderClock, GigBuddyHeader HeaderClockSpace { width: 10; }
    GigBuddyHeader #header-status {
        overlay: screen;  /* 通知浮层：不参与流布局，标题恒居中不动 */
        background: $panel;  /* 与 Header 同色，视觉上是 Header 的一部分 */
        visibility: hidden;
        width: 0; max-width: 0; min-width: 0; height: 1;
        padding: 0; content-align: left middle;
        text-wrap: nowrap;
        text-style: bold;
    }
    GigBuddyHeader #header-status.header-status--visible {
        visibility: visible;
        /* 基础上限 43 列 < 120 列终端的居中标题左边缘；HeaderStatus 显示时
           Python 侧按实时布局收紧宽度，窄终端下也不遮标题 */
        width: 43; max-width: 43; min-width: 0;
        padding: 0 1;
    }
    GigBuddyHeader #header-status.header-status--information { color: $success; }
    GigBuddyHeader #header-status.header-status--warning { color: $warning; }
    GigBuddyHeader #header-status.header-status--error { color: $error; }
    Footer { background: $panel; }
    .footer-key:hover {
        background: $panel-lighten-1;
        color: $text;
    }
    #footer-row {
        height: 1; layout: horizontal;
        background: $panel;
    }
    #footer-row Footer {
        dock: none; height: 1; width: 1fr;
    }
    #app-quit {
        height: 1; width: 16;
        color: $error;
        text-style: bold;
        content-align: center middle;
        background: $boost;
        border-top: none; border-right: none;
        border-bottom: none; border-left: none;
    }
    #app-quit:hover {
        background: $error;
        color: $background;
    }
    #top { layout: horizontal; height: 1fr; }

    /* panels: quiet warm border, amber when something inside has focus.
       REQ-039 两级聚焦视觉：聚焦页面（focus-within）边框亮 + 内容全亮；
       非聚焦页面边框回到 quiet 色 + 内容 opacity 0.8 降半级，让焦点所在
       页面一眼可辨。模态（独立 screen）不在此列，恒为最亮。 */
    LibraryPanel, PresetPanel, ChainPanel, DetailPane {
        border: round $surface-lighten-2;
        border-title-color: $text-muted;
        border-subtitle-color: $text-disabled;
        padding: 0 1;
        opacity: 0.8;
    }
    LibraryPanel:focus-within, PresetPanel:focus-within,
    ChainPanel:focus-within, DetailPane:focus-within {
        border: round $primary;
        border-title-color: $primary;
        opacity: 1;
    }
    #left-col { width: 3fr; layout: vertical; }
    LibraryPanel { height: 3fr; }
    /* The pane hierarchy must have a bounded height. Otherwise DataTable grows
       to every row, which suppresses its scrollbar in LOCAL and TOP CREATORS. */
    LibraryPanel > TabbedContent, LibraryPanel TabPane { height: 1fr; }
    LibraryPanel TabPane { layout: vertical; }
    LibraryPanel DataTable { height: 1fr; }
    LibraryPanel > #library-view-tabs {
        position: absolute; layer: border-tabs;
        width: 1; height: 1; padding: 0;
        opacity: 0;
    }
    /* ContentTabs remains as the content switcher's compatibility mechanism;
       its visible labels are replaced by the single-focus custom strip. Keep
       the compatibility widget out of the compositor: an opacity-only
       overlay would cover the active pane's SearchBar at the same y-position. */
    LibraryPanel > TabbedContent > ContentTabs {
        display: none;
    }
    /* Remote download states are a persistent list legend, not a result row. */
    #tone-login-button, #creators-login-button {
        height: auto; min-width: 24; width: auto; margin: 0 1; padding: 0 1;
        color: $background; text-style: bold;
    }
    #tone-status {
        display: none; dock: bottom; height: 1; padding: 0 1;
        color: $text-muted; content-align: left middle;
    }
    #lib-status { color: $text-muted; padding: 0 1; }
    PresetPanel > MarqueeBar, ChainPanel > MarqueeBar {
        height: 1; padding: 0 1; color: $text;
    }
    /* Keep enough room for the banner, CRUD controls, and at least a few
       preset rows even on the compact 100x32 terminal used in practice. */
    PresetPanel { height: 2fr; min-height: 9; }
    PresetPanel DataTable { height: 1fr; }

    #right-col { width: 2fr; layout: vertical; }
    /* Canonical v0.2 ChainPanel grows only with its 0-6 Slot list. */
    ChainPanel { height: auto; min-height: 7; max-height: 30; }
    ChainPanel.chain-panel-dynamic {
        max-height: 100%; overflow-y: auto;
    }
    ChainPanel .chain-node-row {
        /* 4 = round border 2 + content 2: title line with ▲, filename with ▼ */
        height: 4; width: 100%;
        background: transparent;
        border: round $surface-lighten-2;
        border-title-color: $text-muted;   /* REQ-043: type 在框左上角 */
    }
    ChainPanel .chain-node {
        width: 1fr; height: 2; padding: 0 1;
        background: transparent; border: none;
    }
    ChainPanel .chain-node:hover {
        background: $panel-lighten-1;
    }
    ChainPanel .chain-node:focus {
        background: $panel-lighten-1;
    }
    /* INPUT 节点的高亮画在文本层（render 里 on $panel-lighten-1，只盖左侧），
       这里关掉整块 CSS 背景——否则行尾 PLAY 块会被浅色 hover 整块盖住。 */
    ChainPanel .chain-node.chain-node-input:hover,
    ChainPanel .chain-node.chain-node-input:focus {
        background: transparent;
    }
    /* 缺位节点（AMP/CAB 未加载）：NONE 占位，无背景——模块不加载但
       音频同样直通（与 BYPASS 的区别只在 ◐ 灯与文件行） */
    ChainPanel .chain-node.chain-node-empty {
        background: transparent;
    }
    ChainPanel .chain-node-row:focus-within {
        /* 聚焦只给边框提示；高亮背景留在节点内容区（.chain-node:focus），
           整行不亮。 */
        border: round $accent;
        border-title-color: $accent;
    }
    ChainPanel .chain-switch-col {
        /* 奇数内容宽（11 - padding 2 = 9）：单字符箭头精确居中，
           偶数宽会让 1 字符内容偏向一侧 0.5 格 */
        width: 11; height: 4;
        layout: vertical;
        padding: 0 1;
    }
    ChainPanel .chain-switch-btn {
        /* Fill the switch column's content box. A fixed width of 11 extends
           past the column's padded 9-cell content area, so hover background
           gets clipped while the arrow is centered against the larger box. */
        width: 1fr; height: 1; padding: 0;
        content-align: center middle;
        color: $text; background: transparent; text-style: bold;
    }
    ChainPanel .chain-switch-btn:hover {
        background: $accent; color: $background;
    }
    /* INPUT 行是单行布局（文件名 + 状态 + 行内 PLAY 块，无符号按钮）：
       行高 3 = round 边框 2 + 内容 1，节点内容区相应收为一行 */
    ChainPanel .chain-node-row-input { height: 3; }
    ChainPanel .chain-node-row-input .chain-node { height: 1; }
    ChainPanel .chain-slot-row {
        height: 4; width: 100%;
        background: transparent;
        border: round $surface-lighten-2;
        border-title-color: $text-muted;
    }
    ChainPanel .chain-slot-main {
        width: 1fr; height: 2; layout: horizontal;
    }
    ChainPanel .chain-slot {
        width: 1fr; height: 2; padding: 0 1;
        background: transparent; border: none;
    }
    ChainPanel .chain-slot:hover,
    ChainPanel .chain-slot:focus { background: $panel-lighten-1; }
    ChainPanel .chain-slot-row:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }
    ChainPanel .chain-slot-io {
        width: 26; min-width: 26; max-width: 26; height: 2;
        padding: 0; color: $text;
        background: transparent;
    }
    ChainPanel .chain-slot-actions {
        width: 11; height: 4; layout: vertical; padding: 0 1;
    }
    ChainPanel .chain-slot-action {
        width: 1fr; height: 1; padding: 0;
        content-align: center middle;
        color: $text; background: transparent; text-style: bold;
    }
    ChainPanel .chain-slot-action:hover {
        background: $accent; color: $background;
    }
    ChainPanel .chain-add-slot {
        height: 1; padding: 0 1; color: $text-muted;
        background: transparent;
    }
    ChainPanel .chain-add-slot:focus,
    ChainPanel .chain-add-slot:hover {
        color: $background; background: $accent;
    }
    ChainPanel .chain-effect {
        height: 1; padding: 0 1 0 2; color: $text-muted;
    }
    ChainPanel .chain-params {
        dock: bottom; height: 1; margin: 0; padding: 0 1;
        background: $panel; color: $text;
    }
    ChainPanel.chain-panel-dynamic .chain-params {
        dock: none;
    }
    DetailPane { height: 1fr; }
    DetailPane MarqueeBar { height: 1; color: $text; }

    #bottom { layout: vertical; height: 5; }
    /* InterfaceBar 与内容面板同一套两级聚焦（REQ-039）：平时 LEVEL 标题
       muted + 内容 dim；聚焦其按钮（focus-within）时标题/边框转 primary。 */
    InterfaceBar {
        height: 5; layout: horizontal;
        border: round $surface-lighten-2;
        border-title-color: $text-muted;
        padding: 0 1; align: center middle;
        opacity: 0.8;
    }
    InterfaceBar:focus-within {
        border: round $primary;
        border-title-color: $primary;
        opacity: 1;
    }
    InterfaceBar MeterBar { height: 2; width: 1fr; border: none; padding: 0 1; }
    InterfaceBar #cpu-status {
        width: 13; height: 1; color: $text-muted;
        content-align: center middle; margin-right: 1;
    }
    InterfaceBar #runtime-status {
        width: auto; height: 1; color: $text-muted;
        content-align: center middle; margin-right: 3;
    }
    InterfaceBar .audio-action {
        height: 3; width: 18; content-align: center middle;
        border: round $secondary; text-style: bold; color: $foreground;
        margin-left: 1;
    }
    InterfaceBar #audio-mute {
        width: 9;
        content-align: center middle;
    }
    InterfaceBar .audio-action:hover, InterfaceBar .audio-action:focus {
        background: $background; color: $accent; border: round $accent;
    }
    /* Muted is the error-coloured counterpart to the outlined idle action.
       Keeping the same surface and geometry makes MUTE and MUTED read as one
       control instead of two unrelated button treatments. */
    InterfaceBar #audio-mute.muted,
    InterfaceBar #audio-mute.muted:hover,
    InterfaceBar #audio-mute.muted:focus {
        background: $background; color: $error; border: round $error;
    }

    /* tables: warm header, amber cursor row */
    /* Keep both scrollbar axes to one terminal cell. This is intentionally
       narrow for dense panes; the axis-specific properties avoid Textual's
       horizontal/vertical shorthand order being misread. */
    ScrollableContainer {
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: $secondary;
        scrollbar-color-hover: $accent;
    }
    DataTable, Tree {
        background: $surface;
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: $secondary;
        scrollbar-color-hover: $accent;
    }
    DataTable > .datatable--header {
        background: $boost; color: $primary; text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: $primary; color: $background; text-style: bold;
    }
    /* REQ-039 两级光标：面板内默认 $secondary（非聚焦页面光标降一级，仍在
       页内最显眼），面板激活（focus-within）时提回 $primary 最亮。Textual
       无 :not() 伪类，用"默认降级 + 激活提亮"双规则表达。模态里的表不在
       这 3 个面板内，光标保持全局 $primary。 */
    LibraryPanel DataTable > .datatable--cursor,
    PresetPanel DataTable > .datatable--cursor,
    DetailPane DataTable > .datatable--cursor {
        background: $secondary;
    }
    LibraryPanel:focus-within DataTable > .datatable--cursor,
    PresetPanel:focus-within DataTable > .datatable--cursor,
    DetailPane:focus-within DataTable > .datatable--cursor {
        background: $primary;
    }
    DataTable > .datatable--hover { background: $panel-lighten-1; }
    Tree > .tree--cursor {
        background: $primary; color: $background; text-style: bold;
    }
    Tree > .tree--guides { color: $secondary; }
    OptionList { border: none; background: transparent; }
    OptionList > .option-list--option-highlighted {
        background: $primary; color: $background; text-style: bold;
    }
    /* persistent borders on inputs/selects: focus only recolors, never adds
       the 2-row round border that pushes the layout (the "TYPE jumps" bug) */
    Input, Select {
        background: $surface;
        border: round $surface-lighten-2;
    }
    Select > SelectCurrent {
        height: 1; padding: 0 1; content-align: left middle;
        border: none !important; background: $surface;
    }
    Select.-textual-compact > SelectCurrent {
        border: none !important; padding: 0 1;
    }
    Input:hover, Select:hover { border: round $surface-lighten-3; }
    Input:focus, Select:focus { border: round $accent; }
    Input > .input--placeholder { color: $text-disabled; }

    /* SearchBar is the one-line query surface: its controls use a background
       track with no frame, while every other Input/Select keeps the normal
       framed control above. These app-level rules intentionally sit after the
       global control skin so it cannot restore four-sided boxes. */
    SearchBar > Input, SearchBar > Select {
        background: $boost;
        border-top: none;
        border-right: none;
        border-left: none;
        border-bottom: none;
    }
    SearchBar > Input:focus, SearchBar > Select:focus {
        background: $surface-lighten-1;
        border-top: none;
        border-right: none;
        border-left: none;
        border-bottom: none;
    }
    SearchBar > Select.-textual-compact > SelectCurrent,
    SearchBar > Select:focus > SelectCurrent {
        background: $surface-lighten-1;
        border-top: none !important;
        border-right: none !important;
        border-left: none !important;
        border-bottom: none !important;
    }
    SearchBar > Input:hover, SearchBar > Select:hover {
        border-top: none;
        border-right: none;
        border-left: none;
        border-bottom: none;
    }

    /* overlays (select dropdown / command palette) share one raised
       look: lighter surface + accent border, clearly off the main background */
    SelectOverlay {
        background: $panel-lighten-1; opacity: 1;
        border: round $accent;
    }
    /* Notifications live in GigBuddyHeader; hide Textual's fallback rack so
       it cannot reserve or cover any part of the workspace. */
    ToastRack { display: none; }
    CommandPalette {
        background: $panel-lighten-1;
        border: round $accent;
    }

    #unsupported-size {
        display: none;
        layer: unsupported-size;
        position: absolute;
        width: 100%; height: 100%; offset: 0 0;
        padding: 1 2;
        content-align: center middle;
        text-align: center;
        color: $error;
        background: $background;
        border: round $error;
        text-style: bold;
    }
    #unsupported-size.unsupported-size--visible { display: block; }

    /* tabs: Textual's built-in Tabs{height:2} + Tab{height:1} leave zero content
       rows under a round border (labels vanish) and clip the bottom border —
       both overridden here to height:3 (border 2 + label 1) */
    TabbedContent > ContentTabs { height: 3; }
    TabbedContent Tab {
        height: 3;
        padding: 0 2; margin: 0 1 0 0;
        color: $text-muted; background: $background; text-style: none;
        border: round $surface-lighten-2;
    }
    TabbedContent Tab:hover { color: $foreground; background: $boost; }
    TabbedContent Tab.-active {
        color: $accent; background: $background; text-style: bold;
        border: round $accent;
    }
    /* Textual themes may supply a focus fill for the active tab. The tab is a
       navigation marker, not a destructive/action button, so keep it outlined. */
    TabbedContent Tab.-active:hover { background: $background; }
    """

    COMMAND_PALETTE_BINDING = "ctrl+p"  # phosphor-style command menu
    PARAM_HOLD_COMMIT_INTERVAL = 0.08
    BINDINGS = [
        Binding("/", "focus_search", "search"),
        Binding("t", "next_theme", "theme"),
        # 链参数步进（v0.1.1 交互契约）：App 全局生效，任何界面位置按
        # g/G/m/M/q/Q 都能步进；聚焦 ChainParams 时由它的 edit_guard
        # 遮蔽成 no-op，避免编辑态误步进。
        Binding("g", "bump_gain(-0.05)", "gain -", show=False),
        Binding("G", "bump_gain(+0.05)", "gain +", show=False),
        Binding("m", "bump_master(-0.05)", "master -", show=False),
        Binding("M", "bump_master(+0.05)", "master +", show=False),
        # REQ-017: preset 应用改的是链配置（live_chain.json）——undo/redo
        # 即链配置快照的恢复/还原（ctrl+shift+z = redo；无 y 键冲突）
        Binding("ctrl+z", "undo_chain", "undo preset"),
        Binding("ctrl+shift+z", "redo_chain", "redo preset"),
        Binding("q", "bump_quality(-0.05)", "quality -", show=False),
        Binding("Q", "bump_quality(+0.05)", "quality +", show=False),
        # no single-key quit: Ctrl+C twice (from any screen/modal) exits
        Binding("ctrl+c", "request_quit", "quit (×2)", show=False,
                priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield GigBuddyHeader(show_clock=True)
        with Vertical():
            with Horizontal(id="top"):
                with Vertical(id="left-col"):
                    yield LibraryPanel()
                    yield PresetPanel()
                with Vertical(id="right-col"):
                    yield ChainPanel()
                    yield DetailPane()
            with Vertical(id="bottom"):
                yield InterfaceBar()
        with Horizontal(id="footer-row"):
            yield AudioActionButton("ctrl+c ×2 quit", "quit", "app-quit")
            yield Footer()
        yield Static("Minimum terminal size: 80x32", id="unsupported-size")

    def __init__(self, dev_in: str = "", dev_out: str = "", in_ch: int = 0,
                 spawn_engine: bool = True, theme: str | None = None) -> None:
        super().__init__()
        # Status messages are rendered by GigBuddyHeader instead of Textual's
        # floating ToastRack, which would cover the library tables.
        self._disable_notifications = True
        self._dev_in = dev_in
        self._dev_out = dev_out
        self._in_ch = in_ch
        self._spawn_engine = spawn_engine
        self._engine: subprocess.Popen | None = None
        self._engine_log_handle = None
        self._engine_lock_handle = None
        self._engine_restart_failures = 0
        self._engine_retry_at = 0.0
        self._engine_restart_exhausted = False
        self._engine_failure_notified = False
        self._engine_started_at = 0.0
        self._block = 256
        self._sr = 48000
        self._audio_ins: list[str] = []
        self._audio_outs: list[str] = []
        # double-click toggles: remembered values for restoring IR / amp gain
        self._ir_backup: str | None = None
        self._amp_model_backup: str | None = None
        self._last_quit_at = 0.0  # Ctrl+C twice within QUIT_WINDOW_S exits
        # 播放控制操作时间戳：操作后短暂抑制 level.json 回传覆盖，
        # 避免引擎处理链延迟期间 play_state 来回跳变（0.2s tick 旧状态覆盖）
        self._playback_op_ts = 0.0
        self._header_status_timer = None
        self._header_status_identity: str | None = None
        self._tone3000_logged_in = False
        self._tone3000_auth_generation = 0
        self._last_refresh_chain_fingerprint: str | None = None
        self._last_refresh_chain_token: tuple | None = None
        self._last_refresh_chain_config: dict | None = None
        self._last_catalog_refresh_at: float | None = None
        self._last_ui_cpu_sample_at: float | None = None
        self._last_ui_cpu_sample_time: float | None = None
        self._ui_cpu_percent: float | None = None
        self._last_engine_cpu_pid: int | None = None
        self._last_engine_cpu_sample_at: float | None = None
        self._engine_cpu_percent: float | None = None
        self._total_cpu_percent: float | None = None
        self._last_runtime_status_report: tuple[int | None, str] | None = None
        self._mutation_refresh = MutationRefreshCoordinator(
            self.call_after_refresh, self._reconcile_after_mutation,
            self._capture_mutation_anchors)
        self._remote_detail_request_id = 0
        self._remote_detail_cache: dict[int, dict] = {}
        # Textual queries are scoped to the active Screen. Keep the persistent
        # main-surface widgets explicitly so a modal cannot hide them from the
        # mutation coordinator.
        self._mutation_pages: tuple[object, ...] = ()
        self._mutation_anchors: dict[int, ViewAnchor] = {}
        self._device_request_generation = 0
        self._save_confirm_name: str | None = None
        self._save_confirm_at = 0.0
        self._save_confirm_chain: str | None = None
        # Parameter holds use one background commit worker. The UI can keep
        # rendering the local value while the managed runtime acknowledges the
        # latest coalesced chain candidate.
        self._param_hold_lock = threading.Lock()
        self._param_hold_generation = 0
        self._param_hold_pending: tuple[
            int, int | None, int | None, str, float, bool, bool,
            tuple[str | None, ...] | None, tuple[int, ...] | None
        ] | None = None
        self._param_hold_slot_identity: int | None = None
        self._param_hold_slot_paths: tuple[str | None, ...] | None = None
        self._param_hold_slot_identities: tuple[int, ...] | None = None
        self._param_hold_worker_active = False
        self._param_hold_last_commit_at = 0.0
        # All managed chain writes share one file/runtime critical section.
        # Slot actions and single-step parameters use the queue below; the
        # existing coalesced hold worker enters the same lock.
        self._managed_transaction_lock = threading.Lock()
        self._managed_writer_queue: Queue[_ManagedChainJob | None] = Queue()
        self._managed_writer_thread: threading.Thread | None = None
        self._managed_writer_lock = threading.Lock()
        self._managed_writer_stopping = False
        self._managed_write_targets: dict[
            tuple[str, int], _ManagedWriteTarget] = {}
        self._managed_write_targets_lock = threading.Lock()
        self._managed_slot_projection: tuple[
            str, int, _ManagedWriteTarget] | None = None
        self._calibration_generation = 0
        for guitar_theme in GUITAR_AMP_THEMES:
            self.register_theme(guitar_theme)
        self.register_theme(COMPAT_DARK_THEME)
        self._limited_color = False
        # Pin danger/state colors across every theme (built-in ones included)
        # before the first CSS generation picks the theme.
        for th in self.available_themes.values():
            th.variables.update(FIXED_SEMANTIC_COLORS)
            # Footer hover 高亮：用各主题的 boost 色（比 panel 亮一档）
            th.variables.setdefault(
                "block-hover-background",
                th.variables.get("boost", "#3d2e1f"))
        self.theme = theme or GIGBUDDY_THEME.name

    def get_default_screen(self):
        """Use the selection-aware screen for the main application surface."""
        return ShiftSelectableScreen(id="_default")

    QUIT_WINDOW_S = 1.5
    CATALOG_REFRESH_INTERVAL_S = 0.5
    UI_CPU_SAMPLE_INTERVAL_S = 0.5

    def action_request_quit(self) -> None:
        """Copy a text selection, or require two Ctrl+C presses to quit.

        Bound at app level, so it works from every screen and modal; the
        command palette's Quit entry exits immediately. A selected detail
        table takes the usual terminal shortcut precedence over quitting.
        ctrl+c is always the two-press quit — the Quit button's confirmation
        modal is only opened by activating the button itself.
        """
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.screen.clear_selection()
            self._last_quit_at = 0.0
            self.notify("Selected text copied")
            return
        now = time.monotonic()
        if now - self._last_quit_at < self.QUIT_WINDOW_S:
            self.exit()
        else:
            self._last_quit_at = now
            self.notify("Press ctrl+c again to quit")

    def get_system_commands(self, screen) -> SystemCommand:
        """Command palette (ctrl+p) entries — phosphor-style action menu."""
        yield from super().get_system_commands(screen)

        yield SystemCommand(title="Search TONE3000…", help="focus the library search box",
                            callback=self.action_focus_search, discover=True)
        yield SystemCommand(title="Gain -0.05", help="decrease input gain",
                            callback=lambda: self.action_bump_gain(-0.05))
        yield SystemCommand(title="Gain +0.05", help="increase input gain",
                            callback=lambda: self.action_bump_gain(0.05))
        yield SystemCommand(title="Master -0.05", help="decrease output volume",
                            callback=lambda: self.action_bump_master(-0.05))
        yield SystemCommand(title="Master +0.05", help="increase output volume",
                            callback=lambda: self.action_bump_master(0.05))
        yield SystemCommand(title="Quality -0.05", help="decrease model quality",
                            callback=lambda: self.action_bump_quality(-0.05))
        yield SystemCommand(title="Quality +0.05", help="increase model quality",
                            callback=lambda: self.action_bump_quality(0.05))
        yield SystemCommand(title="Focus Presets", help="focus the Presets panel",
                            callback=self.action_open_preset_picker, discover=True)
        yield SystemCommand(title="Save active preset", help="save the current chain",
                            callback=self.action_save_preset, discover=True)
        yield SystemCommand(title="Save preset as…", help="create a new named preset",
                            callback=self.action_save_preset_as, discover=True)
        yield SystemCommand(title="Audio settings…", help="input, output, buffer and sample rate",
                            callback=self.action_open_audio_settings, discover=True)
        yield SystemCommand(title="Next theme", help="cycle color themes (also: t)",
                            callback=self.action_next_theme, discover=True)
        yield SystemCommand(title="Quit", help="quit GigBuddy",
                            callback=self.action_quit, discover=True)

    def _on_notify(self, event) -> None:
        """Show the newest notification in the single-line application header."""
        notification = event.notification
        self._notifications.clear().add(notification)
        message = notification.message
        if notification.title:
            message = f"{notification.title}: {message}"

        try:
            self.query_one(HeaderStatus).show_status(message, notification.severity)
        except NoMatches:
            # Notifications raised while the initial screen is composing are
            # still retained in ``_notifications`` and will be shown once the
            # header is mounted by the next notification.
            pass

        if self._header_status_timer is not None:
            self._header_status_timer.stop()
        identity = notification.identity
        self._header_status_identity = identity
        self._header_status_timer = self.set_timer(
            max(0.0, notification.time_left),
            partial(self._clear_header_status, identity),
            name="clear header status",
        )

    def _clear_header_status(self, identity: str) -> None:
        """Clear a status only if it still belongs to the notification timer."""
        if identity != self._header_status_identity:
            return
        self._header_status_identity = None
        self._header_status_timer = None
        self._notifications.clear()
        try:
            self.query_one(HeaderStatus).clear_status()
        except NoMatches:
            pass

    def _publish_mutation(self, operation: str, keys=(), revision=None) -> None:
        """Publish one successful persistence change to the app coordinator."""
        self.post_message(MutationCommitted(operation, keys, revision))

    def on_mutation_committed(self, event: MutationCommitted) -> None:
        self._mutation_refresh.receive(event)

    def on_models_downloaded(self, event: ModelsDownloaded) -> None:
        """Show one global completion notice regardless of the active view."""
        count = max(0, int(event.count))
        noun = "model" if count == 1 else "models"
        self.notify(f"Downloaded {count} {noun}")

    def _registered_mutation_pages(self) -> tuple[object, ...]:
        """Return retained main-surface pages, including inactive panes."""
        pages = self._mutation_pages
        if pages:
            return pages
        page_types = (ChainPanel, DetailPane, LibraryPanel, PresetPanel)
        return tuple(
            page for page_type in page_types
            for page in self.query(page_type)
        )

    def _capture_mutation_anchors(self) -> None:
        """Capture every retained page before one reconcile cycle starts."""
        anchors: dict[int, ViewAnchor] = {}
        for page in self._registered_mutation_pages():
            capture = getattr(page, "capture_view_anchor", None)
            if not callable(capture):
                continue
            try:
                anchor = capture()
            except Exception as exc:
                self.log.debug("view anchor capture failed for %r: %s", page, exc)
                continue
            if isinstance(anchor, ViewAnchor):
                anchors[id(page)] = anchor
        self._mutation_anchors = anchors

    def _reconcile_after_mutation(self, event: MutationCommitted) -> None:
        """Reconcile every mounted main-surface page exactly once."""
        pages = self._registered_mutation_pages()
        seen: set[int] = set()
        for page in pages:
            if id(page) in seen:
                continue
            seen.add(id(page))
            anchor = self._mutation_anchors.get(id(page))
            accept = getattr(page, "set_mutation_anchor", None)
            if anchor is not None and callable(accept):
                accept(anchor)
            try:
                page.reconcile_after_mutation(event)
            except (AttributeError, NoMatches):
                continue
            except Exception as exc:
                # One stale/detached page must not prevent the other retained
                # pages from reconciling this committed mutation.
                self.log.error("mutation reconcile failed for %r: %s", page, exc)
            finally:
                restore = getattr(page, "restore_view_anchor", None)
                if anchor is not None and callable(restore):
                    try:
                        restore(anchor)
                    except Exception as exc:
                        self.log.debug(
                            "view anchor restore failed for %r: %s", page, exc)
        self._mutation_anchors = {}

    def _apply_compatible_theme(self) -> None:
        """Terminal color-depth fallback: terminals without truecolor render
        the guitar-amp themes badly (some show as red). Detect the terminal's
        color system via the Rich console; when truecolor is unavailable,
        lock the theme to the two 16-color-safe options (``textual-dark`` and
        ``compat-dark``) and start on ``textual-dark``."""
        try:
            color_system = str(self.console.color_system or "").casefold()
        except Exception:
            color_system = ""
        self._limited_color = color_system != "truecolor"
        if self._limited_color:
            if self.theme not in (COMPAT_DARK_THEME.name, "textual-dark"):
                self.theme = "textual-dark"
                self.notify(
                    "Terminal color support is limited — using the compatible theme")

    def action_next_theme(self) -> None:
        # 受限终端（无 truecolor）：只允许在 16 色安全的两个主题间循环
        if getattr(self, "_limited_color", False):
            themes = [COMPAT_DARK_THEME.name, "textual-dark"]
        else:
            themes = list(GUITAR_AMP_THEME_NAMES)
        # A caller may intentionally start with a built-in Textual theme via
        # ``--theme``; the next explicit theme action should still recover to
        # the restored guitar-amp sequence instead of raising ValueError.
        i = themes.index(self.theme) if self.theme in themes else -1
        self.theme = themes[(i + 1) % len(themes)]
        self.notify(f"Theme: {self.theme}")

    def on_mount(self) -> None:
        self._apply_compatible_theme()
        # REQ-017: 链配置撤销/重做栈（preset 应用快照），启动清空。
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        page_types = (ChainPanel, DetailPane, LibraryPanel, PresetPanel)
        self._mutation_pages = tuple(
            self.query_one(page_type) for page_type in page_types)
        if self._spawn_engine:
            try:
                self._engine_lock_handle = live.acquire_engine_lock()
            except RuntimeError as exc:
                self._spawn_engine = False
                self._engine_restart_exhausted = True
                self.notify(str(exc), severity="error")
                self.call_after_refresh(self.exit)
        self._ensure_engine()
        self.set_interval(0.2, self.refresh_from_files)
        self.query_one("#lib-table-local").focus()
        self._device_request_generation += 1
        self.run_worker(partial(self._load_devices,
                                self._device_request_generation), name="devices")
        self.refresh_tone3000_identity()
        self._last_ui_cpu_sample_at = time.monotonic()
        self._last_ui_cpu_sample_time = time.process_time()
        self._update_unsupported_size()

    def refresh_tone3000_identity(self) -> None:
        """Refresh the personalized subtitle without blocking the TUI."""
        self._tone3000_auth_generation += 1
        generation = self._tone3000_auth_generation
        self.run_worker(
            self._load_tone3000_identity(generation), name="tone3000-identity",
            exclusive=True)

    def _apply_tone3000_identity(self, username: str | None, *,
                                 logged_in: bool) -> None:
        """Apply one authenticated identity to the header controls."""
        self._tone3000_logged_in = logged_in
        self.sub_title = (
            f"{username}'s one-stop NAM tone manager"
            if username else self.DEFAULT_SUB_TITLE)
        try:
            button = self.query_one("#header-auth", Button)
            button.label = "log out" if logged_in else "log in"
        except NoMatches:
            pass

    async def _load_tone3000_identity(self, generation: int) -> None:
        try:
            profile = await asyncio.to_thread(library.tone3000.current_user)
        except library.tone3000.AuthenticationRequiredError:
            if generation == self._tone3000_auth_generation:
                self._apply_tone3000_identity(None, logged_in=False)
            return
        except Exception:
            # Preserve the last known auth state across a transient network
            # failure; an unavailable profile is not proof of a logout.
            return
        if generation != self._tone3000_auth_generation:
            return
        username = str(profile.get("username") or "").strip()
        self._apply_tone3000_identity(username or None, logged_in=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "header-auth":
            return
        event.stop()
        self.run_worker(self._toggle_tone3000_auth(), name="tone3000-auth",
                        group="tone3000-auth", exclusive=True)

    async def _toggle_tone3000_auth(self) -> None:
        try:
            button = self.query_one("#header-auth", Button)
        except NoMatches:
            return
        button.disabled = True
        try:
            if self._tone3000_logged_in:
                no_environment_token = await asyncio.to_thread(
                    library.tone3000.logout)
                self._tone3000_auth_generation += 1
                self._apply_tone3000_identity(None, logged_in=False)
                self.notify("TONE3000 logged out")
                if not no_environment_token:
                    self.refresh_tone3000_identity()
                return

            await asyncio.to_thread(library.tone3000.login)
            self._tone3000_auth_generation += 1
            self._apply_tone3000_identity(None, logged_in=True)
            self.notify("TONE3000 logged in")
            self.refresh_tone3000_identity()
        except library.tone3000.AuthenticationRequiredError:
            self.notify("TONE3000 login cancelled — select log in",
                        severity="warning")
        except Exception:
            self.notify("TONE3000 login unavailable — select log in",
                        severity="error")
        finally:
            button.disabled = False

    def on_resize(self, _event) -> None:
        self._update_unsupported_size()

    def _update_unsupported_size(self) -> None:
        """Keep the minimum-size state tied to the live terminal region."""
        try:
            overlay = self.query_one("#unsupported-size", Static)
        except NoMatches:
            return
        unsupported = self.size.width < 80 or self.size.height < 32
        overlay.set_class(unsupported, "unsupported-size--visible")

    def _ensure_engine(self) -> None:
        """Spawn (or restart) the engine when the chain has a signal source and
        no engine is running. Without this a fresh chain-less boot would spawn
        the engine into an immediate silent exit (realtime_cli --live requires
        a model) — the user gets a hint instead, and picking a tone later starts
        audio. A dry-file input starts the engine even with AMP bypassed
        (model=null) so playback still runs through the chain.
        Also recovers from engine crashes via the 0.2s tick."""
        if not self._spawn_engine:
            return
        if self._engine_restart_exhausted:
            return
        if self._engine is not None and self._engine.poll() is None:
            return
        now = time.monotonic()
        if self._engine is not None:
            return_code = self._engine.poll()
            uptime = now - self._engine_started_at
            self._engine = None
            if self._engine_log_handle is not None:
                self._engine_log_handle.close()
                self._engine_log_handle = None
            self._record_engine_failure(return_code, uptime)
            if self._engine_restart_exhausted:
                return
        if now < self._engine_retry_at:
            return
        cfg = live.read_chain()
        model_path = cfg.get("model") or next(
            (slot.get("path") for slot in cfg.get("slots", [])
             if isinstance(slot, dict) and slot.get("path")), "")
        inp = live.chain_input(cfg)
        dry_path = inp.get("file") if inp.get("source") == "file" else ""
        # Instrument input is already a valid source even when the chain has
        # zero effective Slots. This is the legal direct-through state in the
        # v0.2 engine contract; only a file input needs an existing WAV.
        has_source = (inp.get("source") != "file"
                      or (model_path and Path(model_path).exists())
                      or (dry_path and Path(dry_path).exists()))
        if not has_source:
            if self._engine is not None:
                self.notify("Engine stopped — pick a tone to restart audio",
                            severity="warning")
            return
        if not self._start_engine():
            self._record_engine_failure(None, 0.0)

    def _record_engine_failure(self, return_code, uptime: float) -> None:
        if uptime >= 1.0:
            self._engine_restart_failures = 0
        self._engine_restart_failures += 1
        if not self._engine_failure_notified:
            detail = f" ({return_code})" if return_code is not None else ""
            self.notify(
                f"Engine exited{detail}; retrying with backoff",
                severity="warning")
            self._engine_failure_notified = True
        if self._engine_restart_failures >= 5:
            self._engine_restart_exhausted = True
            self.notify(
                "Engine restart paused after repeated failures",
                severity="error")
            return
        self._engine_retry_at = time.monotonic() + min(
            5.0, 0.1 * (2 ** (self._engine_restart_failures - 1)))

    def _managed_engine_active(self) -> bool:
        """Whether this App owns a live managed runtime transaction target."""
        return bool(
            self._spawn_engine
            and self._engine is not None
            and self._engine.poll() is None
        )

    def _current_chain_target_index(self) -> int | None:
        """Read the current canonical target for non-Slot managed writes."""
        try:
            panel = self.query_one(ChainPanel)
        except NoMatches:
            return None
        state = getattr(panel, "state", None)
        return getattr(state, "target_index", None)

    def _current_chain_identity_snapshot(
            self) -> tuple[tuple[str | None, ...], tuple[int, ...]] | None:
        try:
            panel = self.query_one(ChainPanel)
        except NoMatches:
            return None
        state = getattr(panel, "state", None)
        if state is None:
            return None
        return state.slot_paths, state.slot_identities

    def _enqueue_managed_mutation(
            self, mutation: Callable[[ChainState], object], note: str, *,
            failure_note: str | None = None,
            on_success: Callable[[dict, int | None], None] | None = None,
            target_index: int | None = None,
            target_identity: int | None = None,
            slot_paths: tuple[str | None, ...] | None = None,
            slot_identities: tuple[int, ...] | None = None,
            base_fingerprint: str | None = None,
            base_revision: int | None = None,
    ) -> bool:
        """Queue one managed mutation without blocking Textual's event loop."""
        if not self._managed_engine_active():
            return False
        job = _ManagedChainJob(
            mutation=mutation,
            note=note,
            failure_note=failure_note,
            on_success=on_success,
            target_index=target_index,
            target_identity=target_identity,
            slot_paths=slot_paths,
            slot_identities=slot_identities,
            base_fingerprint=base_fingerprint,
            base_revision=base_revision,
        )
        with self._managed_writer_lock:
            if self._managed_writer_stopping:
                return False
            thread = self._managed_writer_thread
            if thread is None or not thread.is_alive():
                thread = threading.Thread(
                    target=self._drain_managed_mutations,
                    name="managed-chain-writer", daemon=True)
                self._managed_writer_thread = thread
                thread.start()
            self._managed_writer_queue.put(job)
        return True

    def _remember_managed_write_target(self, fingerprint: str | None,
                                       revision: int | None,
                                       before_target: int | None,
                                       after_target: int | None,
                                       before_paths: tuple[str | None, ...],
                                       after_paths: tuple[str | None, ...],
                                       before_identities: tuple[int, ...],
                                       after_identities: tuple[int, ...]) -> None:
        """Expose a just-written target before runtime acknowledgement."""
        if (not isinstance(fingerprint, str) or not fingerprint
                or isinstance(revision, bool)
                or not isinstance(revision, int)):
            return
        with self._managed_write_targets_lock:
            latest = self._managed_slot_projection
            if (latest is not None
                    and (latest[2].after_paths != before_paths
                         or latest[2].after_identities != before_identities)):
                # The file/runtime chain was replaced outside the known
                # managed sequence. Old process-local identities cannot be
                # safely projected through that boundary.
                self._managed_write_targets.clear()
            transition = _ManagedWriteTarget(
                before_target, after_target,
                before_paths, after_paths,
                before_identities, after_identities)
            self._managed_write_targets[(fingerprint, revision)] = transition
            self._managed_slot_projection = (
                fingerprint, revision,
                transition)
            # Keep enough recent transitions for queued jobs and an early
            # poll to resolve either side of the runtime-ack race.
            if len(self._managed_write_targets) > 32:
                for key in list(self._managed_write_targets)[:-16]:
                    self._managed_write_targets.pop(key, None)

    def _pending_managed_write_target(
            self, fingerprint: str | None,
            revision: int | None) -> tuple[bool, _ManagedWriteTarget | None]:
        """Return the target transition for one exact managed file write."""
        if (not isinstance(fingerprint, str) or not fingerprint
                or isinstance(revision, bool)
                or not isinstance(revision, int)):
            return False, None
        with self._managed_write_targets_lock:
            key = (fingerprint, revision)
            if key not in self._managed_write_targets:
                return False, None
            return True, self._managed_write_targets[key]

    def _projected_slot_identities(
            self, fingerprint: str | None, revision: int | None,
            paths: tuple[str | None, ...]
    ) -> tuple[int, ...] | None:
        with self._managed_write_targets_lock:
            projection = self._managed_slot_projection
        if projection is None or projection[:2] != (fingerprint, revision):
            return None
        transition = projection[2]
        if transition.after_paths != paths:
            return None
        return transition.after_identities

    def _managed_state_from_snapshot(
            self, chain: dict, fingerprint: str | None,
            *, slot_paths: tuple[str | None, ...] | None = None,
            slot_identities: tuple[int, ...] | None = None,
    ) -> ChainState:
        """Rebuild a managed state while retaining known Slot identities."""
        paths = tuple(
            slot.get("path") for slot in chain.get("slots", ()))
        projected = self._projected_slot_identities(
            fingerprint, chain.get("revision"), paths)
        if projected is not None:
            return ChainState.from_chain_with_identities(chain, projected)
        if slot_identities is not None:
            if slot_paths != paths:
                raise ChainStateError(
                    "queued Slot identity is no longer available")
            return ChainState.from_chain_with_identities(chain, slot_identities)
        return ChainState(chain)

    def _validate_managed_job_snapshot(
            self, job: _ManagedChainJob, chain: dict,
            fingerprint: str | None) -> None:
        """Reject a queued Slot job that crossed an unknown chain boundary."""
        if job.slot_identities is None:
            return
        revision = chain.get("revision")
        same_base = (
            job.base_revision is not None
            and revision == job.base_revision
            and ((job.base_fingerprint is None and fingerprint is None)
                 or (job.base_fingerprint is not None
                     and fingerprint == job.base_fingerprint))
        )
        if same_base:
            return
        known, transition = self._pending_managed_write_target(
            fingerprint, revision)
        if (not known or transition is None
                or transition.before_paths != tuple(job.slot_paths or ())
                or transition.before_identities != job.slot_identities):
            raise ChainStateError(
                "queued Slot mutation discarded: chain was replaced")

    def _discard_managed_write_target(self, fingerprint: str | None,
                                      revision: int | None) -> None:
        """Remove a projection whose file write was rolled back."""
        if (not isinstance(fingerprint, str) or not fingerprint
                or isinstance(revision, bool)
                or not isinstance(revision, int)):
            return
        with self._managed_write_targets_lock:
            self._managed_write_targets.pop((fingerprint, revision), None)
            if (self._managed_slot_projection is not None
                    and self._managed_slot_projection[:2]
                    == (fingerprint, revision)):
                self._managed_slot_projection = None

    def _remember_managed_rollback_target(
            self, forward_identity: tuple[str, int] | None) -> None:
        """Publish the inverse identity transition after a failed commit."""
        if forward_identity is None:
            return
        with self._managed_write_targets_lock:
            forward = self._managed_write_targets.get(forward_identity)
        if forward is None:
            return
        try:
            chain, fingerprint = live.read_chain_snapshot()
        except Exception:
            return
        paths = tuple(
            slot.get("path") for slot in chain.get("slots", ()))
        if paths != forward.before_paths:
            # The file was changed again or rollback did not reach the base
            # chain. Do not attach process-local identities to that state.
            return
        revision = chain.get("revision")
        self._remember_managed_write_target(
            fingerprint, revision,
            forward.after_target_identity,
            forward.before_target_identity,
            forward.after_paths,
            forward.before_paths,
            forward.after_identities,
            forward.before_identities)

    def _drain_managed_mutations(self) -> None:
        """Serialize managed file/runtime transactions on a background thread."""
        while True:
            job = self._managed_writer_queue.get()
            if job is None:
                return
            with self._managed_writer_lock:
                if self._managed_writer_stopping:
                    continue
            write_identity: tuple[str, int] | None = None
            before_identities: tuple[int, ...] = ()
            before_paths: tuple[str | None, ...] = ()
            before_target_identity: int | None = None

            def observe_file_write(fingerprint: str | None,
                                   revision: int | None,
                                   target_index: int | None,
                                   target_identity: int | None,
                                   slot_identities: tuple[int, ...],
                                   slot_paths: tuple[str | None, ...]) -> None:
                nonlocal write_identity
                if (isinstance(fingerprint, str)
                        and isinstance(revision, int)
                        and not isinstance(revision, bool)):
                    write_identity = (fingerprint, revision)
                self._remember_managed_write_target(
                    fingerprint, revision, before_target_identity,
                    target_identity, before_paths, slot_paths,
                    before_identities, slot_identities)

            try:
                if not self._managed_engine_active():
                    raise RuntimeError("managed engine is no longer active")
                # Read the latest file for every job. This preserves ordered
                # clicks even while the UI callback for the previous job is
                # waiting in Textual's message queue.
                with self._managed_transaction_lock:
                    base_chain, base_fingerprint = live.read_chain_snapshot()
                    self._validate_managed_job_snapshot(
                        job, base_chain, base_fingerprint)
                    state = self._managed_state_from_snapshot(
                        base_chain, base_fingerprint,
                        slot_paths=job.slot_paths,
                        slot_identities=job.slot_identities)
                    before_identities = state.slot_identities
                    before_paths = state.slot_paths
                    before_target_identity = (
                        job.target_identity
                        if job.target_identity is not None
                        else state.target_identity)
                    if job.target_identity is not None:
                        try:
                            state.focus_identity(job.target_identity)
                        except ChainStateError:
                            pass
                    elif (job.target_index is not None
                          and job.target_index < state.slot_count):
                        state.focus_slot(job.target_index)
                    if job.slot_identities is not None:
                        state.set_index_aliases(job.slot_identities)

                    def transactional_mutation(draft: ChainState):
                        result = job.mutation(draft)
                        if result is False:
                            raise _ManagedNoOp
                        return result

                    persisted = state.commit(
                        _ManagedChainAdapter(
                            self, expected_chain=base_chain),
                        transactional_mutation,
                        on_file_written=observe_file_write)
                    target_index = state.target_index
                    write_fingerprint = state.managed_fingerprint
                    target_identity = state.target_identity
            except _ManagedNoOp:
                if write_identity is not None:
                    self._discard_managed_write_target(*write_identity)
                self._dispatch_managed_writer_callback(
                    self._managed_job_failed, job,
                    job.failure_note or job.note)
            except Exception as exc:
                if write_identity is not None:
                    self._remember_managed_rollback_target(write_identity)
                    self._discard_managed_write_target(*write_identity)
                self._dispatch_managed_writer_callback(
                    self._managed_job_failed, job,
                    f"Chain unchanged: {exc}")
            else:
                self._dispatch_managed_writer_callback(
                    self._managed_job_succeeded, job, persisted, target_index,
                    write_fingerprint, target_identity, state.slot_identities)

    def _dispatch_managed_writer_callback(self, callback, *args) -> None:
        loop = getattr(self, "_loop", None)
        if loop is None or loop.is_closed() or not loop.is_running():
            return
        completed = threading.Event()

        def invoke() -> None:
            try:
                with self._context():
                    callback(*args)
            except Exception:
                # The app may unmount between dispatch and execution. The
                # file transaction is already complete, so no UI callback is
                # safe or useful after that boundary.
                pass
            finally:
                completed.set()

        try:
            # ``call_from_thread`` creates its coroutine before it notices a
            # closing loop. Queue directly so teardown cannot leak an
            # unawaited coroutine. Keep the normal writer queue serialized by
            # waiting for the UI callback; a closing loop is bounded below.
            loop.call_soon_threadsafe(invoke)
        except RuntimeError:
            return
        completed.wait(timeout=1.0)

    def _managed_job_succeeded(self, job: _ManagedChainJob,
                               persisted: dict,
                               target_index: int | None,
                               write_fingerprint: str | None = None,
                               target_identity: int | None = None,
                               slot_identities: tuple[int, ...] | None = None
                               ) -> None:
        if not getattr(self, "is_mounted", False):
            return
        try:
            panel = self.query_one(ChainPanel)
            owns_target = panel.state.target_identity == job.target_identity
            should_refocus = (
                owns_target
                and job.target_identity is not None
                and self._focused_slot_identity(panel) == job.target_identity)
            # A poll may have observed the file before this callback. Applying
            # the candidate first keeps the callback correct in both orders.
            panel.state.apply_candidate(persisted, identities=slot_identities)
            if owns_target:
                # DetailPane focus is intentionally allowed to remain external,
                # but the transaction still owns the canonical target when the
                # user has not selected another Slot during the wait. Apply the
                # worker's post-mutation target independently of keyboard focus.
                try:
                    if target_identity is None:
                        panel.state.clear_target()
                    else:
                        panel.state.focus_identity(target_identity)
                except ChainStateError:
                    pass
            if should_refocus and target_identity is not None:
                try:
                    panel.state.focus_identity(target_identity)
                except ChainStateError:
                    should_refocus = False
            committed = self._publish_chain_write(persisted)
            # A path/order-changing commit can make adopt_managed_chain fall
            # through to conservative reconciliation. Reapply only the
            # transaction's target when the user did not focus another Slot.
            if should_refocus and target_identity is not None:
                try:
                    panel.state.focus_identity(target_identity)
                except ChainStateError:
                    should_refocus = False
            if job.on_success is not None:
                job.on_success(
                    committed, target_index if should_refocus else None)
            if job.note:
                self.notify(job.note)
        except Exception as exc:
            self.notify(f"Chain changed but UI refresh failed: {exc}",
                        severity="error")

    def _managed_job_failed(self, job: _ManagedChainJob, message: str) -> None:
        if not getattr(self, "is_mounted", False):
            return
        try:
            current = live.read_chain()
            if current:
                self._publish_chain_write(current)
        except Exception:
            pass
        if not message:
            return
        self.notify(message, severity="warning" if message == job.failure_note
                    else "error")

    def _start_engine(self) -> None:
        """Spawn realtime_cli as a child; it hot-swaps via live_chain.json and feeds
        level.json back. Killed on TUI exit. Use --no-engine if running it externally."""
        root = Path(__file__).resolve().parent.parent
        cmd = [str(root / "bin" / "realtime_cli"),
               "--live", str(live.CHAIN_FILE), "--level-file", str(live.LEVEL_FILE),
               "--root", str(root), "--managed",
               "--control-file", str(live.CONTROL_FILE)]   # REQ-035 portable：chain 内相对路径按根解析
        if self._dev_in:
            cmd += ["--in", self._dev_in]
        if self._dev_out:
            cmd += ["--out", self._dev_out]
        if self._in_ch:
            cmd += ["--ch", str(self._in_ch)]
        if self._block:
            cmd += ["--block", str(self._block)]
        if self._sr:
            cmd += ["--sr", str(self._sr)]
        try:
            # Control/reply files are process-local session state. Remove only
            # these exact sidecars so a restarted engine cannot consume a
            # request from the previous process or inherit its ready marker.
            for sidecar in (live.CONTROL_FILE, live.CONTROL_REPLY_FILE,
                            live.LEVEL_FILE):
                try:
                    sidecar.unlink()
                except FileNotFoundError:
                    pass
            log = open(root / "data" / "engine.log", "a", encoding="utf-8")
            self._engine_log_handle = log
            self._engine = subprocess.Popen(cmd, stdout=log, stderr=log,
                                            stdin=subprocess.DEVNULL)
            self._engine_started_at = time.monotonic()
            return True
        except OSError as e:
            if self._engine_log_handle is not None:
                self._engine_log_handle.close()
                self._engine_log_handle = None
            self.notify(f"(engine spawn failed: {e})", severity="error")
            return False

    def on_unmount(self) -> None:
        self._device_request_generation += 1
        with self._param_hold_lock:
            # In-flight file/runtime work cannot be force-killed safely, but
            # invalidating the generation prevents late callbacks from
            # touching widgets after the app has been unmounted.
            self._param_hold_generation += 1
            self._param_hold_pending = None
            self._param_hold_slot_identity = None
            self._param_hold_slot_paths = None
            self._param_hold_slot_identities = None
        with self._managed_writer_lock:
            self._managed_writer_stopping = True
            while True:
                try:
                    self._managed_writer_queue.get_nowait()
                except Empty:
                    break
            self._managed_writer_queue.put(None)
        with self._managed_write_targets_lock:
            self._managed_write_targets.clear()
            self._managed_slot_projection = None
        self._calibration_generation += 1
        if self._header_status_timer is not None:
            self._header_status_timer.stop()
            self._header_status_timer = None
        self._kill_engine()
        if self._engine_lock_handle is not None:
            live.release_engine_lock()
            self._engine_lock_handle = None

    def _device_request_alive(self, generation: int) -> bool:
        return (generation == self._device_request_generation
                and bool(getattr(self, "is_mounted", False)))

    async def _load_devices(self, generation: int | None = None) -> None:
        """Enumerate audio interfaces via `realtime_cli --list` and fill the DeviceBar.

        Retries once after 12s (the engine may hold the audio device); on failure
        the pickers stay enabled with the CLI defaults so audio is still usable.
        """
        if generation is None:
            self._device_request_generation += 1
            generation = self._device_request_generation
        root = Path(__file__).resolve().parent.parent
        for attempt in range(2):
            try:
                out = await asyncio.to_thread(
                    subprocess.run,
                    [str(root / "bin" / "realtime_cli"), "--list"],
                    capture_output=True, text=True, timeout=12)
                ins, outs = _parse_devices(out.stdout or "")
                if (ins or outs) and self._device_request_alive(generation):
                    self._audio_ins, self._audio_outs = ins, outs
                    for bar in self.query(DeviceBar):
                        bar.set_devices(ins, outs, self._dev_in or "", self._dev_out or "")
                    return
            except Exception:
                pass
            await asyncio.sleep(1)
        if self._device_request_alive(generation):
            self.notify("Device list unavailable — keep IN/OUT defaults", severity="warning")

    def _kill_engine(self) -> None:
        if self._engine:
            self._engine.terminate()
            try:
                self._engine.wait(timeout=3)
            except Exception:
                self._engine.kill()
            self._engine = None
        if self._engine_log_handle is not None:
            self._engine_log_handle.close()
            self._engine_log_handle = None

    def _audio_levels(self) -> tuple[float, float, str, float]:
        """Read telemetry only while this app's managed engine is alive.

        ``level.json`` is a shared sidecar and can outlive a crashed engine.
        Treating its last sample as current makes the UI display microphone
        levels and playback state from a previous session, even though no
        audio callback is running.
        """
        if (getattr(self, "_spawn_engine", False)
                and not self._managed_engine_active()):
            return 0.0, 0.0, live.PLAY_STOPPED, 0.0
        return live.read_levels()

    def _sample_ui_cpu(self) -> float | None:
        """Return this Python TUI process's CPU use as a one-core percent."""
        now = time.monotonic()
        cpu_time = time.process_time()
        previous_at = self._last_ui_cpu_sample_at
        previous_cpu = self._last_ui_cpu_sample_time
        if previous_at is None or previous_cpu is None:
            self._last_ui_cpu_sample_at = now
            self._last_ui_cpu_sample_time = cpu_time
            return self._ui_cpu_percent
        elapsed = now - previous_at
        if elapsed < self.UI_CPU_SAMPLE_INTERVAL_S:
            return self._ui_cpu_percent
        self._last_ui_cpu_sample_at = now
        self._last_ui_cpu_sample_time = cpu_time
        self._ui_cpu_percent = max(
            0.0, (cpu_time - previous_cpu) / elapsed * 100.0)
        return self._ui_cpu_percent

    def _sample_engine_cpu(self) -> float | None:
        """Read CPU use for the managed realtime engine, if it is alive."""
        engine = getattr(self, "_engine", None)
        if engine is None or engine.poll() is not None:
            self._last_engine_cpu_pid = None
            self._last_engine_cpu_sample_at = None
            self._engine_cpu_percent = 0.0
            return self._engine_cpu_percent

        pid = engine.pid
        now = time.monotonic()
        if pid != self._last_engine_cpu_pid:
            self._last_engine_cpu_pid = pid
            self._last_engine_cpu_sample_at = None
            self._engine_cpu_percent = None
        previous_at = self._last_engine_cpu_sample_at
        if (previous_at is not None
                and now - previous_at < self.UI_CPU_SAMPLE_INTERVAL_S):
            return self._engine_cpu_percent
        self._last_engine_cpu_sample_at = now
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "%cpu="],
                capture_output=True, text=True, check=False, timeout=0.2)
            value = float(result.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return self._engine_cpu_percent
        self._engine_cpu_percent = max(0.0, value)
        return self._engine_cpu_percent

    def _sample_total_cpu(self) -> float | None:
        """Return TUI plus its managed realtime engine CPU as one-core percent."""
        ui_cpu = self._sample_ui_cpu()
        engine_cpu = self._sample_engine_cpu()
        if ui_cpu is None:
            return self._total_cpu_percent
        managed_engine = (
            getattr(self, "_spawn_engine", False)
            and getattr(self, "_engine", None) is not None
            and self._engine.poll() is None)
        if managed_engine and engine_cpu is None:
            return self._total_cpu_percent
        self._total_cpu_percent = max(0.0, ui_cpu + (engine_cpu or 0.0))
        return self._total_cpu_percent

    def on_device_changed(self, event: DeviceChanged) -> None:
        """Interface changes restart only the isolated realtime engine."""
        if event.kind == "mute":
            # chain-level toggle, works regardless of engine ownership
            cfg = live.read_chain()
            cfg["mute"] = not bool(cfg.get("mute", False))
            if cfg["mute"]:
                note = "MUTED (click again to restore)"
            else:
                note = f"Unmuted · master {signed_fixed(cfg.get('master', 1.0))}"
            cfg = self._commit_external_chain(cfg)
            if cfg is None:
                return
            self.query_one(InterfaceBar).set_muted(bool(cfg.get("mute", False)))
            self._publish_mutation(
                "mute", ("chain:mute",), cfg.get("revision"))
            self.notify(note)
            return
        if event.kind == "buffer":
            self._block = int(event.name)
        elif event.kind == "sr":
            self._sr = int(event.name)
        elif event.kind == "in":
            self._dev_in = event.name
        elif event.kind == "out":
            self._dev_out = event.name
        if not self._spawn_engine:
            self.notify("Audio setting recorded for this session; external engine not restarted")
            return
        self._kill_engine()
        self._engine_restart_failures = 0
        self._engine_retry_at = 0.0
        self._engine_restart_exhausted = False
        self._engine_failure_notified = False
        if not self._start_engine():
            self._record_engine_failure(None, 0.0)
        self.notify(f"Engine restarted · IN {self._dev_in or 'default'} · "
                    f"OUT {self._dev_out or 'default'} · block {self._block} · "
                    f"SR {self._sr / 1000:g} kHz")

    def on_interface_bar_settings_requested(self, _event: InterfaceBar.SettingsRequested) -> None:
        self.action_open_audio_settings()

    def on_interface_bar_quit_requested(self, _event: InterfaceBar.QuitRequested) -> None:
        """QUIT button: open the second-stage confirmation before exiting."""
        self.push_screen(QuitConfirmModal())

    def action_open_audio_settings(self) -> None:
        self.push_screen(AudioSettingsScreen(
            self._audio_ins, self._audio_outs, self._dev_in, self._dev_out,
            self._block, self._sr))

    def on_pack_install_screen_installed(self, event: PackInstallScreen.Installed) -> None:
        """Pack installed: preserve the existing detail flow and reconcile once."""
        # Canonical v0.2 opens PackInstallScreen from Remote Pack; dismissing
        # it must leave that Pack view in place so the coordinator can update
        # the installed marker without changing the user's context. Legacy
        # model/ir chains still use the old picker flow.
        if self.query_one(ChainPanel)._legacy_mode:
            self.on_tone_selected(ToneSelected(event.tone_id))

    def on_local_uninstall_screen_uninstalled(
        self, event: LocalUninstallScreen.Uninstalled) -> None:
        tone_ids = tuple(event.tone_ids)
        self.query_one(LibraryPanel).remove_local_selection(tone_ids)
        self.notify(f"Uninstalled {event.count} file(s) · metadata retained")

    def refresh_from_files(self) -> None:
        """Poll meters and external state without repainting unchanged views."""
        # The interval may fire once while Textual is tearing down a test or
        # closing the app. Ignore that final tick instead of querying detached
        # widgets.
        try:
            meter = self.query_one(MeterBar)
            chain = self.query_one(ChainPanel)
            library_panel = self.query_one(LibraryPanel)
            preset_panel = self.query_one(PresetPanel)
            detail = self.query_one(DetailPane)
        except NoMatches:
            return
        self._ensure_engine()   # restart after crash / start after picking a tone
        if (getattr(self, "_spawn_engine", False)
                and not self._managed_engine_active()):
            in_lvl, out_lvl, play_state, play_pos = (
                0.0, 0.0, live.PLAY_STOPPED, 0.0)
            runtime_revision, runtime_status = live.read_runtime_status()
        else:
            (in_lvl, out_lvl, play_state, play_pos), (
                runtime_revision, runtime_status) = live.read_level_snapshot()
        meter.levels = (in_lvl, out_lvl)
        runtime_report = (runtime_revision, runtime_status)
        if (runtime_status == "rejected"
                and runtime_report != self._last_runtime_status_report):
            self.notify(
                "Runtime rejected the chain; previous applied revision kept",
                severity="error")
        self._last_runtime_status_report = runtime_report
        chain_token = live.chain_change_token()
        if (chain_token == self._last_refresh_chain_token
                and self._last_refresh_chain_config is not None):
            cfg = self._last_refresh_chain_config
            chain_changed = False
        else:
            cfg = live.read_chain()
            self._last_refresh_chain_token = chain_token
            self._last_refresh_chain_config = cfg
            try:
                chain_revision = chain_fingerprint(cfg)
            except (TypeError, ValueError):
                chain_revision = None
            chain_changed = chain_revision != self._last_refresh_chain_fingerprint
            if chain_changed:
                self._clear_external_bypass_candidates(cfg)
                chain.chain = cfg
                self._last_refresh_chain_fingerprint = chain_revision
        chain_error = live.consume_chain_error()
        if chain_error:
            self.notify(
                f"Chain update rejected; keeping last valid chain: {chain_error}",
                severity="error")
        # 用户刚操作播放控制（写入 chain 后引擎处理有延迟）：窗口内不用
        # level.json 的旧 play_state 覆盖，避免 PLAY/PAUSE 来回跳变
        if time.monotonic() - self._playback_op_ts > 0.4:
            chain.update_playback(play_state, play_pos)  # 引擎实际播放状态（0.1s 回传）
        interface = self.query_one(InterfaceBar)
        interface.set_muted(bool(cfg.get("mute", False)))
        interface.set_runtime_status(cfg.get("revision"), runtime_revision,
                                     runtime_status)
        if chain_changed:
            detail.refresh_pack_active(cfg)  # pack 视图的 ▶ 标记跟随外部链变更
        library_panel.check_active_tab()
        # Keep the focused local model marker responsive without re-running the
        # database-backed catalog refresh on every meter tick.
        library_panel.sync_active_slot()
        self._refresh_catalog_panels(library_panel, preset_panel)
        interface.set_cpu_usage(self._sample_total_cpu())

    def _refresh_catalog_panels(self, library_panel, preset_panel, *,
                                now: float | None = None) -> None:
        """Refresh DB-backed catalogs at a lower-cost background cadence."""
        now = time.monotonic() if now is None else now
        previous = self._last_catalog_refresh_at
        if (previous is not None
                and now - previous < self.CATALOG_REFRESH_INTERVAL_S):
            return
        self._last_catalog_refresh_at = now
        library_panel.refresh_rows()
        # Incremental: external fingerprint changes (chain mtime, active preset)
        # must not clear+rebuild the table, which resets the scroll and leaves
        # a window where a pending mutation reconcile captures the reset
        # viewport instead of the user's actual position.
        preset_panel.refresh_presets(incremental=True)

    def _clear_external_bypass_candidates(self, cfg: dict) -> None:
        """Drop process-local bypass recovery after an external chain edit.

        ``null`` is intentionally the persisted representation for both an
        empty slot and a bypassed slot, so the value alone cannot identify who
        wrote it.  ``tui.live`` records fingerprints for this process's own
        atomic writes; a different fingerprint means the candidates are no
        longer trustworthy and must not turn an external empty slot into
        BYPASS on the next refresh.
        """
        if not (self._amp_model_backup or self._ir_backup):
            return
        current = live.chain_file_fingerprint()
        expected = live.last_chain_write_fingerprint()
        if expected is None or current == expected:
            return
        self._amp_model_backup = None
        self._ir_backup = None

    def _publish_chain_write(self, cfg: dict) -> dict:
        """Publish a non-structural TUI chain write without losing Slot UI state."""
        panel = self.query_one(ChainPanel)
        persisted = live.read_chain() or cfg
        managed_fingerprint = live.last_chain_write_fingerprint()
        managed_revision = persisted.get("revision")
        known_write, transition = self._pending_managed_write_target(
            managed_fingerprint, managed_revision)
        identity_transition = bool(
            known_write
            and transition is not None
            and panel.state.slot_paths == transition.before_paths
            and panel.state.slot_identities == transition.before_identities)
        if identity_transition and transition is not None:
            # The callback may race the file poll. Publish the exact known
            # identity transition first; otherwise a rollback/swap that has
            # the same paths could be mistaken for an ordinary chain write.
            try:
                panel.state.mark_managed_write(
                    managed_fingerprint, managed_revision)
                panel.state.reconcile(
                    persisted,
                    fingerprint=managed_fingerprint,
                    revision=managed_revision,
                    preserve_target_index=None,
                    preserve_target_identity=transition.after_target_identity,
                    preserve_slot_identities=transition.after_identities,
                )
            except ChainStateError:
                panel.state.reconcile(
                    persisted,
                    fingerprint=managed_fingerprint,
                    revision=managed_revision,
                )
        else:
            try:
                # This write is already reflected in the process-local Slot
                # objects. Adopt only its chain-level fields so a writer
                # without a byte fingerprint cannot turn a just-created
                # BYPASS into EMPTY. A path/order change (preset load, undo,
                # redo) falls through to conservative reconciliation.
                panel.state.adopt_managed_chain(persisted)
            except ChainStateError:
                panel.state.reconcile(
                    persisted,
                    fingerprint=managed_fingerprint,
                    revision=managed_revision,
                )
        try:
            panel.state.mark_managed_write(
                managed_fingerprint, managed_revision,
            )
        except ChainStateError:
            pass
        try:
            panel._observed_chain_fingerprint = chain_fingerprint(persisted)
        except (TypeError, ValueError):
            panel._observed_chain_fingerprint = None
        panel.chain = persisted
        panel._refresh_dynamic_slots()
        return persisted

    def _commit_external_chain(self, cfg: dict) -> dict | None:
        """Write a chain through the external-engine file boundary once.

        A managed engine gets the full prepare/write/runtime acknowledgement
        path. ``--no-engine`` and externally owned engines intentionally retain
        the file-only contract from the external-engine part of the spec.
        """
        try:
            panel = self.query_one(ChainPanel)
        except NoMatches:
            panel = None
        if (panel is not None and not panel._legacy_mode
                and self._managed_engine_active()):
            try:
                with self._managed_transaction_lock:
                    adapter = _ManagedChainAdapter(
                        self, expected_chain=panel.state.to_chain())
                    committed = panel.state.commit(
                        adapter,
                        lambda draft: draft.apply_candidate(cfg),
                    )
                    return self._publish_chain_write(committed)
            except Exception as exc:
                self.notify(f"Chain unchanged: {exc}", severity="error")
                return None
        base_chain, expected_fingerprint = live.read_chain_snapshot()
        expected_revision = base_chain.get("revision", 0)
        try:
            live.write_chain(
                cfg,
                expected_fingerprint=expected_fingerprint,
                expected_revision=expected_revision,
            )
            return self._publish_chain_write(cfg)
        except Exception as exc:
            self.notify(f"Chain unchanged: {exc}", severity="error")
            return None

    def _bump(self, key: str, delta: float) -> None:
        ranges = {
            "gain": (0.0, 10.0),
            "master": (0.0, 10.0),
            "quality": (0.0, 1.0),
        }
        if key not in ranges:
            self.notify(f"Unknown chain parameter: {key}", severity="warning")
            return
        if self._managed_engine_active():
            def mutation(state: ChainState):
                cfg = state.to_chain()
                previous = float(cfg.get(
                    key, live.CHAIN_PARAMETER_DEFAULTS[key]))
                lo, hi = ranges[key]
                value = round(max(lo, min(hi, previous + delta)), 2)
                if value == previous:
                    return False
                cfg[key] = value
                return state.apply_candidate(cfg)

            self._enqueue_managed_mutation(
                mutation, "",
                on_success=lambda persisted, _target: self._publish_mutation(
                    "chain-param", (f"chain:{key}",),
                    persisted.get("revision")),
                target_index=self._current_chain_target_index(),
            )
            return
        cfg = live.read_chain()
        lo, hi = ranges[key]
        previous = float(cfg.get(key, live.CHAIN_PARAMETER_DEFAULTS[key]))
        value = previous + delta
        cfg[key] = round(max(lo, min(hi, value)), 2)
        if cfg[key] == previous:
            return
        cfg = self._commit_external_chain(cfg)
        if cfg is None:
            return
        self._publish_mutation(
            "chain-param", (f"chain:{key}",), cfg.get("revision"))

    def _set_chain_param(self, key: str, value: float) -> None:
        """手动填写参数（REQ-021）：绝对设置，走与 g·G 同一条写链路径。

        quality 仍按 0..1 钳制（SlimmableContainer 子模型尺寸）。
        """
        lo, hi = (0.0, 1.0) if key == "quality" else (0.0, 10.0)
        value = max(lo, min(hi, value))
        if self._managed_engine_active():
            def mutation(state: ChainState):
                cfg = state.to_chain()
                previous = float(cfg.get(
                    key, live.CHAIN_PARAMETER_DEFAULTS[key]))
                if round(value, 2) == previous:
                    return False
                cfg[key] = round(value, 2)
                return state.apply_candidate(cfg)

            self._enqueue_managed_mutation(
                mutation, "",
                on_success=lambda persisted, _target: self._publish_mutation(
                    "chain-param", (f"chain:{key}",),
                    persisted.get("revision")),
                target_index=self._current_chain_target_index(),
            )
            return
        cfg = live.read_chain()
        previous = float(cfg.get(key, live.CHAIN_PARAMETER_DEFAULTS[key]))
        value = round(value, 2)
        if value == previous:
            return
        cfg[key] = value
        cfg = self._commit_external_chain(cfg)
        if cfg is None:
            return
        self._publish_mutation(
            "chain-param", (f"chain:{key}",), cfg.get("revision"))

    def begin_chain_param_hold(
            self, key: str, *, slot_index: int | None = None,
            slot_identity: int | None = None
    ) -> tuple[int, float]:
        """Start a global or Slot parameter hold and return its value."""
        if slot_index is None and key not in live.CHAIN_PARAMETER_DEFAULTS:
            raise ValueError(f"unknown chain parameter: {key}")
        if slot_index is not None and key not in {
                "input_gain_db", "output_gain_db"}:
            raise ValueError(f"unknown Slot parameter: {key}")
        with self._param_hold_lock:
            self._param_hold_generation += 1
            generation = self._param_hold_generation
            self._param_hold_pending = None
            self._param_hold_slot_identity = slot_identity
            self._param_hold_slot_paths = None
            self._param_hold_slot_identities = None
        cfg = live.read_chain()
        if slot_index is None:
            value = float(cfg.get(key, live.CHAIN_PARAMETER_DEFAULTS[key]))
        else:
            panel = None
            try:
                panel = self.query_one(ChainPanel)
            except NoMatches:
                pass
            ui_state = getattr(panel, "state", None)
            slot_paths = getattr(ui_state, "slot_paths", None)
            slot_identities = getattr(ui_state, "slot_identities", None)
            with self._param_hold_lock:
                self._param_hold_slot_paths = slot_paths
                self._param_hold_slot_identities = slot_identities
            state = self._managed_state_from_snapshot(
                cfg, live.chain_file_fingerprint(),
                slot_paths=slot_paths, slot_identities=slot_identities)
            if slot_identity is not None:
                resolved = state.index_for_identity(slot_identity)
                if resolved is None:
                    raise ChainStateError("slot identity is no longer available")
                value = float(getattr(state.slot(resolved), key))
            else:
                if slot_identities is not None:
                    state.set_index_aliases(slot_identities)
                value = float(getattr(state.slot(slot_index), key))
        return generation, value

    def queue_chain_param_hold(self, generation: int, key: str,
                               value: float, *, force: bool = False,
                               slot_index: int | None = None,
                               slot_identity: int | None = None) -> None:
        """Coalesce a hold value and keep at most one commit worker active."""
        if slot_index is None and key not in live.CHAIN_PARAMETER_DEFAULTS:
            return
        if slot_index is not None and key not in {
                "input_gain_db", "output_gain_db"}:
            return
        lo, hi = ((SLOT_GAIN_MIN_DB, SLOT_GAIN_MAX_DB)
                  if slot_index is not None else
                  ((0.0, 1.0) if key == "quality" else (0.0, 10.0)))
        value = round(max(lo, min(hi, float(value))), 2)
        start_worker = False
        with self._param_hold_lock:
            if generation != self._param_hold_generation:
                return
            slot_identity = (slot_identity
                             if slot_identity is not None
                             else self._param_hold_slot_identity)
            slot_paths = self._param_hold_slot_paths
            slot_identities = self._param_hold_slot_identities
            # generation, slot index, key, value, force, final
            self._param_hold_pending = (
                generation, slot_index, slot_identity, key, value, force, False,
                slot_paths, slot_identities)
            if not self._param_hold_worker_active:
                self._param_hold_worker_active = True
                start_worker = True
        if start_worker:
            threading.Thread(
                target=self._drain_chain_param_hold,
                name="chain-param-hold", daemon=True).start()

    def end_chain_param_hold(self, generation: int, key: str,
                             value: float, *, slot_index: int | None = None,
                             slot_identity: int | None = None
                             ) -> None:
        """Queue the final hold value without waiting on the UI thread."""
        if slot_index is None and key not in live.CHAIN_PARAMETER_DEFAULTS:
            return
        if slot_index is not None and key not in {
                "input_gain_db", "output_gain_db"}:
            return
        lo, hi = ((SLOT_GAIN_MIN_DB, SLOT_GAIN_MAX_DB)
                  if slot_index is not None else
                  ((0.0, 1.0) if key == "quality" else (0.0, 10.0)))
        value = round(max(lo, min(hi, float(value))), 2)
        start_worker = False
        with self._param_hold_lock:
            if generation != self._param_hold_generation:
                return
            slot_identity = (slot_identity
                             if slot_identity is not None
                             else self._param_hold_slot_identity)
            slot_paths = self._param_hold_slot_paths
            slot_identities = self._param_hold_slot_identities
            self._param_hold_pending = (
                generation, slot_index, slot_identity, key, value, True, True,
                slot_paths, slot_identities)
            if not self._param_hold_worker_active:
                self._param_hold_worker_active = True
                start_worker = True
        if start_worker:
            threading.Thread(
                target=self._drain_chain_param_hold,
                name="chain-param-hold", daemon=True).start()

    def _drain_chain_param_hold(self) -> None:
        """Serialize coalesced hold commits off the Textual event loop."""
        with self._param_hold_lock:
            last_commit_at = self._param_hold_last_commit_at
        while True:
            with self._param_hold_lock:
                pending = self._param_hold_pending
                if pending is None:
                    self._param_hold_worker_active = False
                    return
                self._param_hold_pending = None

            (generation, slot_index, slot_identity, key, value,
             force, final, slot_paths, slot_identities) = pending
            if not force and last_commit_at:
                remaining = (
                    self.PARAM_HOLD_COMMIT_INTERVAL
                    - (time.monotonic() - last_commit_at))
                if remaining > 0:
                    time.sleep(remaining)
                # Prefer the newest value that arrived during the throttle
                # window. The final flag must travel with that newest value.
                with self._param_hold_lock:
                    newer = self._param_hold_pending
                    if newer is not None:
                        self._param_hold_pending = None
                        (generation, slot_index, slot_identity, key, value,
                         force, final, slot_paths, slot_identities) = newer

            try:
                committed = self._commit_chain_param_hold(
                    key, value, slot_index=slot_index,
                    slot_identity=slot_identity,
                    slot_paths=slot_paths,
                    slot_identities=slot_identities)
            except Exception as exc:
                try:
                    self.call_from_thread(
                        self._param_hold_failed, generation, slot_index,
                        slot_identity, str(exc))
                except Exception:
                    pass
                with self._param_hold_lock:
                    if generation == self._param_hold_generation:
                        self._param_hold_pending = None
                    self._param_hold_worker_active = False
                return

            last_commit_at = time.monotonic()
            with self._param_hold_lock:
                self._param_hold_last_commit_at = last_commit_at
            try:
                self.call_from_thread(
                    self._param_hold_committed,
                    generation, slot_index, slot_identity, key, value, final,
                    committed)
            except Exception:
                pass

    def _commit_chain_param_hold(
            self, key: str, value: float, *, slot_index: int | None = None,
            slot_identity: int | None = None,
            slot_paths: tuple[str | None, ...] | None = None,
            slot_identities: tuple[int, ...] | None = None,
    ) -> _ManagedParamCommit | None:
        """Commit one absolute global or Slot parameter value."""
        with self._managed_transaction_lock:
            base_chain, base_fingerprint = live.read_chain_snapshot()
            if slot_index is None:
                cfg = dict(base_chain)
                lo, hi = ((0.0, 1.0) if key == "quality"
                          else (0.0, 10.0))
                value = round(max(lo, min(hi, value)), 2)
                previous = float(
                    cfg.get(key, live.CHAIN_PARAMETER_DEFAULTS[key]))
                if value == previous:
                    return None
                cfg[key] = value
            else:
                lo, hi = SLOT_GAIN_MIN_DB, SLOT_GAIN_MAX_DB
                value = round(max(lo, min(hi, value)), 2)
                state = self._managed_state_from_snapshot(
                    base_chain, base_fingerprint,
                    slot_paths=slot_paths, slot_identities=slot_identities)
                if slot_identity is not None:
                    resolved = state.index_for_identity(slot_identity)
                    if resolved is None:
                        raise ChainStateError(
                            "Slot changed before gain commit")
                    previous = float(getattr(state.slot(resolved), key))
                else:
                    if slot_identities is not None:
                        state.set_index_aliases(slot_identities)
                    previous = float(getattr(state.slot(slot_index), key))
                if value == previous:
                    return None
                if slot_identity is not None:
                    state.set_slot_gain_identity(slot_identity, key, value)
                else:
                    if slot_identities is not None:
                        state.set_slot_gain_identity(
                            slot_identities[slot_index], key, value)
                    else:
                        state.set_slot_gain(slot_index, key, value)
                cfg = state.to_chain()
            if self._managed_engine_active():
                # The adapter's expected chain is the file state read before
                # the candidate edit. Passing the already-mutated cfg makes
                # its CAS check reject every managed mouse commit as a false
                # conflict.
                state = self._managed_state_from_snapshot(
                    base_chain, base_fingerprint,
                    slot_paths=slot_paths if slot_index is not None else None,
                    slot_identities=(
                        slot_identities if slot_index is not None else None))
                persisted = state.commit(
                    _ManagedChainAdapter(self, expected_chain=base_chain),
                    lambda draft: draft.apply_candidate(cfg),
                )
                return _ManagedParamCommit(
                    persisted,
                    state.slot_identities if slot_index is not None else None)
            persisted = live.write_chain(
                cfg,
                expected_fingerprint=base_fingerprint,
                expected_revision=base_chain.get("revision", 0),
            )
            committed = (persisted if isinstance(persisted, dict)
                         else live.read_chain() or cfg)
            return _ManagedParamCommit(
                committed,
                state.slot_identities if slot_index is not None else None)

    def _param_hold_committed(
            self, generation: int, slot_index: int | None,
            slot_identity: int | None, key: str, value: float, final: bool,
            commit: _ManagedParamCommit | None) -> None:
        """Publish a completed hold write without refreshing every pane per tick."""
        if generation != self._param_hold_generation:
            return
        if commit is not None:
            try:
                persisted = commit.chain
                if slot_index is not None:
                    # The hold worker owns a separate ChainState snapshot. Copy
                    # its committed Slot trim into the live UI state before
                    # the non-structural publisher adopts the chain-level
                    # fields; adopt_managed_chain intentionally preserves the
                    # existing Slot objects.
                    panel = self.query_one(ChainPanel)
                    panel.state.apply_candidate(
                        persisted, identities=commit.slot_identities)
                committed = self._publish_chain_write(persisted)
            except Exception as exc:
                self._param_hold_failed(
                    generation, slot_index, slot_identity, str(exc))
                return
            if final:
                if slot_index is None:
                    self._publish_mutation(
                        "chain-param", (f"chain:{key}",),
                        committed.get("revision"))
                else:
                    label = ("Input" if key == "input_gain_db"
                             else "Output")
                    focus_index = (
                        panel.state.index_for_identity(slot_identity)
                        if slot_identity is not None
                        and panel.state.target_identity == slot_identity
                        else None)
                    self._publish_slot_commit(
                        committed, focus_index,
                        f"Slot {slot_index + 1:02d} {label} set to "
                        f"{value:+.1f} dB")
        try:
            panel = self.query_one(ChainPanel)
            if slot_index is None:
                panel.params._hold_commit_ack(generation, final=final)
            else:
                resolved_index = (
                    panel.state.index_for_identity(slot_identity)
                    if slot_identity is not None else slot_index)
                widget = (panel._slot_widgets.get(resolved_index)
                          if resolved_index is not None else None)
                if widget is not None:
                    widget._io_hold_commit_ack(generation, final=final)
        except Exception:
            pass

    def _param_hold_failed(self, generation: int, slot_index: int | None,
                           slot_identity: int | None, error: str) -> None:
        """Stop a failed hold and restore the last persisted display value."""
        if generation != self._param_hold_generation:
            return
        with self._param_hold_lock:
            self._param_hold_pending = None
        try:
            panel = self.query_one(ChainPanel)
            if slot_index is None:
                panel.params.abort_param_hold(generation)
            else:
                resolved_index = (
                    panel.state.index_for_identity(slot_identity)
                    if slot_identity is not None else slot_index)
                widget = (panel._slot_widgets.get(resolved_index)
                          if resolved_index is not None else None)
                if widget is not None:
                    widget.abort_io_hold(generation)
            cfg = live.read_chain()
            if cfg:
                self._publish_chain_write(cfg)
        except Exception:
            pass
        self.notify(f"Chain unchanged: {error}", severity="error")

    def action_bump_gain(self, delta: float) -> None:
        self._bump("gain", delta)

    def action_bump_master(self, delta: float) -> None:
        self._bump("master", delta)

    def action_bump_quality(self, delta: float) -> None:
        """A2 model quality (SlimmableContainer sub-model size), clamped 0..1.

        1.0 = full precision (default), lower = lighter CPU. A1 models ignore it.
        """
        self._bump("quality", delta)

    def action_focus_search(self) -> None:
        focused = self.focused
        if focused is not None and any(
                isinstance(ancestor, PresetPanel)
                for ancestor in focused.ancestors_with_self):
            self.query_one(PresetPanel).focus_search()
            return
        self.query_one(LibraryPanel).focus_search()

    # ---- 干声试听：播放控制（space/s/l）与输入源选择器 ----

    def _playback_edit(self, edit) -> None:
        """播放控制公共路径：读链 → 改 input → 写回 → 刷 INPUT 行"""
        if (getattr(self, "_spawn_engine", False)
                and not self._managed_engine_active()):
            self.notify("Audio engine unavailable — playback unchanged",
                        severity="error")
            return
        cfg = live.read_chain()
        if not cfg:
            return
        inp = live.chain_input(cfg)
        if inp.get("source") != "file":
            self.notify("Instrument input active — click the INPUT row to pick a dry file")
            return
        managed_playback = self._managed_engine_active()
        target_index = None
        target_identity = None
        if managed_playback:
            try:
                panel = self.query_one(ChainPanel)
                managed_playback = not panel._legacy_mode
                state = getattr(panel, "state", None)
                target_index = getattr(state, "target_index", None)
                target_identity = getattr(state, "target_identity", None)
            except (AttributeError, NoMatches):
                # Unit seams without a mounted panel still exercise the
                # managed writer; the real App always has ChainPanel here.
                pass
        if managed_playback:
            def mutation(state: ChainState):
                latest = state.to_chain()
                latest_input = live.chain_input(latest)
                if latest_input.get("source") != "file":
                    return False
                edit(latest_input)
                latest["input"] = latest_input
                return state.apply_candidate(latest)

            queued = self._enqueue_managed_mutation(
                mutation, "", failure_note="Dry playback unchanged",
                on_success=lambda _persisted, _target: setattr(
                    self, "_playback_op_ts", time.monotonic()),
                target_index=target_index,
                target_identity=target_identity)
            if not queued:
                self.notify("Managed engine is no longer active", severity="error")
            return
        edit(inp)
        cfg["input"] = inp
        persisted = self._commit_external_chain(cfg)
        if persisted is None:
            return
        self._playback_op_ts = time.monotonic()

    def action_playback_toggle(self) -> None:
        self._playback_edit(
            lambda inp: inp.__setitem__(
                "state", live.PLAY_PAUSED if inp.get("state") == live.PLAY_PLAYING
                else live.PLAY_PLAYING))

    def action_playback_stop(self) -> None:
        self._playback_edit(lambda inp: inp.__setitem__("state", live.PLAY_STOPPED))

    def action_playback_loop(self) -> None:
        self._playback_edit(lambda inp: inp.__setitem__("loop", not inp.get("loop", False)))

    def on_input_node_widget_playback_requested(self, event) -> None:
        """INPUT 节点聚焦时 space/s/l → 同全局播放控制"""
        if event.action == "toggle":
            self.action_playback_toggle()
        elif event.action == "stop":
            self.action_playback_stop()
        elif event.action == "loop":
            self.action_playback_loop()

    def on_input_node_widget_source_requested(self, event) -> None:
        """聚焦 INPUT 节点后按 Enter → 打开输入源选择器（干声试听）"""
        self.action_open_input_source()

    def _unload_slot(self, key: str, node_kind: str, note: str) -> None:
        """卸载链槽（model/ir → null）：模块不加载、音频直通。

        watch_chain 无法从链值区分"卸载"与"双击 BYPASS"（都是 null）——
        BYPASS 保留内容显示，卸载必须强制重置节点为 NONE 空态。后续
        0.2s tick 的 watch_chain 因 label 已是 NONE 走空态分支，保持稳定。
        """
        cfg = live.read_chain()
        cfg[key] = None
        persisted = self._commit_external_chain(cfg)
        if persisted is None:
            return
        panel = self.query_one(ChainPanel)
        panel.chain = persisted
        node = next((n for n in panel.query(NodeWidget)
                     if n.kind == node_kind), None)
        if node is not None:
            node.set_title(None)
            node.set_label("NONE")
            node.set_bypassed(False)
            node.set_class(True, "chain-node-empty")
        self._publish_mutation("slot-unload", (f"slot:{key}",),
                                persisted.get("revision"))
        self.notify(note)

    def on_node_widget_delete_requested(self, event) -> None:
        """d 键删除聚焦模块：置 null 空槽（模块不加载、音频直通）。

        与双击 BYPASS 不同：BYPASS 保留内容显示（加载但直通），删除是
        缺位（NONE 灰底）。恢复 BYPASS 备份一并清除。

        链值已为 null（BYPASS 态或 preset 缺位）时不再提前返回：删除
        语义降级为清掉残留的 BYPASS/内容显示（REQ-016 bug②——此前
        BYPASS 节点按 d 被 "already empty" 守卫拒绝，看起来 delete 无效）。
        """
        key = "model" if event.kind == "amp" else "ir"
        if event.kind == "slot":
            key = "ir" if Path(event.path or "").suffix.casefold() == ".wav" else "model"
        panel = self.query_one(ChainPanel)
        node = next((n for n in panel.query(NodeWidget)
                     if n.kind == event.kind), None)
        if not live.read_chain().get(key):
            # 链上已无此模块：仅当节点本就显示空态（NONE）时无需动作；
            # 节点残留内容/BYPASS 标记时必须清掉。
            if node is None or (node.label in {"—", "NONE"} and not node.title):
                self.notify(f"{event.kind.upper()} slot already empty")
                return
        if key == "model":
            self._amp_model_backup = None
            note = "AMP unloaded — empty slot (audio passes through)"
        else:
            self._ir_backup = None
            note = "CAB unloaded — empty slot (audio passes through)"
        self._unload_slot(key, event.kind, note)

    def action_open_input_source(self) -> None:
        self.push_screen(InputSourceScreen())

    def on_input_source_screen_source_changed(self, event) -> None:
        """输入源切换完成：刷新链面板 INPUT 行"""
        self._publish_chain_write(event.chain)
        self._publish_mutation("input-source", ("input",), event.chain.get("revision"))

    def action_open_preset_picker(self) -> None:
        # v0.2 has one Presets surface. Command palette navigation focuses it;
        # it must not open the removed second preset browser.
        self.query_one(PresetPanel).focus_table()

    def action_save_preset(self) -> None:
        active = library.preset_current()
        if not active:
            self._save_confirm_name = None
            self._save_confirm_chain = None
            self.action_save_preset_as()
            return
        chain_signature = json.dumps(
            library.chain_get(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if not library.preset_is_dirty(active):
            self._save_confirm_name = None
            self._save_confirm_chain = None
            self.notify(f"Preset '{active}' is already up to date")
            return
        now = time.monotonic()
        if (self._save_confirm_name == active and
                self._save_confirm_chain == chain_signature and
                now - self._save_confirm_at < 2.0):
            p = library.preset_save(active)
            self._save_confirm_name = None
            self._save_confirm_at = 0.0
            self._save_confirm_chain = None
            self.query_one(PresetPanel)._fingerprint = None
            key = _preset_mutation_key(p.get("id"), p.get("name"))
            self._publish_mutation("preset-save", (key,) if key else ())
            self.notify(f"Preset '{p['name']}' overwritten")
            return
        self._save_confirm_name = active
        self._save_confirm_at = now
        self._save_confirm_chain = chain_signature
        self.notify(f"Press ctrl+s again within 2s to overwrite '{active}'")

    def action_save_preset_as(self) -> None:
        self.push_screen(PresetNameModal())

    def action_open_chain_save_menu(self) -> None:
        self.push_screen(ChainSaveModal())

    def action_clear_all_slots(self) -> None:
        panel = self.query_one(ChainPanel)
        if panel._legacy_mode:
            self.notify("Clear all Slots is unavailable for a legacy chain",
                        severity="warning")
            return
        if not panel.state.slot_count:
            self.notify("No Slots to clear", severity="warning")
            return
        self.push_screen(ClearSlotsConfirm())

    @staticmethod
    def _click_belongs_to_chain(event, panel: ChainPanel) -> bool:
        """Return whether a bubbled click originated inside ChainPanel.

        ``event.widget`` is the widget where Textual first dispatched the
        mouse event; it remains unchanged while the event bubbles through its
        ancestors.  Coordinate-only routing cannot distinguish a ChainPanel
        click from an unrelated Panel click, and some Chain border hit-tests
        intentionally restore Slot focus before checking their coordinates.
        """
        source = getattr(event, "widget", None)
        if source is None:
            return False
        try:
            return any(ancestor is panel
                       for ancestor in source.ancestors_with_self)
        except AttributeError:
            return False

    def on_click(self, event) -> None:
        """Click routing for the chain panel's clickable rows and switch buttons.

        NodeWidget owns keyboard focus, while the row shell is a larger visual
        hit target. Textual forwards mouse events to the deepest widget under
        the pointer, so validate that source before hit-testing the click
        coordinates here.
        """
        if event.screen_x is None:
            return
        panel = self.query_one(ChainPanel)
        if not self._click_belongs_to_chain(event, panel):
            return
        if panel.handle_slot_hint_click(event):
            return
        # Route by coordinates first: hit-testing can land on overlapping
        # siblings, so check the switch-button regions directly (▲ = title
        # line, ▼ = filename line — two rows, no dead space). The node kind
        # (amp | cab) comes from the row's own node — the row class names the
        # position, not the routing kind.
        for col in self.query(".chain-switch-col"):
            if col.region.contains(event.screen_x, event.screen_y):
                row = col.parent
                node = next((n for n in row.query(NodeWidget)), None)
                kind = node.kind if node else "amp"
                event.stop()
                self._focus_node(kind)
                up = next((b for b in col.query(NodeSwitchButton)
                           if b.direction > 0), None)
                down = next((b for b in col.query(NodeSwitchButton)
                             if b.direction < 0), None)
                if up is not None and up.region.contains(event.screen_x, event.screen_y):
                    self._switch_chain_model(kind, -1)
                elif down is not None and down.region.contains(event.screen_x, event.screen_y):
                    self._switch_chain_model(kind, +1)
                return
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        if widget.has_class("chain-slot"):
            event.stop()
            index = getattr(widget, "index", None)
            if index is None:
                return
            identity = getattr(getattr(widget, "snapshot", None),
                               "identity", None)
            self._focus_slot_identity(identity, index)
            if getattr(event, "chain", 1) >= 2:
                self._toggle_slot(index, identity)
            return
        if widget.has_class("chain-node"):
            # Clicking a node focuses it AND opens its Selection (pack list)
            # in the detail pane — mouse focus decides the detail mode
            # (library → description, chain → selection). Double-click
            # additionally toggles bypass.
            event.stop()
            if getattr(widget, "kind", None) == "input":
                # INPUT 节点：单击聚焦，双击打开输入源选择器
                if getattr(event, "chain", 1) >= 2:
                    self.action_open_input_source()
                else:
                    self._focus_node("input")
                return
            self._focus_node(widget.kind)
            if getattr(event, "chain", 1) >= 2:
                # 双击先切换（bypass/恢复）再开 pack：pack 用切换后的链状态
                # 决定去向——否则恢复时槽位仍是 null，_show_node_pack 会把
                # detail 清成"未选中"，违背"detail 永远跟点选一致"。
                self._toggle_node(widget.kind)
            self._show_node_pack(widget.kind)
            return
        # 兜底：链节点框内任意位置（边框、空白、未命中的子区域）→ 聚焦该框。
        for row in self.query(".chain-node-row"):
            if row.region.contains(event.screen_x, event.screen_y):
                node = next((n for n in row.query(NodeWidget)), None)
                if node is None:
                    return
                event.stop()
                self._focus_node(node.kind)
                self._show_node_pack(node.kind)
                return
        for row in self.query(".chain-slot-row"):
            if row.region.contains(event.screen_x, event.screen_y):
                slot = next((s for s in row.query(ChainSlotWidget)), None)
                if slot is None:
                    return
                event.stop()
                identity = slot.snapshot.identity
                self._focus_slot_identity(identity, slot.index)
                if getattr(event, "chain", 1) >= 2:
                    self._toggle_slot(slot.index, identity)
                return
        # 链面板的其余空白（节点行之间的空隙、effect/params 之外的区域）：
        # 点击聚焦面板本身，←/→ 即可切换 detail 视图。
        if panel.region.contains(event.screen_x, event.screen_y):
            event.stop()
            panel.focus()

    def _focus_node(self, kind: str) -> None:
        node = next((n for n in self.query(NodeWidget) if n.kind == kind), None)
        if node:
            node.focus()

    @staticmethod
    def _focused_slot_identity(panel: ChainPanel) -> int | None:
        """Return the logical Slot owning the current keyboard focus."""
        focused = getattr(panel.app, "focused", None)
        if focused is None:
            return None
        try:
            ancestors = focused.ancestors_with_self
        except Exception:
            ancestors = (focused,)
        for widget in ancestors:
            if isinstance(widget, ChainSlotWidget):
                return widget.snapshot.identity
        return None

    def _focus_slot(self, index: int) -> None:
        panel = self.query_one(ChainPanel)
        slot = panel._slot_widgets.get(index)
        if slot is not None:
            slot.focus()

    def _focus_slot_identity(self, identity: int | None,
                             fallback_index: int | None = None) -> None:
        panel = self.query_one(ChainPanel)
        if identity is not None:
            index = panel.state.index_for_identity(identity)
            if index is None:
                return
        else:
            index = fallback_index
        if index is not None:
            self._focus_slot(index)

    def _resolve_slot_index(self, index: int,
                            identity: int | None = None) -> int | None:
        """Resolve a UI Slot event without retargeting a stale identity."""
        panel = self.query_one(ChainPanel)
        if identity is not None:
            resolved = panel.state.index_for_identity(identity)
            if resolved is None:
                self.notify("Slot action discarded: Slot no longer exists",
                            severity="warning")
                return None
            return resolved
        try:
            panel.state.slot(index)
        except ChainStateError as exc:
            self.notify(str(exc), severity="warning")
            return None
        return index

    def _publish_slot_commit(self, persisted: dict,
                             focus_index: int | None, note: str) -> None:
        """Publish one completed Slot transaction on the UI thread."""
        panel = self.query_one(ChainPanel)
        detail = self.query_one(DetailPane)
        keep_pack_focus = detail._pack_mode and detail._pack_origin != "slot"
        focus_identity = (
            panel.state.target_identity if focus_index is not None else None)
        if detail._pack_slot_index is not None:
            detail.refresh_pack_active(persisted)
        if not panel.state.slot_count:
            detail.clear()
        # Structural Slot changes schedule an asynchronous dynamic recompose.
        # Schedule it from the commit boundary rather than waiting for the
        # polling tick. The publisher has already advanced the observed file
        # fingerprint, so a later poll can legitimately see no change and
        # leave a newly added Slot without a widget or SlotFocused message.
        needs_recompose = (
            not panel._legacy_mode
            and len(panel._slot_widgets) != panel.state.slot_count)
        if needs_recompose:
            focus_state = panel._capture_recompose_focus()
            if focus_index is not None and not keep_pack_focus:
                try:
                    focus_identity = panel.state.slot_identity(focus_index)
                except ChainStateError:
                    focus_identity = None
                if focus_identity is not None:
                    focus_state = ("slot", focus_index, focus_identity)
            panel._schedule_dynamic_recompose(
                focus_index if not keep_pack_focus else None,
                focus_state=focus_state,
                force_focus=(focus_index is not None and not keep_pack_focus))

        # Do not focus the old numeric row before a structural worker finishes:
        # the old widget can still be mounted at the same index and overwrite
        # the identity-aware focus restoration with a stale Slot object.
        recompose_pending = getattr(panel, "_recompose_pending", False)
        if (focus_index is not None and not keep_pack_focus
                and not recompose_pending):
            def restore_focus() -> None:
                if focus_identity is None:
                    return
                focused = getattr(self, "focused", None)
                if focused is not None:
                    try:
                        inside_panel = focused is panel or any(
                            ancestor is panel
                            for ancestor in focused.ancestors_with_self)
                    except AttributeError:
                        inside_panel = False
                    if not inside_panel:
                        return
                self._focus_slot_identity(focus_identity, focus_index)

            self.call_after_refresh(restore_focus)
        self._publish_mutation(
            "slot",
            (f"slot:{focus_index}" if focus_index is not None else "chain",),
            persisted.get("revision"),
        )
        self.notify(note)

    def _commit_slot_mutation(self, mutation, note: str,
                              *, failure_note: str | None = None) -> bool:
        """Apply one ordered-Slot mutation and publish the canonical chain.

        T04 owns the process-local candidate and target identity. The App only
        supplies the file boundary here, then records the exact write metadata
        before the polling path reconciles the new revision.
        """
        panel = self.query_one(ChainPanel)
        if panel._legacy_mode:
            self.notify("v0.2 Slot action unavailable for legacy chain",
                        severity="warning")
            return False
        state = panel.state
        if self._managed_engine_active():
            slot_snapshot = self._current_chain_identity_snapshot()
            queued = self._enqueue_managed_mutation(
                mutation, "", failure_note=failure_note or note,
                on_success=lambda persisted, focus_index: (
                    self._publish_slot_commit(
                        persisted, focus_index, note)),
                target_index=state.target_index,
                target_identity=state.target_identity,
                slot_paths=slot_snapshot[0] if slot_snapshot else None,
                slot_identities=slot_snapshot[1] if slot_snapshot else None,
                base_fingerprint=live.chain_file_fingerprint(),
                base_revision=state.to_chain().get("revision"))
            if not queued:
                self.notify("Managed engine is no longer active",
                            severity="error")
            return queued

        before = state.checkpoint()
        try:
            result = mutation(state)
        except ChainStateError as exc:
            self.notify(str(exc), severity="warning")
            return False
        if result is False:
            self.notify(failure_note or note, severity="warning")
            return False
        focus_index = state.target_index
        candidate = state.to_chain()
        persisted = self._commit_external_chain(candidate)
        if persisted is None:
            state.restore_checkpoint(before)
            panel.chain = state.to_chain()
            return False
        try:
            state.mark_managed_write(
                live.last_chain_write_fingerprint(),
                persisted.get("revision"),
            )
        except ChainStateError:
            # A test double may not expose protocol revision metadata. The
            # visible state is still valid; the next poll will conservatively
            # drop process-local bypass candidates.
            pass
        try:
            panel._observed_chain_fingerprint = chain_fingerprint(persisted)
        except (TypeError, ValueError):
            panel._observed_chain_fingerprint = None
        panel.chain = persisted
        panel._refresh_dynamic_slots()
        self._publish_slot_commit(persisted, focus_index, note)
        return True

    def _add_slot(self) -> None:
        panel = self.query_one(ChainPanel)
        index = panel.state.slot_count
        if self._commit_slot_mutation(
                lambda state: state.add_slot(),
                f"Added Slot {index + 1:02d}"):
            # _publish_slot_commit schedules identity-aware focus after the
            # new widget is mounted. Focusing by index here races that mount.
            return

    def _delete_slot(self, index: int, identity: int | None = None) -> None:
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        self._commit_slot_mutation(
            lambda state: state.delete_slot(index),
            f"Deleted Slot {index + 1:02d}")

    def _toggle_slot(self, index: int, identity: int | None = None) -> None:
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        panel = self.query_one(ChainPanel)
        snapshot = panel.state.slot(index)
        if snapshot.status is SlotStatus.EMPTY:
            self._browse_empty_slot(index)
            return
        label = "restored" if snapshot.status is SlotStatus.BYPASS else "bypassed"
        self._commit_slot_mutation(
            lambda state: state.toggle_bypass(index),
            f"Slot {index + 1:02d} {label}")

    def _move_slot(self, index: int, direction: int,
                   identity: int | None = None) -> None:
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        verb = "up" if direction < 0 else "down"
        self._commit_slot_mutation(
            lambda state: state.move_slot(index, direction),
            f"Moved Slot {index + 1:02d} {verb}",
            failure_note=f"Slot {index + 1:02d} cannot move {verb}")

    def _switch_slot_model(self, index: int, direction: int,
                           identity: int | None = None) -> None:
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        panel = self.query_one(ChainPanel)
        snapshot = panel.state.slot(index)
        path = snapshot.path or snapshot.candidate
        if not path:
            self.notify("Empty Slot has no models to switch", severity="warning")
            return
        try:
            siblings = library.local_models_by_tone(path) or []
        except Exception:
            siblings = []
        if len(siblings) <= 1:
            self.notify("Only one model in this pack", severity="warning")
            return
        current = next(
            (i for i, model in enumerate(siblings)
             if model.get("local_path") == path), None)
        if current is None:
            self.notify("Slot file is not a library model", severity="warning")
            return
        next_index = current + direction
        if next_index < 0 or next_index >= len(siblings):
            self.notify("Already at the model boundary", severity="warning")
            return
        next_model = siblings[next_index]
        next_path = next_model["local_path"]
        self._commit_slot_mutation(
            lambda state: state.load_file(index, next_path),
            f"Slot {index + 1:02d} → {live.short_name(next_path)}")

    def _adjust_slot_gain(self, index: int, key: str, delta: float,
                          identity: int | None = None) -> None:
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        if key not in {"input_gain_db", "output_gain_db"}:
            self.notify("Unknown Slot parameter", severity="warning")
            return
        label = "Input" if key == "input_gain_db" else "Output"
        direction = "+" if delta >= 0 else "-"
        self._commit_slot_mutation(
            lambda state: state.adjust_slot_gain(index, key, delta),
            f"Slot {index + 1:02d} {label} {direction}{abs(delta):g} dB",
            failure_note=f"Slot {index + 1:02d} {label} is already at its limit")

    def _calibrate_slot_output(self, index: int,
                               identity: int | None = None) -> None:
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        panel = self.query_one(ChainPanel)
        try:
            snapshot = panel.state.slot(index)
        except ChainStateError as exc:
            self.notify(str(exc), severity="warning")
            return
        path = snapshot.path
        if not path or Path(path).suffix.casefold() != ".nam":
            self.notify("CAL is available only for an active NAM Slot",
                        severity="warning")
            return
        self._calibration_generation += 1
        generation = self._calibration_generation
        identity = snapshot.identity
        slot_paths = panel.state.slot_paths
        slot_identities = panel.state.slot_identities
        threading.Thread(
            target=self._calibrate_slot_worker,
            args=(generation, index, identity, path,
                  slot_paths, slot_identities),
            name="slot-output-calibration", daemon=True).start()
        self.notify(f"Calibrating Slot {index + 1:02d}…")

    def _calibrate_slot_worker(self, generation: int, index: int,
                               identity: int | None,
                               expected_path: str,
                               slot_paths: tuple[str | None, ...] | None = None,
                               slot_identities: tuple[int, ...] | None = None
                               ) -> None:
        try:
            # Calibration and managed prepare share one control/reply sidecar.
            # Serialize the request so a concurrent mutation cannot replace
            # the reply before this worker consumes it.
            with self._managed_transaction_lock:
                request_index = index
                if slot_paths is not None and slot_identities is not None:
                    chain, fingerprint = live.read_chain_snapshot()
                    state = self._managed_state_from_snapshot(
                        chain, fingerprint,
                        slot_paths=slot_paths,
                        slot_identities=slot_identities)
                    if identity is not None:
                        request_index = state.index_for_identity(identity)
                        if request_index is None:
                            raise ChainStateError(
                                "Calibration discarded: Slot no longer exists")
                        snapshot = state.slot(request_index)
                        if snapshot.path != expected_path:
                            raise ChainStateError(
                                "Calibration discarded: Slot changed while waiting")
                    else:
                        state.set_index_aliases(slot_identities)
                        snapshot = state.slot(index)
                        request_index = snapshot.index
                        if snapshot.path != expected_path:
                            raise ChainStateError(
                                "Calibration discarded: Slot changed while waiting")
                elif identity is not None:
                    raise ChainStateError(
                        "Calibration discarded: Slot identity is unavailable")
                result = live.request_output_calibration(request_index)
                value = float(result)
                recommended = float(
                    getattr(result, "recommended_output_gain_db", value))
                clamped = bool(getattr(result, "clamped", False))
        except Exception as exc:
            try:
                self.call_from_thread(
                    self._slot_calibration_failed, generation, str(exc))
            except Exception:
                pass
            return
        try:
            self.call_from_thread(
                self._apply_slot_calibration, generation, index,
                expected_path, value, clamped, recommended,
                identity=identity)
        except Exception:
            pass

    def _slot_calibration_failed(self, generation: int, error: str) -> None:
        if generation != self._calibration_generation:
            return
        self.notify(f"Calibration failed: {error}", severity="error")

    def _apply_slot_calibration(self, generation: int, index: int,
                                expected_path: str, value: float,
                                clamped: bool = False,
                                recommended_output_gain_db: float | None = None,
                                *, identity: int | None = None
                                ) -> None:
        if generation != self._calibration_generation:
            return
        panel = self.query_one(ChainPanel)
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        try:
            snapshot = panel.state.slot(index)
        except ChainStateError as exc:
            self.notify(str(exc), severity="warning")
            return
        if snapshot.path != expected_path:
            self.notify("Calibration discarded: Slot changed while waiting",
                        severity="warning")
            return
        note = f"Calibrated Slot {index + 1:02d} output to {value:+.1f} dB"
        recommended = value
        if clamped:
            recommended = (value if recommended_output_gain_db is None
                           else recommended_output_gain_db)
            note += f" (clamped from {recommended:+.1f} dB)"
        if abs(snapshot.output_gain_db - value) < 0.005:
            if clamped:
                self.notify(
                    f"Slot {index + 1:02d} output is already {value:+.1f} dB "
                    f"(clamped from {recommended:+.1f} dB)")
            else:
                self.notify(f"Slot {index + 1:02d} output is already calibrated")
            return

        def apply_calibration(state: ChainState):
            if identity is not None:
                resolved = state.index_for_identity(identity)
                if resolved is None:
                    return False
                current = state.slot(resolved)
            else:
                try:
                    current = state.slot(index)
                except ChainStateError:
                    return False
            if current.path != expected_path:
                return False
            if identity is not None:
                return state.set_slot_gain_identity(
                    identity, "output_gain_db", value)
            return state.set_slot_gain(index, "output_gain_db", value)

        self._commit_slot_mutation(
            apply_calibration,
            note,
            failure_note="Calibration discarded: Slot changed while waiting")

    def on_add_slot_button_requested(self, _event) -> None:
        self._add_slot()

    def on_chain_slot_widget_switch_requested(
            self, event: ChainSlotWidget.SwitchRequested) -> None:
        self._switch_slot_model(event.index, event.direction, event.identity)

    def on_chain_slot_widget_toggle_requested(
            self, event: ChainSlotWidget.ToggleRequested) -> None:
        self._toggle_slot(event.index, event.identity)

    def on_chain_slot_widget_delete_requested(
            self, event: ChainSlotWidget.DeleteRequested) -> None:
        self._delete_slot(event.index, event.identity)

    def on_chain_slot_widget_tone_requested(
            self, event: ChainSlotWidget.ToneRequested) -> None:
        self._browse_slot(event.index, event.identity)

    def on_chain_slot_widget_move_requested(
            self, event: ChainSlotWidget.MoveRequested) -> None:
        self._move_slot(event.index, event.direction, event.identity)

    def on_chain_slot_widget_param_requested(
            self, event: ChainSlotWidget.ParamRequested) -> None:
        self._adjust_slot_gain(
            event.index, event.key, event.delta, event.identity)

    def on_chain_slot_widget_calibrate_requested(
            self, event: ChainSlotWidget.CalibrateRequested) -> None:
        self._calibrate_slot_output(event.index, event.identity)

    def on_clear_slots_confirm_confirmed(
            self, _event: ClearSlotsConfirm.Confirmed) -> None:
        self._commit_slot_mutation(
            lambda state: state.clear_slots(),
            "Cleared all Slots",
        )

    def on_chain_panel_slot_focused(self, event: ChainPanel.SlotFocused) -> None:
        """Slot focus establishes target and updates the matching Detail view."""
        # Focus messages can still be queued while Textual is unmounting the
        # current screen. A late message must not query a panel that no longer
        # exists or recreate Detail state during shutdown.
        if not getattr(self, "is_mounted", False):
            return
        try:
            self._show_slot_detail(event.index, event.identity)
        except NoMatches:
            return

    def _show_slot_detail(self, index: int,
                          identity: int | None = None) -> None:
        panel = self.query_one(ChainPanel)
        detail = self.query_one(DetailPane)
        if panel._legacy_mode:
            return
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        try:
            snapshot = panel.state.slot(index)
        except ChainStateError:
            return
        if snapshot.status is SlotStatus.EMPTY:
            detail.show_slot_empty(
                index, target=panel.state.target_index == index,
                slot_identity=snapshot.identity)
            return

        path = snapshot.path or snapshot.candidate
        if not path:
            detail.show_slot_empty(
                index, target=panel.state.target_index == index,
                slot_identity=snapshot.identity)
            return
        try:
            local_models = library.local_models_by_tone(path) or []
        except Exception:
            local_models = []
        tone = {}
        if local_models:
            try:
                tone = (library.get_tone(local_models[0].get("tone_id"))
                        if local_models[0].get("tone_id") is not None
                        else library.local_pack_for_path(path)) or {}
            except Exception:
                tone = {}
        if not tone:
            try:
                title = library.tone_title_for_path(path)
            except Exception:
                title = None
            tone = {
                "title": title or Path(path).stem,
                "gear": None,
                "models": [],
            }
        models = tone.get("models") or local_models
        if not models:
            # Keep a valid external/local file visible instead of leaving the
            # DetailPane blank. Protocol validation still decides whether a
            # later replacement can be committed.
            models = [{
                "id": None,
                "name": Path(path).name,
                "local_path": path,
                "architecture": "IR" if Path(path).suffix.lower() == ".wav"
                else "A2",
            }]
        detail.show_slot_pack(
            tone, models, panel.state.to_chain(), index, snapshot)

    def _browse_slot(self, index: int, identity: int | None = None) -> None:
        """Focus a Slot and return to LOCAL so the user can choose its Tone."""
        panel = self.query_one(ChainPanel)
        index = self._resolve_slot_index(index, identity)
        if index is None:
            return
        try:
            panel.state.focus_slot(index)
        except ChainStateError:
            return
        panel._last_focus_slot = index
        panel._refresh_dynamic_slots()
        library_panel = self.query_one(LibraryPanel)
        tab = library_panel.query_one("#--content-tab-pane-local")
        tab.post_message(tab.Clicked(tab))
        library_panel.query_one("#lib-table-local").focus()
        self.notify(f"Select a tone for Slot {index + 1:02d}")

    def _browse_empty_slot(self, index: int,
                           identity: int | None = None) -> None:
        """Compatibility alias for callers that specifically target an empty Slot."""
        self._browse_slot(index, identity)

    def _show_node_pack(self, kind: str) -> None:
        """Open the focused node's tone pack (all files) in the detail pane."""
        cfg = live.read_chain()
        path = cfg.get("model" if kind == "amp" else "ir")
        if not path:
            node = next((n for n in self.query(NodeWidget)
                         if n.kind == kind), None)
            if node is not None and node.bypassed:
                # 双击 BYPASS：槽位置 null 但节点仍显示当前内容——detail 保持
                # 现状（该节点的 pack 已在显示），不能清成"未选中"。
                return
            self.query_one(DetailPane).clear()
            return
        models = library.local_models_by_tone(path)
        if not models:
            self.query_one(DetailPane).clear()
            return
        tone = library.get_tone(models[0]["tone_id"]) or {}
        all_models = tone.get("models") or models
        self.query_one(DetailPane).show_pack(tone, all_models, cfg, kind)

    def _toggle_node(self, kind: str) -> None:
        """Double-click on a node: AMP/CAB bypass / restore (engine直通).

        Bypass keeps the current file displayed and flags the node; the engine
        passes the signal through (model=null / ir=null). Amplifier mute via
        gain is still reachable with G-/G+ keys — the double-click now means
        bypass, not silence.
        """
        cfg = live.read_chain()
        key = "ir" if kind == "cab" else "model"
        if kind == "cab":
            if cfg.get("ir"):
                self._ir_backup = cfg["ir"]
                cfg["ir"] = None  # engine treats null as bypass (pass through)
                note = "CAB bypass (double-click to restore)"
            elif self._ir_backup:
                restored = self._ir_backup
                cfg["ir"] = restored
                self._ir_backup = None
                note = f"CAB restored → {live.short_name(restored)}"
            else:
                self.notify("CAB: nothing to restore")
                return
        else:  # amp bypass: model=null → engine passes input through
            if cfg.get("model"):
                self._amp_model_backup = cfg["model"]
                cfg["model"] = None
                note = "AMP bypass (double-click to restore)"
            elif self._amp_model_backup:
                restored = self._amp_model_backup
                cfg["model"] = restored
                self._amp_model_backup = None
                note = f"AMP restored → {live.short_name(restored)}"
            else:
                self.notify("AMP: nothing to restore")
                return
        persisted = self._commit_external_chain(cfg)
        if persisted is None:
            return
        self._publish_mutation(
            "bypass", (f"chain:{key}",), persisted.get("revision"))
        self.notify(note)

    def on_preset_panel_activated(self, event: PresetPanel.Activated) -> None:
        self._apply_preset(event.name, preset_id=getattr(event, "preset_id", None))

    def on_preset_load_confirm_confirmed(self, event: PresetLoadConfirm.Confirmed) -> None:
        self._apply_preset(event.name, preset_id=getattr(event, "preset_id", None))

    def on_preset_panel_highlighted(self, event: PresetPanel.Highlighted) -> None:
        """Preset row highlighted: mirror its chain summary in the detail pane."""
        # PresetPanel publishes its initial row while the screen is mounting.
        # It must not replace the library detail unless the user is actually
        # navigating the preset list; detail always follows the focused pane.
        focused = self.focused
        if focused is None or not any(
                isinstance(ancestor, PresetPanel)
                for ancestor in focused.ancestors_with_self):
            return
        p = event.preset
        if not p:
            self.query_one(DetailPane).clear()
            return
        try:
            preset_id = p.get("id")
            if isinstance(preset_id, int) and not isinstance(preset_id, bool):
                ch = library.preset_resolved_chain_by_id(preset_id)
            else:
                ch = library.preset_resolved_chain(p["name"])
        except ValueError as e:
            self.query_one(DetailPane).show_text(escape(str(e)))
            return
        active = library.preset_current() == p["name"]
        dirty = active and library.preset_is_dirty(
            p["name"], preset_id=preset_id if isinstance(preset_id, int)
            and not isinstance(preset_id, bool) else None)
        self.query_one(DetailPane).show_preset(
            p, ch, active=active, dirty=dirty)

    def _apply_preset(self, name: str, *, preset_id: int | None = None) -> None:
        self._save_confirm_name = None
        self._save_confirm_chain = None
        # Capture before loading, but only record it after every Slot has been
        # resolved successfully. A missing file must not create an undo step.
        before = self._chain_snapshot()
        current_preset = (
            library.preset_get_by_id(preset_id)
            if isinstance(preset_id, int) and not isinstance(preset_id, bool)
            else library.preset_get(name))
        if current_preset is None:
            self.notify(
                f"Preset '{name}' is no longer available; load cancelled",
                severity="warning")
            return
        name = str(current_preset["name"])
        current_id = current_preset.get("id")
        preset_id = current_id if isinstance(current_id, int) else preset_id
        try:
            panel = self.query_one(ChainPanel)
            if panel._legacy_mode:
                # Keep the read-only v0.1 compatibility path for old chains and
                # its test doubles. Canonical v0.2 writes use the App boundary
                # below so live.py records the exact fingerprint and revision.
                cfg = (library.preset_load_by_id(preset_id)
                       if isinstance(preset_id, int)
                       else library.preset_load(name))
            else:
                resolved = (library.preset_resolved_chain_by_id(preset_id)
                            if isinstance(preset_id, int)
                            else library.preset_resolved_chain(name))
                current = live.read_chain()
                cfg = dict(current)
                cfg["slots"] = [
                    {"path": slot.get("path"),
                     **{key: slot[key] for key in
                        ("input_gain_db", "output_gain_db") if key in slot},
                     **({"candidate": slot["candidate"]}
                        if slot.get("candidate") else {})}
                    for slot in resolved.get("slots", ())
                ]
                for key in ("gain", "master", "quality"):
                    cfg[key] = resolved[key]
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        if panel._legacy_mode:
            # The legacy setter renders from the App-level recovery fields;
            # clear them after the legacy file write succeeds but before the
            # panel reconciles the replacement.
            self._amp_model_backup = None
            self._ir_backup = None
            persisted = self._publish_chain_write(cfg)
        else:
            persisted = self._commit_external_chain(cfg)
            if persisted is None:
                return
            try:
                library.preset_set_active(name)
            except Exception as exc:
                self.notify(
                    f"Preset '{name}' loaded but active state was not updated: {exc}",
                    severity="warning",
                )
            # A successful whole-chain replacement invalidates all process-local
            # bypass recovery candidates. Failed resolution/commit keeps them.
            self._amp_model_backup = None
            self._ir_backup = None
            panel.state.reset_transient_context()
            panel._refresh_dynamic_slots()
            self.query_one(DetailPane).clear_slot_target_context()
        self._push_preset_undo(before)
        key = _preset_mutation_key(preset_id, name)
        self._publish_mutation(
            "preset-load", (key,) if key else (), persisted.get("revision"))
        self.notify(f"Preset '{name}' loaded — ctrl+z undo")

    # ---- REQ-017: preset 链配置撤销/重做 ----------------------------------

    # 快照域 = preset 涉及的链配置（与 preset_save 内容一致）；input 输入源
    # 按既有语义"preset 不存输入源"不入快照——preset 应用保留输入源，undo
    # 恢复的也只是 preset 内容域，input 始终跟随当前链不变。
    _CHAIN_SNAPSHOT_KEYS = ("slots", "gain", "master", "quality")
    _CHAIN_UNDO_LIMIT = 50

    def _chain_snapshot(self) -> dict:
        """当前链的 preset 内容域快照。

        只存链上真实存在的键：链协议里"键缺失 = 默认值"（watch_chain 对
        quality 等做 ``float(chain.get("quality", 1.0))``），恢复时把 None
        写进链文件会让浮点解析崩溃。应用前链上没有的键，preset_load 之后
        也按 preset 语义存在或显式 null（ir）——undo 恢复时保留现状即可，
        与应用前状态等效。"""
        cfg = live.read_chain()
        return deepcopy({key: cfg[key] for key in self._CHAIN_SNAPSHOT_KEYS if key in cfg})

    def _restore_chain(self, snap: dict) -> dict:
        """恢复快照 → 写 live_chain.json（引擎热切换）→ UI 跟随。"""
        cfg = live.read_chain()
        cfg.update(snap)
        if "slots" in snap:
            cfg.pop("model", None)
            cfg.pop("ir", None)
        persisted = self._commit_external_chain(cfg)
        if persisted is None:
            raise OSError("external chain commit failed")
        panel = self.query_one(ChainPanel)
        panel.state.reset_transient_context()
        panel._refresh_dynamic_slots()
        self.query_one(DetailPane).clear_slot_target_context()
        return persisted

    def _push_undo(self, snap: dict) -> None:
        self._undo_stack.append(snap)
        if len(self._undo_stack) > self._CHAIN_UNDO_LIMIT:
            self._undo_stack.pop(0)

    def _push_redo(self, snap: dict) -> None:
        self._redo_stack.append(snap)
        if len(self._redo_stack) > self._CHAIN_UNDO_LIMIT:
            self._redo_stack.pop(0)

    def _push_preset_undo(self, snap: dict | None = None) -> None:
        """Preset 应用入栈：快照当前链进 undo 栈，redo 栈清空（新动作
        使旧 redo 失效）。与栈顶相同（preset 内容未变）则跳过。"""
        snap = self._chain_snapshot() if snap is None else deepcopy(snap)
        if self._undo_stack and self._undo_stack[-1] == snap:
            return
        self._push_undo(snap)
        self._redo_stack.clear()

    def action_undo_chain(self) -> None:
        if not self._undo_stack:
            self.notify("Nothing to undo")
            return
        snap = self._undo_stack[-1]
        current = self._chain_snapshot()
        try:
            persisted = self._restore_chain(snap)
        except Exception as exc:
            self.notify(f"Undo failed: {exc}", severity="error")
            return
        self._undo_stack.pop()
        self._push_redo(current)
        self._publish_mutation("undo", ("chain",), persisted.get("revision"))
        self.notify("Undo preset")

    def action_redo_chain(self) -> None:
        if not self._redo_stack:
            self.notify("Nothing to redo")
            return
        snap = self._redo_stack[-1]
        current = self._chain_snapshot()
        try:
            persisted = self._restore_chain(snap)
        except Exception as exc:
            self.notify(f"Redo failed: {exc}", severity="error")
            return
        self._redo_stack.pop()
        self._push_undo(current)
        self._publish_mutation("redo", ("chain",), persisted.get("revision"))
        self.notify("Redo preset")

    def on_node_widget_switch_requested(self, event) -> None:
        """↑/↓ on a focused AMP/IR node: step through the sibling models of the
        same tone folder, hot-swap the chain and mirror the file in the detail pane."""
        self._switch_chain_model(event.kind, event.direction)

    def _switch_chain_model(self, kind: str, direction: int) -> None:
        cfg = live.read_chain()
        key = "model" if kind == "amp" else "ir"
        path = cfg.get(key)
        if not path:
            self.notify(f"{kind.upper()}: no model loaded")
            return
        siblings = library.local_models_by_tone(path)
        if not siblings:
            self.notify(f"{kind.upper()}: not a library model")
            return
        if kind == "cab":
            siblings = [m for m in siblings if library.model_is_ir(m)]
        else:
            siblings = [m for m in siblings if not library.model_is_ir(m)]
        if len(siblings) <= 1:
            self.notify(f"{kind.upper()}: only one model in this folder")
            return
        cur = next((i for i, m in enumerate(siblings) if m["local_path"] == path), None)
        if cur is None:
            return
        nxt = siblings[(cur + direction) % len(siblings)]
        cfg[key] = nxt["local_path"]
        persisted = self._commit_external_chain(cfg)
        if persisted is None:
            return
        detail = self.query_one(DetailPane)
        if detail._pack_mode:
            # 聚焦打开的是 pack 视图：换模型只移动 ▶ 标记，不替换整个视图
            detail.refresh_pack_active(cfg)
        else:
            tone = (library.get_tone(nxt.get("tone_id"))
                    if nxt.get("tone_id") is not None
                    else library.local_pack_for_path(nxt["local_path"]))
            detail.show_model(tone, nxt)
        self._publish_mutation(
            "slot-model", (f"chain:{key}",), persisted.get("revision"))
        self.notify(f"{kind.upper()} → {live.short_name(nxt['local_path'])}")

    def on_detail_pane_pack_install_requested(self, event) -> None:
        """selection 视图里 Enter/双击一行 → 二级菜单详情页（与库表 remote
        行 Enter 同一屏：预览文件、勾选安装/卸载）。event.tone 是 tone dict
        （REQ-038 修复：旧实现把 model dict 传进来，model 的 id 被当作
        tone id 使用）。"""
        if not event.tone:
            return
        self.push_screen(PackInstallScreen(event.tone))

    def on_detail_pane_pack_expanded_requested(self, event) -> None:
        """Expand the current PACK into the reusable large install screen."""
        if event.tone:
            self.push_screen(PackInstallScreen(event.tone))

    def on_detail_pane_pack_files_installed(self, event) -> None:
        """Compatibility hook; completion toast is App-level."""
        return

    def on_detail_pane_pack_files_uninstalled(self, event) -> None:
        """pack 表 u 键批量卸载完成：toast + one coordinated refresh."""
        self.notify(f"Uninstalled {event.count} file(s) · metadata retained")

    def on_pack_install_screen_uninstalled(self, event) -> None:
        """PackInstallScreen 的 u 键卸载完成：统一协调刷新。"""
        self.notify(f"Uninstalled {event.count} file(s) from tone {event.tone_id} "
                    "· metadata retained")

    def on_detail_pane_pack_file_picked(self, event) -> None:
        """Pack 列表选中一个文件：热换对应链槽（IR 行换 ir、其余换 model）。

        重复选择链上已加载的项 = 卸载（置 null 空槽，音频直通但模块不再
        加载，与 BYPASS 的"加载但直通"不同）；amp-cab 包选 AMP 行时
        CAB 显式置 null（pop 不会让引擎移除旧 IR）。
        """
        if event.slot_index is not None:
            panel = self.query_one(ChainPanel)
            index = event.slot_index
            if not event.path:
                return
            if panel._legacy_mode:
                self.notify("v0.2 Slot action unavailable for legacy chain",
                            severity="warning")
                return
            target_index = (panel.state.index_for_identity(event.slot_identity)
                            if event.slot_identity is not None else index)
            if target_index is None or panel.state.target_index != target_index:
                self.notify("Select the target Slot before loading a file",
                            severity="warning")
                return
            if Path(event.path).suffix.lower() not in {".nam", ".wav"}:
                self.notify("Unsupported Slot file format",
                            severity="error")
                return
            if self._commit_slot_mutation(
                    lambda state: (
                        state.load_file_identity(event.slot_identity, event.path)
                        if event.slot_identity is not None
                        else state.load_file(index, event.path)),
                    f"Slot {target_index + 1:02d} → {live.short_name(event.path)}"):
                self.query_one(DetailPane).refresh_pack_active(
                    panel.state.to_chain())
            return
        try:
            panel = self.query_one(ChainPanel)
        except NoMatches:
            panel = None
        if panel is not None and not panel._legacy_mode:
            self.notify("Select a target Slot before loading a file",
                        severity="warning")
            return
        cfg = live.read_chain()
        if not event.path:
            return
        if cfg.get(event.slot) == event.path:
            # 重复选择已加载项 → BYPASS（模块加载但音频直通，保留内容显示；
            # 卸载只在链面板 d 键 delete）
            node_kind = "amp" if event.slot == "model" else "cab"
            self._toggle_node(node_kind)
            self.query_one(DetailPane).refresh_pack_active(live.read_chain())
            return
        backup_attr = "_amp_model_backup" if event.slot == "model" else "_ir_backup"
        node_kind = "amp" if event.slot == "model" else "cab"
        if not cfg.get(event.slot) and getattr(self, backup_attr, None) == event.path:
            # Selecting the recovery candidate while bypassed is the same
            # action as the node double-click restore path.
            self._toggle_node(node_kind)
            self.query_one(DetailPane).refresh_pack_active(live.read_chain())
            return
        cfg[event.slot] = event.path
        # Loading a different file exits bypass and replaces its in-process
        # recovery candidate; retaining the old candidate would make a later
        # delete look like a bypass state.
        setattr(self, backup_attr, None)
        note = f" · {event.slot.upper()} → {live.short_name(event.path)}"
        if event.slot == "model" and event.tone_gear == "amp-cab":
            cfg["ir"] = None
            self._ir_backup = None
            note += " · CAB empty (Amp + Cab model)"
        persisted = self._commit_external_chain(cfg)
        if persisted is None:
            return
        self.query_one(DetailPane).refresh_pack_active(persisted)
        self._publish_mutation(
            "slot-load", (f"chain:{event.slot}",), persisted.get("revision"))
        self.notify(note.lstrip(" ·"))

    def on_detail_pane_pack_closed(self, event) -> None:
        """Esc 从 pack 文件列表回到链节点（其 ↑/↓ 换模型、双击切换恢复）。"""
        if event.slot_index is not None:
            self._focus_slot_identity(event.slot_identity, event.slot_index)
        elif event.kind:
            self._focus_node(event.kind)

    def on_preset_name_modal_saved(self, event: PresetNameModal.Saved) -> None:
        key = _preset_mutation_key(getattr(event, "preset_id", None), event.name)
        self._publish_mutation("preset-save", (key,) if key else ())
        self.notify(f"Preset '{event.name}' saved")

    def on_chain_save_modal_saved(self, event: ChainSaveModal.Saved) -> None:
        key = _preset_mutation_key(getattr(event, "preset_id", None), event.name)
        self._publish_mutation("preset-save", (key,) if key else ())
        self.notify(f"Preset '{event.name}' saved")

    def on_preset_rename_modal_renamed(self, event: PresetRenameModal.Renamed) -> None:
        key = _preset_mutation_key(
            getattr(event, "preset_id", None), event.new_name)
        self._publish_mutation("preset-rename", (key,) if key else ())
        self.notify(f"Preset '{event.old_name}' renamed to '{event.new_name}'")

    def on_preset_note_modal_updated(self, event: PresetNoteModal.Updated) -> None:
        key = _preset_mutation_key(getattr(event, "preset_id", None), event.name)
        self._publish_mutation("preset-update", (key,) if key else ())
        self.notify(f"Preset '{event.name}' note updated")

    def on_preset_edit_modal_saved(self, event: PresetEditModal.Saved) -> None:
        """Persisted draft: publish once, then optionally load it explicitly."""
        key = _preset_mutation_key(getattr(event, "preset_id", None), event.name)
        self._publish_mutation("preset-update", (key,) if key else ())
        if event.load:
            self._apply_preset(
                event.name, preset_id=getattr(event, "preset_id", None))
        note = f"Preset '{event.name}' updated"
        bypassed = getattr(event, "bypassed", 0)
        if bypassed:
            note += f" · {bypassed} BYPASS saved as EMPTY"
        self.notify(note)

    def on_preset_delete_modal_deleted(self, event: PresetDeleteModal.Deleted) -> None:
        names = list(event.names)
        stale = list(getattr(event, "stale", ()))
        preset_ids = list(getattr(event, "preset_ids", ()))
        keys = tuple(
            key for key in (_preset_mutation_key(preset_id) for preset_id in preset_ids)
            if key is not None
        )
        if keys:
            self._publish_mutation(
                "preset-delete", keys)
        note = f"Deleted {len(names)} preset(s)"
        if stale:
            note += " · stale: " + ", ".join(stale)
        if stale:
            self.notify(note, severity="warning")
        else:
            self.notify(note)

    def on_tone_selected(self, event: ToneSelected) -> None:
        """Enter/double-click on a local Library row opens its PACK view."""
        self._remote_detail_request_id += 1
        if event.pack_id:
            t = library.local_pack_by_id(event.pack_id)
            if not t:
                return
            if not self.query_one(ChainPanel)._legacy_mode:
                self.query_one(DetailPane).show_library_pack(t)
                return
            picker_kind = (
                "ir" if t.get("format") == "ir" else
                "amp" if t.get("format") == "nam" else "slot")
            self.push_screen(TonePickerScreen(
                picker_kind,
                pack_id=event.pack_id, tone_type=t.get("gear") or "amp"))
            return
        if event.tone_id is None:
            return
        t = library.get_tone(event.tone_id)
        if t:
            if not self.query_one(ChainPanel)._legacy_mode:
                self.query_one(DetailPane).show_library_pack(t)
                return
            kind = "ir" if library.model_is_ir({}, t) else "amp"
            self.push_screen(TonePickerScreen(
                kind, tone_id=int(t["id"]), tone_type=t.get("gear") or "amp"))

    def on_remote_tone_selected(self, event: RemoteToneSelected) -> None:
        """Canonical remote rows open PACK in DetailPane."""
        self._remote_detail_request_id += 1
        detail = self.query_one(DetailPane)
        detail.show_library_pack(event.tone, remote=True)
        self._hydrate_remote_tone(event.tone, self._remote_detail_request_id)

    def on_link_clicked(self, event) -> None:
        """Click a metadata link (author/tag) → TONE3000 search for it."""
        href = unquote(getattr(event, "href", "") or "")
        if href.startswith(("https://", "http://")):
            webbrowser.open(href)
            self.notify("Opened link in browser")
            return
        if not href.startswith("search:"):
            return
        _, kind, value = href.split(":", 2)
        panel = self.query_one(LibraryPanel)
        tab = panel.query_one("#--content-tab-pane-tone")
        tab.post_message(tab.Clicked(tab))  # user-path tab switch (no rollback)
        if kind == "author":
            panel.run_worker(partial(panel._show_search, f"@{value}"), name="search",
                             exclusive=True)
        elif kind == "tag":
            panel.run_worker(partial(panel._show_search, f"#{value}"), name="search",
                             exclusive=True)
        self.notify(f"Searched {kind}: {value}")

    def on_tone_highlighted(self, event: ToneHighlighted) -> None:
        self._remote_detail_request_id += 1
        request_id = self._remote_detail_request_id
        detail = self.query_one(DetailPane)
        if event.tone:
            detail.show(event.tone, remote=event.remote)
            if event.remote:
                self._hydrate_remote_tone(event.tone, request_id)
        else:
            detail.clear()

    def _hydrate_remote_tone(self, tone: dict, request_id: int) -> None:
        """Fetch complete TONE3000 metadata for a lightweight list row."""
        try:
            tone_id = int(tone.get("id") or 0)
        except (TypeError, ValueError):
            return
        if not tone_id:
            return
        cached = self._remote_detail_cache.get(tone_id)
        if cached is not None:
            self.query_one(DetailPane).update_tone_metadata(cached)
            return
        self.run_worker(
            partial(self._fetch_remote_tone, tone_id, request_id),
            name="remote-tone-detail", exclusive=True)

    async def _fetch_remote_tone(self, tone_id: int, request_id: int) -> None:
        try:
            tone = await asyncio.to_thread(library.tone3000.tone_by_id, tone_id)
        except Exception:
            return
        if not tone:
            return
        self._remote_detail_cache[tone_id] = dict(tone)
        if request_id != self._remote_detail_request_id:
            return
        detail = self.query_one(DetailPane)
        detail.update_tone_metadata(tone)

    def on_creator_focused(self, event) -> None:
        """TOP CREATORS 行聚焦 → 作者信息 + top 音色列表（REQ-012）。"""
        self.query_one(DetailPane).show_creator(event.username)

    def on_verified_author(self, event: VerifiedAuthor) -> None:
        self.query_one(LibraryPanel).mark_verified_author(event.username)

    def on_tone_picker_screen_picked(self, event: TonePickerScreen.Picked) -> None:
        """Node picked: write chain config → engine hot-swaps.

        path None removes the slot — the key is set to null (not popped), so
        the engine sees the change and passes the signal through; an absent
        key would leave the old model loaded.
        """
        cfg = live.read_chain()
        key = "model" if event.kind == "amp" else "ir"
        if event.path is None:
            cfg[key] = None
            setattr(self, "_amp_model_backup" if key == "model" else "_ir_backup", None)
            note = f"Chain updated: {key} → empty"
        else:
            backup_attr = "_amp_model_backup" if key == "model" else "_ir_backup"
            node_kind = "amp" if key == "model" else "cab"
            if not cfg.get(key) and getattr(self, backup_attr, None) == event.path:
                self._toggle_node(node_kind)
                self.query_one(ChainPanel).chain = live.read_chain()
                return
            if cfg.get(key) == event.path:
                self._toggle_node(node_kind)
                self.query_one(ChainPanel).chain = live.read_chain()
                return
            cfg[key] = event.path
            setattr(self, backup_attr, None)
            note = f"Chain updated: {key} → {live.short_name(event.path)}"
        if event.tone_type == "amp-cab":
            cfg["ir"] = None  # 显式 null（pop 不会让引擎移除旧 IR）
            self._ir_backup = None
            note += " · CAB empty (Amp + Cab model)"
        persisted = self._commit_external_chain(cfg)
        if persisted is None:
            return
        self._publish_mutation(
            "tone-picker", (f"chain:{key}",), persisted.get("revision"))
        self.notify(note)


def main() -> None:
    parser = argparse.ArgumentParser(prog="gigbuddy", description="GigBuddy tone-chain TUI")
    parser.add_argument("--in", dest="dev_in", default="",
                        help="input device name fragment (default: system default)")
    parser.add_argument("--out", dest="dev_out", default="",
                        help="output device name fragment (default: system default)")
    parser.add_argument("--ch", type=int, default=1, help="input channel (default: 1)")
    parser.add_argument("--no-engine", action="store_true",
                        help="engine already running externally (skip spawn)")
    parser.add_argument("--theme", default=None,
                        help="startup color theme (default: gigbuddy; t cycles guitar-amp themes)")
    args = parser.parse_args()
    GigBuddyApp(dev_in=args.dev_in, dev_out=args.dev_out, in_ch=args.ch,
                spawn_engine=not args.no_engine, theme=args.theme).run()


if __name__ == "__main__":
    main()
