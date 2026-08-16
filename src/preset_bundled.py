"""Repository-owned Preset source and SQLite registration.

The repository JSON is authoritative for bundled identity and content.  This
module scans one stable filesystem snapshot and projects it into SQLite without
owning model downloads or user-editable Preset files.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import preset_document


BUNDLED_PRESET_DOCUMENT_KIND = "gigbuddy-bundled-preset"


class BundledPresetReadError(ValueError):
    """A repository Preset could not be read, so its snapshot is partial."""


class BundledRuntime(Protocol):
    BUNDLED_PRESETS_DIR: Path
    PRESETS_DIR: Path

    def connect(self) -> sqlite3.Connection: ...


@dataclass(frozen=True, slots=True)
class BundledPresetEntry:
    source_key: str
    name: str
    note: str
    chain: dict
    model_sources: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class BundledCatalogSnapshot:
    token: tuple
    entries: tuple[BundledPresetEntry, ...]
    invalid_names: tuple[str, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class BundledRegistration:
    managed: tuple[BundledPresetEntry, ...]
    invalid_names: tuple[str, ...]
    live_state_keys: frozenset[str]
    candidate_token: tuple | None


def preset_owned_by_bundle(row: Any | None) -> bool:
    """Return whether a row carries complete repository provenance."""
    if row is None or row["source"] != "bundled":
        return False
    source_key = row["source_key"]
    return isinstance(source_key, str) and bool(source_key.strip())


class BundledPresetSource:
    """Read and validate a stable snapshot of repository Preset JSON."""

    def __init__(self, runtime: Callable[[], BundledRuntime]) -> None:
        self._runtime = runtime

    def token(self) -> tuple:
        def file_token(path: Path) -> tuple[int, int, int]:
            try:
                stat = path.stat()
            except OSError:
                return (0, 0, 0)
            return (stat.st_ino, stat.st_size, stat.st_mtime_ns)

        directory = Path(self._runtime().BUNDLED_PRESETS_DIR)
        try:
            stat = directory.stat()
            directory_token = (stat.st_dev, stat.st_ino, stat.st_mtime_ns)
        except OSError:
            directory_token = (0, 0, 0)
        try:
            documents = tuple(
                (path.name, *file_token(path))
                for path in sorted(directory.glob("*.json"))
            )
        except OSError:
            documents = ()
        return (str(directory.resolve(strict=False)), directory_token, documents)

    @staticmethod
    def parse(path: Path) -> BundledPresetEntry:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BundledPresetReadError(
                f"could not read bundled Preset JSON: {exc}") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid bundled Preset JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("bundled Preset document must be an object")
        if document.get("kind") != BUNDLED_PRESET_DOCUMENT_KIND:
            raise ValueError(
                f"kind must be '{BUNDLED_PRESET_DOCUMENT_KIND}'")
        if document.get("schema_version") != 1:
            raise ValueError("schema_version must be 1")
        name = document.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        source_key = document.get("catalog_key", path.stem)
        if (not isinstance(source_key, str) or not source_key.strip()
                or Path(source_key).name != source_key):
            raise ValueError("catalog_key must be a simple non-empty string")

        raw_chain = document.get("chain")
        chain = preset_document.parse_portable_chain(raw_chain)
        model_sources: list[tuple[int, int]] = []
        raw_slots = (
            raw_chain.get("slots", []) if isinstance(raw_chain, dict) else [])
        for index, slot in enumerate(raw_slots):
            if not isinstance(slot, dict):
                continue
            model_id = slot.get("model_id")
            if (isinstance(model_id, bool) or not isinstance(model_id, int)
                    or model_id <= 0):
                continue
            tone_id = slot.get("tone_id")
            if (isinstance(tone_id, bool) or not isinstance(tone_id, int)
                    or tone_id <= 0):
                raise ValueError(
                    f"Slot {index + 1:02d} tone_id must be a positive integer")
            model_sources.append((model_id, tone_id))
        return BundledPresetEntry(
            source_key=source_key.strip(),
            name=name.strip(),
            note=preset_document.normalize_note(document.get("note")),
            chain=chain,
            model_sources=tuple(model_sources),
        )

    def scan(
            self, *, token: Callable[[], tuple] | None = None,
            parse: Callable[[Path], BundledPresetEntry] | None = None,
    ) -> BundledCatalogSnapshot:
        token = token or self.token
        parse = parse or self.parse
        snapshot_token = token()
        entries: list[BundledPresetEntry] = []
        invalid_names: list[str] = []
        directory = Path(self._runtime().BUNDLED_PRESETS_DIR)
        complete = directory.is_dir()
        paths: list[Path] = []
        if complete:
            try:
                paths = sorted(directory.glob("*.json"))
            except OSError:
                complete = False
            else:
                complete = bool(paths)
        for path in paths:
            try:
                entries.append(parse(path))
            except (BundledPresetReadError, ValueError):
                complete = False
                invalid_names.append(path.stem)

        source_key_counts: dict[str, int] = {}
        name_counts: dict[str, int] = {}
        for entry in entries:
            source_key_counts[entry.source_key] = (
                source_key_counts.get(entry.source_key, 0) + 1)
            name_counts[entry.name] = name_counts.get(entry.name, 0) + 1
        duplicate_keys = {
            key for key, count in source_key_counts.items() if count > 1}
        duplicate_names = {
            name for name, count in name_counts.items() if count > 1}
        if duplicate_keys or duplicate_names:
            complete = False
            invalid_names.extend(
                entry.name for entry in entries
                if entry.source_key in duplicate_keys
                or entry.name in duplicate_names)
            entries = [
                entry for entry in entries
                if entry.source_key not in duplicate_keys
                and entry.name not in duplicate_names]
        if token() != snapshot_token:
            complete = False
        return BundledCatalogSnapshot(
            token=snapshot_token,
            entries=tuple(entries),
            invalid_names=tuple(dict.fromkeys(invalid_names)),
            complete=complete,
        )


class BundledPresetRegistry:
    """Project one bundled source snapshot into the Preset namespace."""

    def __init__(
            self, runtime: Callable[[], BundledRuntime], *,
            database_token: Callable[[], tuple],
            tracked_files: Callable[[sqlite3.Connection], dict[int, dict]],
            file_key: Callable[[int], str],
            quarantine_file: Callable[[Path, Path], Path | None],
            warn_after_commit: Callable[..., None],
    ) -> None:
        self._runtime = runtime
        self._database_token = database_token
        self._tracked_files = tracked_files
        self._file_key = file_key
        self._quarantine_file = quarantine_file
        self._warn_after_commit = warn_after_commit
        self._lock = threading.RLock()
        self.registered_token: tuple | None = None

    def registration_token(self, source_token: Callable[[], tuple]) -> tuple:
        return (self._database_token(), source_token())

    def is_current(self, source_token: Callable[[], tuple]) -> bool:
        current = self.registration_token(source_token)
        with self._lock:
            return self.registered_token == current

    def finalize(
            self, candidate: tuple | None,
            source_token: Callable[[], tuple]) -> None:
        current = self.registration_token(source_token)
        with self._lock:
            if candidate is not None and current == candidate:
                self.registered_token = candidate
            elif self.registered_token == candidate:
                self.registered_token = None

    def _tracked_runtime_path(
            self, tracked: dict[int, dict], preset_id: int) -> Path | None:
        file_name = tracked.get(preset_id, {}).get("file")
        if not isinstance(file_name, str) or Path(file_name).name != file_name:
            return None
        return Path(self._runtime().PRESETS_DIR) / file_name

    @staticmethod
    def _temporary_name(
            conn: sqlite3.Connection, preset_id: int,
            reserved_names: set[str]) -> str:
        stem = f"__gigbuddy_bundled_sync_{preset_id}"
        candidate = stem
        counter = 1
        while (candidate in reserved_names or conn.execute(
                "SELECT 1 FROM presets WHERE name = ?", (candidate,)
        ).fetchone() is not None):
            candidate = f"{stem}_{counter}"
            counter += 1
        return candidate

    def register(
            self, snapshot: BundledCatalogSnapshot, *,
            source_token: Callable[[], tuple],
    ) -> BundledRegistration:
        runtime = self._runtime()
        catalog_snapshot = snapshot.token
        entries = list(snapshot.entries)
        invalid_names = list(snapshot.invalid_names)
        catalog_complete = snapshot.complete

        with self._lock:
            managed: list[BundledPresetEntry] = []
            runtime_files_to_quarantine: set[Path] = set()
            candidate_token: tuple | None = None
            now = datetime.now(timezone.utc).isoformat()
            with runtime.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE presets SET source='user', source_key=NULL "
                    "WHERE source='bundled' AND "
                    "(source_key IS NULL OR TRIM(source_key)='')"
                )
                tracked: dict[int, dict] | None = None
                valid_keys = {entry.source_key for entry in entries}
                entry_by_key = {entry.source_key: entry for entry in entries}

                active_row = conn.execute(
                    "SELECT value FROM settings WHERE key='active_preset'"
                ).fetchone()
                active_bundled_key: str | None = None
                if active_row is not None:
                    active_owner = conn.execute(
                        "SELECT source, source_key FROM presets WHERE name = ?",
                        (active_row["value"],),
                    ).fetchone()
                    if preset_owned_by_bundle(active_owner):
                        active_bundled_key = active_owner["source_key"]

                initial_owned_rows = conn.execute(
                    "SELECT * FROM presets "
                    "WHERE source='bundled' AND source_key IS NOT NULL "
                    "AND TRIM(source_key)!=''"
                ).fetchall()
                initial_by_key = {
                    row["source_key"]: row for row in initial_owned_rows}
                initial_by_name = {
                    row["name"]: row for row in initial_owned_rows}
                initial_bundled_rows = [dict(row) for row in initial_owned_rows]
                initial_bundled_ids = {
                    int(row["id"]) for row in initial_bundled_rows}
                file_key_prefix = self._file_key(0)[:-1]

                def bundled_file_settings(preset_ids: set[int]) -> dict[int, str]:
                    result: dict[int, str] = {}
                    for setting in conn.execute(
                        "SELECT key, value FROM settings WHERE key LIKE ?",
                        (f"{file_key_prefix}%",),
                    ).fetchall():
                        suffix = setting["key"][len(file_key_prefix):]
                        if suffix.isdigit() and int(suffix) in preset_ids:
                            result[int(suffix)] = setting["value"]
                    return result

                initial_file_settings = bundled_file_settings(
                    initial_bundled_ids)
                identity_conflict_keys: set[str] = set()
                for entry in entries:
                    owner = initial_by_name.get(entry.name)
                    if (entry.source_key not in initial_by_key
                            and owner is not None
                            and owner["source_key"] != entry.source_key):
                        identity_conflict_keys.add(entry.source_key)
                        catalog_complete = False
                        if entry.name not in invalid_names:
                            invalid_names.append(entry.name)

                if catalog_complete and source_token() != catalog_snapshot:
                    catalog_complete = False
                if catalog_complete:
                    stale_rows = conn.execute(
                        "SELECT * FROM presets WHERE source='bundled' "
                        "AND source_key IS NOT NULL AND TRIM(source_key)!=''"
                    ).fetchall()
                    for stale in stale_rows:
                        if stale["source_key"] in valid_keys:
                            continue
                        preset_id = int(stale["id"])
                        if tracked is None:
                            tracked = self._tracked_files(conn)
                        runtime_path = self._tracked_runtime_path(
                            tracked, preset_id)
                        if runtime_path is not None:
                            # The row is removed before editable reconciliation
                            # can inspect its tracking token. Preserve the
                            # associated file rather than deleting user edits.
                            runtime_files_to_quarantine.add(runtime_path)
                        conn.execute(
                            "DELETE FROM settings WHERE key = ?",
                            (self._file_key(preset_id),),
                        )
                        conn.execute(
                            "DELETE FROM presets WHERE id = ?", (preset_id,))

                owned_rows = conn.execute(
                    "SELECT id, name, source, source_key, note, chain_json "
                    "FROM presets WHERE source='bundled' "
                    "AND source_key IS NOT NULL AND TRIM(source_key)!=''"
                ).fetchall()
                owned_by_key = {
                    row["source_key"]: row for row in owned_rows}
                name_owners = {
                    row["name"]: row for row in conn.execute(
                        "SELECT id, name, source, source_key FROM presets")
                }
                blocked_keys: set[str] = set(identity_conflict_keys)
                for entry in entries:
                    existing = owned_by_key.get(entry.source_key)
                    owner = name_owners.get(entry.name)
                    if (owner is None or (existing is not None
                                          and owner["id"] == existing["id"])):
                        continue
                    if (not preset_owned_by_bundle(owner)
                            or owner["source_key"] not in entry_by_key):
                        blocked_keys.add(entry.source_key)

                changed = True
                while changed:
                    changed = False
                    for entry in entries:
                        if entry.source_key in blocked_keys:
                            continue
                        existing = owned_by_key.get(entry.source_key)
                        owner = name_owners.get(entry.name)
                        if (owner is not None
                                and (existing is None
                                     or owner["id"] != existing["id"])
                                and owner["source_key"] in blocked_keys):
                            blocked_keys.add(entry.source_key)
                            changed = True

                desired_names = {entry.name for entry in entries}
                for entry in entries:
                    existing = owned_by_key.get(entry.source_key)
                    if (existing is None
                            or existing["name"] == entry.name
                            or entry.source_key in blocked_keys):
                        continue
                    temporary_name = self._temporary_name(
                        conn, int(existing["id"]), desired_names)
                    conn.execute(
                        "UPDATE presets SET name=? WHERE id=?",
                        (temporary_name, int(existing["id"])),
                    )

                for entry in entries:
                    if entry.source_key in blocked_keys:
                        continue
                    existing = conn.execute(
                        "SELECT id, name, source, source_key, note, chain_json "
                        "FROM presets WHERE source = 'bundled' "
                        "AND source_key = ?",
                        (entry.source_key,),
                    ).fetchone()
                    if existing is None:
                        name_match = conn.execute(
                            "SELECT id, name, source, source_key, note, "
                            "chain_json FROM presets WHERE name = ?",
                            (entry.name,),
                        ).fetchone()
                        if name_match is not None:
                            if preset_owned_by_bundle(name_match):
                                catalog_complete = False
                                if entry.name not in invalid_names:
                                    invalid_names.append(entry.name)
                            continue
                    if existing and not preset_owned_by_bundle(existing):
                        continue
                    if existing:
                        name_owner = conn.execute(
                            "SELECT id, source, source_key FROM presets "
                            "WHERE name = ?",
                            (entry.name,),
                        ).fetchone()
                        if (name_owner is not None
                                and int(name_owner["id"])
                                != int(existing["id"])):
                            catalog_complete = False
                            if entry.name not in invalid_names:
                                invalid_names.append(entry.name)
                            continue

                    chain_json = json.dumps(entry.chain, ensure_ascii=False)
                    if existing:
                        if (existing["source_key"] == entry.source_key
                                and existing["name"] == entry.name
                                and existing["note"] == entry.note
                                and existing["chain_json"] == chain_json):
                            managed.append(entry)
                            continue
                        conn.execute(
                            "UPDATE presets SET name=?, note=?, chain_json=?, "
                            "source='bundled', source_key=?, updated_at=? "
                            "WHERE id=?",
                            (entry.name, entry.note, chain_json,
                             entry.source_key, now, int(existing["id"])),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO presets "
                            "(name, note, chain_json, source, source_key, "
                            "created_at, updated_at) "
                            "VALUES (?, ?, ?, 'bundled', ?, ?, ?)",
                            (entry.name, entry.note, chain_json,
                             entry.source_key, now, now),
                        )
                    managed.append(entry)

                if active_bundled_key is not None:
                    active_target = conn.execute(
                        "SELECT name FROM presets WHERE source='bundled' "
                        "AND source_key=?",
                        (active_bundled_key,),
                    ).fetchone()
                    if active_target is None:
                        conn.execute(
                            "DELETE FROM settings WHERE key='active_preset'")
                    else:
                        conn.execute(
                            "UPDATE settings SET value=? "
                            "WHERE key='active_preset'",
                            (active_target["name"],),
                        )

                if source_token() != catalog_snapshot:
                    conn.rollback()
                    managed.clear()
                    runtime_files_to_quarantine.clear()
                else:
                    committed_bundled_rows = [
                        dict(row) for row in conn.execute(
                            "SELECT * FROM presets WHERE source='bundled' "
                            "ORDER BY id"
                        ).fetchall()
                    ]
                    committed_bundled_ids = {
                        int(row["id"]) for row in committed_bundled_rows}
                    committed_file_settings = bundled_file_settings(
                        committed_bundled_ids)
                    committed_active_value = None
                    if active_bundled_key is not None:
                        committed_active = conn.execute(
                            "SELECT value FROM settings "
                            "WHERE key='active_preset'"
                        ).fetchone()
                        committed_active_value = (
                            committed_active["value"]
                            if committed_active is not None else None)
                    conn.commit()
                    if source_token() != catalog_snapshot:
                        conn.execute("BEGIN IMMEDIATE")
                        try:
                            current_rows = [
                                dict(row) for row in conn.execute(
                                    "SELECT * FROM presets "
                                    "WHERE source='bundled' ORDER BY id"
                                ).fetchall()
                            ]
                            current_ids = {
                                int(row["id"]) for row in current_rows}
                            current_file_settings = bundled_file_settings(
                                current_ids)
                            current_active_value = committed_active_value
                            if active_bundled_key is not None:
                                current_active = conn.execute(
                                    "SELECT value FROM settings "
                                    "WHERE key='active_preset'"
                                ).fetchone()
                                current_active_value = (
                                    current_active["value"]
                                    if current_active is not None else None)
                            user_name_conflict = any(
                                (owner := conn.execute(
                                    "SELECT source FROM presets WHERE name=?",
                                    (row["name"],),
                                ).fetchone()) is not None
                                and owner["source"] != "bundled"
                                for row in initial_bundled_rows
                            )
                            projection_unchanged = (
                                current_rows == committed_bundled_rows
                                and current_file_settings
                                == committed_file_settings
                                and current_active_value
                                == committed_active_value
                                and not user_name_conflict
                            )
                            if not projection_unchanged:
                                conn.rollback()
                                self._warn_after_commit(
                                    "Could not restore the previous bundled "
                                    "Preset projection because Preset state "
                                    "changed concurrently",
                                    stacklevel=2,
                                )
                            else:
                                for current in current_rows:
                                    current_id = int(current["id"])
                                    if current_id not in initial_bundled_ids:
                                        conn.execute(
                                            "DELETE FROM settings WHERE key = ?",
                                            (self._file_key(current_id),),
                                        )
                                        conn.execute(
                                            "DELETE FROM presets WHERE id = ?",
                                            (current_id,),
                                        )

                                # Temporarily clear names so a catalog rename or
                                # swap can be restored without hitting UNIQUE(name).
                                reserved_names = {
                                    row["name"] for row in initial_bundled_rows}
                                for current in current_rows:
                                    current_id = int(current["id"])
                                    if current_id not in initial_bundled_ids:
                                        continue
                                    conn.execute(
                                        "UPDATE presets SET name=? WHERE id=?",
                                        (self._temporary_name(
                                            conn, current_id, reserved_names),
                                         current_id),
                                    )

                                for row in initial_bundled_rows:
                                    current = conn.execute(
                                        "SELECT source FROM presets WHERE id=?",
                                        (row["id"],),
                                    ).fetchone()
                                    if (current is not None
                                            and current["source"] != "bundled"):
                                        continue
                                    if current is None:
                                        conn.execute(
                                            "INSERT INTO presets "
                                            "(id, name, note, chain_json, source, "
                                            "source_key, created_at, updated_at) "
                                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                            (row["id"], row["name"], row["note"],
                                             row["chain_json"], row["source"],
                                             row["source_key"], row["created_at"],
                                             row["updated_at"]),
                                        )
                                    else:
                                        conn.execute(
                                            "UPDATE presets SET name=?, note=?, "
                                            "chain_json=?, source=?, source_key=?, "
                                            "created_at=?, updated_at=? WHERE id=?",
                                            (row["name"], row["note"],
                                             row["chain_json"], row["source"],
                                             row["source_key"], row["created_at"],
                                             row["updated_at"], row["id"]),
                                        )

                                for preset_id in initial_bundled_ids:
                                    conn.execute(
                                        "DELETE FROM settings WHERE key = ?",
                                        (self._file_key(preset_id),),
                                    )
                                for preset_id, value in initial_file_settings.items():
                                    conn.execute(
                                        "INSERT INTO settings (key, value) "
                                        "VALUES (?, ?) "
                                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                        (self._file_key(preset_id), value),
                                    )
                                if active_bundled_key is not None:
                                    active_target = next(
                                        (row["name"]
                                         for row in initial_bundled_rows
                                         if row["source_key"]
                                         == active_bundled_key),
                                        None,
                                    )
                                    if active_target is not None:
                                        conn.execute(
                                            "INSERT INTO settings (key, value) "
                                            "VALUES ('active_preset', ?) "
                                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                            (active_target,),
                                        )
                                conn.commit()
                        except Exception:
                            conn.rollback()
                            self._warn_after_commit(
                                "Could not restore the previous bundled "
                                "Preset projection after a catalog race",
                                stacklevel=2,
                            )
                        managed.clear()
                        runtime_files_to_quarantine.clear()
                    else:
                        proposed = self.registration_token(source_token)
                        if proposed[1] == catalog_snapshot:
                            candidate_token = proposed

                live_state_keys = frozenset(
                    (row["source_key"]
                     if isinstance(row["source_key"], str)
                     and row["source_key"] else f"legacy:{row['name']}")
                    for row in conn.execute(
                        "SELECT name, source_key FROM presets "
                        "WHERE source = 'bundled'")
                )

            for runtime_path in runtime_files_to_quarantine:
                try:
                    if runtime_path.is_file():
                        quarantined = self._quarantine_file(
                            runtime_path, Path(runtime.PRESETS_DIR))
                        if quarantined is None:
                            self._warn_after_commit(
                                "Could not quarantine stale Preset file "
                                f"{runtime_path}",
                                stacklevel=2,
                            )
                except OSError as exc:
                    self._warn_after_commit(
                        "Could not quarantine stale Preset file "
                        f"{runtime_path}: {exc}",
                        stacklevel=2,
                    )

            return BundledRegistration(
                managed=tuple(managed),
                invalid_names=tuple(invalid_names),
                live_state_keys=live_state_keys,
                candidate_token=candidate_token,
            )
