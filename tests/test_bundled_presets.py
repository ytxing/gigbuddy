"""Tests for repository-distributed starter Presets."""

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import DataTable

import library
import preset_catalog
from tui.app import GigBuddyApp
from tui.presets import PresetPanel


def test_repository_catalog_documents_are_self_contained():
    directory = Path(__file__).resolve().parent.parent / "presets" / "built-in"
    documents = [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]
    assert len(documents) == 20
    assert len({document["catalog_key"] for _path, document in documents}) == 20
    assert len({document["name"] for _path, document in documents}) == 20
    for path, document in documents:
        assert path.stem == document["catalog_key"]
        for slot in document["chain"]["slots"]:
            assert isinstance(slot["tone_id"], int) and slot["tone_id"] > 0
            assert isinstance(slot["model_id"], int) and slot["model_id"] > 0


def _configure(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path)
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "data" / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "data" / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", tmp_path / "data" / "tones")
    monkeypatch.setattr(library, "PRESETS_DIR", tmp_path / "data" / "presets")
    bundled = tmp_path / "presets" / "built-in"
    monkeypatch.setattr(library, "BUNDLED_PRESETS_DIR", bundled)
    library.TONES_DIR.mkdir(parents=True)
    bundled.mkdir(parents=True)
    library.chain_set({"slots": []})
    return bundled


def _write_bundle(
        directory, *, name="starter", model_id=101, tone_id=1,
        catalog_key=None, filename=None):
    (directory / f"{filename or name}.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-bundled-preset",
        "catalog_key": catalog_key or name,
        "name": name,
        "note": "starter note",
        "chain": {
            "slots": [{
                "tone_id": tone_id,
                "model_id": model_id,
                "output_gain_db": 5.0,
            }],
            "gain": 1.0,
            "master": 1.0,
            "quality": 1.0,
        },
    }), encoding="utf-8")


def _write_user_preset(path, *, name="starter"):
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-preset",
        "name": name,
        "chain": {
            "slots": [],
            "gain": 1.0,
            "master": 1.0,
            "quality": 1.0,
        },
    }), encoding="utf-8")


def test_bundled_sync_keeps_unavailable_preset_visible_and_reports_failure(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    monkeypatch.setattr(
        library, "import_tone",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("network unavailable")),
    )

    report = library.sync_bundled_presets(quiet=True)

    assert report == {
        "total": 1, "ready": 0, "preparing": 0, "failed": 1,
        "failed_presets": ["starter"],
    }
    preset = library.preset_get("starter")
    assert preset["chain"]["slots"] == [
        {"model_id": 101, "output_gain_db": 5.0, "path": None},
    ]
    assert library.chain_get()["slots"] == []


