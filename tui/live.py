"""File channel between the GigBuddy TUI and the realtime engine."""
from copy import deepcopy
import json
import hashlib
import math
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - GigBuddy targets POSIX terminals
    fcntl = None

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import chain_protocol  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CHAIN_FILE = ROOT / "data" / "live_chain.json"
LEVEL_FILE = ROOT / "data" / "level.json"
CONTROL_FILE = ROOT / "data" / "live_control.json"
CONTROL_REPLY_FILE = ROOT / "data" / "live_control.reply.json"
TONES_DIR = ROOT / "data" / "tones"
DRY_INPUTS_DIR = ROOT / "data" / "dry_inputs"

# 干声输入源（live_chain.json 的 input 键）的播放状态常量（与引擎协议一致）
PLAY_STOPPED, PLAY_PLAYING, PLAY_PAUSED = "stopped", "playing", "paused"

# Chain parameter defaults are part of the live protocol.  UI reset actions,
# missing-key fallbacks, and preset-facing views must all use the same values.
CHAIN_PARAMETER_DEFAULTS = {
    "gain": 1.0,
    "master": 1.0,
    "quality": 1.0,
}

# The TUI keeps bypass recovery candidates in process memory.  This marker lets
# the UI distinguish its own atomic writes from a direct edit by another
# process, even when both writes leave a slot set to null.
_last_chain_write_fingerprint: str | None = None
_last_chain_write_path: Path | None = None
_engine_lock_handle = None

# Keep the last successfully normalized chain per file path.  The TUI polls
# this file while another process may be writing it, so a malformed external
# update must not replace a usable in-memory chain with an empty one.
_chain_cache: dict[Path, dict] = {}
_chain_errors: dict[Path, str] = {}
_chain_error_signatures: dict[Path, tuple[str, str | None]] = {}
_UNSET = object()

# Tone-chain node definitions (amp/ir live nodes + placeholder effects)
CHAIN_ORDER = [
    ("amp", "AMP", "guitar → amp model"),
    ("ir", "CAB", "cab simulation"),
    ("comp", "COMP", "compressor (phase 2)"),
    ("od", "OD", "overdrive (phase 2)"),
    ("delay", "DELAY", "delay (phase 2)"),
    ("reverb", "REVERB", "reverb (phase 2)"),
]


def _chain_path() -> Path:
    return Path(CHAIN_FILE)


def acquire_engine_lock():
    """Hold the managed-engine lock for one TUI process."""
    global _engine_lock_handle
    if _engine_lock_handle is not None:
        return _engine_lock_handle
    path = Path(ROOT) / "data" / ".gigbuddy-engine.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise RuntimeError("another managed GigBuddy instance is running") from exc
    _engine_lock_handle = handle
    return handle


def release_engine_lock() -> None:
    global _engine_lock_handle
    handle = _engine_lock_handle
    _engine_lock_handle = None
    if handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _remember_chain(path: Path, chain: dict) -> None:
    _chain_cache[path] = deepcopy(chain)
    _chain_errors.pop(path, None)
    _chain_error_signatures.pop(path, None)


def _record_chain_error(path: Path, error: Exception) -> None:
    message = str(error) or type(error).__name__
    signature = (message, _file_fingerprint(path))
    if _chain_error_signatures.get(path) == signature:
        return
    _chain_error_signatures[path] = signature
    _chain_errors[path] = message


def read_chain() -> dict:
    """Read current chain config, retaining the last valid value on failure.

    Canonical ``slots[]`` paths are returned as absolute paths for in-memory
    TUI/DB comparisons; legacy ``model/ir`` is normalized on read.
    """
    path = _chain_path()
    try:
        chain = chain_protocol.read_chain_file(path, root=ROOT)
    except (OSError, UnicodeError, chain_protocol.ChainProtocolError) as exc:
        _record_chain_error(path, exc)
        cached = _chain_cache.get(path)
        return deepcopy(cached) if cached is not None else {}
    _remember_chain(path, chain)
    return deepcopy(chain)


