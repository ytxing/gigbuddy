"""Validation for path-free Preset documents.

Portable and repository-owned Presets share this chain shape. Keeping its
validation independent from SQLite, local Pack scans, and downloads lets every
caller interpret one document with the same rules.
"""

from __future__ import annotations

import math


PRESET_DEFAULTS = {"gain": 1.0, "master": 1.0, "quality": 1.0}
SLOT_GAIN_DEFAULT_DB = 0.0
SLOT_GAIN_MIN_DB = -24.0
SLOT_GAIN_MAX_DB = 24.0


def number(value: object, name: str, lower: float, upper: float) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Preset {name} must be a finite number")
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"Preset {name} must be between {lower} and {upper}")
    return value


def model_id(value: object, index: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Preset Slot {index + 1:02d} model_id is invalid")
    return value


def slot_gains(
        item: dict, index: int, *, preserve_explicit_defaults: bool = False,
) -> dict[str, int | float]:
    values: dict[str, int | float] = {}
    for key in ("input_gain_db", "output_gain_db"):
        value = number(
            item.get(key, SLOT_GAIN_DEFAULT_DB),
            f"Slot {index + 1:02d} {key}",
            SLOT_GAIN_MIN_DB,
            SLOT_GAIN_MAX_DB,
        )
        if (value != SLOT_GAIN_DEFAULT_DB
                or (preserve_explicit_defaults and key in item)):
            values[key] = value
    return values


def normalize_note(note: object) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise ValueError("Preset note must be a string")
    return note if note.strip() else ""


def parse_portable_slot(item: object, index: int) -> dict:
    """Validate one model-id Slot that contains no machine-local path."""
    if not isinstance(item, dict):
        raise ValueError(
            f"Shareable Preset Slot {index + 1:02d} must be an object")
    if "model_id" not in item:
        raise ValueError(
            f"Shareable Preset Slot {index + 1:02d} must contain model_id")
    if any(key in item for key in ("path", "candidate", "model_key", "pack_id")):
        raise ValueError(
            f"Shareable Preset Slot {index + 1:02d} cannot contain local paths")
    bypass = item.get("bypass", False)
    if not isinstance(bypass, bool):
        raise ValueError(
            f"Shareable Preset Slot {index + 1:02d} bypass must be boolean")
    parsed_model_id = model_id(item.get("model_id"), index)
    if parsed_model_id is None and bypass:
        raise ValueError(
            f"Shareable Preset Slot {index + 1:02d} cannot bypass an empty slot")
    result = {
        "model_id": parsed_model_id,
        **slot_gains(item, index, preserve_explicit_defaults=True),
    }
    if bypass:
        result["bypass"] = True
    return result


def parse_portable_chain(raw: object) -> dict:
    """Parse the path-free chain shared by portable and bundled Presets."""
    if not isinstance(raw, dict):
        raise ValueError("Shareable Preset chain must be an object")
    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, list) or len(raw_slots) > 6:
        raise ValueError("Shareable Preset slots must contain between 0 and 6 items")
    return {
        "slots": [parse_portable_slot(item, index)
                  for index, item in enumerate(raw_slots)],
        "gain": number(raw.get("gain", PRESET_DEFAULTS["gain"]),
                       "gain", 0, 10),
        "master": number(raw.get("master", PRESET_DEFAULTS["master"]),
                         "master", 0, 10),
        "quality": number(raw.get("quality", PRESET_DEFAULTS["quality"]),
                          "quality", 0, 1),
    }


def semantic_chain_key(raw: object) -> tuple | None:
    """Return the chain meaning while ignoring local projection fields.

    SQLite rows may contain machine-local Slot fields such as ``path`` while
    repository documents do not.  Preparation races only need values that
    affect the resulting chain: ordered model IDs, gains, bypass state, and
    the three chain-wide controls.  Invalid rows return ``None`` so callers
    fail closed instead of treating an unreadable projection as current.
    """
    if not isinstance(raw, dict):
        return None
    raw_slots = raw.get("slots", [])
    if not isinstance(raw_slots, (list, tuple)):
        return None

    def scalar(value: object, default: float) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value):
            return None
        return float(value)

    chain_values = tuple(
        scalar(raw[key] if key in raw else default, default)
        for key, default in (
            ("gain", PRESET_DEFAULTS["gain"]),
            ("master", PRESET_DEFAULTS["master"]),
            ("quality", PRESET_DEFAULTS["quality"]),
        )
    )
    if any(value is None for value in chain_values):
        return None

    slots: list[tuple[int | None, float, float, bool]] = []
    for item in raw_slots:
        if not isinstance(item, dict):
            return None
        model = item.get("model_id")
        if (model is not None
                and (isinstance(model, bool) or not isinstance(model, int))):
            return None
        input_gain = scalar(
            item["input_gain_db"]
            if "input_gain_db" in item else SLOT_GAIN_DEFAULT_DB,
            SLOT_GAIN_DEFAULT_DB,
        )
        output_gain = scalar(
            item["output_gain_db"]
            if "output_gain_db" in item else SLOT_GAIN_DEFAULT_DB,
            SLOT_GAIN_DEFAULT_DB,
        )
        bypass = item.get("bypass", False)
        if input_gain is None or output_gain is None or not isinstance(bypass, bool):
            return None
        slots.append((model, input_gain, output_gain, bypass))

    return (tuple(slots), *chain_values)