def test_bundled_registration_does_not_download_or_create_user_files(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    calls = []

    def fail_if_called(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("registration must not download")

    monkeypatch.setattr(library, "import_tone", fail_if_called)

    report = library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)

    assert report == {
        "total": 1, "ready": 0, "preparing": 1, "failed": 0,
        "failed_presets": [],
    }
    assert calls == []
    preset = library.preset_get("starter")
    assert preset["source"] == "bundled"
    assert preset["availability"] == "PREPARING"
    assert not list((tmp_path / "data" / "presets").glob("*.json"))


def test_bundled_preset_stays_visible_with_stale_unsupported_model_metadata(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    with library.connect() as conn:
        library.upsert_tone(conn, {
            "id": 1,
            "title": "Stale metadata",
            "gear": "amp",
            "format": "nam",
            "platform": "nam",
        }, commit=False)
        library.upsert_model(conn, {
            "id": 101,
            "tone_id": 1,
            "model_url": "https://example.invalid/stale.nam",
            "name": "stale.nam",
            "architecture": "custom",
            "local_path": None,
        }, commit=False)
        conn.commit()

    library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)

    preset = library.preset_get("starter")
    assert preset is not None
    assert preset["source"] == "bundled"
    assert preset["availability"] == "PREPARING"


def test_legacy_seed_alias_registers_bundled_catalog_and_preserves_users(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.preset_save("user copy", set_active=False)

    result = library.preset_seed(quiet=True, replace=True)

    assert result == 1
    assert library.preset_get("user copy")["source"] == "user"
    assert library.preset_get("starter")["source"] == "bundled"
    assert not list(library.PRESETS_DIR.glob("*-starter.json"))


def test_fresh_bundled_registration_does_not_scan_local_packs(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("fresh catalog registration scanned local Packs")

    monkeypatch.setattr(library, "scan_local_packs", unexpected_scan)

    assert library.sync_bundled_presets(
        quiet=True, download=False)["total"] == 1


def test_bundled_index_defers_editable_files_until_catalog_refresh(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    editable = library.PRESETS_DIR / "editable.json"
    editable.parent.mkdir(parents=True, exist_ok=True)
    _write_user_preset(editable, name="user preset")

    library.sync_bundled_presets(quiet=True, download=False)

    assert library.preset_get("starter")["source"] == "bundled"
    assert library.preset_get("user preset") is None
    assert editable.is_file()

    library.refresh_preset_catalog()

    assert library.preset_get("user preset")["source"] == "user"


def test_unchanged_catalog_refresh_does_not_rescan_local_packs(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.preset_save("user preset", set_active=False)
    library.refresh_preset_catalog()

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("unchanged Catalog refresh rescanned local Packs")

    monkeypatch.setattr(library, "scan_local_packs", unexpected_scan)

    library.refresh_preset_catalog()


def test_catalog_refresh_quarantines_same_name_user_file_before_import(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    late_file = library.PRESETS_DIR / "late-user.json"
    late_file.parent.mkdir(parents=True, exist_ok=True)
    _write_user_preset(late_file)

    library.sync_bundled_presets(quiet=True, download=False)

    assert late_file.is_file()

    library.refresh_preset_catalog()

    assert not late_file.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("late-user*.json"))
    assert len(quarantined) == 1
    assert library.preset_get("starter")["source"] == "bundled"


def test_preset_getters_are_pure_until_catalog_refresh(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    late_file = library.PRESETS_DIR / "late-user.json"
    late_file.parent.mkdir(parents=True, exist_ok=True)
    _write_user_preset(late_file)

    assert library.preset_list() == []
    assert library.preset_get("starter") is None
    assert library.preset_get_by_id(1) is None
    assert late_file.is_file()
    assert not (library.PRESETS_DIR / ".quarantine").exists()

    library.refresh_preset_catalog()
    presets = library.preset_list()
    assert [(preset["name"], preset["source"])
            for preset in presets] == [("starter", "bundled")]
    assert not late_file.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("late-user*.json"))
    assert len(quarantined) == 1


def test_preset_getters_do_not_scan_local_pack_files(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    local_path = library.TONES_DIR / "external.nam"
    local_path.write_text("model", encoding="utf-8")
    with library.connect() as conn:
        library.upsert_tone(conn, {
            "id": 1,
            "title": "Registered",
            "gear": "amp",
            "format": "nam",
            "platform": "nam",
        }, commit=False)
        library.upsert_model(conn, {
            "id": 101,
            "tone_id": 1,
            "model_url": "https://example.invalid/model.nam",
            "name": local_path.name,
            "architecture_version": "2",
            "architecture": "SlimmableContainer",
            "local_path": str(local_path),
        }, commit=False)
        conn.execute(
            "INSERT INTO presets "
            "(name, note, chain_json, source, created_at, updated_at) "
            "VALUES (?, '', ?, 'user', 'created', 'updated')",
            ("path-only", json.dumps({
                "slots": [{"model_id": 101, "path": str(local_path)}],
                "gain": 1.0,
                "master": 1.0,
                "quality": 1.0,
            })),
        )
        conn.commit()

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("Preset getters must not scan local Pack files")

    monkeypatch.setattr(library, "scan_local_packs", unexpected_scan)

    assert library.preset_get("path-only")["name"] == "path-only"
    assert [preset["name"] for preset in library.preset_list()] == ["path-only"]


def test_cli_preset_list_refreshes_catalog_before_reading(
        tmp_path, monkeypatch, capsys):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)

    assert library.main(["preset", "list", "--json"]) == 0

    presets = json.loads(capsys.readouterr().out)
    assert [(preset["name"], preset["source"])
            for preset in presets] == [("starter", "bundled")]


def test_quarantine_never_overwrites_an_existing_target(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    late_file = library.PRESETS_DIR / "late-user.json"
    quarantine = library.PRESETS_DIR / ".quarantine"
    quarantine.mkdir(parents=True)
    existing = quarantine / late_file.name
    existing.write_text("keep me", encoding="utf-8")
    _write_user_preset(late_file)

    library.sync_bundled_presets(quiet=True, download=False)
    library.refresh_preset_catalog()

    assert existing.read_text(encoding="utf-8") == "keep me"
    moved = quarantine / "late-user-1.json"
    assert json.loads(moved.read_text(encoding="utf-8"))["name"] == "starter"


def test_quarantine_failure_does_not_publish_reconcile_token(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    late_file = library.PRESETS_DIR / "late-user.json"
    late_file.parent.mkdir(parents=True, exist_ok=True)
    _write_user_preset(late_file)
    original_replace = Path.replace

    def fail_late_file(self, target):
        if self == late_file:
            raise OSError("quarantine unavailable")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_late_file)

    library.sync_bundled_presets(quiet=True, download=False)
    with pytest.warns(RuntimeWarning, match="Could not quarantine"):
        library.refresh_preset_catalog()

    assert late_file.exists()
    monkeypatch.setattr(Path, "replace", original_replace)

    library.refresh_preset_catalog()

    assert not late_file.exists()
    assert list((library.PRESETS_DIR / ".quarantine").glob("late-user*.json"))


def test_bundled_sync_preserves_an_ambiguous_legacy_seed_row(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    legacy_chain = {
        "slots": [{"model_id": 101, "path": "data/tones/starter.nam"}],
        "gain": 1.0,
        "master": 1.0,
        "quality": 1.0,
    }
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, source) "
            "VALUES (?, ?, ?, 'user')",
            ("starter", "starter note", json.dumps(legacy_chain)),
        )
        conn.commit()

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["total"] == 0
    preset = library.preset_get("starter")
    assert preset["source"] == "user"
    assert preset["chain"]["slots"] == [
        {"model_id": 101, "path": "data/tones/starter.nam"},
    ]
    assert not list(library.PRESETS_DIR.glob("*.json"))

    library.refresh_preset_catalog()

    assert list(library.PRESETS_DIR.glob("*.json"))


def test_bundled_sync_preserves_a_modified_legacy_seed_row(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    modified_chain = {
        "slots": [{
            "model_id": 101,
            "path": "data/tones/starter.nam",
            "output_gain_db": 2.0,
        }],
        "gain": 1.0,
        "master": 1.0,
        "quality": 1.0,
    }
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, source) "
            "VALUES (?, ?, ?, 'user')",
            ("starter", "starter note", json.dumps(modified_chain)),
        )
        conn.commit()

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["total"] == 0
    preset = library.preset_get("starter")
    assert preset["source"] == "user"
    assert preset["chain"]["slots"][0]["output_gain_db"] == 2.0
    assert not list(library.PRESETS_DIR.glob("*.json"))

    library.refresh_preset_catalog()

    assert list(library.PRESETS_DIR.glob("*.json"))


def test_bundled_sync_preserves_unmapped_legacy_bundled_row(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO presets "
            "(name, note, chain_json, source, source_key) "
            "VALUES (?, '', ?, 'bundled', NULL)",
            ("legacy bundled", json.dumps({"slots": []})),
        )
        conn.commit()

    library.sync_bundled_presets(quiet=True, download=False)

    legacy = library.preset_get("legacy bundled")
    assert legacy["source"] == "user"
    assert legacy["source_key"] is None
    assert library.preset_update_note(
        "legacy bundled", "editable")["note"] == "editable"
    assert library.preset_get("starter")["source"] == "bundled"


def test_null_source_key_never_proves_bundled_ownership(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    user_chain = {
        "slots": [], "gain": 0.5, "master": 1.0, "quality": 1.0,
    }
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO presets "
            "(name, note, chain_json, source, source_key) "
            "VALUES ('starter', 'mine', ?, 'bundled', NULL)",
            (json.dumps(user_chain),),
        )
        conn.commit()

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["total"] == 0
    preset = library.preset_get("starter")
    assert preset["source"] == "user"
    assert preset["source_key"] is None
    assert preset["note"] == "mine"
    assert preset["chain"]["gain"] == 0.5
    assert library.preset_update_note("starter", "still mine")["note"] == \
        "still mine"


def test_bundled_user_name_wins_and_bundled_rows_are_read_only(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.preset_save("starter", note="mine")

    report = library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)

    assert report["total"] == 0
    preset = library.preset_get("starter")
    assert preset["source"] == "user"
    assert preset["note"] == "mine"

    _write_bundle(bundled, name="read-only", model_id=202)
    library.sync_bundled_presets(quiet=True, download=False)
    for operation in (
            lambda: library.preset_rename("read-only", "renamed"),
            lambda: library.preset_update_note("read-only", "changed"),
            lambda: library.preset_update_draft("read-only", {"slots": []}),
            lambda: library.preset_delete("read-only"),
    ):
        try:
            operation()
        except ValueError as exc:
            assert "read-only" in str(exc).casefold()
        else:
            raise AssertionError("bundled preset mutation was accepted")


@pytest.mark.parametrize("command", ["save", "delete"])
def test_cli_reports_bundled_mutation_errors_without_raising(
        tmp_path, monkeypatch, capsys, command):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, name="read-only")
    library.sync_bundled_presets(quiet=True, download=False)

    assert library.main(["preset", command, "read-only"]) == 1

    captured = capsys.readouterr()
    assert "read-only" in captured.err.casefold()
    assert "traceback" not in captured.err.casefold()


def test_cli_seed_replace_reports_that_user_presets_are_preserved(
        tmp_path, monkeypatch, capsys):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)

    assert library.main([
        "preset", "seed", "--replace", "--local-only",
    ]) == 0

    captured = capsys.readouterr()
    assert "deprecated" in captured.err.casefold()
    assert "user presets are preserved" in captured.err.casefold()


@pytest.mark.parametrize("command", ["seed", "bootstrap"])
def test_cli_bundled_prepare_failure_names_each_unavailable_preset(
        tmp_path, monkeypatch, capsys, command):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, name="starter", model_id=101, tone_id=1)
    _write_bundle(bundled, name="second", model_id=202, tone_id=2)

    def fail_import(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(library, "import_tone", fail_import)

    assert library.main(["preset", command]) == 1

    captured = capsys.readouterr()
    assert "Built-in Presets: 0/2 ready." in captured.out
    assert "Unavailable built-in Presets (2): second, starter" in captured.err


def test_tui_bundled_retry_passes_stable_catalog_key():
    captured = {}

    async def retry(preset_id, name, source_key):
        captured.update({
            "preset_id": preset_id, "name": name, "source_key": source_key,
        })

    panel = SimpleNamespace(
        app=SimpleNamespace(notify=lambda *_args, **_kwargs: None),
        _bundled_load_workers=set(),
        _retry_bundled_load=retry,
    )

    def run_worker(coro, **_kwargs):
        asyncio.run(coro)

    panel.run_worker = run_worker
    PresetPanel._request_load(panel, {
        "id": 7,
        "name": "renamed starter",
        "source": "bundled",
        "source_key": "starter-key",
        "availability": "UNAVAILABLE",
    })

    assert captured == {
        "preset_id": 7,
        "name": "renamed starter",
        "source_key": "starter-key",
    }


def test_tui_background_sync_exception_refreshes_unavailable_state(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    data = tmp_path / "data"
    (data / "dry_inputs").mkdir(parents=True)
    monkeypatch.setattr("tui.app.live.ROOT", tmp_path)
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", library.CHAIN_FILE)
    monkeypatch.setattr("tui.app.live.TONES_DIR", library.TONES_DIR)
    download_started = threading.Event()
    release_download = threading.Event()

    def fail_download(*_args, **_kwargs):
        download_started.set()
        if not release_download.wait(timeout=5):
            raise AssertionError("background download was not released")
        raise RuntimeError("background preparation crashed")

    monkeypatch.setattr(
        library._PRESET_CATALOG._preparation, "_download_models",
        fail_download,
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one("#preset-table", DataTable)
            def table_state():
                return next(
                    (
                        str(table.get_cell(row.key, "state"))
                        for row in table.ordered_rows
                        if str(table.get_cell(row.key, "name")) == "starter"
                    ),
                    None,
                )

            try:
                for _ in range(40):
                    if download_started.is_set():
                        break
                    await pilot.pause(0.025)
                assert download_started.is_set()
                assert table_state() == "PREPARING"
            finally:
                release_download.set()

            for _ in range(40):
                preset = library.preset_get("starter")
                if (preset is not None
                        and preset["availability"] == "UNAVAILABLE"
                        and table_state() == "UNAVAILABLE"):
                    break
                await pilot.pause(0.025)

            assert library.preset_get("starter")["availability"] == "UNAVAILABLE"
            assert table_state() == "UNAVAILABLE"

    asyncio.run(scenario())


def test_late_same_name_user_file_is_preserved_and_quarantined(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)

    late_file = library.PRESETS_DIR / "late-user.json"
    late_file.parent.mkdir(parents=True, exist_ok=True)
    _write_user_preset(late_file)

    library.refresh_preset_catalog()

    assert not late_file.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("late-user*.json"))
    assert len(quarantined) == 1
    assert json.loads(quarantined[0].read_text())["name"] == "starter"
    assert library.preset_get("starter")["source"] == "bundled"


def test_invalid_bundled_document_does_not_garbage_collect_previous_row(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)

    (bundled / "starter.json").write_text("{not json", encoding="utf-8")

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["failed"] == 1
    assert library.preset_get("starter")["source"] == "bundled"


def test_bundled_file_key_survives_a_display_name_change(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    original = library.preset_get("starter")

    document = json.loads((bundled / "starter.json").read_text())
    document["name"] = "renamed starter"
    (bundled / "starter.json").write_text(
        json.dumps(document), encoding="utf-8")

    library.sync_bundled_presets(quiet=True, download=False)

    renamed = library.preset_get("renamed starter")
    assert renamed["source"] == "bundled"
    assert renamed["id"] == original["id"]
    assert library.preset_get("starter") is None


def test_display_name_does_not_reassign_a_different_catalog_key(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    document = json.loads((bundled / "starter.json").read_text())
    document["catalog_key"] = "replacement"
    (bundled / "starter.json").write_text(
        json.dumps(document), encoding="utf-8")

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["failed_presets"] == ["starter"]
    assert library.preset_get("starter")["source_key"] == "starter"


def test_bundled_display_name_change_moves_active_name(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    library.preset_set_active("starter")

    document = json.loads((bundled / "starter.json").read_text())
    document["name"] = "renamed starter"
    (bundled / "starter.json").write_text(
        json.dumps(document), encoding="utf-8")

    library.sync_bundled_presets(quiet=True, download=False)

    assert library.preset_current() == "renamed starter"


def test_bundled_registration_cache_rechecks_after_user_name_is_deleted(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.preset_save("starter")
    library.sync_bundled_presets(quiet=True, download=False)
    assert library.preset_get("starter")["source"] == "user"

    assert library.preset_delete("starter") is True

    assert library.preset_get("starter")["source"] == "bundled"


def test_bundled_registration_cache_tracks_wal_preset_changes(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.preset_save("starter")
    library.sync_bundled_presets(quiet=True, download=False)
    keeper = sqlite3.connect(library.DB_FILE)
    try:
        assert keeper.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        assert library.preset_get("starter")["source"] == "user"
        keeper.execute("BEGIN")
        keeper.execute("SELECT COUNT(*) FROM presets").fetchone()
        database_state = library.DB_FILE.stat()

        assert library.preset_delete("starter") is True
        refreshed_state = library.DB_FILE.stat()
        assert (refreshed_state.st_size, refreshed_state.st_mtime_ns) == (
            database_state.st_size, database_state.st_mtime_ns)

        assert library.preset_get("starter")["source"] == "bundled"
    finally:
        keeper.rollback()
        keeper.close()


def test_download_does_not_cache_a_changed_preset_namespace(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(
        bundled, name="blocked", model_id=101,
        catalog_key="blocked-key", filename="blocked")
    _write_bundle(
        bundled, name="downloaded", model_id=202,
        catalog_key="downloaded-key", filename="downloaded")
    user = library.preset_save("blocked")
    user_file = next(library.PRESETS_DIR.glob(f"{user['id']}-*.json"))

    def delete_blocker_during_download(_tone_id, *, model_ids, quiet):
        del model_ids, quiet
        with library.connect() as conn:
            conn.execute("DELETE FROM presets WHERE id = ?", (user["id"],))
            conn.execute(
                "DELETE FROM settings WHERE key = ?",
                (f"preset_file:{user['id']}",),
            )
            conn.commit()
        user_file.unlink()
        return {"id": _tone_id}

    monkeypatch.setattr(library, "import_tone", delete_blocker_during_download)

    library.sync_bundled_presets(quiet=True, download=True)
    library.refresh_preset_catalog()

    assert library.preset_get("blocked")["source"] == "bundled"


def test_user_source_key_cannot_block_bundled_identity(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO presets "
            "(name, note, chain_json, source, source_key) "
            "VALUES ('user copy', '', ?, 'user', 'starter')",
            (json.dumps({"slots": []}),),
        )
        conn.commit()

    library.sync_bundled_presets(quiet=True, download=False)

    assert library.preset_get("user copy")["source"] == "user"
    assert library.preset_get("starter")["source"] == "bundled"


def test_modified_legacy_bundled_runtime_file_is_quarantined(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    preset = library.preset_get("starter")
    runtime = library.PRESETS_DIR / "old-starter.json"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    _write_user_preset(runtime)
    runtime_document = json.loads(runtime.read_text())
    runtime_document["note"] = "edited"
    runtime.write_text(json.dumps(runtime_document), encoding="utf-8")
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (f"preset_file:{preset['id']}", json.dumps({
                "file": runtime.name,
                "token": "stale-token",
            })),
        )
        conn.commit()

    library.refresh_preset_catalog()

    assert not runtime.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("old-starter*.json"))
    assert len(quarantined) == 1
    assert json.loads(quarantined[0].read_text())["note"] == "edited"


def test_removed_bundled_preset_preserves_modified_legacy_runtime_file(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, name="first", catalog_key="first-key")
    _write_bundle(bundled, name="second", catalog_key="second-key")
    library.sync_bundled_presets(quiet=True, download=False)
    preset = library.preset_get("first")

    runtime = library.PRESETS_DIR / "old-first.json"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    _write_user_preset(runtime, name="first")
    document = json.loads(runtime.read_text(encoding="utf-8"))
    document["note"] = "edited after the bundled import"
    runtime.write_text(json.dumps(document), encoding="utf-8")
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (f"preset_file:{preset['id']}", json.dumps({
                "file": runtime.name,
                "token": "stale-token",
            })),
        )
        conn.commit()

    (bundled / "first.json").unlink()
    library.sync_bundled_presets(quiet=True, download=False)
    library.refresh_preset_catalog()

    assert not runtime.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("old-first*.json"))
    assert len(quarantined) == 1
    assert json.loads(quarantined[0].read_text(encoding="utf-8"))["note"] == (
        "edited after the bundled import")


def test_getter_does_not_repeat_catalog_registration_after_explicit_sync(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)

    def unexpected_sync(*_args, **_kwargs):
        raise AssertionError("getter repeated bundled catalog registration")

    monkeypatch.setattr(library, "sync_bundled_presets", unexpected_sync)

    assert library.preset_get("starter")["source"] == "bundled"


def test_import_rejects_a_bundled_name_before_downloading(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    source = tmp_path / "shared.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "starter",
        "chain": {"slots": [{"model_id": 202}]},
    }), encoding="utf-8")
    calls = []

    def unexpected_download(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("read-only conflict must be checked before download")

    monkeypatch.setattr(library, "_download_shareable_models", unexpected_download)

    with pytest.raises(ValueError, match="read-only"):
        library.preset_import(source, quiet=True)

    assert calls == []


def test_bundled_sync_removes_deleted_or_renamed_catalog_rows(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('active_preset', 'starter')"
        )
        conn.commit()
    library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)
    assert library._PRESET_CATALOG.preparation_state_snapshot()["starter"] == {
        "status": "PREPARING", "error": "",
    }

    (bundled / "starter.json").unlink()
    _write_bundle(bundled, name="renamed", model_id=202)
    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["total"] == 1
    with library.connect() as conn:
        rows = conn.execute(
            "SELECT name, source FROM presets ORDER BY name").fetchall()
        active = conn.execute(
            "SELECT value FROM settings WHERE key = 'active_preset'").fetchone()
    assert [(row["name"], row["source"]) for row in rows] == [
        ("renamed", "bundled"),
    ]
    assert active is None
    assert "starter" not in (
        library._PRESET_CATALOG.preparation_state_snapshot())

    saved = library.preset_save("starter")
    assert saved["source"] == "user"


def test_bundled_rename_can_reuse_a_removed_catalog_name(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(
        bundled, name="first", model_id=101,
        catalog_key="first-key", filename="first-key")
    _write_bundle(
        bundled, name="second", model_id=202,
        catalog_key="second-key", filename="second-key")
    library.sync_bundled_presets(quiet=True, download=False)
    original = library.preset_get("first")
    library.preset_set_active("first")

    (bundled / "second-key.json").unlink()
    _write_bundle(
        bundled, name="second", model_id=101,
        catalog_key="first-key", filename="first-key")
    library.sync_bundled_presets(quiet=True, download=False)

    renamed = library.preset_get("second")
    assert renamed["source_key"] == "first-key"
    assert renamed["id"] == original["id"]
    assert library.preset_current() == "second"
    with library.connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM presets WHERE source_key = 'second-key'"
        ).fetchone() is None


def test_bundled_display_names_can_swap_without_changing_identity(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(
        bundled, name="first", model_id=101,
        catalog_key="first-key", filename="first-key")
    _write_bundle(
        bundled, name="second", model_id=202,
        catalog_key="second-key", filename="second-key")
    library.sync_bundled_presets(quiet=True, download=False)
    first_id = library.preset_get("first")["id"]
    second_id = library.preset_get("second")["id"]
    library.preset_set_active("first")

    _write_bundle(
        bundled, name="second", model_id=101,
        catalog_key="first-key", filename="first-key")
    _write_bundle(
        bundled, name="first", model_id=202,
        catalog_key="second-key", filename="second-key")
    library.sync_bundled_presets(quiet=True, download=False)

    assert library.preset_get("second")["id"] == first_id
    assert library.preset_get("first")["id"] == second_id
    assert library.preset_current() == "second"


def test_bundled_sync_keeps_catalog_when_directory_is_temporarily_missing(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    bundled.rename(tmp_path / "catalog-away")

    library.sync_bundled_presets(quiet=True, download=False)

    with library.connect() as conn:
        row = conn.execute(
            "SELECT source FROM presets WHERE name = 'starter'").fetchone()
    assert row is not None
    assert row["source"] == "bundled"


def test_bundled_sync_keeps_catalog_when_directory_is_temporarily_empty(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    (bundled / "starter.json").unlink()

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["total"] == 0
    with library.connect() as conn:
        row = conn.execute(
            "SELECT source FROM presets WHERE name = 'starter'").fetchone()
    assert row is not None
    assert row["source"] == "bundled"


def test_bundled_sync_does_not_partially_apply_duplicate_catalog_identity(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    duplicate = bundled / "second.json"
    document = json.loads((bundled / "starter.json").read_text())
    document["catalog_key"] = "starter"
    document["name"] = "second"
    duplicate.write_text(json.dumps(document), encoding="utf-8")

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["failed_presets"] == ["second", "starter"]
    assert library.preset_get("starter")["source"] == "bundled"
    assert library.preset_get("second") is None


def test_duplicate_catalog_names_are_reserved_from_first_file_import(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(
        bundled, name="first", model_id=101,
        catalog_key="duplicate-key", filename="first")
    _write_bundle(
        bundled, name="second", model_id=202,
        catalog_key="duplicate-key", filename="second")
    late_file = library.PRESETS_DIR / "second-user.json"
    late_file.parent.mkdir(parents=True, exist_ok=True)
    _write_user_preset(late_file, name="second")

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["failed_presets"] == ["first", "second"]
    assert late_file.is_file()

    library.refresh_preset_catalog()

    assert not late_file.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("second-user*.json"))
    assert len(quarantined) == 1
    assert library.preset_get("second") is None


def test_moving_catalog_snapshot_does_not_garbage_collect_or_cache(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, name="first", model_id=101)
    _write_bundle(bundled, name="second", model_id=202)
    library.sync_bundled_presets(quiet=True, download=False)
    (bundled / "second.json").unlink()
    catalog = library._PRESET_CATALOG
    original_parse = catalog._parse_document
    parse_calls = 0

    def parse_while_catalog_moves(path):
        nonlocal parse_calls
        parse_calls += 1
        entry = original_parse(path)
        _write_bundle(bundled, name="second", model_id=202)
        return entry

    monkeypatch.setattr(catalog, "_parse_document", parse_while_catalog_moves)

    library.sync_bundled_presets(quiet=True, download=False)

    assert library.preset_get("second")["source"] == "bundled"
    first_scan_calls = parse_calls
    library.refresh_preset_catalog()
    assert parse_calls > first_scan_calls


def test_catalog_change_between_final_check_and_commit_restores_stale_row(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(
        bundled, name="first", model_id=101,
        catalog_key="first-key", filename="first-key")
    _write_bundle(
        bundled, name="second", model_id=202,
        catalog_key="second-key", filename="second-key")
    library.sync_bundled_presets(quiet=True, download=False)
    second = library.preset_get("second")
    library.preset_set_active("second")
    second_document = (bundled / "second-key.json").read_text()
    (bundled / "second-key.json").unlink()
    catalog = library._PRESET_CATALOG
    original_token = catalog._catalog_token
    calls = 0

    def restore_after_final_token_was_read():
        nonlocal calls
        calls += 1
        token = original_token()
        if calls == 4:
            (bundled / "second-key.json").write_text(second_document)
        return token

    monkeypatch.setattr(
        catalog, "_catalog_token", restore_after_final_token_was_read)

    library.sync_bundled_presets(quiet=True, download=False)

    with library.connect() as conn:
        restored = conn.execute(
            "SELECT id, source_key FROM presets WHERE source_key='second-key'"
        ).fetchone()
    assert restored is not None
    assert restored["id"] == second["id"]
    assert library.preset_current() == "second"
    original_parse = catalog._parse_document
    parse_calls = 0

    def record_parse(path):
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(path)

    monkeypatch.setattr(catalog, "_parse_document", record_parse)
    library.refresh_preset_catalog()
    assert parse_calls > 0


def test_catalog_change_after_commit_does_not_publish_updated_bundled_row(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(
        bundled, name="first", model_id=101,
        catalog_key="first-key", filename="first-key")
    library.sync_bundled_presets(quiet=True, download=False)
    original_document = (bundled / "first-key.json").read_text()
    updated = json.loads(original_document)
    updated["name"] = "first-updated"
    (bundled / "first-key.json").write_text(json.dumps(updated))

    catalog = library._PRESET_CATALOG
    original_token = catalog._catalog_token
    calls = 0

    def restore_after_final_token_was_read():
        nonlocal calls
        calls += 1
        token = original_token()
        if calls == 4:
            (bundled / "first-key.json").write_text(original_document)
        return token

    monkeypatch.setattr(catalog, "_catalog_token",
                        restore_after_final_token_was_read)

    library.sync_bundled_presets(quiet=True, download=False)

    assert library.preset_get("first") is not None
    assert library.preset_get("first-updated") is None


def test_catalog_change_after_commit_restores_complete_bundled_projection(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(
        bundled, name="first", model_id=101,
        catalog_key="first-key", filename="first-key")
    _write_bundle(
        bundled, name="second", model_id=202,
        catalog_key="second-key", filename="second-key")
    _write_bundle(
        bundled, name="third", model_id=303,
        catalog_key="third-key", filename="third-key")
    library.sync_bundled_presets(quiet=True, download=False)
    original_documents = {
        path.name: path.read_text(encoding="utf-8")
        for path in bundled.glob("*.json")
    }
    initial = {
        preset["source_key"]: (preset["id"], preset["name"])
        for preset in library.preset_list()
        if preset["source"] == "bundled"
    }
    library.preset_set_active("second")
    third_id = initial["third-key"][0]
    tracked_value = json.dumps({"file": "legacy-third.json", "token": "old"})
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (f"preset_file:{third_id}", tracked_value),
        )
        conn.commit()

    _write_bundle(
        bundled, name="second", model_id=101,
        catalog_key="first-key", filename="first-key")
    _write_bundle(
        bundled, name="first", model_id=202,
        catalog_key="second-key", filename="second-key")
    (bundled / "third-key.json").unlink()
    _write_bundle(
        bundled, name="fourth", model_id=404,
        catalog_key="fourth-key", filename="fourth-key")

    catalog = library._PRESET_CATALOG
    original_token = catalog._catalog_token
    calls = 0

    def restore_catalog_after_commit():
        nonlocal calls
        calls += 1
        token = original_token()
        if calls == 4:
            for path in bundled.glob("*.json"):
                path.unlink()
            for name, document in original_documents.items():
                (bundled / name).write_text(document, encoding="utf-8")
        return token

    monkeypatch.setattr(catalog, "_catalog_token", restore_catalog_after_commit)

    library.sync_bundled_presets(quiet=True, download=False)

    restored = {
        preset["source_key"]: (preset["id"], preset["name"])
        for preset in library.preset_list()
        if preset["source"] == "bundled"
    }
    assert restored == initial
    assert library.preset_get("fourth") is None
    assert library.preset_current() == "second"
    with library.connect() as conn:
        tracked = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (f"preset_file:{third_id}",),
        ).fetchone()
    assert tracked["value"] == tracked_value


def test_catalog_race_does_not_overwrite_concurrent_user_preset(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(
        bundled, name="first", model_id=101,
        catalog_key="first-key", filename="first-key")
    library.sync_bundled_presets(quiet=True, download=False)
    original_document = (bundled / "first-key.json").read_text()
    updated = json.loads(original_document)
    updated["name"] = "renamed"
    (bundled / "first-key.json").write_text(json.dumps(updated))

    catalog = library._PRESET_CATALOG
    original_token = catalog._catalog_token
    calls = 0

    def create_user_after_commit():
        nonlocal calls
        calls += 1
        token = original_token()
        if calls == 4:
            (bundled / "first-key.json").write_text(original_document)
        elif calls == 5:
            now = "2026-08-16T00:00:00+00:00"
            with library.connect() as conn:
                conn.execute(
                    "INSERT INTO presets "
                    "(name, note, chain_json, source, created_at, updated_at) "
                    "VALUES ('first', 'user', ?, 'user', ?, ?)",
                    (json.dumps({"slots": []}), now, now),
                )
                conn.commit()
        return token

    monkeypatch.setattr(catalog, "_catalog_token", create_user_after_commit)

    with pytest.warns(RuntimeWarning, match="changed concurrently"):
        library.sync_bundled_presets(quiet=True, download=False)

    user = library.preset_get("first")
    assert user["source"] == "user"
    assert user["note"] == "user"


def test_bundled_sync_keeps_catalog_when_a_document_cannot_be_read(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    library.sync_bundled_presets(quiet=True, download=False)
    monkeypatch.setattr(
        library._PRESET_CATALOG, "_parse_document",
        lambda _path: (_ for _ in ()).throw(
            preset_catalog.BundledPresetReadError("temporarily unavailable")),
    )

    report = library.sync_bundled_presets(quiet=True, download=False)

    assert report["failed_presets"] == ["starter"]
    with library.connect() as conn:
        row = conn.execute(
            "SELECT source FROM presets WHERE name = 'starter'").fetchone()
    assert row is not None
    assert row["source"] == "bundled"


def test_bundled_sync_downloads_missing_models_before_marking_ready(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)

    def fake_import(_tone_id, **_kwargs):
        path = library.TONES_DIR / "tone" / "starter.nam"
        path.parent.mkdir()
        path.write_text(json.dumps({"metadata": {"loudness": -23.0}}),
                        encoding="utf-8")
        with library.connect() as conn:
            library.upsert_tone(conn, {
                "id": 1, "title": "Starter", "gear": "amp",
                "platform": "nam",
            })
            library.upsert_model(conn, {
                "id": 101, "tone_id": 1, "model_url": "starter",
                "name": "starter.nam", "architecture": "SlimmableContainer",
                "local_path": str(path),
            })
        return {"id": 1}

    monkeypatch.setattr(library, "import_tone", fake_import)

    report = library.sync_bundled_presets(quiet=True)

    assert report["total"] == report["ready"] == 1
    assert report["failed"] == 0
    assert library.preset_resolved_chain("starter")["slots"][0]["path"].endswith(
        "starter.nam")


def test_bundled_sync_downloads_from_document_tone_identity(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, model_id=101, tone_id=7)
    calls = []

    def fake_import(tone_id, *, model_ids, quiet):
        calls.append((tone_id, tuple(model_ids), quiet))
        path = library.TONES_DIR / "document-owned.nam"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"metadata": {"loudness": -18}}), encoding="utf-8")
        with library.connect() as conn:
            library.upsert_tone(conn, {
                "id": tone_id, "title": "Document Owned", "gear": "amp",
                "format": "nam", "platform": "nam",
            }, commit=False)
            library.upsert_model(conn, {
                "id": model_ids[0], "tone_id": tone_id,
                "model_url": "document-owned", "name": path.name,
                "architecture": "SlimmableContainer", "local_path": str(path),
            }, commit=False)
            conn.commit()
        return {"id": tone_id}

    monkeypatch.setattr(library, "import_tone", fake_import)

    report = library.sync_bundled_presets(quiet=True)

    assert report == {
        "total": 1, "ready": 1, "preparing": 0, "failed": 0,
        "failed_presets": [],
    }
    assert calls == [(7, (101,), True)]


def test_bundled_retry_is_limited_to_the_selected_preset(tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, name="first", model_id=101)
    _write_bundle(bundled, name="second", model_id=202)
    calls = []

    def fake_import(tone_id, *, model_ids, quiet):
        calls.append((tone_id, tuple(model_ids), quiet))
        for model_id in model_ids:
            path = library.TONES_DIR / f"{model_id}.nam"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"metadata": {"loudness": -18}}),
                            encoding="utf-8")
            with library.connect() as conn:
                library.upsert_tone(conn, {
                    "id": tone_id, "title": f"Tone {tone_id}", "gear": "amp",
                    "format": "nam", "platform": "nam",
                }, commit=False)
                library.upsert_model(conn, {
                    "id": model_id, "tone_id": tone_id,
                    "model_url": str(model_id), "name": path.name,
                    "architecture": "SlimmableContainer", "local_path": str(path),
                }, commit=False)
                conn.commit()
        return {"id": tone_id}

    monkeypatch.setattr(library, "import_tone", fake_import)
    first = library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)
    assert first["preparing"] == 2

    retry = library.sync_bundled_presets(
        quiet=True, download=True, preset_names=["first"])

    assert retry["total"] == retry["ready"] == 1
    assert calls == [(1, (101,), True)]
    assert library.preset_get("first")["availability"] == "READY"
    assert library.preset_get("second")["availability"] == "PREPARING"


def test_bundled_registration_does_not_wait_for_background_download(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    download_started = threading.Event()
    release_download = threading.Event()

    def fake_import(_tone_id, *, model_ids, quiet):
        del quiet
        download_started.set()
        if not release_download.wait(timeout=5):
            raise AssertionError("test download was not released")
        path = library.TONES_DIR / "background.nam"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"metadata": {"loudness": -18}}),
                        encoding="utf-8")
        with library.connect() as conn:
            library.upsert_tone(conn, {
                "id": 1, "title": "Starter", "gear": "amp",
                "format": "nam", "platform": "nam",
            }, commit=False)
            library.upsert_model(conn, {
                "id": model_ids[0], "tone_id": 1,
                "model_url": "starter", "name": path.name,
                "architecture": "SlimmableContainer",
                "local_path": str(path),
            }, commit=False)
            conn.commit()
        return {"id": 1}

    monkeypatch.setattr(library, "import_tone", fake_import)
    result_holder = {}

    def download_bundle():
        result_holder["result"] = library.sync_bundled_presets(quiet=True)

    download_thread = threading.Thread(target=download_bundle)
    download_thread.start()
    assert download_started.wait(timeout=5)

    registration_done = threading.Event()

    def register_only():
        result_holder["registration"] = library.sync_bundled_presets(
            quiet=True, download=False)
        registration_done.set()

    registration_thread = threading.Thread(target=register_only)
    registration_thread.start()
    assert registration_done.wait(timeout=1)
    assert result_holder["registration"]["preparing"] == 1

    release_download.set()
    download_thread.join(timeout=5)
    registration_thread.join(timeout=5)
    assert not download_thread.is_alive()
    assert not registration_thread.is_alive()
    assert result_holder["result"]["ready"] == 1


def test_old_download_cannot_leave_replaced_chain_preparing(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, model_id=101, tone_id=1)
    download_started = threading.Event()
    release_download = threading.Event()

    def delayed_import(_tone_id, *, model_ids, quiet):
        del model_ids, quiet
        download_started.set()
        if not release_download.wait(timeout=5):
            raise AssertionError("test download was not released")
        return {"id": 1}

    monkeypatch.setattr(library, "import_tone", delayed_import)
    result_holder = {}

    def download_old_chain():
        result_holder["old"] = library.sync_bundled_presets(quiet=True)

    download_thread = threading.Thread(target=download_old_chain)
    download_thread.start()
    assert download_started.wait(timeout=5)

    document_path = bundled / "starter.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    document["chain"]["slots"] = [{
        "tone_id": 2,
        "model_id": 202,
        "output_gain_db": 5.0,
    }]
    document_path.write_text(json.dumps(document), encoding="utf-8")
    replacement = library.sync_bundled_presets(
        quiet=True, download=False)
    assert replacement["preparing"] == 1

    release_download.set()
    download_thread.join(timeout=5)
    assert not download_thread.is_alive()

    preset = library.preset_get("starter")
    assert preset["chain"]["slots"][0]["model_id"] == 202
    assert preset["availability"] == "UNAVAILABLE"
    assert "starter" not in (
        library._PRESET_CATALOG.preparation_state_snapshot())