def read_chain_snapshot() -> tuple[dict, str | None]:
    """Read a chain and its exact file fingerprint from one writer snapshot.

    The file-only commit path needs both values for CAS. Reading them through
    separate calls can pair a chain from one write with the fingerprint from a
    later write, so keep the lock held while reading and normalizing one exact
    payload.
    """
    path = _chain_path()
    with chain_protocol.chain_file_lock(path):
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            _remember_chain(path, {})
            return {}, None
        except OSError as exc:
            _record_chain_error(path, exc)
            cached = _chain_cache.get(path)
            return (deepcopy(cached) if cached is not None else {}, None)

        fingerprint = hashlib.sha256(payload).hexdigest()
        try:
            chain = chain_protocol.normalize_chain(
                json.loads(payload.decode("utf-8")), root=ROOT)
        except (UnicodeError, json.JSONDecodeError,
                chain_protocol.ChainProtocolError) as exc:
            _record_chain_error(path, exc)
            cached = _chain_cache.get(path)
            chain = deepcopy(cached) if cached is not None else {}
        else:
            _remember_chain(path, chain)
        return deepcopy(chain), fingerprint


def consume_chain_error() -> str | None:
    """Return and clear the current chain read error for the main App."""
    return _chain_errors.pop(_chain_path(), None)


def write_chain(cfg: dict, *, expected_fingerprint: str | None | object = _UNSET,
                expected_revision: int | None | object = _UNSET,
                revision: int | None = None) -> dict:
    """Write chain config (tmp+rename atomic; engine hot-swaps within 0.3s).

    The protocol module validates and atomically writes canonical ``slots[]``.
    """
    path = _chain_path()
    kwargs = {}
    if expected_fingerprint is not _UNSET:
        kwargs["expected_fingerprint"] = expected_fingerprint
    if expected_revision is not _UNSET:
        kwargs["expected_revision"] = expected_revision
    if revision is not None:
        kwargs["revision"] = revision
    normalized = chain_protocol.write_chain_file(path, cfg, root=ROOT, **kwargs)
    _remember_chain(path, normalized)
    global _last_chain_write_fingerprint, _last_chain_write_path
    _last_chain_write_fingerprint = chain_file_fingerprint()
    _last_chain_write_path = path
    return normalized


def restore_chain_bytes(payload: bytes | None, *,
                        expected_fingerprint: str | None | object = _UNSET) -> None:
    """Restore the exact pre-commit payload without overwriting a new writer."""
    path = _chain_path()
    with chain_protocol.chain_file_lock(path):
        current_fingerprint = chain_file_fingerprint()
        if (expected_fingerprint is not _UNSET
                and current_fingerprint != expected_fingerprint):
            raise chain_protocol.ChainFileConflict(
                f"cannot restore chain after external update (expected "
                f"{expected_fingerprint}, found {current_fingerprint})")
        if payload is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            _chain_cache.pop(path, None)
            _chain_errors.pop(path, None)
            _chain_error_signatures.pop(path, None)
        else:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".restore", dir=path.parent)
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
            _remember_chain(path, chain_protocol.read_chain_file(path, root=ROOT))
    global _last_chain_write_fingerprint, _last_chain_write_path
    _last_chain_write_fingerprint = chain_file_fingerprint()
    _last_chain_write_path = path


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def request_runtime_prepare(chain: dict, transaction_id: str, *,
                            timeout: float = 2.0,
                            poll_interval: float = 0.02) -> str:
    """Ask the managed engine to load a candidate without publishing it."""
    if not isinstance(transaction_id, str) or not transaction_id:
        raise ValueError("managed prepare requires a transaction id")
    session_id = wait_for_engine_ready(
        timeout=timeout, poll_interval=poll_interval)
    candidate_revision = chain.get("revision")
    _write_json_atomic(CONTROL_FILE, {
        "operation": "prepare",
        "transaction_id": transaction_id,
        "revision": candidate_revision,
        "candidate": chain,
    })
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = json.loads(CONTROL_REPLY_FILE.read_text())
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            response = None
        if isinstance(response, dict):
            if response.get("transaction_id") != transaction_id:
                time.sleep(poll_interval)
                continue
            response_session = response.get("session_id")
            if response_session != session_id:
                time.sleep(poll_interval)
                continue
            if response.get("revision") != candidate_revision:
                raise RuntimeError("managed engine returned the wrong prepare revision")
            status = response.get("status")
            if status == "prepared":
                return session_id
            if status == "rejected":
                raise RuntimeError(
                    f"managed engine rejected candidate: "
                    f"{response.get('error') or 'preparation failed'}")
        time.sleep(poll_interval)
    raise TimeoutError(
        f"timed out waiting for managed prepare transaction {transaction_id}")


