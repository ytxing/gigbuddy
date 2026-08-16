"""Preset catalog lifecycle and read projection.

The product-facing interface remains in ``library.py``. This module owns the
catalog state behind that compatibility adapter so callers do not coordinate
repository JSON, editable JSON, SQLite, and model availability themselves.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, TypeAlias

from preset_bundled import (
    BUNDLED_PRESET_DOCUMENT_KIND,
    BundledCatalogSnapshot as _BundledCatalogSnapshot,
    BundledPresetEntry as _BundledPresetEntry,
    BundledPresetReadError,
    BundledPresetRegistry,
    BundledPresetSource,
    preset_owned_by_bundle,
)
from preset_editable import (
    PRESET_DOCUMENT_KIND,
    PRESET_FILE_SETTING_PREFIX,
    PRESET_UPDATED_UNSET,
    SHAREABLE_PRESET_DOCUMENT_KIND,
    EditablePresetStore,
    PresetConflictError,
    PresetRecoveryError,
    _warn_after_commit,
    ensure_preset_mutable,
    is_shareable_preset_file,
    preset_file_key,
    quarantine_preset_file,
    tracked_preset_files,
)
from preset_preparation import ModelPreparation, PreparationGeneration


class CatalogRuntime(Protocol):
    """Local persistence and download Adapter supplied by ``library.py``."""

    DB_FILE: Path
    PRESETS_DIR: Path
    BUNDLED_PRESETS_DIR: Path

    def connect(self) -> sqlite3.Connection: ...
    def scan_local_packs(self) -> list[dict]: ...
    def _canonical_preset_chain(
            self, raw: object, *, scan_local: bool = True) -> dict: ...
    def _validate_preset_draft_references(
            self, chain: dict, *, scan_local: bool = True) -> None: ...
    def _preset_has_unsupported_registered_asset(self, chain: dict) -> bool: ...
    def _installed_model_ids(self, model_ids: list[int]) -> set[int]: ...
    def import_tone(
            self, tone_id: int, *, quiet: bool,
            model_ids: list[int]) -> dict: ...


@dataclass(frozen=True, slots=True)
class ByName:
    name: str


@dataclass(frozen=True, slots=True)
class ById:
    preset_id: int


@dataclass(frozen=True, slots=True)
class AllPresets:
    pass


CatalogQuery: TypeAlias = ByName | ById | AllPresets
CatalogRead: TypeAlias = dict | list[dict] | None


@dataclass(frozen=True, slots=True)
class BundleTarget:
    names: tuple[str, ...] | None = None
    keys: tuple[str, ...] | None = None

    @classmethod
    def from_sequences(
            cls, names, keys) -> "BundleTarget":
        return cls(
            names=None if names is None else tuple(str(name) for name in names),
            keys=None if keys is None else tuple(str(key) for key in keys),
        )


@dataclass(frozen=True, slots=True)
class RefreshCatalog:
    pass


@dataclass(frozen=True, slots=True)
class IndexCatalog:
    target: BundleTarget = BundleTarget()


@dataclass(frozen=True, slots=True)
class AnnouncePreparation:
    target: BundleTarget = BundleTarget()


@dataclass(frozen=True, slots=True)
class PrepareCatalog:
    target: BundleTarget = BundleTarget()
    quiet: bool = False


SyncCommand: TypeAlias = (
    RefreshCatalog | IndexCatalog | AnnouncePreparation | PrepareCatalog)


@dataclass(frozen=True, slots=True)
class SyncReport:
    total: int = 0
    ready: int = 0
    preparing: int = 0
    failed: int = 0
    failed_presets: tuple[str, ...] = ()

    def as_legacy_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "ready": self.ready,
            "preparing": self.preparing,
            "failed": self.failed,
            "failed_presets": list(self.failed_presets),
        }


class PresetCatalog:
    """Application-facing orchestration for Preset catalog operations.

    Paths and functions are resolved from the runtime on every operation. This
    is intentional: installed paths vary, and the existing test interface
    replaces ``library`` paths and external adapters dynamically.
    """

    def __init__(self, runtime: Callable[[], CatalogRuntime]) -> None:
        self._runtime = runtime
        self._bundled_source = BundledPresetSource(runtime)
        self._editable = EditablePresetStore(
            runtime, reserved_names=self._reserved_bundled_names)
        self.reconcile_lock = self._editable.reconcile_lock
        self._bundled_registry = BundledPresetRegistry(
            runtime,
            database_token=self._editable.database_token,
            tracked_files=tracked_preset_files,
            file_key=preset_file_key,
            quarantine_file=quarantine_preset_file,
            warn_after_commit=_warn_after_commit,
        )
        self._preparation = ModelPreparation(runtime)

    def _catalog_token(self) -> tuple:
        return self._bundled_source.token()

    @staticmethod
    def _parse_document(path: Path) -> _BundledPresetEntry:
        return BundledPresetSource.parse(path)

    def _scan_catalog(self) -> _BundledCatalogSnapshot:
        return self._bundled_source.scan(
            token=self._catalog_token,
            parse=self._parse_document,
        )

    @staticmethod
    def _reserved_names_from_snapshot(
            snapshot: _BundledCatalogSnapshot) -> set[str]:
        return {
            entry.name for entry in snapshot.entries
        } | {
            name for name in snapshot.invalid_names
            if isinstance(name, str) and name
        }

    def _reserved_bundled_names(self) -> set[str]:
        return self._reserved_names_from_snapshot(self._scan_catalog())

    @staticmethod
    def _model_ids_from_chain(chain: dict | None) -> list[int]:
        return ModelPreparation.model_ids_from_chain(chain)

    def synchronize(self, command: SyncCommand) -> SyncReport:
        """Converge sources according to one explicit caller intent."""
        if isinstance(command, RefreshCatalog):
            snapshot = self._scan_catalog()
            if not self._bundled_registry.is_current(self._catalog_token):
                self._synchronize_bundled(
                    quiet=False,
                    download=False,
                    target=BundleTarget(),
                    mark_preparing=False,
                    snapshot=snapshot,
                    reconcile_editable=True,
                )
            else:
                self._reconcile_editable(snapshot)
            return SyncReport()
        if isinstance(command, IndexCatalog):
            return self._synchronize_bundled(
                quiet=False,
                download=False,
                target=command.target,
                mark_preparing=False,
            )
        if isinstance(command, AnnouncePreparation):
            failure_generations = self._preparation_generations_for_target(
                command.target)
            try:
                return self._synchronize_bundled(
                    quiet=False,
                    download=False,
                    target=command.target,
                    mark_preparing=True,
                    failure_generations=failure_generations,
                )
            except Exception as exc:
                self._mark_preparation_unavailable(failure_generations, exc)
                raise
        if isinstance(command, PrepareCatalog):
            failure_generations = self._preparation_generations_for_target(
                command.target)
            try:
                return self._synchronize_bundled(
                    quiet=command.quiet,
                    download=True,
                    target=command.target,
                    mark_preparing=False,
                    failure_generations=failure_generations,
                )
            except Exception as exc:
                self._mark_preparation_unavailable(failure_generations, exc)
                raise
        raise TypeError(
            f"unsupported catalog command: {type(command).__name__}")

    def _preparation_generations_for_target(
            self, target: BundleTarget) -> dict[
                str, tuple[int, ...] | PreparationGeneration]:
        """Capture the preparation generations touched by a command."""
        expected: dict[str, tuple[int, ...] | PreparationGeneration] = {
            **self._preparation.state_models_snapshot(),
            **self._preparation.state_generations_snapshot(),
        }
        states = self._preparation.snapshot()
        requested_keys = set(target.keys or ())
        requested_names = set(target.names or ())
        all_targets = target.names is None and target.keys is None
        for state_key, state in states.items():
            if state.get("status") != "PREPARING":
                continue
            if (all_targets or state_key in requested_keys
                    or state_key in {f"legacy:{name}"
                                     for name in requested_names}):
                # Older in-memory state may predate the model-generation map.
                # An empty tuple lets it be completed without weakening checks
                # for states that do carry a concrete generation.
                expected.setdefault(state_key, ())
        try:
            with self._runtime().connect() as conn:
                rows = conn.execute(
                    "SELECT name, source_key, chain_json FROM presets "
                    "WHERE source='bundled' AND source_key IS NOT NULL "
                    "AND TRIM(source_key)!=''"
                ).fetchall()
        except Exception:
            rows = []
        entries_by_key: dict[str, _BundledPresetEntry] | None = None
        for row in rows:
            state_key = ModelPreparation.state_key(
                row["source_key"], row["name"])
            selected = (
                all_targets
                or row["source_key"] in requested_keys
                or row["name"] in requested_names
            )
            if not selected:
                continue
            requested_keys.add(row["source_key"])
            if (state_key not in expected
                    and states.get(state_key, {}).get("status") == "PREPARING"):
                try:
                    chain = json.loads(row["chain_json"])
                    if entries_by_key is None:
                        try:
                            entries_by_key = {
                                entry.source_key: entry
                                for entry in self._scan_catalog().entries
                            }
                        except Exception:
                            entries_by_key = {}
                    entry = entries_by_key.get(row["source_key"])
                    generation = (
                        ModelPreparation.generation_for_chain(
                            chain, entry.model_sources)
                        if entry is not None else None
                    )
                    expected[state_key] = (
                        generation if generation is not None else tuple(
                            self._model_ids_from_chain(chain)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        return {
            state_key: generation for state_key, generation in expected.items()
            if all_targets or state_key in requested_keys
        }

    def _mark_preparation_unavailable(
            self, expected_models: dict[
                str, tuple[int, ...] | PreparationGeneration],
            error: BaseException) -> None:
        """Finish only the preparation generation that raised the error."""
        self._preparation.mark_unavailable(expected_models, error)

    def _ensure_registered(self) -> None:
        """Index a changed repository snapshot without preparing models."""
        if self._bundled_registry.is_current(self._catalog_token):
            return
        self.synchronize(IndexCatalog())

    def _reconcile_editable(
            self, snapshot: _BundledCatalogSnapshot | None = None) -> None:
        """Reconcile editable JSON while reserving repository display names."""
        if snapshot is None:
            snapshot = self._scan_catalog()
        with self.reconcile_lock:
            if self._catalog_token() != snapshot.token:
                return
            self._editable.reconcile(
                self._reserved_names_from_snapshot(snapshot))

    def change_token(self) -> tuple:
        """Return repository source signals without opening SQLite."""
        return self._catalog_token()

    def preparation_state_snapshot(self) -> dict[str, dict[str, str]]:
        """Return a detached view of transient built-in preparation state."""
        return self._preparation.snapshot()

    def read(self, query: CatalogQuery) -> CatalogRead:
        """Read the current SQLite projection without synchronizing sources."""
        if isinstance(query, ByName):
            return self._decorate(self._editable.read_by_name(query.name))
        if isinstance(query, ById):
            return self._decorate(
                self._editable.read_by_id(query.preset_id))
        if isinstance(query, AllPresets):
            presets = self._editable.read_all()
            bundled_ids = [
                model_id
                for preset in presets
                if preset_owned_by_bundle(preset)
                for model_id in self._model_ids_from_chain(preset.get("chain"))
            ]
            installed_ids = self._runtime()._installed_model_ids(bundled_ids)
            for preset in presets:
                if preset_owned_by_bundle(preset):
                    preset["availability"] = self._availability(
                        preset.get("name", ""),
                        preset.get("chain"),
                        installed_ids,
                        source_key=preset.get("source_key"),
                    )
            return presets
        raise TypeError(f"unsupported catalog query: {type(query).__name__}")

    def current_name(self) -> str | None:
        return self._editable.current_name()

    def set_active(self, name: str | None) -> None:
        self._editable.set_active(name)

    def assert_editable_name(self, name: str) -> None:
        self._ensure_registered()
        self._editable.assert_editable_name(name)

    def upsert_editable(
            self, name: str, chain: dict, note: str | None, *,
            set_active: bool = False,
            preserve_existing_note: bool = False,
    ) -> dict | None:
        return self._editable.upsert_editable(
            name,
            chain,
            note,
            set_active=set_active,
            preserve_existing_note=preserve_existing_note,
        )

    def delete_editable_by_name(self, name: str) -> bool:
        return self._editable.delete_editable_by_name(name)

    def delete_editable_by_id(self, preset_id: int) -> dict[str, object]:
        return self._editable.delete_editable_by_id(preset_id)

    def rename_editable(self, preset_id: int, new_name: str) -> dict | None:
        return self._editable.rename_editable(preset_id, new_name)

    def update_editable_note(
            self, preset_id: int, note: str | None) -> dict | None:
        return self._editable.update_editable_note(preset_id, note)

    def update_editable_draft(
            self, preset_id: int, chain: dict, note: str | None, *,
            expected_updated_at: str | None | object = PRESET_UPDATED_UNSET,
    ) -> dict | None:
        return self._editable.update_editable_draft(
            preset_id,
            chain,
            note,
            expected_updated_at=expected_updated_at,
        )

    def _synchronize_bundled(
            self, *, quiet: bool, download: bool, target: BundleTarget,
            mark_preparing: bool,
            snapshot: _BundledCatalogSnapshot | None = None,
            reconcile_editable: bool = False,
            failure_generations: dict[
                str, tuple[int, ...] | PreparationGeneration] | None = None,
    ) -> SyncReport:
        """Register repository Presets and optionally prepare their models."""
        if snapshot is None:
            snapshot = self._scan_catalog()
        # Registration can quarantine stale editable projections after its
        # SQLite commit. Keep that file phase serialized with editable writes;
        # model discovery and downloads below intentionally stay outside it.
        with self.reconcile_lock:
            registration = self._bundled_registry.register(
                snapshot, source_token=self._catalog_token)
            if reconcile_editable:
                self._reconcile_editable(snapshot)
            live_state_keys = set(registration.live_state_keys)
            self._preparation.reconcile_live_entries(
                registration.managed, live_state_keys)
            self._preparation.prune(live_state_keys)

        requested_names = (
            None if target.names is None else set(target.names))
        requested_keys = (
            None if target.keys is None else set(target.keys))
        selected = [
            entry for entry in registration.managed
            if ((requested_names is None and requested_keys is None)
                or (requested_names is not None
                    and entry.name in requested_names)
                or (requested_keys is not None
                    and entry.source_key in requested_keys))
        ]
        if requested_names is None and requested_keys is None:
            selected_invalid = list(registration.invalid_names)
        elif requested_names is not None:
            selected_invalid = [
                name for name in registration.invalid_names
                if name in requested_names]
        else:
            selected_invalid = []

        if failure_generations is not None:
            failure_generations.clear()
            failure_generations.update(
                self._preparation.state_generations_for_entries(selected))

        def current_generations() -> dict[str, PreparationGeneration]:
            current_snapshot = (
                self._scan_catalog()
                if (download or mark_preparing) else snapshot
            )
            entries_by_key = {
                entry.source_key: entry
                for entry in current_snapshot.entries
            }
            with self._runtime().connect() as conn:
                result: dict[str, PreparationGeneration] = {}
                for row in conn.execute(
                        "SELECT source_key, chain_json FROM presets "
                        "WHERE source = 'bundled' "
                        "AND source_key IS NOT NULL"):
                    entry = entries_by_key.get(row["source_key"])
                    if entry is None:
                        continue
                    try:
                        chain = json.loads(row["chain_json"])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    generation = ModelPreparation.generation_for_chain(
                        chain, entry.model_sources)
                    if generation is not None:
                        result[row["source_key"]] = generation
                return result

        try:
            prepared = self._preparation.synchronize(
                selected,
                selected_invalid,
                quiet=quiet,
                download=download,
                mark_preparing=mark_preparing,
                current_generations=current_generations,
            )
            self._bundled_registry.finalize(
                registration.candidate_token, self._catalog_token)
        except Exception as exc:
            if failure_generations is not None:
                self._preparation.mark_unavailable(failure_generations, exc)
            raise
        return SyncReport(
            total=prepared.total,
            ready=prepared.ready,
            preparing=prepared.preparing,
            failed=prepared.failed,
            failed_presets=prepared.failed_presets,
        )

    def _decorate(self, preset: dict | None) -> dict | None:
        if preset is None:
            return None
        if preset_owned_by_bundle(preset):
            preset["availability"] = self._availability(
                preset.get("name", ""), preset.get("chain"),
                source_key=preset.get("source_key"),
            )
        return preset

    def _availability(
            self, name: str, chain: dict | None,
            installed_ids: set[int] | None = None, *,
            source_key: str | None = None) -> str:
        return self._preparation.availability(
            name, chain, installed_ids, source_key=source_key)
