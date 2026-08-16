"""Transient model preparation for repository-owned Presets.

This module deliberately owns no Preset rows or JSON documents.  It derives
availability from verified local model files and keeps only process-local
PREPARING/UNAVAILABLE state while downloads are in flight.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from preset_document import semantic_chain_key


class PreparationRuntime(Protocol):
    def _installed_model_ids(self, model_ids: list[int]) -> set[int]: ...

    def import_tone(
            self, tone_id: int, *, quiet: bool,
            model_ids: list[int]) -> dict: ...


class PreparationEntry(Protocol):
    source_key: str
    name: str
    chain: dict
    model_sources: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class PreparationGeneration:
    """Semantic identity of one repository-owned preparation request."""

    chain: tuple
    model_sources: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class PreparationReport:
    total: int = 0
    ready: int = 0
    preparing: int = 0
    failed: int = 0
    failed_presets: tuple[str, ...] = ()


class ModelPreparation:
    """Own built-in model downloads and their process-local state."""

    def __init__(self, runtime: Callable[[], PreparationRuntime]) -> None:
        self._runtime = runtime
        self._state_lock = threading.RLock()
        self._download_lock = threading.Lock()
        self._states: dict[str, dict[str, str]] = {}
        self._state_models: dict[str, tuple[int, ...]] = {}
        self._state_generations: dict[str, PreparationGeneration] = {}
        self._live_generations: dict[str, PreparationGeneration] = {}

    @staticmethod
    def state_key(source_key: str | None, name: str) -> str:
        return (source_key if isinstance(source_key, str) and source_key
                else f"legacy:{name}")

    @staticmethod
    def model_ids_from_chain(chain: dict | None) -> list[int]:
        if not isinstance(chain, dict):
            return []
        return [
            int(slot["model_id"])
            for slot in chain.get("slots", [])
            if isinstance(slot, dict)
            and isinstance(slot.get("model_id"), int)
            and not isinstance(slot.get("model_id"), bool)
        ]

    @classmethod
    def model_ids(cls, entries: Sequence[PreparationEntry]) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        for entry in entries:
            for model_id in cls.model_ids_from_chain(entry.chain):
                if model_id not in seen:
                    seen.add(model_id)
                    ids.append(model_id)
        return ids

    def missing_model_ids(
            self, chain: dict | None,
            installed_ids: set[int] | None = None) -> list[int]:
        model_ids = self.model_ids_from_chain(chain)
        installed = (
            self._runtime()._installed_model_ids(model_ids)
            if installed_ids is None else installed_ids)
        return [model_id for model_id in model_ids if model_id not in installed]

    def snapshot(self) -> dict[str, dict[str, str]]:
        with self._state_lock:
            return {key: dict(value) for key, value in self._states.items()}

    def state_models_snapshot(self) -> dict[str, tuple[int, ...]]:
        with self._state_lock:
            return dict(self._state_models)

    def state_generations_snapshot(self) -> dict[str, PreparationGeneration]:
        with self._state_lock:
            return dict(self._state_generations)

    def reconcile_live_entries(
            self, entries: Sequence[PreparationEntry],
            live_keys: set[str]) -> None:
        """Publish the generations owned by the latest committed catalog."""
        expected = self.state_generations_for_entries(entries)
        with self._state_lock:
            for state_key in list(self._live_generations):
                if state_key not in live_keys:
                    self._live_generations.pop(state_key, None)
            for state_key, generation in expected.items():
                previous = self._live_generations.get(state_key)
                self._live_generations[state_key] = generation
                if (previous is not None and previous != generation
                        and self._state_generations.get(state_key)
                        != generation):
                    self._states.pop(state_key, None)
                    self._state_models.pop(state_key, None)
                    self._state_generations.pop(state_key, None)

    @classmethod
    def state_models_for_entries(
            cls, entries: Sequence[PreparationEntry]) -> dict[str, tuple[int, ...]]:
        return {
            cls.state_key(entry.source_key, entry.name): tuple(
                cls.model_ids_from_chain(entry.chain))
            for entry in entries
        }

    @classmethod
    def generation_for_entry(
            cls, entry: PreparationEntry) -> PreparationGeneration | None:
        chain = semantic_chain_key(entry.chain)
        if chain is None:
            return None
        return PreparationGeneration(chain, tuple(entry.model_sources))

    @classmethod
    def state_generations_for_entries(
            cls, entries: Sequence[PreparationEntry],
    ) -> dict[str, PreparationGeneration]:
        result: dict[str, PreparationGeneration] = {}
        for entry in entries:
            generation = cls.generation_for_entry(entry)
            if generation is not None:
                result[cls.state_key(entry.source_key, entry.name)] = generation
        return result

    @classmethod
    def generation_for_chain(
            cls, chain: object,
            model_sources: Sequence[tuple[int, int]] = (),
    ) -> PreparationGeneration | None:
        semantic = semantic_chain_key(chain)
        if semantic is None:
            return None
        return PreparationGeneration(semantic, tuple(model_sources))

    def prune(self, live_keys: set[str]) -> None:
        with self._state_lock:
            for state_key in list(self._states):
                if state_key not in live_keys:
                    self._states.pop(state_key, None)
                    self._state_models.pop(state_key, None)
                    self._state_generations.pop(state_key, None)
            for state_key in list(self._live_generations):
                if state_key not in live_keys:
                    self._live_generations.pop(state_key, None)

    def mark_unavailable(
            self, expected: dict[
                str, tuple[int, ...] | PreparationGeneration],
            error: BaseException) -> None:
        """Finish affected preparation after an unexpected outer failure."""
        detail = f"{type(error).__name__}: {error}"
        with self._state_lock:
            for state_key, expected_generation in expected.items():
                if isinstance(expected_generation, PreparationGeneration):
                    if self._live_generations.get(state_key) != expected_generation:
                        continue
                    current_generation = self._state_generations.get(state_key)
                    if (current_generation is not None
                            and current_generation != expected_generation):
                        continue
                    model_ids = tuple(
                        slot[0] for slot in expected_generation.chain[0]
                        if slot[0] is not None)
                    self._state_generations[state_key] = expected_generation
                else:
                    model_ids = expected_generation
                    current_models = self._state_models.get(state_key)
                    if (current_models is not None
                            and current_models != model_ids):
                        continue
                self._states[state_key] = {
                    "status": "UNAVAILABLE",
                    "error": detail,
                }
                self._state_models[state_key] = model_ids

    def availability(
            self, name: str, chain: dict | None,
            installed_ids: set[int] | None = None, *,
            source_key: str | None = None) -> str:
        """Derive availability from verified files and explicit preparation."""
        if not self.missing_model_ids(chain, installed_ids):
            return "READY"
        state_key = self.state_key(source_key, name)
        semantic = semantic_chain_key(chain)
        with self._state_lock:
            live_generation = self._live_generations.get(state_key)
            state = (
                self._states.get(state_key, {}).get("status")
                if (semantic is not None
                    and (generation := self._state_generations.get(state_key))
                    is not None
                    and generation == live_generation
                    and live_generation.chain == semantic)
                else None
            )
        return state if state in {"PREPARING", "UNAVAILABLE"} else "UNAVAILABLE"

    def synchronize(
            self, entries: Sequence[PreparationEntry], invalid_names: Sequence[str],
            *, quiet: bool, download: bool, mark_preparing: bool,
            current_generations: Callable[
                [], dict[str, PreparationGeneration]],
    ) -> PreparationReport:
        """Prepare selected entries without owning their durable registration."""
        selected = list(entries)
        model_ids = self.model_ids(selected)
        runtime = self._runtime()
        installed_ids = runtime._installed_model_ids(model_ids)
        live_generations = current_generations()
        with self._state_lock:
            for entry in selected:
                if (not self.missing_model_ids(entry.chain, installed_ids)
                        or not (download or mark_preparing)):
                    continue
                entry_models = tuple(self.model_ids_from_chain(entry.chain))
                entry_generation = self.generation_for_entry(entry)
                if (entry_generation is None
                        or live_generations.get(entry.source_key)
                        != entry_generation):
                    continue
                state_key = self.state_key(entry.source_key, entry.name)
                self._states[state_key] = {
                    "status": "PREPARING", "error": "",
                }
                self._state_models[state_key] = entry_models
                self._state_generations[state_key] = entry_generation

        if download:
            # Registration and readers must remain responsive during network I/O.
            with self._download_lock:
                _missing, errors = self._download_models(
                    selected, model_ids, quiet=quiet)
                installed_ids = runtime._installed_model_ids(model_ids)
            attempted_download = True
        else:
            errors = {}
            attempted_download = False

        current = current_generations()
        with self._state_lock:
            current_selected: list[PreparationEntry] = []
            for entry in selected:
                state_key = self.state_key(entry.source_key, entry.name)
                model_ids = tuple(self.model_ids_from_chain(entry.chain))
                entry_generation = self.generation_for_entry(entry)
                if (entry_generation is not None
                        and current.get(entry.source_key) == entry_generation):
                    current_selected.append(entry)
                elif self._state_generations.get(state_key) == entry_generation:
                    self._states.pop(state_key, None)
                    self._state_models.pop(state_key, None)
                    self._state_generations.pop(state_key, None)

            preparing: list[str] = []
            failed_names = list(invalid_names)
            ready = 0
            for entry in current_selected:
                name = entry.name
                missing = self.missing_model_ids(entry.chain, installed_ids)
                state_key = self.state_key(entry.source_key, name)
                model_ids = tuple(self.model_ids_from_chain(entry.chain))
                generation = self.generation_for_entry(entry)
                if generation is None:
                    continue
                if not missing:
                    ready += 1
                    self._states[state_key] = {
                        "status": "READY", "error": "",
                    }
                    self._state_models[state_key] = model_ids
                    self._state_generations[state_key] = generation
                elif attempted_download:
                    failed_names.append(name)
                    detail = "; ".join(
                        errors.get(model_id, "model file was not downloaded")
                        for model_id in missing)
                    self._states[state_key] = {
                        "status": "UNAVAILABLE", "error": detail,
                    }
                    self._state_models[state_key] = model_ids
                    self._state_generations[state_key] = generation
                elif mark_preparing:
                    preparing.append(name)
                elif (
                    self._state_generations.get(state_key) == generation
                    and self._states.get(
                        state_key, {}).get("status") == "UNAVAILABLE"
                ):
                    failed_names.append(name)
                else:
                    preparing.append(name)

            failed_names = sorted(set(failed_names))
            return PreparationReport(
                total=len(current_selected) + len(invalid_names),
                ready=ready,
                preparing=len(set(preparing)),
                failed=len(failed_names),
                failed_presets=tuple(failed_names),
            )

    def _download_models(
            self, entries: Sequence[PreparationEntry], model_ids: list[int], *,
            quiet: bool,
    ) -> tuple[set[int], dict[int, str]]:
        runtime = self._runtime()
        installed = runtime._installed_model_ids(model_ids)
        missing = [model_id for model_id in model_ids if model_id not in installed]
        tone_by_model: dict[int, int] = {}
        ambiguous: set[int] = set()
        for entry in entries:
            for model_id, tone_id in entry.model_sources:
                previous = tone_by_model.get(model_id)
                if previous is not None and previous != tone_id:
                    ambiguous.add(model_id)
                else:
                    tone_by_model[model_id] = tone_id

        grouped: dict[int, list[int]] = {}
        unresolved: set[int] = set()
        errors: dict[int, str] = {}
        for model_id in missing:
            tone_id = tone_by_model.get(model_id)
            if model_id in ambiguous:
                unresolved.add(model_id)
                errors[model_id] = "bundled documents disagree on the parent tone"
            elif tone_id is None:
                unresolved.add(model_id)
                errors[model_id] = "bundled document does not declare a parent tone"
            else:
                grouped.setdefault(tone_id, []).append(model_id)

        for tone_id, tone_model_ids in grouped.items():
            try:
                runtime.import_tone(
                    tone_id, quiet=quiet, model_ids=tone_model_ids)
            except Exception as exc:
                unresolved.update(tone_model_ids)
                for model_id in tone_model_ids:
                    errors[model_id] = str(exc)

        installed = runtime._installed_model_ids(model_ids)
        still_missing = {
            model_id for model_id in model_ids
            if model_id in unresolved or model_id not in installed
        }
        for model_id in still_missing:
            errors.setdefault(model_id, "model file was not downloaded")
        return still_missing, errors
