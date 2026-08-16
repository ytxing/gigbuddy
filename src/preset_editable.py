"""User-editable Preset persistence and crash recovery.

This module owns the SQLite/file recovery protocol for editable Presets.  It
does not scan repository Presets, download models, or derive availability.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import preset_document
from preset_bundled import preset_owned_by_bundle
import tone3000


PRESET_DOCUMENT_KIND = "gigbuddy-preset"
SHAREABLE_PRESET_DOCUMENT_KIND = "gigbuddy-shareable-preset"
PRESET_FILE_SETTING_PREFIX = "preset_file:"
PRESET_UPDATED_UNSET = object()


class PresetConflictError(ValueError):
    """An edit was based on an older version of a Preset row."""


class PresetRecoveryError(RuntimeError):
    """An editable Preset mutation failed and could not restore its files."""

    def __init__(self, original: BaseException,
                 recovery_errors: list[BaseException]) -> None:
        self.original = original
        self.recovery_errors = tuple(recovery_errors)
        super().__init__(
            f"Preset mutation failed and file recovery was incomplete: {original}; "
            f"recovery errors: {', '.join(str(error) for error in recovery_errors)}"
        )


class _EditableSnapshotChanged(RuntimeError):
    """An editable file changed while one reconciliation pass was reading it."""


def ensure_preset_mutable(row: Any | None) -> None:
    if preset_owned_by_bundle(row):
        raise ValueError(
            f"Built-in Preset '{row['name']}' is read-only; save a copy "
            "with a new name to edit it.")


def preset_file_key(preset_id: int) -> str:
    return f"{PRESET_FILE_SETTING_PREFIX}{preset_id}"


def preset_file_token(path: Path) -> str:
    stat = path.stat()
    return (
        f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:"
        f"{stat.st_mtime_ns}:{stat.st_ctime_ns}"
    )


def preset_file_identity(path: Path) -> tuple[int, int, int, int]:
    """Return fields that survive this module's own rename/link protocol."""
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def preset_file_matches_token(path: Path, expected: str) -> bool:
    """Match current tokens and the three-field token written before v1.2.4."""
    stat = path.stat()
    current = (
        f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:"
        f"{stat.st_mtime_ns}:{stat.st_ctime_ns}"
    )
    legacy = f"{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"
    return expected == current or expected == legacy


def preset_file_token_is_current(token: object) -> bool:
    """Return whether a stored token has the v1.2.4 five-field shape."""
    if not isinstance(token, str):
        return False
    fields = token.split(":")
    if len(fields) != 5:
        return False
    try:
        return all(int(field) >= 0 for field in fields)
    except ValueError:
        return False


def preset_filename(preset_id: int, name: str) -> str:
    return f"{preset_id}-{tone3000.slugify(name, 64)}.json"


def preset_file_path(presets_dir: Path, preset_id: int, name: str) -> Path:
    return Path(presets_dir) / preset_filename(preset_id, name)


