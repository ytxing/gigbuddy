"""Canonical live-chain protocol for GigBuddy v0.2.

The protocol is deliberately independent from the TUI, SQLite and realtime
engine.  Callers work with absolute paths in memory; only this module emits
the project-relative ``slots[]`` representation on disk.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
import warnings

try:
    import fcntl
except ImportError:  # pragma: no cover - the project runs on POSIX hosts
    fcntl = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TONES_DIR_NAME = "data/tones"
DRY_INPUTS_DIR_NAME = "data/dry_inputs"

DEFAULTS = {
    "gain": 1.0,
    "master": 1.0,
    "quality": 1.0,
    "mute": False,
    "revision": 0,
}
PLAY_STATES = {"playing", "paused", "stopped"}
SUPPORTED_EXTENSIONS = {".nam", ".wav"}


class ChainProtocolError(ValueError):
    """Raised when a candidate chain cannot be represented canonically."""


class ChainFileConflict(ChainProtocolError):
    """Raised when a compare-and-swap write sees a different chain file."""


_UNSET = object()


@contextmanager
def chain_file_lock(path: Path):
    """Serialize writers that participate in the chain-file CAS protocol."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def file_fingerprint(path: Path) -> str | None:
    """Return the SHA-256 fingerprint of one exact file payload."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _file_payload(path: Path) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def _encoded_chain(chain: dict[str, Any], *, root: Path) -> bytes:
    payload = _serializable_chain(chain, root=root)
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def serialized_chain_fingerprint(chain: dict[str, Any], *, root: Path = PROJECT_ROOT) -> str:
    """Return the fingerprint of the bytes emitted by a canonical write."""
    return hashlib.sha256(_encoded_chain(chain, root=Path(root).resolve())).hexdigest()


def _number(value: Any, name: str, lower: float, upper: float) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChainProtocolError(f"{name} must be a finite number")
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ChainProtocolError(f"{name} must be between {lower} and {upper}")
    return value


def _rooted(path: str | os.PathLike[str], root: Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else root / value


def _allowed_file(path: Any, *, root: Path, directory: str,
                  extensions: set[str], require_exists: bool = True
                  ) -> tuple[Path, str]:
    if not isinstance(path, str) or not path:
        raise ChainProtocolError("file path must be a non-empty string")
    candidate = _rooted(path, root)
    try:
        resolved = candidate.resolve(strict=require_exists)
        base = (root / directory).resolve(strict=False)
        resolved.relative_to(base)
    except (OSError, ValueError) as exc:
        raise ChainProtocolError(f"path is outside {directory}: {path}") from exc
    if require_exists and not resolved.is_file():
        raise ChainProtocolError(f"path is not a regular file: {path}")
    if not require_exists:
        try:
            if candidate.exists() and not resolved.is_file():
                raise ChainProtocolError(f"path is not a regular file: {path}")
        except OSError as exc:
            raise ChainProtocolError(f"path cannot be inspected: {path}") from exc
    if resolved.suffix.lower() not in extensions:
        raise ChainProtocolError(f"unsupported file format: {path}")
    return resolved, str(resolved.relative_to(root))


def _normalize_input(value: Any, *, root: Path) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ChainProtocolError("input must be an object")
    output = {
        key: item for key, item in value.items()
        if key not in {"source", "file", "state", "loop"}
    }
    source = value.get("source", "instrument")
    if not isinstance(source, str) or source not in {"instrument", "file"}:
        raise ChainProtocolError("input.source must be instrument or file")
    state = value.get("state", "stopped")
    if not isinstance(state, str) or state not in PLAY_STATES:
        raise ChainProtocolError("input.state is invalid")
    loop = value.get("loop", False)
    if not isinstance(loop, bool):
        raise ChainProtocolError("input.loop must be boolean")
    file_value = value.get("file")
    if source == "instrument":
        if file_value is not None or state != "stopped" or loop:
            raise ChainProtocolError("instrument input cannot have file, playback or loop state")
        output.update({"source": "instrument", "file": None,
                       "state": "stopped", "loop": False})
        return output
    if file_value is None:
        raise ChainProtocolError("file input requires input.file")
    absolute, _ = _allowed_file(
        file_value, root=root, directory=DRY_INPUTS_DIR_NAME,
        extensions={".wav"}, require_exists=False
    )
    output.update({"source": "file", "file": str(absolute),
                   "state": state, "loop": loop})
    return output


def normalize_chain(candidate: Any, *, root: Path = PROJECT_ROOT,
                    revision: int | None = None) -> dict[str, Any]:
    """Validate and return the canonical in-memory chain.

    Paths in the returned value are absolute so existing TUI/DB lookup code can
    continue comparing them.  Unknown top-level fields are preserved; unknown
    fields inside Slot/input objects are intentionally removed on write.
    """
    if not isinstance(candidate, dict):
        raise ChainProtocolError("chain must be an object")
    root = Path(root).resolve()
    output = {k: v for k, v in candidate.items()
              if k not in {"model", "ir", "slots", "input", "gain", "master",
                           "quality", "mute", "revision"}}

    has_slots = "slots" in candidate
    has_legacy_fields = any(key in candidate for key in ("model", "ir"))
    if has_slots and has_legacy_fields:
        warnings.warn(
            "live chain contains slots and legacy model/ir fields; "
            "slots take precedence",
            RuntimeWarning,
            stacklevel=2,
        )

    if has_slots:
        raw_slots = candidate["slots"]
    else:
        raw_slots = []
        for legacy_key in ("model", "ir"):
            legacy = candidate.get(legacy_key)
            if legacy is not None:
                raw_slots.append({"path": legacy})
    if not isinstance(raw_slots, list) or len(raw_slots) > 6:
        raise ChainProtocolError("slots must contain between 0 and 6 items")
    slots: list[dict[str, str | None]] = []
    for index, item in enumerate(raw_slots):
        if not isinstance(item, dict):
            raise ChainProtocolError(f"slot {index} must be an object")
        if "path" not in item:
            raise ChainProtocolError(f"slot {index} must contain path")
        path = item["path"]
        extra = {key: value for key, value in item.items() if key != "path"}
        if path is None:
            slots.append({"path": None, **extra})
            continue
        absolute, _ = _allowed_file(
            path, root=root, directory=TONES_DIR_NAME,
            extensions=SUPPORTED_EXTENSIONS,
        )
        slots.append({"path": str(absolute), **extra})
    output["slots"] = slots
    output["gain"] = _number(candidate.get("gain", DEFAULTS["gain"]), "gain", 0, 10)
    master = _number(candidate.get("master", DEFAULTS["master"]), "master", 0, 10)
    output["quality"] = _number(candidate.get("quality", DEFAULTS["quality"]), "quality", 0, 1)
    mute = candidate.get("mute", DEFAULTS["mute"])
    if not isinstance(mute, bool):
        raise ChainProtocolError("mute must be boolean")
    # v0.1 encoded MUTE as master=0 because the explicit mute field did not
    # exist. Preserve that meaning only when the field is absent; a v0.2
    # writer that wants master=0 must emit mute=false explicitly.
    if "mute" not in candidate and master == 0:
        master = DEFAULTS["master"]
        mute = True
    output["master"] = master
    output["mute"] = mute
    old_revision = candidate.get("revision", DEFAULTS["revision"])
    if isinstance(old_revision, bool) or not isinstance(old_revision, int) or old_revision < 0:
        raise ChainProtocolError("revision must be a non-negative integer")
    new_revision = old_revision if revision is None else revision
    if isinstance(new_revision, bool) or not isinstance(new_revision, int) or new_revision < 0:
        raise ChainProtocolError("revision must be a non-negative integer")
    output["revision"] = new_revision
    output["input"] = _normalize_input(candidate.get("input"), root=root)
    return output


def _serializable_chain(chain: dict[str, Any], *, root: Path) -> dict[str, Any]:
    normalized = normalize_chain(chain, root=root)
    result = {k: v for k, v in normalized.items() if k != "slots"}
    result["slots"] = []
    for slot in normalized["slots"]:
        path = slot["path"]
        result["slots"].append({"path": None if path is None else
                                 str(Path(path).resolve().relative_to(root))})
    # Unknown input fields are readable compatibility data, but canonical
    # writes intentionally emit only the protocol-owned fields.
    input_value = {
        key: result["input"][key]
        for key in ("source", "file", "state", "loop")
    }
    result["input"] = input_value
    if input_value["source"] == "file" and input_value["file"]:
        input_value["file"] = str(Path(input_value["file"]).resolve().relative_to(root))
    return result


def read_chain_file(path: Path, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Read and normalize one chain file; missing files return an empty chain."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ChainProtocolError(f"invalid chain file: {path}") from exc
    return normalize_chain(raw, root=root)


def write_chain_file(path: Path, candidate: dict[str, Any], *, root: Path = PROJECT_ROOT,
                     expected_fingerprint: str | None | object = _UNSET,
                     expected_revision: int | None | object = _UNSET,
                     revision: int | None = None) -> dict[str, Any]:
    """Validate and atomically replace a chain file.

    When expected metadata is supplied this is a compare-and-swap write: a
    concurrent or external update is rejected before the temporary file is
    renamed.  The default call keeps the original increment-on-write API.
    """
    path = Path(path)
    with chain_file_lock(path):
        current_payload = _file_payload(path)
        current_fingerprint = (None if current_payload is None else
                               hashlib.sha256(current_payload).hexdigest())
        if (expected_fingerprint is not _UNSET
                and current_fingerprint != expected_fingerprint):
            raise ChainFileConflict(
                f"chain file changed externally (expected {expected_fingerprint}, "
                f"found {current_fingerprint})")

        current_revision = 0
        current = {}
        if current_payload is not None:
            # A present but malformed file is not a safe CAS base.  In particular,
            # never turn an invalid external update into revision 1.
            current = read_chain_file(path, root=root)
            current_revision = int(current.get("revision", 0))
        if (expected_revision is not _UNSET
                and current_revision != expected_revision):
            raise ChainFileConflict(
                f"chain revision changed externally (expected {expected_revision}, "
                f"found {current_revision})")

        next_revision = current_revision + 1 if revision is None else revision
        if (isinstance(next_revision, bool) or not isinstance(next_revision, int)
                or next_revision < 0):
            raise ChainProtocolError("revision must be a non-negative integer")
        if revision is not None and next_revision != current_revision + 1:
            raise ChainFileConflict(
                f"prepared revision {next_revision} does not follow current "
                f"revision {current_revision}")
        normalized = normalize_chain(candidate, root=root, revision=next_revision)
        payload = _encoded_chain(normalized, root=Path(root).resolve())
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return normalized