def test_late_old_worker_cannot_reannounce_a_replaced_chain(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, model_id=101, tone_id=1)
    probe_started = threading.Event()
    release_probe = threading.Event()
    original_probe = library._installed_model_ids
    first_probe = True

    def delayed_first_probe(model_ids):
        nonlocal first_probe
        if first_probe:
            first_probe = False
            probe_started.set()
            if not release_probe.wait(timeout=5):
                raise AssertionError("test probe was not released")
        return original_probe(model_ids)

    monkeypatch.setattr(library, "_installed_model_ids", delayed_first_probe)
    monkeypatch.setattr(library, "import_tone", lambda *_args, **_kwargs: {})
    result_holder = {}

    def download_old_chain():
        result_holder["old"] = library.sync_bundled_presets(quiet=True)

    download_thread = threading.Thread(target=download_old_chain)
    download_thread.start()
    assert probe_started.wait(timeout=5)

    document_path = bundled / "starter.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    document["chain"]["slots"] = [{
        "tone_id": 2,
        "model_id": 202,
        "output_gain_db": 5.0,
    }]
    document_path.write_text(json.dumps(document), encoding="utf-8")
    replacement = library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)
    assert replacement["preparing"] == 1

    release_probe.set()
    download_thread.join(timeout=5)
    assert not download_thread.is_alive()
    assert result_holder["old"]["preparing"] == 0
    assert library.preset_get("starter")["availability"] == "PREPARING"


