"""Shared mutation events and the single app-level refresh coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from textual.message import Message


class MutationCommitted(Message):
    """A persistence-changing operation completed successfully.

    ``keys`` are stable business identities rather than cursor positions.  The
    app coalesces events posted during one event-loop turn before reconciling
    registered page instances.
    """

    def __init__(self, operation: str, keys: Iterable[str] = (),
                 revision: str | int | None = None,
                 *, operations: Iterable[str] | None = None) -> None:
        super().__init__()
        self.operation = str(operation)
        self.kind = self.operation  # compatibility with callers using "kind"
        self.operations = tuple(str(item) for item in (
            operations if operations is not None else (self.operation,)))
        self.keys = tuple(str(key) for key in keys)
        self.object_keys = self.keys
        self.revision = revision


class ModelsDownloaded(Message):
    """A tone download completed, independent of the originating view."""

    def __init__(self, tone_id: int, count: int,
                 model_ids: Iterable[int] = ()) -> None:
        super().__init__()
        self.tone_id = int(tone_id)
        self.count = int(count)
        self.model_ids = tuple(int(model_id) for model_id in model_ids)


@dataclass(frozen=True)
class ViewAnchor:
    """Stable view position captured before a page reconciles its rows."""

    screen_id: str | None = None
    app_tab: str | None = None
    view_tab_id: str | None = None
    focused_widget: str | None = None
    cursor_row_key: str | None = None
    cursor_column: int = 0
    first_visible_row_key: str | None = None
    row_offset: float = 0.0
    scroll_x: float = 0
    scroll_y: float = 0
    selection_keys: tuple[str, ...] = ()
    confirmation_state: Any = None
    detail_context_key: str | None = None


def view_context(widget: Any) -> tuple[str | None, str]:
    """Return the screen and app-tab identity for a mounted page."""
    try:
        screen_id = getattr(widget.screen, "id", None)
    except Exception:
        screen_id = None
    app = getattr(widget, "app", None)
    app_tab = getattr(app, "active_app_tab", None)
    if not isinstance(app_tab, str) or not app_tab:
        app_tab = getattr(app, "_active_app_tab", None)
    return screen_id, app_tab if isinstance(app_tab, str) and app_tab else "main"


def focused_widget_key(owner: Any) -> str | None:
    """Return a stable widget id only when focus is inside ``owner``."""
    app = getattr(owner, "app", None)
    focused = getattr(app, "focused", None)
    if focused is None:
        return None
    try:
        inside = any(item is owner for item in focused.ancestors_with_self)
    except Exception:
        inside = False
    if not inside:
        return None
    widget_id = getattr(focused, "id", None)
    if isinstance(widget_id, str) and widget_id:
        return widget_id
    return type(focused).__name__


class MutationRefreshCoordinator:
    """Merge same-turn commits and invoke every registered page once."""

    def __init__(self, schedule: Callable[[Callable[[], None]], Any],
                 reconcile: Callable[[MutationCommitted], None],
                 capture: Callable[[], None] | None = None) -> None:
        self._schedule = schedule
        self._reconcile = reconcile
        self._capture = capture
        self._pending: list[MutationCommitted] = []
        self._scheduled = False

    def receive(self, event: MutationCommitted) -> None:
        self._pending.append(event)
        if self._scheduled:
            return
        self._scheduled = True
        # Capture the view anchors synchronously while the pages still reflect
        # the pre-mutation viewport. Deferring the capture to ``flush()`` lets
        # an unrelated refresh (e.g. the 0.1s tick rebuilding the preset table
        # once the chain/active fingerprint changes) wipe the table -- and
        # reset its scroll -- before the anchors are read, so the restore
        # would resurrect the wrong (reset) position.
        if self._capture is not None:
            self._capture()
        self._schedule(self.flush)

    def flush(self) -> None:
        self._scheduled = False
        events = self._pending
        self._pending = []
        if not events:
            return
        groups: list[list[MutationCommitted]] = []
        seen_objects: set[int] = set()
        for event in events:
            event_identity = id(event)
            if event_identity in seen_objects:
                continue
            seen_objects.add(event_identity)
            if groups and self._can_merge(groups[-1][-1], event):
                groups[-1].append(event)
            else:
                groups.append([event])
        for group in groups:
            self._reconcile(self._merge(group))

    @staticmethod
    def _can_merge(previous: MutationCommitted,
                   current: MutationCommitted) -> bool:
        """Only coalesce duplicate delivery of one committed revision."""
        if previous is current:
            return True
        if previous.revision is None or current.revision is None:
            return False
        return previous.revision == current.revision

    @staticmethod
    def _merge(events: list[MutationCommitted]) -> MutationCommitted:
        keys: list[str] = []
        seen: set[str] = set()
        for event in events:
            for key in event.keys:
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        first = events[0]
        return MutationCommitted(
            first.operation if len(events) == 1 else "batch",
            keys,
            first.revision,
            operations=(operation for event in events
                        for operation in event.operations),
        )