def tracked_preset_files(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE ?",
        (f"{PRESET_FILE_SETTING_PREFIX}%",),
    ).fetchall()
    tracked: dict[int, dict] = {}
    for row in rows:
        try:
            preset_id = int(row["key"][len(PRESET_FILE_SETTING_PREFIX):])
            value = json.loads(row["value"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and isinstance(value.get("file"), str):
            tracked[preset_id] = value
    return tracked


def quarantine_preset_file(path: Path, presets_dir: Path) -> Path | None:
    """Move an unowned JSON conflict aside without destroying user data."""
    quarantine = Path(presets_dir) / ".quarantine"
    target = quarantine / path.name
    try:
        quarantine.mkdir(parents=True, exist_ok=True)
        counter = 1
        while True:
            try:
                target.touch(exist_ok=False)
                break
            except FileExistsError:
                target = quarantine / f"{path.stem}-{counter}{path.suffix}"
                counter += 1
        path.replace(target)
        return target
    except OSError as exc:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        warnings.warn(
            f"Could not quarantine conflicting Preset file {path}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return None


def interrupted_transaction_preset_id(path: Path) -> int | None:
    """Return the Preset id encoded by one Catalog transaction directory."""
    name = Path(path).name
    prefix = ".preset-"
    if not name.startswith(prefix):
        return None
    raw_id, separator, _suffix = name[len(prefix):].partition("-")
    if not separator or not raw_id.isdigit():
        return None
    return int(raw_id)


def is_shareable_preset_file(path: Path) -> bool:
    """Identify an untracked share file without validating or importing it."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (isinstance(document, dict)
            and document.get("kind") == SHAREABLE_PRESET_DOCUMENT_KIND)


def _write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _warn_after_commit(message: str, *, stacklevel: int) -> None:
    """Report best-effort cleanup without turning a committed write into failure."""
    try:
        warnings.warn(
            message,
            RuntimeWarning,
            stacklevel=stacklevel,
        )
    except Exception:
        # Warning filters may promote RuntimeWarning to an exception. At this
        # point durable state is already committed, so the public operation
        # must still report its actual outcome.
        pass


class _EditableFileTransaction:
    """Recover one editable Preset's files around a SQLite commit."""

    def __init__(self, presets_dir: Path, preset_id: int) -> None:
        self.presets_dir = Path(presets_dir)
        self.preset_id = preset_id
        self.directory: Path | None = None
        self.backups: list[tuple[Path, Path]] = []
        self.published: Path | None = None
        self.published_identity: tuple[int, int, int, int] | None = None
        self.database_committed = False
        self.existing_isolated = False

    def __enter__(self) -> "_EditableFileTransaction":
        self.presets_dir.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(
            prefix=f".preset-{self.preset_id}-", dir=self.presets_dir))
        return self

    def publish(
            self, target: Path, document: dict, *,
            expected_files: dict[Path, str] | None = None,
    ) -> tuple[Path, str]:
        if self.directory is None:
            raise RuntimeError("Preset file transaction has not started")
        target = Path(target)
        staged = self.directory / "staged.json"
        _write_json_atomic(staged, document)
        staged_identity = preset_file_identity(staged)
        self.isolate_expected(expected_files or {})
        self.isolate_existing()
        try:
            # A hard link publishes the fully-written inode atomically and,
            # unlike replace(), refuses to overwrite a file created by an
            # external editor after isolation.
            os.link(staged, target)
        except FileExistsError as exc:
            raise _EditableSnapshotChanged(
                f"Preset file changed during reconciliation: {target}") from exc
        self.published = target
        self.published_identity = staged_identity
        if preset_file_identity(target) != staged_identity:
            raise _EditableSnapshotChanged(
                f"Preset file changed during reconciliation: {target}")
        staged.unlink()
        return target, preset_file_token(target)

    def isolate_expected(self, expected_files: dict[Path, str]) -> None:
        """Isolate and verify files captured by the caller's read snapshot."""
        for expected_path, expected_token in (expected_files or {}).items():
            expected_path = Path(expected_path)
            try:
                if not preset_file_matches_token(expected_path, expected_token):
                    raise _EditableSnapshotChanged(
                        f"Preset file changed during reconciliation: "
                        f"{expected_path}")
                expected_identity = preset_file_identity(expected_path)
            except OSError as exc:
                raise _EditableSnapshotChanged(
                    f"Preset file changed during reconciliation: "
                    f"{expected_path}") from exc
            self.isolate(expected_path)
            backup = next((backup for original, backup in self.backups
                           if original == expected_path), None)
            if (backup is None
                    or preset_file_identity(backup) != expected_identity):
                raise _EditableSnapshotChanged(
                    f"Preset file changed during reconciliation: {expected_path}")

    def isolate_existing(self) -> None:
        if self.directory is None:
            raise RuntimeError("Preset file transaction has not started")
        if self.existing_isolated:
            raise RuntimeError("Preset files have already been isolated")
        self.existing_isolated = True
        prefix = f"{self.preset_id}-"
        for current in sorted(self.presets_dir.glob(f"{prefix}*.json")):
            self.isolate(current)

    def isolate(self, path: Path) -> None:
        if self.directory is None:
            raise RuntimeError("Preset file transaction has not started")
        path = Path(path)
        if not path.exists() or any(
                original == path for original, _backup in self.backups):
            return
        backup = self.directory / f"backup-{len(self.backups)}-{path.name}"
        path.replace(backup)
        self.backups.append((path, backup))

    def mark_database_committed(self) -> None:
        self.database_committed = True

    def _recover(self, original: BaseException) -> None:
        if self.directory is None:
            return
        errors: list[BaseException] = []
        if self.published is not None and self.published.exists():
            try:
                if (self.published_identity is not None
                        and preset_file_identity(
                            self.published) == self.published_identity):
                    self.published.replace(self.directory / "discarded.json")
            except OSError as exc:
                errors.append(exc)
        for target, backup in reversed(self.backups):
            if not backup.exists():
                continue
            # An external editor may have recreated or replaced the path after
            # isolation. Keep that newer top-level file; SQLite rollback still
            # retains the prior row and the next reconcile will import it.
            if target.exists():
                continue
            try:
                backup.replace(target)
            except OSError as exc:
                errors.append(exc)
        if errors:
            raise PresetRecoveryError(original, errors) from original
        self._cleanup(warn=True)

    def _cleanup(self, *, warn: bool) -> None:
        if self.directory is None or not self.directory.exists():
            return
        errors: list[OSError] = []
        try:
            children = list(self.directory.iterdir())
        except OSError as exc:
            children = []
            errors.append(exc)
        for path in children:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(exc)
        try:
            self.directory.rmdir()
        except OSError as exc:
            errors.append(exc)
        if warn and errors:
            _warn_after_commit(
                "Could not clean committed Preset transaction directory "
                f"{self.directory}: {errors[0]}",
                stacklevel=3,
            )

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_value is not None or not self.database_committed:
            self._recover(exc_value or RuntimeError(
                "Preset database transaction did not commit"))
        else:
            self._cleanup(warn=True)
        return False


class _EditableFileBatch:
    """Coordinate file recovery for one multi-row SQLite transaction."""

    def __init__(self, presets_dir: Path) -> None:
        self.presets_dir = Path(presets_dir)
        self.transactions: dict[int, _EditableFileTransaction] = {}
        self.quarantines: list[tuple[Path, Path]] = []

    def for_preset(self, preset_id: int) -> _EditableFileTransaction:
        transaction = self.transactions.get(preset_id)
        if transaction is None:
            transaction = _EditableFileTransaction(
                self.presets_dir, preset_id)
            transaction.__enter__()
            self.transactions[preset_id] = transaction
        return transaction

    def quarantine(self, path: Path) -> Path | None:
        """Stage one conflict move until the surrounding SQLite commit."""
        original = Path(path)
        target = quarantine_preset_file(original, self.presets_dir)
        if target is not None:
            self.quarantines.append((original, target))
        return target

    def committed(self) -> None:
        transactions = list(self.transactions.values())
        for transaction in transactions:
            transaction.mark_database_committed()
        self.transactions.clear()
        self.quarantines.clear()
        for transaction in reversed(transactions):
            transaction.__exit__(None, None, None)

    def recover(self, original: BaseException) -> None:
        transactions = list(self.transactions.values())
        self.transactions.clear()
        errors: list[BaseException] = []
        for transaction in reversed(transactions):
            try:
                transaction.__exit__(type(original), original,
                                     original.__traceback__)
            except BaseException as exc:
                errors.append(exc)
        quarantines = list(self.quarantines)
        self.quarantines.clear()
        for source, quarantined in reversed(quarantines):
            if source.exists():
                continue
            if not quarantined.exists():
                errors.append(FileNotFoundError(
                    f"quarantined Preset disappeared during recovery: "
                    f"{quarantined}"))
                continue
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                quarantined.replace(source)
            except OSError as exc:
                errors.append(exc)
        if errors:
            raise PresetRecoveryError(original, errors) from original

class EditableRuntime(Protocol):
    DB_FILE: Path
    PRESETS_DIR: Path

    def connect(self) -> sqlite3.Connection: ...
    def scan_local_packs(self) -> list[dict]: ...
    def _canonical_preset_chain(
            self, raw: object, *, scan_local: bool = True) -> dict: ...
    def _validate_preset_draft_references(
            self, chain: dict, *, scan_local: bool = True) -> None: ...
    def _preset_has_unsupported_registered_asset(self, chain: dict) -> bool: ...


class EditablePresetStore:
    """Own editable rows, JSON projections, reconciliation, and recovery."""

    def __init__(
            self, runtime: Callable[[], EditableRuntime], *,
            reserved_names: Callable[[], set[str]],
    ) -> None:
        self._runtime = runtime
        self._reserved_names = reserved_names
        self.reconcile_lock = threading.RLock()
        self._database_token_cache: dict[
            tuple[Path, bool], tuple[tuple, tuple]
        ] = {}
        self._database_token_cache_lock = threading.Lock()
        self._reconcile_active = False
        self._reconcile_token: tuple | None = None

    def database_token(self, *, include_file_settings: bool = False) -> tuple:
        """Fingerprint only the SQLite namespace relevant to Presets."""
        db_path = Path(self._runtime().DB_FILE).resolve(strict=False)

        def file_state(path: Path) -> tuple[int, int, int, int]:
            try:
                stat = path.stat()
            except OSError:
                return (0, 0, 0, 0)
            return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

        database_state = file_state(db_path)
        state = (database_state, file_state(Path(f"{db_path}-wal")))
        identity = database_state[:2]
        prefix = (str(db_path), identity)
        cache_key = (db_path, include_file_settings)
        with self._database_token_cache_lock:
            cached = self._database_token_cache.get(cache_key)
            if cached is not None and cached[0] == state:
                return cached[1]

        def cache(result: tuple) -> tuple:
            with self._database_token_cache_lock:
                self._database_token_cache[cache_key] = (state, result)
            return result

        if identity == (0, 0):
            return cache((*prefix, ()))
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(db_path, timeout=0.1)
            columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(presets)").fetchall()
            }
            if not {"source", "source_key"} <= columns:
                return cache((*prefix, ("legacy-schema",)))
            preset_rows = tuple(conn.execute(
                "SELECT id, name, source, source_key, note, chain_json, "
                "created_at, updated_at FROM presets ORDER BY id"
            ).fetchall())
            if not include_file_settings:
                return cache((*prefix, preset_rows))
            settings = tuple(conn.execute(
                "SELECT key, value FROM settings "
                "WHERE key = 'active_preset' OR key LIKE ? ORDER BY key",
                (f"{PRESET_FILE_SETTING_PREFIX}%",),
            ).fetchall())
            return cache((*prefix, preset_rows, settings))
        except sqlite3.Error:
            return (*prefix, ("temporarily-unavailable",))
        finally:
            if conn is not None:
                conn.close()

    def _editable_token(self) -> tuple:
        """Return the local state that can make reconciliation necessary."""
        def stat_token(path: Path) -> tuple[int, int, int, int, int]:
            try:
                stat = path.stat()
            except OSError:
                return (0, 0, 0, 0, 0)
            return (
                stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns,
            )

        preset_dir = Path(self._runtime().PRESETS_DIR)
        try:
            preset_files = tuple(
                (path.name, *stat_token(path))
                for path in sorted(preset_dir.glob("*.json"))
            )
        except OSError:
            preset_files = ()
        return (
            self.database_token(include_file_settings=True),
            str(preset_dir),
            preset_files,
        )

    def _editable_tracking_is_current(self) -> bool:
        """Return whether every tracked JSON still matches its committed token."""
        try:
            with self._runtime().connect() as conn:
                tracked = tracked_preset_files(conn)
            preset_dir = Path(self._runtime().PRESETS_DIR)
            for state in tracked.values():
                file_name = state.get("file")
                expected = state.get("token")
                if (not isinstance(file_name, str)
                        or Path(file_name).name != file_name
                        or not isinstance(expected, str)
                        or not preset_file_token_is_current(expected)
                        or not preset_file_matches_token(
                            preset_dir / file_name, expected)):
                    return False
            return True
        except (OSError, sqlite3.Error):
            return False

    def _interrupted_transaction_groups(self) -> dict[int, list[Path]]:
        preset_dir = Path(self._runtime().PRESETS_DIR)
        groups: dict[int, list[Path]] = {}
        try:
            directories = sorted(
                path for path in preset_dir.glob(".preset-*")
                if path.is_dir())
        except OSError:
            return groups
        for directory in directories:
            preset_id = interrupted_transaction_preset_id(directory)
            if preset_id is None:
                warnings.warn(
                    f"Ignoring unrecognized Preset transaction directory "
                    f"{directory}", RuntimeWarning, stacklevel=2)
                continue
            groups.setdefault(preset_id, []).append(directory)
        return groups

    @staticmethod
    def _transaction_has_staged_document(directories: list[Path]) -> bool:
        return any((directory / "staged.json").is_file()
                   for directory in directories)

    @staticmethod
    def _remove_transaction_directories(directories: list[Path]) -> bool:
        complete = True
        for directory in directories:
            try:
                children = list(directory.iterdir())
            except OSError as exc:
                warnings.warn(
                    f"Could not inspect interrupted Preset transaction "
                    f"{directory}: {exc}", RuntimeWarning, stacklevel=3)
                complete = False
                continue
            for child in children:
                try:
                    child.unlink()
                except OSError as exc:
                    warnings.warn(
                        f"Could not clean interrupted Preset transaction file "
                        f"{child}: {exc}", RuntimeWarning, stacklevel=3)
                    complete = False
            try:
                directory.rmdir()
            except OSError as exc:
                warnings.warn(
                    f"Could not clean interrupted Preset transaction directory "
                    f"{directory}: {exc}", RuntimeWarning, stacklevel=3)
                complete = False
        return complete

    @staticmethod
    def _preserve_transaction_directories(
            directories: list[Path], file_batch: _EditableFileBatch) -> bool:
        complete = True
        for directory in directories:
            try:
                children = list(directory.iterdir())
            except OSError as exc:
                warnings.warn(
                    f"Could not inspect interrupted Preset transaction "
                    f"{directory}: {exc}", RuntimeWarning, stacklevel=3)
                complete = False
                continue
            for child in children:
                if not child.is_file():
                    complete = False
                    warnings.warn(
                        f"Could not preserve unexpected Preset transaction entry "
                        f"{child}", RuntimeWarning, stacklevel=3)
                    continue
                if file_batch.quarantine(child) is None:
                    complete = False
            try:
                directory.rmdir()
            except OSError as exc:
                warnings.warn(
                    f"Could not clean interrupted Preset transaction directory "
                    f"{directory}: {exc}", RuntimeWarning, stacklevel=3)
                complete = False
        return complete

    def _recover_interrupted_transactions(
            self, conn: sqlite3.Connection, presets_dir: Path,
            file_batch: _EditableFileBatch) -> None:
        """Recover file transactions whose process ended before cleanup."""
        groups = self._interrupted_transaction_groups()
        if not groups:
            return
        tracked = tracked_preset_files(conn)
        for preset_id, directories in groups.items():
            row = conn.execute(
                "SELECT id FROM presets WHERE id = ?", (preset_id,)
            ).fetchone()
            state = tracked.get(preset_id)
            expected_path: Path | None = None
            expected_token: str | None = None
            if state is not None:
                file_name = state.get("file")
                token = state.get("token")
                if (isinstance(file_name, str)
                        and Path(file_name).name == file_name
                        and isinstance(token, str)):
                    expected_path = presets_dir / file_name
                    expected_token = token

            durable_file_matches = False
            if row is not None and expected_path is not None:
                try:
                    durable_file_matches = (
                        preset_file_matches_token(
                            expected_path, expected_token))
                except OSError:
                    durable_file_matches = False

            staged = self._transaction_has_staged_document(directories)
            if durable_file_matches and not staged and len(directories) == 1:
                self._remove_transaction_directories(directories)
                continue

            if durable_file_matches:
                # The committed projection is intact. Only an unpublished
                # staged document remains uncertain, so preserve that intent.
                for directory in directories:
                    staged_path = directory / "staged.json"
                    if (staged_path.is_file()
                            and file_batch.quarantine(staged_path) is None):
                        raise PresetRecoveryError(
                            RuntimeError("interrupted Preset recovery failed"),
                            [OSError(f"could not preserve {staged_path}")],
                        )
                self._remove_transaction_directories(directories)
                continue

            # SQLite is the commit record. Preserve every uncertain top-level
            # and staged/backup file, then republish the committed row below.
            top_level = sorted(presets_dir.glob(f"{preset_id}-*.json"))
            for path in top_level:
                if file_batch.quarantine(path) is None:
                    raise PresetRecoveryError(
                        RuntimeError("interrupted Preset recovery failed"),
                        [OSError(f"could not preserve {path}")],
                    )
            if not self._preserve_transaction_directories(
                    directories, file_batch):
                raise PresetRecoveryError(
                    RuntimeError("interrupted Preset recovery failed"),
                    [OSError("could not preserve interrupted transaction files")],
                )
            conn.execute(
                "DELETE FROM settings WHERE key = ?",
                (preset_file_key(preset_id),),
            )

    def project_row(self, row: sqlite3.Row) -> dict | None:
        """Project one SQLite row into the canonical in-memory Preset shape."""
        runtime = self._runtime()
        value = dict(row)
        raw_json = value.pop("chain_json")
        if value.get("note") is None:
            value["note"] = ""
        try:
            value["chain"] = runtime._canonical_preset_chain(
                json.loads(raw_json), scan_local=False)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            value["chain"] = None
            value["chain_error"] = str(exc)
        if (not preset_owned_by_bundle(value)
                and value["chain"] is not None
                and runtime._preset_has_unsupported_registered_asset(
                    value["chain"])):
            return None
        return value

    def parse_editable_document(
            self, path: Path, *, scan_local: bool = True,
    ) -> tuple[str, str, dict, str | None, str | None]:
        """Read and validate one user-editable Preset document."""
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("document must be an object")
        if document.get("kind") != PRESET_DOCUMENT_KIND:
            raise ValueError(f"kind must be '{PRESET_DOCUMENT_KIND}'")
        if document.get("schema_version") != 1:
            raise ValueError("schema_version must be 1")
        name = document.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        runtime = self._runtime()
        chain = runtime._canonical_preset_chain(
            document.get("chain"), scan_local=scan_local)
        runtime._validate_preset_draft_references(
            chain, scan_local=scan_local)
        note = preset_document.normalize_note(document.get("note"))
        created_at = document.get("created_at")
        updated_at = document.get("updated_at")
        if created_at is not None and not isinstance(created_at, str):
            raise ValueError("created_at must be a string or null")
        if updated_at is not None and not isinstance(updated_at, str):
            raise ValueError("updated_at must be a string or null")
        return name.strip(), note, chain, created_at, updated_at

    def editable_document(
            self, row: sqlite3.Row, *, scan_local: bool = False) -> dict:
        """Build the durable JSON projection for one editable Preset row."""
        raw_chain = json.loads(row["chain_json"])
        return {
            "schema_version": 1,
            "kind": PRESET_DOCUMENT_KIND,
            "id": row["id"],
            "name": row["name"],
            "note": preset_document.normalize_note(row["note"]),
            "chain": self._runtime()._canonical_preset_chain(
                raw_chain, scan_local=scan_local),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _publish_editable_row(
            self, conn: sqlite3.Connection, row: sqlite3.Row,
            files: _EditableFileTransaction, *,
            scan_local: bool = False,
            expected_files: dict[Path, str] | None = None) -> Path:
        """Publish one row inside a caller-owned database/file transaction."""
        if preset_owned_by_bundle(row):
            raise ValueError(
                f"Built-in Preset '{row['name']}' is read-only and is not stored "
                "under data/presets.")
        presets_dir = Path(self._runtime().PRESETS_DIR)
        path = preset_file_path(presets_dir, int(row["id"]), row["name"])
        _published_path, published_token = files.publish(
            path,
            self.editable_document(row, scan_local=scan_local),
            expected_files=expected_files,
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (preset_file_key(int(row["id"])), json.dumps({
                "file": path.name,
                "token": published_token,
            })),
        )
        return path

    def _commit_editable_row(
            self, conn: sqlite3.Connection, row: sqlite3.Row, *,
            scan_local: bool = False) -> Path:
        """Publish one row and commit SQLite as one recoverable operation."""
        presets_dir = Path(self._runtime().PRESETS_DIR)
        preset_id = int(row["id"])
        path = preset_file_path(presets_dir, preset_id, row["name"])
        expected_files = self._tracked_file_expectation(conn, preset_id)
        try:
            with _EditableFileTransaction(presets_dir, preset_id) as files:
                self._publish_editable_row(
                    conn, row, files, scan_local=scan_local,
                    expected_files=expected_files)
                conn.commit()
                files.mark_database_committed()
        except _EditableSnapshotChanged as exc:
            raise PresetConflictError(
                "Preset changed externally; refresh and retry.") from exc
        return path

    def _tracked_file_expectation(
            self, conn: sqlite3.Connection, preset_id: int,
    ) -> dict[Path, str]:
        state = tracked_preset_files(conn).get(preset_id)
        if state is None:
            return {}
        file_name = state.get("file")
        token = state.get("token")
        if (not isinstance(file_name, str)
                or Path(file_name).name != file_name
                or not isinstance(token, str)):
            return {}
        return {Path(self._runtime().PRESETS_DIR) / file_name: token}

    def _delete_editable_row(
            self, conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
        """Delete a captured row while keeping its JSON undiscoverable."""
        ensure_preset_mutable(row)
        preset_id = int(row["id"])
        name = row["name"]
        expected_files = self._tracked_file_expectation(conn, preset_id)
        try:
            with _EditableFileTransaction(
                    Path(self._runtime().PRESETS_DIR), preset_id) as files:
                files.isolate_expected(expected_files)
                files.isolate_existing()
                cur = conn.execute(
                    "DELETE FROM presets WHERE id = ?", (preset_id,))
                active = conn.execute(
                    "SELECT value FROM settings WHERE key='active_preset'"
                ).fetchone()
                if cur.rowcount and active and active["value"] == name:
                    conn.execute("DELETE FROM settings WHERE key='active_preset'")
                if cur.rowcount:
                    conn.execute(
                        "DELETE FROM settings WHERE key=?",
                        (preset_file_key(preset_id),),
                    )
                conn.commit()
                files.mark_database_committed()
        except _EditableSnapshotChanged as exc:
            raise PresetConflictError(
                "Preset changed externally; refresh and retry.") from exc
        return cur.rowcount > 0

    def read_by_name(self, name: str) -> dict | None:
        with self._runtime().connect() as conn:
            row = conn.execute(
                "SELECT * FROM presets WHERE name = ?", (name,)
            ).fetchone()
        return self.project_row(row) if row else None

    def read_by_id(self, preset_id: int) -> dict | None:
        if isinstance(preset_id, bool) or not isinstance(preset_id, int):
            raise ValueError("preset id must be an integer")
        with self._runtime().connect() as conn:
            row = conn.execute(
                "SELECT * FROM presets WHERE id = ?", (preset_id,)
            ).fetchone()
        return self.project_row(row) if row else None

    def read_all(self) -> list[dict]:
        with self._runtime().connect() as conn:
            rows = conn.execute(
                "SELECT * FROM presets ORDER BY updated_at DESC"
            ).fetchall()
        return [
            preset for row in rows
            if (preset := self.project_row(row)) is not None
        ]

    def reconcile(self, reserved_names: set[str]) -> None:
        self._reconcile_files(reserved_bundled_names=reserved_names)

    def _reconcile_editable(self) -> None:
        self.reconcile(self._reserved_names())

    def current_name(self) -> str | None:
        """Return the active Preset name only while its row remains visible."""
        with self._runtime().connect() as conn:
            row = conn.execute(
                "SELECT p.* FROM settings s "
                "JOIN presets p ON p.name = s.value "
                "WHERE s.key = 'active_preset'"
            ).fetchone()
        return row["name"] if row and self.project_row(row) is not None else None

    def set_active(self, name: str | None) -> None:
        """Set the active Preset, rejecting names that do not exist."""
        with self.reconcile_lock:
            with self._runtime().connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if name is not None:
                    row = conn.execute(
                        "SELECT * FROM presets WHERE name = ?", (name,)
                    ).fetchone()
                    if row is None or self.project_row(row) is None:
                        raise ValueError(f"Preset '{name}' not found.")
                if name is None:
                    conn.execute(
                        "DELETE FROM settings WHERE key = 'active_preset'")
                else:
                    conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        ("active_preset", name),
                    )
                conn.commit()

    def _prepare_mutation(self) -> None:
        self._reconcile_editable()

    def assert_editable_name(self, name: str) -> None:
        """Reject repository-owned names before an expensive external action."""
        with self.reconcile_lock:
            self._prepare_mutation()
            with self._runtime().connect() as conn:
                row = conn.execute(
                    "SELECT name, source, source_key FROM presets WHERE name = ?",
                    (name,),
                ).fetchone()
        ensure_preset_mutable(row)

    def upsert_editable(
            self, name: str, chain: dict, note: str | None, *,
            set_active: bool = False,
            preserve_existing_note: bool = False,
    ) -> dict | None:
        """Create or replace one editable Preset and its JSON projection."""
        name = name.strip()
        if not name:
            raise ValueError("Preset name cannot be empty.")
        now = datetime.now(timezone.utc).isoformat()
        runtime = self._runtime()
        with self.reconcile_lock:
            self._prepare_mutation()
            with runtime.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT name, note, source, source_key FROM presets "
                    "WHERE name = ?",
                    (name,),
                ).fetchone()
                ensure_preset_mutable(existing)
                stored_note = (
                    preset_document.normalize_note(existing["note"])
                    if preserve_existing_note and note is None and existing
                    else preset_document.normalize_note(note)
                )
                conn.execute(
                    "INSERT INTO presets "
                    "(name, note, chain_json, source, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET note=excluded.note, "
                    "chain_json=excluded.chain_json, source='user', "
                    "source_key=NULL, updated_at=excluded.updated_at",
                    (name, stored_note, json.dumps(chain, ensure_ascii=False),
                     "user", now, now),
                )
                preset_id = int(conn.execute(
                    "SELECT id FROM presets WHERE name = ?", (name,)
                ).fetchone()["id"])
                if set_active:
                    conn.execute(
                        "INSERT INTO settings (key, value) "
                        "VALUES ('active_preset', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (name,),
                    )
                row = conn.execute(
                    "SELECT * FROM presets WHERE id = ?", (preset_id,)
                ).fetchone()
                self._commit_editable_row(conn, row)
        result = self.read_by_id(preset_id)
        return result if isinstance(result, dict) else None

    def delete_editable_by_name(self, name: str) -> bool:
        runtime = self._runtime()
        with self.reconcile_lock:
            self._prepare_mutation()
            with runtime.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT id, name, source, source_key FROM presets "
                    "WHERE name = ?",
                    (name,),
                ).fetchone()
                if row is None:
                    return False
                return self._delete_editable_row(conn, row)

    def delete_editable_by_id(self, preset_id: int) -> dict[str, object]:
        if isinstance(preset_id, bool) or not isinstance(preset_id, int):
            raise ValueError("preset id must be an integer")
        runtime = self._runtime()
        with self.reconcile_lock:
            self._prepare_mutation()
            with runtime.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT id, name, source, source_key FROM presets WHERE id = ?",
                    (preset_id,),
                ).fetchone()
                if row is None:
                    return {"id": preset_id, "name": None, "deleted": False,
                            "stale": True}
                name = row["name"]
                deleted = self._delete_editable_row(conn, row)
                result = {
                    "id": preset_id,
                    "name": name,
                    "deleted": deleted,
                    "stale": not deleted,
                }
        return result

    def rename_editable(self, preset_id: int, new_name: str) -> dict | None:
        if isinstance(preset_id, bool) or not isinstance(preset_id, int):
            raise ValueError("preset id must be an integer")
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("Preset name cannot be empty.")
        now = datetime.now(timezone.utc).isoformat()
        with self.reconcile_lock:
            self._prepare_mutation()
            with self._runtime().connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT name, source, source_key FROM presets WHERE id = ?",
                    (preset_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Preset id {preset_id} no longer exists.")
                ensure_preset_mutable(row)
                old_name = row["name"]
                if old_name != new_name and conn.execute(
                        "SELECT 1 FROM presets WHERE name = ?", (new_name,)
                ).fetchone():
                    raise ValueError(f"Preset '{new_name}' already exists.")
                conn.execute(
                    "UPDATE presets SET name = ?, updated_at = ? WHERE id = ?",
                    (new_name, now, preset_id),
                )
                conn.execute(
                    "UPDATE settings SET value = ? "
                    "WHERE key = 'active_preset' AND value = ?",
                    (new_name, old_name),
                )
                refreshed = conn.execute(
                    "SELECT * FROM presets WHERE id = ?", (preset_id,)
                ).fetchone()
                self._commit_editable_row(conn, refreshed)
        result = self.read_by_id(preset_id)
        return result if isinstance(result, dict) else None

    def update_editable_note(
            self, preset_id: int, note: str | None) -> dict | None:
        if isinstance(preset_id, bool) or not isinstance(preset_id, int):
            raise ValueError("preset id must be an integer")
        now = datetime.now(timezone.utc).isoformat()
        stored_note = preset_document.normalize_note(note)
        with self.reconcile_lock:
            self._prepare_mutation()
            with self._runtime().connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT name, source, source_key FROM presets WHERE id = ?",
                    (preset_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Preset id {preset_id} no longer exists.")
                ensure_preset_mutable(row)
                cur = conn.execute(
                    "UPDATE presets SET note = ?, updated_at = ? WHERE id = ?",
                    (stored_note, now, preset_id),
                )
                if not cur.rowcount:
                    raise ValueError(f"Preset id {preset_id} no longer exists.")
                refreshed = conn.execute(
                    "SELECT * FROM presets WHERE id = ?", (preset_id,)
                ).fetchone()
                self._commit_editable_row(conn, refreshed)
        result = self.read_by_id(preset_id)
        return result if isinstance(result, dict) else None

    def update_editable_draft(
            self, preset_id: int, chain: dict, note: str | None, *,
            expected_updated_at: str | None | object = PRESET_UPDATED_UNSET,
    ) -> dict | None:
        if isinstance(preset_id, bool) or not isinstance(preset_id, int):
            raise ValueError("preset id must be an integer")
        now = datetime.now(timezone.utc).isoformat()
        stored_note = preset_document.normalize_note(note)
        with self.reconcile_lock:
            self._prepare_mutation()
            with self._runtime().connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT name, source, source_key FROM presets WHERE id = ?",
                    (preset_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Preset id {preset_id} no longer exists.")
                ensure_preset_mutable(row)
                params: list[object] = [
                    stored_note, json.dumps(chain, ensure_ascii=False), now,
                    preset_id,
                ]
                where = "id = ?"
                if expected_updated_at is not PRESET_UPDATED_UNSET:
                    where += " AND updated_at IS ?"
                    params.append(expected_updated_at)
                cur = conn.execute(
                    "UPDATE presets SET note = ?, chain_json = ?, updated_at = ? "
                    f"WHERE {where}", params)
                if not cur.rowcount:
                    if expected_updated_at is not PRESET_UPDATED_UNSET:
                        exists = conn.execute(
                            "SELECT 1 FROM presets WHERE id = ?", (preset_id,)
                        ).fetchone()
                        if exists:
                            raise PresetConflictError(
                                "preset changed externally; reopen it before saving")
                    raise ValueError(f"Preset id {preset_id} no longer exists.")
                refreshed = conn.execute(
                    "SELECT * FROM presets WHERE id = ?", (preset_id,)
                ).fetchone()
                self._commit_editable_row(conn, refreshed)
        result = self.read_by_id(preset_id)
        return result if isinstance(result, dict) else None

    def _reconcile_files(
            self, *, reserved_bundled_names: set[str] | None = None) -> None:
        """Make editable JSON Presets and the SQLite projection agree."""
        runtime = self._runtime()
        preset_dir = Path(runtime.PRESETS_DIR)
        reserved_names = {
            name.strip() for name in (reserved_bundled_names or set())
            if isinstance(name, str) and name.strip()
        }
        reserved_token = tuple(sorted(reserved_names))
        quarantine_failed = False
        file_batch = _EditableFileBatch(preset_dir)
        with self.reconcile_lock:
            if self._reconcile_active:
                return
            current_token = (
                self._editable_token(), reserved_token)
            if (self._reconcile_token == current_token
                    and self._editable_tracking_is_current()
                    and not self._interrupted_transaction_groups()):
                return
            self._reconcile_active = True
            try:
                preset_dir.mkdir(parents=True, exist_ok=True)
                has_editable_files = any(preset_dir.glob("*.json"))
                with runtime.connect() as conn:
                    has_editable_rows = conn.execute(
                        "SELECT 1 FROM presets WHERE source!='bundled' "
                        "OR source_key IS NULL OR TRIM(source_key)='' LIMIT 1"
                    ).fetchone() is not None
                    if has_editable_files or has_editable_rows:
                        # Refresh before taking SQLite's write reservation.
                        runtime.scan_local_packs()
                    conn.execute("BEGIN IMMEDIATE")
                    self._recover_interrupted_transactions(
                        conn, preset_dir, file_batch)
                    tracked = tracked_preset_files(conn)
                    rows = conn.execute(
                        "SELECT * FROM presets ORDER BY id").fetchall()
                    rows_by_id = {int(row["id"]): row for row in rows}
                    claimed: set[Path] = set()

                    for preset_id, state in tracked.items():
                        path = preset_dir / state["file"]
                        claimed.add(path)
                        row = rows_by_id.get(preset_id)
                        if row is None:
                            conn.execute(
                                "DELETE FROM settings WHERE key = ?",
                                (preset_file_key(preset_id),),
                            )
                            continue
                        if preset_owned_by_bundle(row):
                            # Repository documents supersede legacy runtime
                            # copies, while changed copies remain recoverable.
                            if path.is_file():
                                try:
                                    unchanged = (
                                        preset_file_matches_token(
                                            path, state.get("token")))
                                except OSError:
                                    unchanged = False
                                if unchanged:
                                    try:
                                        path.unlink()
                                    except OSError as exc:
                                        quarantine_failed = True
                                        warnings.warn(
                                            "Could not remove stale Preset file "
                                            f"{path}: {exc}", RuntimeWarning,
                                            stacklevel=2)
                                        continue
                                elif file_batch.quarantine(path) is None:
                                    quarantine_failed = True
                                    continue
                            conn.execute(
                                "DELETE FROM settings WHERE key = ?",
                                (preset_file_key(preset_id),),
                            )
                            continue
                        if not path.is_file():
                            conn.execute(
                                "DELETE FROM presets WHERE id = ?", (preset_id,))
                            conn.execute(
                                "DELETE FROM settings WHERE key = ?",
                                (preset_file_key(preset_id),),
                            )
                            conn.execute(
                                "DELETE FROM settings WHERE key='active_preset' "
                                "AND value=?",
                                (row["name"],),
                            )
                            rows_by_id.pop(preset_id, None)
                            continue
                        token = preset_file_token(path)
                        if preset_file_matches_token(path, state.get("token")):
                            if not preset_file_token_is_current(
                                    state.get("token")):
                                conn.execute(
                                    "UPDATE settings SET value=? WHERE key=?",
                                    (json.dumps({
                                        **state,
                                        "file": path.name,
                                        "token": token,
                                    }), preset_file_key(preset_id)),
                                )
                            continue
                        try:
                            name, note, chain, created_at, _ = (
                                self.parse_editable_document(
                                    path, scan_local=False))
                            conflict = conn.execute(
                                "SELECT id FROM presets WHERE name = ? AND id != ?",
                                (name, preset_id),
                            ).fetchone()
                            if conflict:
                                raise ValueError(
                                    f"Preset '{name}' already exists")
                            now = datetime.now(timezone.utc).isoformat()
                            conn.execute(
                                "UPDATE presets SET name=?, note=?, chain_json=?, "
                                "source='user', source_key=NULL, "
                                "created_at=COALESCE(?, created_at), updated_at=? "
                                "WHERE id=?",
                                (name, note, json.dumps(chain, ensure_ascii=False),
                                 created_at, now, preset_id),
                            )
                            if row["name"] != name:
                                conn.execute(
                                    "UPDATE settings SET value=? "
                                    "WHERE key='active_preset' AND value=?",
                                    (name, row["name"]),
                                )
                            refreshed = conn.execute(
                                "SELECT * FROM presets WHERE id=?", (preset_id,)
                            ).fetchone()
                            files = file_batch.for_preset(preset_id)
                            new_path = self._publish_editable_row(
                                conn, refreshed, files, scan_local=False,
                                expected_files={path: token})
                            claimed.discard(path)
                            claimed.add(new_path)
                            if new_path != path:
                                files.isolate(path)
                        except ValueError as exc:
                            if is_shareable_preset_file(path):
                                continue
                            warnings.warn(
                                f"Ignoring invalid Preset file {path}: {exc}",
                                RuntimeWarning, stacklevel=2)

                    rows = conn.execute(
                        "SELECT * FROM presets ORDER BY id").fetchall()
                    tracked = tracked_preset_files(conn)
                    for row in rows:
                        preset_id = int(row["id"])
                        if preset_owned_by_bundle(row):
                            continue
                        if preset_id not in tracked:
                            path = self._publish_editable_row(
                                conn, row, file_batch.for_preset(preset_id),
                                scan_local=False)
                            claimed.add(path)

                    for path in sorted(preset_dir.glob("*.json")):
                        if path in claimed:
                            continue
                        try:
                            source_token = preset_file_token(path)
                            name, note, chain, created_at, updated_at = (
                                self.parse_editable_document(
                                    path, scan_local=False))
                            if name in reserved_names:
                                if file_batch.quarantine(path) is None:
                                    quarantine_failed = True
                                continue
                            existing = conn.execute(
                                "SELECT source, source_key FROM presets "
                                "WHERE name=?",
                                (name,),
                            ).fetchone()
                            if preset_owned_by_bundle(existing):
                                if file_batch.quarantine(path) is None:
                                    quarantine_failed = True
                                continue
                            if existing:
                                if file_batch.quarantine(path) is None:
                                    quarantine_failed = True
                                continue
                            now = datetime.now(timezone.utc).isoformat()
                            cur = conn.execute(
                                "INSERT INTO presets "
                                "(name, note, chain_json, created_at, updated_at) "
                                "VALUES (?, ?, ?, ?, ?)",
                                (name, note, json.dumps(chain, ensure_ascii=False),
                                 created_at or now, updated_at or now),
                            )
                            row = conn.execute(
                                "SELECT * FROM presets WHERE id=?",
                                (cur.lastrowid,),
                            ).fetchone()
                            preset_id = int(row["id"])
                            files = file_batch.for_preset(preset_id)
                            new_path = self._publish_editable_row(
                                conn, row, files, scan_local=False,
                                expected_files={path: source_token})
                            if new_path != path:
                                files.isolate(path)
                        except ValueError as exc:
                            if is_shareable_preset_file(path):
                                continue
                            warnings.warn(
                                f"Ignoring invalid Preset file {path}: {exc}",
                                RuntimeWarning, stacklevel=2)
                    conn.commit()
                    file_batch.committed()
                self._reconcile_token = (
                    None if quarantine_failed
                    else (self._editable_token(), reserved_token))
            except _EditableSnapshotChanged as exc:
                file_batch.recover(exc)
                self._reconcile_token = None
            except BaseException as exc:
                file_batch.recover(exc)
                raise
            finally:
                self._reconcile_active = False
