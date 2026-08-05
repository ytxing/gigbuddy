"""Focused v0.2 Preset storage tests; no Textual app is started."""

import json
from pathlib import Path

import pytest

import library


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(library, "ROOT", root)
    monkeypatch.setattr(library, "DB_FILE", root / "data" / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", root / "data" / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", root / "data" / "tones")
    (root / "data" / "tones").mkdir(parents=True)
    (root / "data" / "dry_inputs").mkdir(parents=True)
    (root / "data" / "dry_inputs" / "one.wav").write_bytes(b"dry")
    yield


def _seed_models() -> tuple[Path, Path]:
    amp = library.ROOT / "data" / "tones" / "amp.nam"
    cab = library.ROOT / "data" / "tones" / "cab.wav"
    amp.write_bytes(b"amp")
    cab.write_bytes(b"cab")
    with library.connect() as conn:
        library.upsert_tone(conn, {"id": 1, "title": "Test", "gear": "amp-cab"})
        library.upsert_model(conn, {
            "id": 101, "tone_id": 1, "model_url": "amp",
            "name": "amp.nam", "architecture": "SlimmableContainer",
            "local_path": str(amp),
        })
        library.upsert_model(conn, {
            "id": 202, "tone_id": 1, "model_url": "cab",
            "name": "cab.wav", "architecture": "IR",
            "local_path": str(cab),
        })
    return amp, cab


def _write_legacy(name: str, chain: dict) -> None:
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, created_at, updated_at) "
            "VALUES (?, ?, ?, 'now', 'now')",
            (name, "legacy", json.dumps(chain)),
        )
        conn.commit()


def test_save_preserves_order_duplicates_empty_and_excludes_runtime_state():
    amp, cab = _seed_models()
    library.chain_set({
        "slots": [
            {"path": str(amp)}, {"path": None}, {"path": str(amp)},
            {"path": str(cab)}, {"path": None}, {"path": str(cab)},
        ],
        "gain": 0.8, "master": 0.0, "quality": 0.7,
        "mute": True,
        "input": {"source": "file", "file": "data/dry_inputs/one.wav",
                  "state": "playing", "loop": True},
    })

    saved = library.preset_save("six", note="snapshot")

    assert saved["note"] == "snapshot"
    assert saved["chain"] == {
        "slots": [
            {"model_id": 101, "path": "data/tones/amp.nam"},
            {"model_id": None, "path": None},
            {"model_id": 101, "path": "data/tones/amp.nam"},
            {"model_id": 202, "path": "data/tones/cab.wav"},
            {"model_id": None, "path": None},
            {"model_id": 202, "path": "data/tones/cab.wav"},
        ],
        "gain": 0.8, "master": 0.0, "quality": 0.7,
    }
    with library.connect() as conn:
        raw = json.loads(conn.execute(
            "SELECT chain_json FROM presets WHERE name='six'"
        ).fetchone()[0])
        note = conn.execute(
            "SELECT note FROM presets WHERE name='six'"
        ).fetchone()[0]
    assert set(raw) == {"slots", "gain", "master", "quality"}
    assert "model" not in raw and "ir" not in raw
    assert note == "snapshot"


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ({"model_id": 101, "model_path": "data/tones/amp.nam"},
         [{"model_id": 101, "path": "data/tones/amp.nam"}]),
        ({"ir_model_id": 202, "ir_path": "data/tones/cab.wav"},
         [{"model_id": 202, "path": "data/tones/cab.wav"}]),
        ({"model_id": 101, "model_path": "data/tones/amp.nam",
          "ir_model_id": 202, "ir_path": "data/tones/cab.wav"},
         [{"model_id": 101, "path": "data/tones/amp.nam"},
          {"model_id": 202, "path": "data/tones/cab.wav"}]),
        ({"model_id": None, "model_path": None,
          "ir_model_id": None, "ir_path": None}, []),
    ],
)
def test_legacy_presets_are_read_as_slots_without_startup_migration(legacy, expected):
    _seed_models()
    _write_legacy("old", {**legacy, "gain": 0.5})

    preset = library.preset_get("old")

    assert preset["chain"]["slots"] == expected
    with library.connect() as conn:
        stored = json.loads(conn.execute(
            "SELECT chain_json FROM presets WHERE name='old'"
        ).fetchone()[0])
    assert "slots" not in stored


def test_mixed_legacy_fields_warn_and_slots_win():
    _seed_models()
    _write_legacy("mixed", {
        "slots": [{"model_id": 202, "path": "data/tones/cab.wav"}],
        "model_id": 101, "model_path": "data/tones/amp.nam",
    })

    with pytest.warns(RuntimeWarning, match="slots take precedence"):
        preset = library.preset_get("mixed")

    assert preset["chain"]["slots"] == [
        {"model_id": 202, "path": "data/tones/cab.wav"}
    ]


