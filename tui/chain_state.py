"""In-process ordered Slot state for the v0.2 TUI.

The live protocol deliberately stores only ``slots[].path``.  This module
keeps the extra UI state that cannot be persisted: the current target and the
recovery candidate for a bypassed Slot.  Internal Slot objects are used as
the identity while the process is alive; no Slot id is exposed or written.

The class is intentionally independent from Textual and the file/runtime
implementations.  Panels can issue state commands and a managed adapter can
commit the resulting complete chain as one transaction.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Protocol


MAX_SLOTS = 6
_UNSET = object()


class ChainStateError(ValueError):
    """Raised when a Slot state command cannot be represented."""


class ChainStateRollbackError(RuntimeError):
    """Raised when a failed commit cannot fully restore its side effects."""

    def __init__(self, original: Exception, rollback_errors: Sequence[Exception]) -> None:
        self.original = original
        self.rollback_errors = tuple(rollback_errors)
        super().__init__(
            f"commit failed and rollback was incomplete: {original}; "
            f"rollback errors: {', '.join(str(error) for error in rollback_errors)}"
        )


class SlotStatus(str, Enum):
    ACTIVE = "active"
    BYPASS = "bypass"
    EMPTY = "empty"


class SlotOverlay(str, Enum):
    LOADING = "loading"
    ERROR = "error"


@dataclass(frozen=True)
class SlotSnapshot:
    """Read-only view of one current ordered Slot."""

    index: int
    path: str | None
    candidate: str | None
    status: SlotStatus
    overlay: SlotOverlay | None = None
    error: str | None = None
    operation_id: int | str | None = None

    @property
    def display_state(self) -> str:
        """Return the temporary overlay when present, otherwise the base state."""
        return (self.overlay.value if self.overlay is not None
                else self.status.value)


@dataclass(frozen=True)
class ChainSnapshot:
    """Immutable-shaped state view suitable for rendering and assertions."""

    slots: tuple[SlotSnapshot, ...]
    target_index: int | None
    chain: Mapping[str, Any]
    managed_fingerprint: str | None
    managed_revision: int | None
    chain_error: str | None = None


@dataclass(frozen=True)
class CommitReceipt:
    """Metadata returned by a successful managed file commit.

    ``fingerprint`` must identify the exact bytes/canonical payload that the
    polling path will observe.  A missing value intentionally makes the next
    poll conservative and drops process-local bypass candidates.
    """

    fingerprint: str | None = None
    revision: int | None = None


@dataclass(frozen=True)
class PreparedCommit:
    """One runtime/file candidate with the revision both sides must share."""

    chain: Mapping[str, Any]
    runtime: Any
    revision: int

    def __post_init__(self) -> None:
        if (isinstance(self.revision, bool) or not isinstance(self.revision, int)
                or self.revision < 0):
            raise ChainStateError("prepared revision must be a non-negative integer")


class ManagedCommitAdapter(Protocol):
    """File/runtime boundary used by :meth:`ChainState.commit`.

    ``prepare`` must fully validate and prepare the candidate runtime before
    ``write_file`` is called.  The restore methods receive the exact previous
    in-memory file payload and runtime snapshot captured before the attempt.
    """

    def snapshot_runtime(self) -> Any:
        ...

    def prepare(self, chain: dict[str, Any]) -> PreparedCommit:
        ...

    def write_file(self, chain: dict[str, Any]) -> CommitReceipt | Mapping[str, Any]:
        ...

    def apply_runtime(self, prepared: PreparedCommit) -> None:
        ...

    def restore_file(self, chain: dict[str, Any]) -> None:
        ...

    def restore_runtime(self, snapshot: Any) -> None:
        ...


@dataclass
class _Slot:
    path: str | None
    candidate: str | None = None
    overlay: SlotOverlay | None = None
    error: str | None = None
    operation_id: int | str | None = None

    @property
    def status(self) -> SlotStatus:
        if self.path is not None:
            return SlotStatus.ACTIVE
        if self.candidate is not None:
            return SlotStatus.BYPASS
        return SlotStatus.EMPTY


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(value))


def _slot_paths(chain: Mapping[str, Any]) -> list[str | None]:
    """Read ordered paths without assigning an identity to a persistent Slot."""
    if "slots" in chain:
        raw_slots = chain.get("slots")
        if not isinstance(raw_slots, list):
            raise ChainStateError("slots must be a list")
    else:
        raw_slots = []
        for key in ("model", "ir"):
            value = chain.get(key)
            if value is not None:
                raw_slots.append({"path": value})
    if len(raw_slots) > MAX_SLOTS:
        raise ChainStateError(f"a chain cannot contain more than {MAX_SLOTS} slots")

    paths: list[str | None] = []
    for index, item in enumerate(raw_slots):
        if not isinstance(item, Mapping):
            raise ChainStateError(f"slot {index} must be an object")
        if "path" not in item:
            raise ChainStateError(f"slot {index} must contain path")
        path = item.get("path")
        if path == "":
            raise ChainStateError(f"slot {index} path must not be empty")
        if path is not None and not isinstance(path, str):
            raise ChainStateError(f"slot {index} path must be a string or null")
        paths.append(path)
    return paths


def _validate_chain_shape(chain: Mapping[str, Any]) -> None:
    """Validate fields observable by the state seam before replacing state.

    Path roots, file extensions and runtime loadability belong to the protocol
    or managed adapter.  Structural chain fields are cheap to validate here so
    a malformed poll cannot turn the last valid state into an Empty chain.
    """
    _slot_paths(chain)
    ranges = {"gain": (0.0, 10.0), "master": (0.0, 10.0), "quality": (0.0, 1.0)}
    for key, (lower, upper) in ranges.items():
        if key not in chain:
            continue
        value = chain[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not lower <= value <= upper):
            raise ChainStateError(f"{key} must be between {lower} and {upper}")
    if "mute" in chain and not isinstance(chain["mute"], bool):
        raise ChainStateError("mute must be boolean")
    if "revision" in chain:
        revision = chain["revision"]
        if (isinstance(revision, bool) or not isinstance(revision, int)
                or revision < 0):
            raise ChainStateError("revision must be a non-negative integer")

    if "input" not in chain or chain["input"] is None:
        return
    input_value = chain["input"]
    if not isinstance(input_value, Mapping):
        raise ChainStateError("input must be an object")
    source = input_value.get("source", "instrument")
    if source not in {"instrument", "file"}:
        raise ChainStateError("input.source is invalid")
    state = input_value.get("state", "stopped")
    if state not in {"playing", "paused", "stopped"}:
        raise ChainStateError("input.state is invalid")
    loop = input_value.get("loop", False)
    if not isinstance(loop, bool):
        raise ChainStateError("input.loop must be boolean")
    file_value = input_value.get("file")
    if source == "instrument":
        if file_value is not None or state != "stopped" or loop:
            raise ChainStateError("instrument input has an invalid playback state")
    elif not isinstance(file_value, str) or not file_value:
        raise ChainStateError("file input requires a file")


def _persistent_slots(chain: Mapping[str, Any]) -> list[dict[str, str | None]]:
    return [{"path": path} for path in _slot_paths(chain)]


def _chain_with_slots(chain: Mapping[str, Any], paths: Sequence[str | None]) -> dict[str, Any]:
    output = _copy_mapping(chain)
    # Legacy fields are read-only compatibility input.  A state payload is
    # always emitted through the ordered slots representation.
    output.pop("model", None)
    output.pop("ir", None)
    output["slots"] = [{"path": path} for path in paths]
    return output


def chain_fingerprint(chain: Mapping[str, Any]) -> str:
    """Fingerprint the canonical JSON payload used by a state poller.

    The live file adapter may instead provide a fingerprint of its exact
    serialized bytes through ``CommitReceipt``.  This helper is useful for
    pure adapters and tests because it is independent of whitespace/order.
    """
    payload = json.dumps(
        _chain_with_slots(chain, _slot_paths(chain)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ChainState:
    """Ordered Slot state and managed chain commit seam.

    The list contains private object identities, not persistent ids.  Moving
    an item swaps those objects, so its target/candidate state follows local
    reorder even when two items have identical paths.
    """

    def __init__(self, chain: Mapping[str, Any] | None = None) -> None:
        initial = _copy_mapping(chain or {})
        _validate_chain_shape(initial)
        paths = _slot_paths(initial)
        self._chain = _chain_with_slots(initial, paths)
        self._slots = [_Slot(path) for path in paths]
        self._target: _Slot | None = None
        self._managed_fingerprint: str | None = None
        self._managed_revision: int | None = None
        self._chain_error: str | None = None
        self._next_operation_id = 0

    @property
    def target_index(self) -> int | None:
        if self._target is None:
            return None
        return next((index for index, slot in enumerate(self._slots)
                     if slot is self._target), None)

    @property
    def target(self) -> SlotSnapshot | None:
        index = self.target_index
        return self.slot(index) if index is not None else None

    @property
    def slot_count(self) -> int:
        return len(self._slots)

    @property
    def managed_fingerprint(self) -> str | None:
        return self._managed_fingerprint

    @property
    def managed_revision(self) -> int | None:
        return self._managed_revision

    @property
    def chain_error(self) -> str | None:
        return self._chain_error

    @property
    def slots(self) -> tuple[SlotSnapshot, ...]:
        return tuple(self._slot_snapshot(index, slot)
                     for index, slot in enumerate(self._slots))

    @property
    def chain(self) -> dict[str, Any]:
        return self.to_chain()

    def snapshot(self) -> ChainSnapshot:
        return ChainSnapshot(
            slots=self.slots,
            target_index=self.target_index,
            chain=self.to_chain(),
            managed_fingerprint=self._managed_fingerprint,
            managed_revision=self._managed_revision,
            chain_error=self._chain_error,
        )

    def checkpoint(self) -> "ChainState":
        """Return a process-local rollback checkpoint.

        The checkpoint intentionally preserves private Slot object identity,
        so target and bypass candidates can be restored after a failed UI
        mutation without inventing a persistent Slot id.
        """
        return self._copy_state()

    def restore_checkpoint(self, checkpoint: "ChainState") -> None:
        """Restore a checkpoint produced by :meth:`checkpoint`."""
        if not isinstance(checkpoint, ChainState):
            raise ChainStateError("checkpoint must be a ChainState")
        self._restore_state(checkpoint)

    def to_chain(self) -> dict[str, Any]:
        return _chain_with_slots(self._chain, [slot.path for slot in self._slots])

    def slot(self, index: int) -> SlotSnapshot:
        return self._slot_snapshot(index, self._slots[self._check_index(index)])

    def focus_slot(self, index: int) -> SlotSnapshot:
        slot = self._slots[self._check_index(index)]
        self._target = slot
        return self.slot(index)

    def clear_target(self) -> None:
        self._target = None

    def reset_transient_context(self) -> None:
        """Drop target and process-local recovery state after whole replacement."""
        self._target = None
        for slot in self._slots:
            slot.candidate = None
            slot.overlay = None
            slot.error = None
            slot.operation_id = None

    def add_slot(self) -> int:
        if len(self._slots) >= MAX_SLOTS:
            raise ChainStateError(f"a chain cannot contain more than {MAX_SLOTS} slots")
        self._mark_local_mutation()
        self._slots.append(_Slot(None))
        self._target = self._slots[-1]
        return len(self._slots) - 1

    def delete_slot(self, index: int) -> SlotSnapshot:
        index = self._check_index(index)
        deleted = self._slots[index]
        was_target = self._target is deleted
        self._mark_local_mutation()
        self._slots.pop(index)
        if was_target:
            # The new item at the deleted position wins.  When the deleted
            # item was last, use the preceding item instead.
            if index < len(self._slots):
                self._target = self._slots[index]
            elif self._slots:
                self._target = self._slots[-1]
            else:
                self._target = None
        return SlotSnapshot(
            index=index,
            path=deleted.path,
            candidate=deleted.candidate,
            status=deleted.status,
            overlay=deleted.overlay,
            error=deleted.error,
            operation_id=deleted.operation_id,
        )

    def clear_slots(self) -> int:
        """Remove every Slot and clear the process-local target context."""
        count = len(self._slots)
        if count:
            self._mark_local_mutation()
            self._slots.clear()
        self._target = None
        return count

    def move_slot(self, index: int, direction: int) -> bool:
        """Swap one item with its immediate neighbour.

        ``direction=-1`` moves up and ``direction=1`` moves down.  Object
        identity, rather than path or index, carries target and candidates.
        """
        index = self._check_index(index)
        if direction not in (-1, 1):
            raise ChainStateError("slot direction must be -1 or 1")
        other = index + direction
        if other < 0 or other >= len(self._slots):
            return False
        self._mark_local_mutation()
        moved = self._slots[index]
        self._slots[index], self._slots[other] = self._slots[other], self._slots[index]
        self._target = moved
        return True

    def toggle_bypass(self, index: int) -> bool:
        """Toggle Active <-> Bypass for one Slot; Empty is a no-op."""
        index = self._check_index(index)
        slot = self._slots[index]
        if slot.path is not None:
            self._mark_local_mutation()
            slot.candidate = slot.path
            slot.path = None
            slot.overlay = None
            slot.error = None
            slot.operation_id = None
            return True
        if slot.candidate is not None:
            self._mark_local_mutation()
            slot.path = slot.candidate
            slot.candidate = None
            slot.overlay = None
            slot.error = None
            slot.operation_id = None
            return True
        return False

    def load_file(self, index: int, path: str) -> SlotSnapshot:
        """Load or reselect one file using the target Slot's own identity.

        Selecting the current active file enters Bypass.  Selecting the
        current bypass candidate restores it.  Any other file replaces the
        Slot and clears its old candidate.
        """
        if not isinstance(path, str) or not path:
            raise ChainStateError("a Slot file path must be a non-empty string")
        index = self._check_index(index)
        slot = self._slots[index]
        if slot.path == path:
            self._mark_local_mutation()
            slot.candidate = path
            slot.path = None
        elif slot.path is None and slot.candidate == path:
            self._mark_local_mutation()
            slot.path = path
            slot.candidate = None
        else:
            self._mark_local_mutation()
            slot.path = path
            slot.candidate = None
        slot.overlay = None
        slot.error = None
        slot.operation_id = None
        return self.slot(index)

    def load_target_file(self, path: str) -> SlotSnapshot:
        """Load a file into the selected target, without implicit Slot creation."""
        index = self.target_index
        if index is None:
            raise ChainStateError("add or select a target slot")
        return self.load_file(index, path)

    def toggle_target_bypass(self) -> bool:
        index = self.target_index
        if index is None:
            raise ChainStateError("add or select a target slot")
        return self.toggle_bypass(index)

    def begin_loading(self, index: int, operation_id: int | str | None = None) -> SlotSnapshot:
        index = self._check_index(index)
        slot = self._slots[index]
        if slot.overlay is SlotOverlay.LOADING:
            return self.slot(index)
        if operation_id is None:
            self._next_operation_id += 1
            operation_id = self._next_operation_id
        slot.overlay = SlotOverlay.LOADING
        slot.error = None
        slot.operation_id = operation_id
        return self.slot(index)

    def finish_loading(self, index: int, operation_id: int | str,
                       *, error: str | None = None) -> bool:
        """Apply only the currently owned async result; stale results no-op."""
        index = self._check_index(index)
        slot = self._slots[index]
        if slot.overlay is not SlotOverlay.LOADING or slot.operation_id != operation_id:
            return False
        slot.operation_id = None
        if error is None:
            slot.overlay = None
            slot.error = None
        else:
            if not isinstance(error, str) or not error:
                raise ChainStateError("Slot error must be a non-empty string")
            slot.overlay = SlotOverlay.ERROR
            slot.error = error
        return True

    def set_error(self, index: int, message: str) -> SlotSnapshot:
        index = self._check_index(index)
        if not isinstance(message, str) or not message:
            raise ChainStateError("Slot error must be a non-empty string")
        slot = self._slots[index]
        slot.overlay = SlotOverlay.ERROR
        slot.error = message
        slot.operation_id = None
        return self.slot(index)

    def clear_overlay(self, index: int) -> SlotSnapshot:
        index = self._check_index(index)
        slot = self._slots[index]
        slot.overlay = None
        slot.error = None
        slot.operation_id = None
        return self.slot(index)

    def replace_chain(self, chain: Mapping[str, Any]) -> None:
        """Apply an overall chain replacement and discard UI-only identity."""
        incoming = _copy_mapping(chain)
        _validate_chain_shape(incoming)
        paths = _slot_paths(incoming)
        self._chain = _chain_with_slots(incoming, paths)
        self._slots = [_Slot(path) for path in paths]
        self._target = None
        self._managed_fingerprint = None
        self._managed_revision = None
        self._chain_error = None

    def apply_candidate(self, chain: Mapping[str, Any]) -> None:
        """Apply a committed candidate while retaining target identity when possible.

        Managed commits prepare a private candidate before the runtime/file
        boundary.  When only parameters or input changed, preserving the
        existing Slot objects keeps BYPASS candidates and the focused target
        intact; a changed Slot path gets fresh objects but retains the target
        index when that index still exists.
        """
        incoming = _copy_mapping(chain)
        _validate_chain_shape(incoming)
        paths = _slot_paths(incoming)
        current_paths = [slot.path for slot in self._slots]
        target_index = self.target_index
        if paths != current_paths:
            self._slots = [_Slot(path) for path in paths]
            self._target = (
                self._slots[target_index]
                if target_index is not None and target_index < len(self._slots)
                else None
            )
        self._chain = _chain_with_slots(
            incoming, [slot.path for slot in self._slots])
        self._chain_error = None

    def reconcile(self, chain: Mapping[str, Any], *, fingerprint: str | None = None,
                  revision: int | None | object = _UNSET) -> bool:
        """Reconcile one polled chain file.

        Returns ``True`` when the poll was treated as an overall replacement.
        Only the exact fingerprint/revision pair recorded for the most recent
        TUI write preserves bypass candidates.  Unknown or external writes
        rebuild Slot objects, making every ``path:null`` an Empty Slot.
        """
        incoming = _copy_mapping(chain)
        try:
            _validate_chain_shape(incoming)
            paths = _slot_paths(incoming)
        except ChainStateError as exc:
            # A malformed external file must not destroy the last valid chain
            # or its process-local candidate.  Keep the valid state and expose
            # a recoverable chain-level error to the renderer.
            self._chain_error = str(exc)
            return False
        observed_revision = revision
        if observed_revision is _UNSET:
            value = incoming.get("revision")
            observed_revision = value if isinstance(value, int) and not isinstance(value, bool) else None
        can_preserve = (
            fingerprint is not None
            and observed_revision is not None
            and fingerprint == self._managed_fingerprint
            and observed_revision == self._managed_revision
            and _persistent_slots(incoming) == _persistent_slots(self.to_chain())
        )
        if can_preserve:
            # Keep the private Slot objects and only refresh chain-level data
            # such as input/parameters/revision.
            self._chain = _chain_with_slots(incoming, [slot.path for slot in self._slots])
            self._chain_error = None
            return False
        self._chain = _chain_with_slots(incoming, paths)
        self._slots = [_Slot(path) for path in paths]
        self._target = None
        self._managed_fingerprint = None
        self._managed_revision = None
        self._chain_error = None
        return True

    def adopt_managed_chain(self, chain: Mapping[str, Any]) -> None:
        """Accept a chain just committed by this process without polling.

        The caller already mutated the private Slot objects before writing the
        file.  Re-running the external-poll identity rules here is incorrect
        when a test adapter or a legacy writer cannot provide a byte
        fingerprint: a freshly bypassed ``path:null`` would become Empty and
        the current target would be lost.  Keep Slot identity only when the
        committed persistent paths match the state already in memory; callers
        must use :meth:`reconcile` for an overall replacement.
        """
        incoming = _copy_mapping(chain)
        _validate_chain_shape(incoming)
        paths = _slot_paths(incoming)
        current_paths = [slot.path for slot in self._slots]
        if paths != current_paths:
            raise ChainStateError(
                "managed chain changes Slot order or paths; reconcile required"
            )
        self._chain = _chain_with_slots(incoming, current_paths)
        self._chain_error = None

    def mark_managed_write(self, fingerprint: str | None, revision: int | None) -> None:
        """Record the exact TUI write that a later poll may preserve."""
        if revision is not None and (
                isinstance(revision, bool) or not isinstance(revision, int)
                or revision < 0):
            raise ChainStateError("managed revision must be a non-negative integer")
        if fingerprint is not None and (
                not isinstance(fingerprint, str) or not fingerprint):
            raise ChainStateError("managed fingerprint must be a non-empty string")
        self._managed_fingerprint = fingerprint
        self._managed_revision = revision
        if revision is not None:
            self._chain["revision"] = revision

    def commit(self, adapter: ManagedCommitAdapter,
               mutation: Callable[["ChainState"], Any] | None = None,
               *, fingerprint: str | None = None,
               revision: int | None = None) -> dict[str, Any]:
        """Prepare, write and apply a complete candidate chain atomically.

        The mutation runs against a private copy.  A prepare failure therefore
        cannot alter visible state or call the file/runtime boundary.  Once
        writing starts, any file/runtime failure invokes both restore hooks and
        restores the original state snapshot before re-raising the original
        error.
        """
        if mutation is None:
            raise ChainStateError("a chain mutation is required")
        self._validate_managed_metadata(fingerprint, revision)

        before = self._copy_state()
        previous_chain = self.to_chain()
        previous_runtime = adapter.snapshot_runtime()
        working = self._copy_state()
        mutation(working)
        candidate_chain = working.to_chain()
        if revision is not None:
            candidate_chain["revision"] = revision

        try:
            prepared = adapter.prepare(copy.deepcopy(candidate_chain))
            if not isinstance(prepared, PreparedCommit):
                raise ChainStateError("prepare must return PreparedCommit")
            prepared_chain = _copy_mapping(prepared.chain)
            _validate_chain_shape(prepared_chain)
            if prepared.revision != prepared_chain.get("revision"):
                raise ChainStateError("prepared chain revision does not match its plan")
            if revision is not None and prepared.revision != revision:
                raise ChainStateError("prepared revision does not match requested revision")
            if _persistent_slots(prepared_chain) != _persistent_slots(candidate_chain):
                raise ChainStateError("prepare cannot change Slot order or paths")
        except Exception:
            self._restore_state(before)
            raise

        write_started = False
        try:
            write_started = True
            receipt_value = adapter.write_file(copy.deepcopy(prepared_chain))
            receipt = self._coerce_receipt(receipt_value)
            committed_fingerprint = fingerprint if fingerprint is not None else receipt.fingerprint
            committed_revision = revision if revision is not None else receipt.revision
            self._validate_managed_metadata(committed_fingerprint, committed_revision)
            if committed_revision is None:
                raise ChainStateError("managed write must return a revision receipt")
            if committed_revision != prepared.revision:
                raise ChainStateError("file receipt revision does not match prepared runtime")
            if committed_fingerprint is None and committed_revision is not None:
                committed_fingerprint = chain_fingerprint(prepared_chain)
            adapter.apply_runtime(prepared)
        except Exception as exc:
            rollback_errors: list[Exception] = []
            if write_started:
                try:
                    adapter.restore_file(copy.deepcopy(previous_chain))
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
                try:
                    adapter.restore_runtime(previous_runtime)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            self._restore_state(before)
            if rollback_errors:
                raise ChainStateRollbackError(exc, rollback_errors) from exc
            raise

        working._chain = _chain_with_slots(
            prepared_chain, [slot.path for slot in working._slots])
        self._restore_state(working)
        self.mark_managed_write(committed_fingerprint, committed_revision)
        return self.to_chain()

    def _coerce_receipt(self, value: Any) -> CommitReceipt:
        if isinstance(value, CommitReceipt):
            return value
        if isinstance(value, Mapping):
            fingerprint = value.get("fingerprint")
            revision = value.get("revision")
            return CommitReceipt(fingerprint=fingerprint, revision=revision)
        raise ChainStateError("managed write must return a commit receipt")

    @staticmethod
    def _validate_managed_metadata(fingerprint: str | None,
                                   revision: int | None) -> None:
        if revision is not None and (
                isinstance(revision, bool) or not isinstance(revision, int)
                or revision < 0):
            raise ChainStateError("managed revision must be a non-negative integer")
        if fingerprint is not None and (
                not isinstance(fingerprint, str) or not fingerprint):
            raise ChainStateError("managed fingerprint must be a non-empty string")

    def _mark_local_mutation(self) -> None:
        self._managed_fingerprint = None
        self._managed_revision = None
        self._chain_error = None

    def _slot_snapshot(self, index: int, slot: _Slot) -> SlotSnapshot:
        return SlotSnapshot(
            index=index,
            path=slot.path,
            candidate=slot.candidate,
            status=slot.status,
            overlay=slot.overlay,
            error=slot.error,
            operation_id=slot.operation_id,
        )

    def _check_index(self, index: int) -> int:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ChainStateError("slot index must be an integer")
        if index < 0 or index >= len(self._slots):
            raise ChainStateError(f"slot index {index} is out of range")
        return index

    def _copy_state(self) -> "ChainState":
        other = ChainState.__new__(ChainState)
        other._chain = copy.deepcopy(self._chain)
        other._slots = copy.deepcopy(self._slots)
        target_index = self.target_index
        other._target = (other._slots[target_index]
                         if target_index is not None else None)
        other._managed_fingerprint = self._managed_fingerprint
        other._managed_revision = self._managed_revision
        other._chain_error = self._chain_error
        other._next_operation_id = self._next_operation_id
        return other

    def _restore_state(self, other: "ChainState") -> None:
        self._chain = copy.deepcopy(other._chain)
        self._slots = copy.deepcopy(other._slots)
        target_index = other.target_index
        self._target = (self._slots[target_index]
                        if target_index is not None else None)
        self._managed_fingerprint = other._managed_fingerprint
        self._managed_revision = other._managed_revision
        self._chain_error = other._chain_error
        self._next_operation_id = other._next_operation_id


__all__ = [
    "ChainSnapshot",
    "ChainState",
    "ChainStateError",
    "ChainStateRollbackError",
    "CommitReceipt",
    "MAX_SLOTS",
    "ManagedCommitAdapter",
    "PreparedCommit",
    "SlotOverlay",
    "SlotSnapshot",
    "SlotStatus",
    "chain_fingerprint",
]