def test_old_download_exception_cannot_mark_replaced_chain_unavailable(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, model_id=101, tone_id=1)
    download_started = threading.Event()
    release_download = threading.Event()

    def delayed_failure(_entries, _model_ids, *, quiet):
        del quiet
        download_started.set()
        if not release_download.wait(timeout=5):
            raise AssertionError("test download was not released")
        raise RuntimeError("old worker failed")

    monkeypatch.setattr(
        library._PRESET_CATALOG._preparation, "_download_models",
        delayed_failure,
    )
    result_holder = {}

    def download_old_chain():
        try:
            library.sync_bundled_presets(quiet=True)
        except Exception as exc:
            result_holder["error"] = exc

    download_thread = threading.Thread(target=download_old_chain)
    download_thread.start()
    assert download_started.wait(timeout=5)

    document_path = bundled / "starter.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    document["chain"]["slots"] = [{
        "tone_id": 2,
        "model_id": 202,
        "output_gain_db": 5.0,
    }]
    document_path.write_text(json.dumps(document), encoding="utf-8")
    replacement = library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)
    assert replacement["preparing"] == 1
    assert library.preset_get("starter")["availability"] == "PREPARING"

    release_download.set()
    download_thread.join(timeout=5)
    assert not download_thread.is_alive()
    assert isinstance(result_holder.get("error"), RuntimeError)
    assert library.preset_get("starter")["availability"] == "PREPARING"
    assert library._PRESET_CATALOG.preparation_state_snapshot()["starter"][
        "status"] == "PREPARING"