def test_load_keeps_input_and_mute_but_applies_slots_and_parameters():
    amp, cab = _seed_models()
    library.chain_set({
        "slots": [{"path": str(amp)}], "gain": 0.2, "master": 0.4,
        "quality": 0.2, "mute": True,
        "input": {"source": "file", "file": "data/dry_inputs/one.wav",
                  "state": "paused", "loop": True},
    })
    library.preset_save("target")
    library.chain_set({
        "slots": [{"path": str(cab)}, {"path": None}],
        "gain": 0.9, "master": 0.0, "quality": 1.0, "mute": False,
        "input": {"source": "file", "file": "data/dry_inputs/one.wav",
                  "state": "playing", "loop": False},
    })

    loaded = library.preset_load("target")

    assert [slot["path"] for slot in loaded["slots"]] == [str(amp)]
    assert loaded["gain"] == 0.2
    assert loaded["master"] == 0.4
    assert loaded["quality"] == 0.2
    assert loaded["mute"] is False
    assert loaded["input"]["state"] == "playing"
    assert library.preset_is_dirty("target") is False


def test_load_rejects_any_missing_slot_without_changing_live_chain():
    amp, cab = _seed_models()
    library.chain_set({"slots": [{"path": str(amp)}], "gain": 0.4})
    library.preset_save("broken")
    # Overwrite explicitly with a two-Slot canonical snapshot, then remove the
    # later file. The first Slot remains valid but the whole apply must reject.
    with library.connect() as conn:
        conn.execute(
            "UPDATE presets SET chain_json=? WHERE name='broken'",
            (json.dumps({"slots": [
                {"model_id": 101, "path": "data/tones/amp.nam"},
                {"model_id": 202, "path": "data/tones/cab.wav"},
            ], "gain": 0.9, "master": 1.0, "quality": 1.0}),),
        )
        conn.commit()
    cab.unlink()
    before = library.CHAIN_FILE.read_text()

    with pytest.raises(ValueError, match="Slot 02"):
        library.preset_load("broken")

    assert library.CHAIN_FILE.read_text() == before
    assert library.preset_current() == "broken"


def test_dirty_distinguishes_order_and_parameters_but_ignores_mute_and_input():
    amp, cab = _seed_models()
    library.chain_set({
        "slots": [{"path": str(amp)}, {"path": None}, {"path": str(amp)}],
        "gain": 1.0, "master": 0.0, "quality": 1.0,
        "mute": False,
        "input": {"source": "file", "file": "data/dry_inputs/one.wav",
                  "state": "stopped", "loop": False},
    })
    library.preset_save("dirty")
    library.chain_set({
        "slots": [{"path": str(amp)}, {"path": str(amp)}, {"path": None}],
        "gain": 1.0, "master": 0.0, "quality": 1.0, "mute": True,
        "input": {"source": "file", "file": "data/dry_inputs/one.wav",
                  "state": "playing", "loop": True},
    })
    assert library.preset_is_dirty("dirty") is True

    library.chain_set({
        "slots": [{"path": str(amp)}, {"path": None}, {"path": str(amp)}],
        "gain": 1.0, "master": 0.0, "quality": 1.0, "mute": True,
        "input": {"source": "file", "file": "data/dry_inputs/one.wav",
                  "state": "playing", "loop": True},
    })
    assert library.preset_is_dirty("dirty") is False

    library.chain_set({
        "slots": [{"path": str(amp)}, {"path": str(cab)}, {"path": None}],
        "gain": 1.0, "master": 0.0, "quality": 1.0, "mute": True,
        "input": {"source": "file", "file": "data/dry_inputs/one.wav",
                  "state": "playing", "loop": True},
    })
    assert library.preset_is_dirty("dirty") is True


def test_model_id_resolution_falls_back_to_saved_path():
    amp, _cab = _seed_models()
    library.chain_set({"slots": [{"path": str(amp)}]})
    library.preset_save("fallback")
    with library.connect() as conn:
        conn.execute("UPDATE models SET local_path=NULL WHERE id=101")
        conn.commit()

    resolved = library.preset_resolved_chain("fallback")

    assert resolved["slots"] == [{"model_id": 101, "path": str(amp)}]


def test_model_id_resolution_has_priority_over_stale_saved_path():
    amp, cab = _seed_models()
    library.chain_set({"slots": [{"path": str(amp)}]})
    library.preset_save("priority")
    with library.connect() as conn:
        conn.execute("UPDATE models SET local_path=? WHERE id=101", (str(cab),))
        conn.commit()

    resolved = library.preset_resolved_chain("priority")

    assert resolved["slots"] == [{"model_id": 101, "path": str(cab)}]


def test_note_whitespace_is_stored_as_empty_string():
    _seed_models()
    library.chain_set({"slots": []})
    library.preset_save("notes", note="   \t")
    library.preset_update_note("notes", "  ")

    with library.connect() as conn:
        note = conn.execute(
            "SELECT note FROM presets WHERE name='notes'"
        ).fetchone()[0]
    assert note == ""
    assert library.preset_get("notes")["note"] == ""
