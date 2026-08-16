#!/usr/bin/env python3
"""Prepare a fresh GigBuddy checkout for the v0.2 TUI.

This script owns orchestration only. The library registers the repository's
built-in Preset catalog; the TUI or an explicit ``gigbuddy preset bootstrap``
command prepares any missing remote models. ``tone3000`` handles the official
dry-input downloads.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import library  # noqa: E402
import tone3000  # noqa: E402


def _download_dry_inputs(kind: str) -> bool:
    destination = ROOT / "data" / "dry_inputs"
    names = (None if kind == "all"
             else list(tone3000.DRY_INPUT_STARTER_KEYS))
    missing = tone3000.fetch_dry_inputs_missing(destination, names=names)
    if not missing:
        print(f"Dry inputs: ready ({len(names or tone3000.DRY_INPUTS)})")
        return True
    tone3000.fetch_dry_inputs(destination, names=missing)
    remaining = tone3000.fetch_dry_inputs_missing(destination, names=names)
    if remaining:
        print(f"Dry inputs incomplete: {len(remaining)} missing", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Register GigBuddy built-in Presets and prepare runtime data"
    )
    parser.add_argument(
        "--skip-presets", action="store_true",
        help="do not register the built-in Preset catalog",
    )
    parser.add_argument(
        "--skip-dry-inputs", action="store_true",
        help="do not download official TONE3000 dry-input WAV files",
    )
    parser.add_argument(
        "--dry-inputs", choices=("all", "starter"), default="all",
        help="download all dry inputs or only the ten common starter files",
    )
    args = parser.parse_args(argv)

    library.TONES_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "dry_inputs").mkdir(parents=True, exist_ok=True)
    library.connect().close()
    if not library.CHAIN_FILE.exists():
        library.chain_set({"slots": []})

    ok = True
    if not args.skip_presets:
        result = library.sync_bundled_presets(download=False)
        if result["failed"]:
            print(
                f"Built-in Presets: {result['failed']} invalid document(s)",
                file=sys.stderr,
            )
            ok = False

    if not args.skip_dry_inputs:
        try:
            ok = _download_dry_inputs(args.dry_inputs) and ok
        except Exception as exc:
            print(f"Dry-input download failed: {exc}", file=sys.stderr)
            ok = False

    if ok:
        print("GigBuddy data is ready. Start with: .venv/bin/python -m tui")
        return 0
    print(
        "Bootstrap is incomplete. Re-run ./install.sh or the failed command "
        "after checking network access.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
