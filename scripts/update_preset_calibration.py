#!/usr/bin/env python3
"""Write the NAM-recommended output calibration into every Preset Slot.

Each Slot that resolves to a local ``.nam`` model gets ``output_gain_db``
set to the model's recommended output adjustment, exactly as the realtime
engine computes it (``-18 - metadata.loudness`` dB, bounded to the protocol's
[-24, 24] range and rounded to two decimals).  IR (``.wav``) Slots and Slots
without loudness metadata have no calibration and keep the 0 dB default
(which the canonical writers omit).

Processing is idempotent: existing ``output_gain_db`` values are replaced
with the freshly computed recommendation.  ``--dry-run`` only prints what
would change.

Usage:
    python scripts/update_preset_calibration.py [--dry-run] [--presets-dir DIR]...
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import chain_protocol  # noqa: E402

DATA_ROOT = chain_protocol.managed_data_root(PROJECT_ROOT)
PRESETS_DIR = DATA_ROOT / "presets"
SHAREABLE_DIR = PROJECT_ROOT / "shareable-presets"
DB_FILE = DATA_ROOT / "gigbuddy.db"

SLOT_GAIN_MIN_DB = -24.0
SLOT_GAIN_MAX_DB = 24.0
NAM_TARGET_LOUDNESS_DB = -18.0  # NeuralAudio GetRecommendedOutputDBAdjustment()


def _nam_loudness_db(path: Path) -> float | None:
    """Return the top-level ``metadata.loudness`` of a .nam file.

    The engine's ``ReadNAMConfig`` reads the top-level metadata block even
    for packed ``SlimmableContainer`` files, so this mirrors the runtime
    recommendation exactly.  The submodel metadata is only a defensive
    fallback for files without a top-level block.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    metadata = document.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("loudness"), (int, float)):
        return float(metadata["loudness"])
    config = document.get("config")
    if isinstance(config, dict):
        submodels = config.get("submodels")
        if isinstance(submodels, list) and submodels:
            model = submodels[0].get("model") if isinstance(submodels[0], dict) else None
            if isinstance(model, dict) and isinstance(model.get("metadata"), dict):
                loudness = model["metadata"].get("loudness")
                if isinstance(loudness, (int, float)):
                    return float(loudness)
    return None


def recommended_output_gain_db(path: Path) -> float | None:
    """Return the clamped, rounded NAM output recommendation (None if none)."""
    loudness = _nam_loudness_db(path)
    if loudness is None:
        return None
    recommendation = NAM_TARGET_LOUDNESS_DB - loudness
    recommendation = max(SLOT_GAIN_MIN_DB,
                         min(SLOT_GAIN_MAX_DB, recommendation))
    return round(recommendation, 2)


def _model_local_path(model_id: int) -> str | None:
    try:
        with sqlite3.connect(DB_FILE) as conn:
            row = conn.execute(
                "SELECT local_path FROM models WHERE id = ?", (model_id,)
            ).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _slot_nam_path(slot: dict) -> Path | None:
    """Resolve a Slot to its local .nam file, if any."""
    raw_path = slot.get("path")
    if isinstance(raw_path, str) and raw_path.strip():
        candidate = PROJECT_ROOT / raw_path
        if candidate.suffix.lower() == ".nam" and candidate.is_file():
            return candidate
        return None
    model_id = slot.get("model_id")
    if isinstance(model_id, int) and model_id > 0:
        local = _model_local_path(model_id)
        if local:
            candidate = PROJECT_ROOT / local
            if candidate.suffix.lower() == ".nam" and candidate.is_file():
                return candidate
    return None


def _insert_gain(slot: dict, value: float) -> bool:
    """Insert ``output_gain_db`` right after ``model_id``; True if changed."""
    key = "output_gain_db"
    if slot.get(key) == value:
        return False
    rebuilt: dict = {}
    inserted = False
    for slot_key, slot_value in slot.items():
        rebuilt[slot_key] = slot_value
        if slot_key == "model_id" and not inserted:
            rebuilt[key] = value
            inserted = True
    if not inserted:
        rebuilt = {key: value, **rebuilt}
    slot.clear()
    slot.update(rebuilt)
    return True


def update_preset_file(path: Path, *, dry_run: bool) -> tuple[int, int, str]:
    """Update one Preset document; returns (changed_slots, slot_count, detail)."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return 0, 0, f"ERROR: invalid JSON: {exc}"
    if not isinstance(document, dict) or not isinstance(document.get("chain"), dict):
        return 0, 0, "ERROR: not a Preset document"
    slots = document["chain"].get("slots")
    if not isinstance(slots, list):
        return 0, 0, "ERROR: chain.slots is not a list"

    changed = 0
    details = []
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict):
            continue
        nam_path = _slot_nam_path(slot)
        if nam_path is None:
            continue
        recommendation = recommended_output_gain_db(nam_path)
        if recommendation is None or recommendation == 0.0:
            details.append(f"slot{index + 1}: no calibration (loudness absent/neutral)")
            continue
        before = slot.get("output_gain_db")
        if _insert_gain(slot, recommendation):
            changed += 1
            details.append(
                f"slot{index + 1}: output_gain_db {before if before is not None else '0.0'} "
                f"-> {recommendation:+.2f} dB")
        else:
            details.append(f"slot{index + 1}: output_gain_db already {recommendation:+.2f} dB")

    if changed and not dry_run:
        payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        import os
        import tempfile
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
    return changed, len(slots), "; ".join(details)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print planned changes without writing files")
    parser.add_argument("--presets-dir", action="append", default=[],
                        help="additional Preset directories to process")
    args = parser.parse_args(argv)

    directories = [PRESETS_DIR, SHAREABLE_DIR] + [
        Path(value) for value in args.presets_dir]
    files: list[Path] = []
    for directory in directories:
        if directory.is_dir():
            files.extend(sorted(directory.glob("*.json")))

    total_changed = total_slots = 0
    for path in files:
        changed, slot_count, detail = update_preset_file(path, dry_run=args.dry_run)
        total_changed += changed
        total_slots += slot_count
        action = "would change" if changed and args.dry_run else "changed"
        print(f"{path.relative_to(PROJECT_ROOT)}: {action} {changed}/{slot_count} slots")
        if detail:
            print(f"    {detail}")

    mode = "dry-run" if args.dry_run else "written"
    print(f"\n{len(files)} Presets, {total_slots} slots, {total_changed} slot gains {mode}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