def test_old_download_cannot_clear_same_model_gain_replacement(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, model_id=101, tone_id=1)
    download_started = threading.Event()
    release_download = threading.Event()

    def delayed_import(_tone_id, *, model_ids, quiet):
        del model_ids, quiet
        download_started.set()
        if not release_download.wait(timeout=5):
            raise AssertionError("test download was not released")
        return {"id": 1}

    monkeypatch.setattr(library, "import_tone", delayed_import)
    result_holder = {}

    def download_old_chain():
        result_holder["old"] = library.sync_bundled_presets(quiet=True)

    download_thread = threading.Thread(target=download_old_chain)
    download_thread.start()
    assert download_started.wait(timeout=5)

    document_path = bundled / "starter.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    document["chain"]["slots"][0]["output_gain_db"] = 7.0
    document_path.write_text(json.dumps(document), encoding="utf-8")
    replacement = library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)
    assert replacement["preparing"] == 1

    release_download.set()
    download_thread.join(timeout=5)
    assert not download_thread.is_alive()
    assert result_holder["old"]["preparing"] == 0
    preset = library.preset_get("starter")
    assert preset["chain"]["slots"][0]["output_gain_db"] == 7.0
    assert preset["availability"] == "PREPARING"


def test_old_exception_cannot_pollute_source_replacement_after_plain_refresh(
        tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled, model_id=101, tone_id=1)
    download_started = threading.Event()
    release_download = threading.Event()

    def delayed_failure(_entries, _model_ids, *, quiet):
        del quiet
        download_started.set()
        if not release_download.wait(timeout=5):
            raise AssertionError("test download was not released")
        raise RuntimeError("old worker failed")

    monkeypatch.setattr(
        library._PRESET_CATALOG._preparation, "_download_models",
        delayed_failure,
    )
    result_holder = {}

    def download_old_chain():
        try:
            library.sync_bundled_presets(quiet=True)
        except Exception as exc:
            result_holder["error"] = exc

    download_thread = threading.Thread(target=download_old_chain)
    download_thread.start()
    assert download_started.wait(timeout=5)

    document_path = bundled / "starter.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    document["chain"]["slots"][0]["tone_id"] = 2
    document_path.write_text(json.dumps(document), encoding="utf-8")
    library.refresh_preset_catalog()
    assert library.preset_get("starter")["availability"] == "UNAVAILABLE"
    assert "starter" not in (
        library._PRESET_CATALOG.preparation_state_snapshot())

    release_download.set()
    download_thread.join(timeout=5)
    assert not download_thread.is_alive()
    assert isinstance(result_holder.get("error"), RuntimeError)
    assert library.preset_get("starter")["availability"] == "UNAVAILABLE"
    assert "starter" not in (
        library._PRESET_CATALOG.preparation_state_snapshot())