def request_output_calibration(slot_index: int, *, timeout: float = 2.0,
                               poll_interval: float = 0.02) -> float:
    """Ask the managed engine for one active NAM Slot's output trim."""
    if isinstance(slot_index, bool) or not isinstance(slot_index, int):
        raise ValueError("Slot calibration requires an integer index")
    if slot_index < 0 or slot_index >= 6:
        raise ValueError("Slot calibration index is out of range")
    session_id = wait_for_engine_ready(
        timeout=timeout, poll_interval=poll_interval)
    request_id = uuid.uuid4().hex
    _write_json_atomic(CONTROL_FILE, {
        "operation": "calibrate_output",
        "request_id": request_id,
        "slot_index": slot_index,
    })
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = json.loads(CONTROL_REPLY_FILE.read_text())
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            response = None
        if isinstance(response, dict):
            if response.get("request_id") != request_id:
                time.sleep(poll_interval)
                continue
            if response.get("session_id") != session_id:
                time.sleep(poll_interval)
                continue
            if response.get("status") == "calibrated":
                value = response.get("output_gain_db")
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or not chain_protocol.SLOT_GAIN_MIN_DB <= value
                        <= chain_protocol.SLOT_GAIN_MAX_DB):
                    raise RuntimeError("engine returned an invalid output recommendation")
                return float(value)
            if response.get("status") == "rejected":
                raise RuntimeError(
                    f"output calibration failed: "
                    f"{response.get('error') or 'request rejected'}")
        time.sleep(poll_interval)
    raise TimeoutError(
        f"timed out waiting for output calibration Slot {slot_index + 1:02d}")


def wait_for_engine_ready(*, timeout: float = 2.0,
                          poll_interval: float = 0.02) -> str:
    """Wait for the current managed engine session before sending control."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = json.loads(CONTROL_REPLY_FILE.read_text())
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            response = None
        if (isinstance(response, dict)
                and response.get("status") in {"ready", "prepared", "rejected"}
                and isinstance(response.get("session_id"), str)
                and response["session_id"]):
            # The engine emits ``ready`` once per process, then replaces the
            # same sidecar with each prepare result.  A later prepare must
            # reuse that live session instead of waiting for a second ready
            # message that the engine never emits.
            return response["session_id"]
        time.sleep(poll_interval)
    raise TimeoutError("timed out waiting for managed engine ready")


def _file_fingerprint(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def chain_file_fingerprint() -> str | None:
    """Return the current chain bytes' fingerprint, or ``None`` if unreadable."""
    return _file_fingerprint(_chain_path())


def level_file_fingerprint() -> str | None:
    """Return the current runtime report fingerprint for commit waits."""
    return _file_fingerprint(LEVEL_FILE)