def test_cli_style_bundled_load_retries_only_when_loading(tmp_path, monkeypatch):
    bundled = _configure(tmp_path, monkeypatch)
    _write_bundle(bundled)
    calls = []

    def fake_import(_tone_id, *, model_ids, quiet):
        calls.append(tuple(model_ids))
        path = library.TONES_DIR / "loaded.nam"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"metadata": {"loudness": -18}}),
                        encoding="utf-8")
        with library.connect() as conn:
            library.upsert_tone(conn, {
                "id": 1, "title": "Starter", "gear": "amp",
                "format": "nam", "platform": "nam",
            }, commit=False)
            library.upsert_model(conn, {
                "id": 101, "tone_id": 1, "model_url": "starter",
                "name": path.name, "architecture": "SlimmableContainer",
                "local_path": str(path),
            }, commit=False)
            conn.commit()
        return {"id": 1}

    monkeypatch.setattr(library, "import_tone", fake_import)
    library.sync_bundled_presets(quiet=True, download=False)
    document = json.loads((bundled / "starter.json").read_text())
    document["name"] = "renamed starter"
    (bundled / "starter.json").write_text(
        json.dumps(document), encoding="utf-8")
    library.sync_bundled_presets(quiet=True, download=False)

    loaded = library.preset_load("renamed starter")

    assert calls == [(101,)]
    assert loaded["slots"][0]["path"].endswith("loaded.nam")
    assert library.chain_get()["slots"][0]["path"].endswith("loaded.nam")


def test_connect_rechecks_cached_legacy_preset_schema(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    monkeypatch.setattr(library, "DB_FILE", db_file)
    with sqlite3.connect(db_file) as raw:
        raw.executescript(library.SCHEMA)
        raw.execute("DROP TABLE presets")
        raw.execute(
            "CREATE TABLE presets ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT UNIQUE NOT NULL, note TEXT, chain_json TEXT NOT NULL, "
            "created_at TEXT, updated_at TEXT)"
        )
    resolved = db_file.resolve(strict=False)
    library._SCHEMA_READY.add(resolved)
    try:
        with library.connect() as conn:
            columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(presets)").fetchall()
            }
            index_sql = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND name='idx_presets_source_key'"
            ).fetchone()[0]
    finally:
        library._SCHEMA_READY.discard(resolved)

    assert {"source", "source_key"} <= columns
    assert "source = 'bundled'" in index_sql