def read_runtime_report() -> dict[str, object]:
    """Read runtime acknowledgement identity from the telemetry file."""
    try:
        d = json.loads(LEVEL_FILE.read_text())
        revision = d.get("runtime_revision")
        if (isinstance(revision, bool)
                or not isinstance(revision, int) or revision < 0):
            revision = None
        status = d.get("runtime_status")
        if status not in {"applied", "rejected", "unknown"}:
            status = "unknown"
        transaction_id = d.get("runtime_transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            transaction_id = None
        session_id = d.get("runtime_session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = None
        ack_seq = d.get("runtime_ack_seq")
        if (isinstance(ack_seq, bool)
                or not isinstance(ack_seq, int) or ack_seq < 0):
            ack_seq = None
        return {
            "revision": revision,
            "status": status,
            "transaction_id": transaction_id,
            "session_id": session_id,
            "ack_seq": ack_seq,
        }
    except Exception:
        return {
            "revision": None,
            "status": "unknown",
            "transaction_id": None,
            "session_id": None,
            "ack_seq": None,
        }


def wait_for_runtime_revision(
        revision: int, *, transaction_id: str | None = None,
        expected_session_id: str | None = None,
        previous: tuple[dict[str, object] | tuple[int | None, str], str | None] | None = None,
        timeout: float = 2.0, poll_interval: float = 0.02) -> None:
    """Wait for a fresh acknowledgement belonging to one transaction.

    ``level.json`` is rewritten for every meter tick, so its byte fingerprint
    is deliberately not part of the acknowledgement signal.  The optional
    transaction id and monotonic ack sequence make an old applied/rejected
    report impossible to consume as the current commit.
    """
    if expected_session_id is not None and (
            not isinstance(expected_session_id, str) or not expected_session_id):
        raise ValueError("expected runtime session must be a non-empty string")
    if expected_session_id is not None and (
            not isinstance(transaction_id, str) or not transaction_id):
        raise ValueError(
            "expected runtime session requires a transaction id")
    previous_report = previous[0] if previous else None
    if isinstance(previous_report, tuple):
        previous_report = {
            "revision": previous_report[0],
            "status": previous_report[1],
            "transaction_id": None,
            "session_id": None,
            "ack_seq": None,
        }
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        report = read_runtime_report()
        same_report = (previous_report is not None
                       and report == previous_report)
        previous_ack = (previous_report.get("ack_seq")
                        if isinstance(previous_report, dict) else None)
        current_ack = report.get("ack_seq")
        fresh = not same_report
        previous_session = (previous_report.get("session_id")
                            if isinstance(previous_report, dict) else None)
        current_session = report.get("session_id")
        session_matches = (
            expected_session_id is None
            or current_session == expected_session_id
        )
        session_changed = (isinstance(current_session, str)
                           and current_session != previous_session)
        session_changed_to_expected = (
            expected_session_id is not None
            and current_session == expected_session_id
            and current_session != previous_session
        )
        if expected_session_id is not None:
            # Managed commits are bound to the session captured by prepare.
            # A session restart resets ack_seq, so the first report from the
            # expected session establishes a new baseline. Within one session
            # the sequence must still advance beyond the pre-commit report.
            fresh = (
                session_matches
                and isinstance(current_ack, int)
                and (session_changed_to_expected
                     or (isinstance(previous_ack, int)
                         and current_ack > previous_ack))
            )
        elif session_changed:
            # A restarted managed engine starts ack_seq from zero again. A
            # new session is itself a fresh acknowledgement baseline.
            fresh = True
        if (isinstance(previous_ack, int) and isinstance(current_ack, int)):
            if not session_changed and not session_changed_to_expected:
                fresh = current_ack > previous_ack
        identity_matches = (
            session_matches
            and (transaction_id is None
                 or report.get("transaction_id") == transaction_id)
        )
        revision_matches = report.get("revision") == revision
        if (report.get("revision") == revision
                and report.get("status") == "applied"
                and identity_matches and fresh):
            return
        if (report.get("status") == "rejected" and fresh
                and identity_matches
                and (revision_matches if expected_session_id is not None else
                     transaction_id is not None or revision_matches)):
            raise RuntimeError(
                f"managed runtime rejected revision {revision} "
                f"(runtime revision {report.get('revision')})")
        time.sleep(poll_interval)
    raise TimeoutError(f"timed out waiting for managed revision {revision}")


def last_chain_write_fingerprint() -> str | None:
    """Return the last fingerprint written through this Python process."""
    if _last_chain_write_path != _chain_path():
        return None
    return _last_chain_write_fingerprint


def read_levels() -> tuple[float, float, str, float]:
    """Read engine levels (in, out, play_state, play_pos_sec); 0/stopped on missing/broken file"""
    try:
        d = json.loads(LEVEL_FILE.read_text())
        return (float(d.get("in", 0.0)), float(d.get("out", 0.0)),
                d.get("play_state", PLAY_STOPPED), float(d.get("play_pos", 0.0)))
    except Exception:
        return 0.0, 0.0, PLAY_STOPPED, 0.0


def read_runtime_status() -> tuple[int | None, str]:
    """Read the optional file/runtime revision report from ``level.json``.

    Older or externally managed engines may only write level and playback
    fields.  Treat that as an explicit unknown state instead of claiming that
    the file revision reached the DSP runtime.
    """
    report = read_runtime_report()
    return report["revision"], report["status"]


def chain_input(chain: dict) -> dict:
    """当前链的 input 键（缺省 = 乐器输入）"""
    inp = chain.get("input")
    return inp if isinstance(inp, dict) else {"source": "instrument"}


def short_name(path: str) -> str:
    """Path → display name (basename without extension)"""
    return os.path.basename(path)
