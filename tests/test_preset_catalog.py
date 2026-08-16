"""Behavior tests for the Preset Catalog interface."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import warnings

import pytest

import library
import preset_catalog
import preset_document
import preset_editable
from preset_catalog import (
    AnnouncePreparation,
    ByName,
    PresetCatalog,
    RefreshCatalog,
    SyncReport,
)


def _configure_catalog(tmp_path, monkeypatch):
    data = tmp_path / "data"
    bundled = tmp_path / "presets" / "built-in"
    monkeypatch.setattr(library, "ROOT", tmp_path)
    monkeypatch.setattr(library, "DB_FILE", data / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", data / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", data / "tones")
    monkeypatch.setattr(library, "PRESETS_DIR", data / "presets")
    monkeypatch.setattr(library, "BUNDLED_PRESETS_DIR", bundled)
    bundled.mkdir(parents=True)
    (bundled / "starter.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-bundled-preset",
        "catalog_key": "starter",
        "name": "starter",
        "note": "starter note",
        "chain": {
            "slots": [{
                "tone_id": 1,
                "model_id": 101,
                "output_gain_db": 5.0,
            }],
            "gain": 1.0,
            "master": 1.0,
            "quality": 1.0,
        },
    }), encoding="utf-8")


def test_semantic_chain_key_ignores_projection_fields_but_tracks_behavior():
    chain = {
        "slots": [{"model_id": 101, "path": "/machine/model.nam"}],
        "gain": 1.0,
        "master": 1.0,
        "quality": 1.0,
    }
    projected = {
        **chain,
        "slots": [{
            "model_id": 101,
            "path": "/other-machine/model.nam",
            "candidate": {"path": "/other-machine/model.nam"},
        }],
    }

    assert preset_document.semantic_chain_key(chain) == (
        preset_document.semantic_chain_key(projected))
    changed_gain = {
        **chain,
        "slots": [{"model_id": 101, "output_gain_db": 1.0}],
    }
    changed_bypass = {
        **chain,
        "slots": [{"model_id": 101, "bypass": True}],
    }
    assert preset_document.semantic_chain_key(changed_gain) != (
        preset_document.semantic_chain_key(chain))
    assert preset_document.semantic_chain_key(changed_bypass) != (
        preset_document.semantic_chain_key(chain))
    assert preset_document.semantic_chain_key({**chain, "gain": None}) is None


def test_catalog_reads_the_preparation_state_it_announced(tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    catalog = PresetCatalog(lambda: library)

    report = catalog.synchronize(AnnouncePreparation())

    assert report == SyncReport(total=1, preparing=1)
    assert catalog.read(ByName("starter"))["availability"] == "PREPARING"


def test_current_preparation_generation_does_not_rescan_catalog(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    catalog = PresetCatalog(lambda: library)
    catalog.synchronize(AnnouncePreparation())

    def unexpected_scan():
        raise AssertionError("current generations must not rescan documents")

    monkeypatch.setattr(catalog, "_scan_catalog", unexpected_scan)

    generations = catalog._preparation_generations_for_target(
        preset_catalog.BundleTarget())

    assert list(generations) == ["starter"]


def test_refresh_registers_and_reconciles_one_bundled_snapshot(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    catalog = PresetCatalog(lambda: library)
    original_scan = catalog._scan_catalog
    snapshots = []

    def capture_scan():
        snapshot = original_scan()
        snapshots.append(snapshot)
        return snapshot

    reconciled = []
    monkeypatch.setattr(catalog, "_scan_catalog", capture_scan)
    monkeypatch.setattr(
        catalog._editable, "reconcile", lambda names: reconciled.append(names))

    catalog.synchronize(RefreshCatalog())

    assert len(snapshots) == 1
    assert reconciled == [{"starter"}]


def test_bundled_registration_holds_the_editable_reconciliation_lock(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    catalog = PresetCatalog(lambda: library)
    original_register = catalog._bundled_registry.register
    lock_owned = []

    def capture_register(*args, **kwargs):
        lock_owned.append(catalog.reconcile_lock._is_owned())
        return original_register(*args, **kwargs)

    monkeypatch.setattr(catalog._bundled_registry, "register", capture_register)

    catalog.synchronize(RefreshCatalog())

    assert lock_owned == [True]


def test_refresh_does_not_reconcile_names_from_a_stale_bundled_snapshot(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    catalog = PresetCatalog(lambda: library)
    user_file = library.PRESETS_DIR / "late-user.json"
    user_file.parent.mkdir(parents=True)
    user_file.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-preset",
        "name": "late",
        "chain": {"slots": [], "gain": 1.0, "master": 1.0, "quality": 1.0},
    }), encoding="utf-8")
    original_register = catalog._bundled_registry.register
    added = False

    def add_bundle_after_registration(*args, **kwargs):
        nonlocal added
        registration = original_register(*args, **kwargs)
        if not added:
            added = True
            source = json.loads(
                (library.BUNDLED_PRESETS_DIR / "starter.json").read_text(
                    encoding="utf-8"))
            source.update({"catalog_key": "late", "name": "late"})
            (library.BUNDLED_PRESETS_DIR / "late.json").write_text(
                json.dumps(source), encoding="utf-8")
        return registration

    monkeypatch.setattr(
        catalog._bundled_registry, "register", add_bundle_after_registration)

    catalog.synchronize(RefreshCatalog())

    assert catalog.read(ByName("late")) is None
    assert user_file.is_file()

    monkeypatch.setattr(catalog._bundled_registry, "register", original_register)
    catalog.synchronize(RefreshCatalog())

    assert catalog.read(ByName("late"))["source"] == "bundled"
    assert not user_file.exists()
    assert list((library.PRESETS_DIR / ".quarantine").glob("late-user*.json"))


def test_announce_preparation_failure_finishes_preparing_state(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    catalog = PresetCatalog(lambda: library)
    catalog._preparation._states["starter"] = {
        "status": "PREPARING", "error": "",
    }

    def fail_synchronization(**_kwargs):
        raise RuntimeError("catalog changed during preparation")

    monkeypatch.setattr(catalog, "_synchronize_bundled", fail_synchronization)

    with pytest.raises(RuntimeError, match="catalog changed"):
        catalog.synchronize(AnnouncePreparation())

    assert catalog.preparation_state_snapshot()["starter"]["status"] == (
        "UNAVAILABLE")


def test_rename_publish_failure_preserves_original_preset(tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    old_path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    new_path = library.PRESETS_DIR / f"{saved['id']}-after.json"
    original_link = preset_editable.os.link

    def fail_new_document(source, target, *args, **kwargs):
        if Path(target) == new_path:
            raise OSError("injected Preset publish failure")
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(preset_editable.os, "link", fail_new_document)

    with pytest.raises(OSError, match="injected Preset publish failure"):
        library.preset_rename_by_id(saved["id"], "after")

    assert library.preset_get_by_id(saved["id"])["name"] == "before"
    assert json.loads(old_path.read_text(encoding="utf-8"))["name"] == "before"
    assert not new_path.exists()

    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "before"
    assert old_path.is_file()


def test_rename_commit_failure_restores_original_preset(tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    old_path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    new_path = library.PRESETS_DIR / f"{saved['id']}-after.json"
    original_commit = library._ManagedConnection.commit

    def fail_commit(_connection):
        raise sqlite3.OperationalError("injected SQLite commit failure")

    monkeypatch.setattr(library._ManagedConnection, "commit", fail_commit)

    with pytest.raises(sqlite3.OperationalError, match="injected SQLite"):
        library.preset_rename_by_id(saved["id"], "after")

    monkeypatch.setattr(library._ManagedConnection, "commit", original_commit)
    assert library.preset_get_by_id(saved["id"])["name"] == "before"
    assert json.loads(old_path.read_text(encoding="utf-8"))["name"] == "before"
    assert not new_path.exists()

    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "before"


def test_refresh_recovers_a_rename_interrupted_before_sqlite_commit(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    old_path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    new_path = library.PRESETS_DIR / f"{saved['id']}-after.json"
    source_root = Path(__file__).resolve().parents[1]
    child = """
import os
from pathlib import Path

import library

root = Path(os.environ["GIGBUDDY_CRASH_TEST_ROOT"])
library.ROOT = root
library.DB_FILE = root / "data" / "gigbuddy.db"
library.CHAIN_FILE = root / "data" / "live_chain.json"
library.TONES_DIR = root / "data" / "tones"
library.PRESETS_DIR = root / "data" / "presets"
library.BUNDLED_PRESETS_DIR = root / "presets" / "built-in"

# Warm the schema cache before replacing the explicit mutation commit.
with library.connect():
    pass

original_commit = library._ManagedConnection.commit
new_path = library.PRESETS_DIR / (
    os.environ["GIGBUDDY_CRASH_TEST_PRESET_ID"] + "-after.json")

def crash_before_commit(connection):
    transaction_dirs = list(library.PRESETS_DIR.glob(
        ".preset-" + os.environ["GIGBUDDY_CRASH_TEST_PRESET_ID"] + "-*"))
    if new_path.is_file() and transaction_dirs:
        os._exit(91)
    return original_commit(connection)

library._ManagedConnection.commit = crash_before_commit
library.preset_rename_by_id(
    int(os.environ["GIGBUDDY_CRASH_TEST_PRESET_ID"]), "after")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root / "src")
    env["GIGBUDDY_CRASH_TEST_ROOT"] = str(tmp_path)
    env["GIGBUDDY_CRASH_TEST_PRESET_ID"] = str(saved["id"])

    result = subprocess.run(
        [sys.executable, "-c", child], env=env,
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 91, result.stderr
    assert not old_path.exists()
    assert new_path.is_file()
    assert list(library.PRESETS_DIR.glob(f".preset-{saved['id']}-*"))
    before_refresh = library.preset_get_by_id(saved["id"])
    assert before_refresh is not None
    assert before_refresh["name"] == "before"

    library.refresh_preset_catalog()

    restored = library.preset_get_by_id(saved["id"])
    assert restored is not None
    assert restored["name"] == "before"
    assert old_path.is_file()
    assert not new_path.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob(f"{saved['id']}-after*.json"))
    assert len(quarantined) == 1
    assert not list(library.PRESETS_DIR.glob(f".preset-{saved['id']}-*"))


def test_refresh_finishes_a_rename_committed_before_process_exit(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    old_path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    new_path = library.PRESETS_DIR / f"{saved['id']}-after.json"
    source_root = Path(__file__).resolve().parents[1]
    child = """
import os
from pathlib import Path

import library
import preset_editable

root = Path(os.environ["GIGBUDDY_CRASH_TEST_ROOT"])
library.ROOT = root
library.DB_FILE = root / "data" / "gigbuddy.db"
library.CHAIN_FILE = root / "data" / "live_chain.json"
library.TONES_DIR = root / "data" / "tones"
library.PRESETS_DIR = root / "data" / "presets"
library.BUNDLED_PRESETS_DIR = root / "presets" / "built-in"

def crash_after_commit(_transaction):
    os._exit(92)

preset_editable._EditableFileTransaction.mark_database_committed = (
    crash_after_commit)
library.preset_rename_by_id(
    int(os.environ["GIGBUDDY_CRASH_TEST_PRESET_ID"]), "after")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root / "src")
    env["GIGBUDDY_CRASH_TEST_ROOT"] = str(tmp_path)
    env["GIGBUDDY_CRASH_TEST_PRESET_ID"] = str(saved["id"])

    result = subprocess.run(
        [sys.executable, "-c", child], env=env,
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 92, result.stderr
    assert not old_path.exists()
    assert new_path.is_file()
    assert library.preset_get_by_id(saved["id"])["name"] == "after"
    assert list(library.PRESETS_DIR.glob(f".preset-{saved['id']}-*"))

    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "after"
    assert new_path.is_file()
    assert not old_path.exists()
    assert not list(library.PRESETS_DIR.glob(f".preset-{saved['id']}-*"))


def test_refresh_recovers_a_delete_interrupted_before_sqlite_commit(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("delete-me", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-delete-me.json"
    source_root = Path(__file__).resolve().parents[1]
    child = """
import os
from pathlib import Path

import library

root = Path(os.environ["GIGBUDDY_CRASH_TEST_ROOT"])
library.ROOT = root
library.DB_FILE = root / "data" / "gigbuddy.db"
library.CHAIN_FILE = root / "data" / "live_chain.json"
library.TONES_DIR = root / "data" / "tones"
library.PRESETS_DIR = root / "data" / "presets"
library.BUNDLED_PRESETS_DIR = root / "presets" / "built-in"

with library.connect():
    pass

original_commit = library._ManagedConnection.commit

def crash_before_commit(connection):
    preset_id = os.environ["GIGBUDDY_CRASH_TEST_PRESET_ID"]
    path = library.PRESETS_DIR / f"{preset_id}-delete-me.json"
    if (not path.exists()
            and list(library.PRESETS_DIR.glob(f".preset-{preset_id}-*"))):
        os._exit(93)
    return original_commit(connection)

library._ManagedConnection.commit = crash_before_commit
library.preset_delete_by_id(
    int(os.environ["GIGBUDDY_CRASH_TEST_PRESET_ID"]))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root / "src")
    env["GIGBUDDY_CRASH_TEST_ROOT"] = str(tmp_path)
    env["GIGBUDDY_CRASH_TEST_PRESET_ID"] = str(saved["id"])

    result = subprocess.run(
        [sys.executable, "-c", child], env=env,
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 93, result.stderr
    assert not path.exists()
    assert library.preset_get_by_id(saved["id"])["name"] == "delete-me"

    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "delete-me"
    assert path.is_file()
    assert not list(library.PRESETS_DIR.glob(f".preset-{saved['id']}-*"))


def test_refresh_finishes_a_delete_committed_before_process_exit(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("delete-me", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-delete-me.json"
    source_root = Path(__file__).resolve().parents[1]
    child = """
import os
from pathlib import Path

import library
import preset_editable

root = Path(os.environ["GIGBUDDY_CRASH_TEST_ROOT"])
library.ROOT = root
library.DB_FILE = root / "data" / "gigbuddy.db"
library.CHAIN_FILE = root / "data" / "live_chain.json"
library.TONES_DIR = root / "data" / "tones"
library.PRESETS_DIR = root / "data" / "presets"
library.BUNDLED_PRESETS_DIR = root / "presets" / "built-in"

def crash_after_commit(_transaction):
    os._exit(94)

preset_editable._EditableFileTransaction.mark_database_committed = (
    crash_after_commit)
library.preset_delete_by_id(
    int(os.environ["GIGBUDDY_CRASH_TEST_PRESET_ID"]))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root / "src")
    env["GIGBUDDY_CRASH_TEST_ROOT"] = str(tmp_path)
    env["GIGBUDDY_CRASH_TEST_PRESET_ID"] = str(saved["id"])

    result = subprocess.run(
        [sys.executable, "-c", child], env=env,
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 94, result.stderr
    assert not path.exists()
    assert library.preset_get_by_id(saved["id"]) is None
    assert list(library.PRESETS_DIR.glob(f".preset-{saved['id']}-*"))

    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"]) is None
    assert not path.exists()
    assert not list(library.PRESETS_DIR.glob(f".preset-{saved['id']}-*"))
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("backup-*-delete-me.json"))
    assert len(quarantined) == 1


def test_refresh_preserves_a_staged_only_interrupted_edit(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    transaction = library.PRESETS_DIR / f".preset-{saved['id']}-interrupted"
    transaction.mkdir()
    staged = transaction / "staged.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["name"] = "after"
    staged.write_text(json.dumps(document), encoding="utf-8")

    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "before"
    assert path.is_file()
    assert not transaction.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("staged*.json"))
    assert len(quarantined) == 1
    assert json.loads(quarantined[0].read_text(encoding="utf-8"))["name"] == (
        "after")


def test_refresh_recovers_multiple_interrupted_directories_from_sqlite(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    original_path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    original_document = json.loads(original_path.read_text(encoding="utf-8"))
    first = library.PRESETS_DIR / f".preset-{saved['id']}-first"
    second = library.PRESETS_DIR / f".preset-{saved['id']}-second"
    first.mkdir()
    second.mkdir()
    original_path.replace(first / f"backup-0-{original_path.name}")
    published = library.PRESETS_DIR / f"{saved['id']}-after.json"
    changed = {**original_document, "name": "after"}
    published.write_text(json.dumps(changed), encoding="utf-8")
    (second / "staged.json").write_text(
        json.dumps({**original_document, "name": "later"}),
        encoding="utf-8",
    )

    library.refresh_preset_catalog()

    current = library.preset_get_by_id(saved["id"])
    assert current is not None
    assert current["name"] == "before"
    assert original_path.is_file()
    assert not published.exists()
    assert not list(library.PRESETS_DIR.glob(f".preset-{saved['id']}-*"))
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("*.json"))
    assert len(quarantined) == 3


def test_interrupted_recovery_quarantine_failure_keeps_database_row_visible(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    original_path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    transaction = library.PRESETS_DIR / f".preset-{saved['id']}-interrupted"
    transaction.mkdir()
    original_path.replace(transaction / f"backup-0-{original_path.name}")
    published = library.PRESETS_DIR / f"{saved['id']}-after.json"
    document = json.loads(
        (transaction / f"backup-0-{original_path.name}").read_text(
            encoding="utf-8"))
    published.write_text(
        json.dumps({**document, "name": "after"}), encoding="utf-8")
    original_quarantine = preset_editable.quarantine_preset_file

    def fail_published_quarantine(path, presets_dir):
        if Path(path) == published:
            return None
        return original_quarantine(path, presets_dir)

    monkeypatch.setattr(
        preset_editable, "quarantine_preset_file", fail_published_quarantine)

    with pytest.raises(preset_catalog.PresetRecoveryError):
        library.refresh_preset_catalog()

    current = library.preset_get_by_id(saved["id"])
    assert current is not None
    assert current["name"] == "before"
    assert published.is_file()

    monkeypatch.setattr(
        preset_editable, "quarantine_preset_file", original_quarantine)
    library.refresh_preset_catalog()
    assert library.preset_get_by_id(saved["id"])["name"] == "before"
    assert original_path.is_file()


def test_interrupted_recovery_rolls_back_if_a_later_publish_fails(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    original_path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    original_document = json.loads(original_path.read_text(encoding="utf-8"))
    transaction = library.PRESETS_DIR / f".preset-{saved['id']}-interrupted"
    transaction.mkdir()
    backup = transaction / f"backup-0-{original_path.name}"
    original_path.replace(backup)
    published = library.PRESETS_DIR / f"{saved['id']}-after.json"
    published.write_text(
        json.dumps({**original_document, "name": "after"}), encoding="utf-8")
    new_file = library.PRESETS_DIR / "later.json"
    new_file.write_text(
        json.dumps({**original_document, "id": None, "name": "later"}),
        encoding="utf-8",
    )
    original_link = preset_editable.os.link

    def fail_later_publish(source, target, *args, **kwargs):
        if Path(target).name.endswith("-later.json"):
            raise OSError("injected later Preset publish failure")
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(preset_editable.os, "link", fail_later_publish)

    with pytest.raises(OSError, match="injected later Preset publish failure"):
        library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "before"
    assert published.is_file()
    assert backup.is_file()
    assert new_file.is_file()

    monkeypatch.setattr(preset_editable.os, "link", original_link)
    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "before"
    assert original_path.is_file()
    assert not published.exists()
    assert not transaction.exists()
    assert library.preset_get("later") is not None


def test_refresh_quarantines_an_untracked_duplicate_user_preset(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("duplicate-name", set_active=False)
    tracked = next(library.PRESETS_DIR.glob(f"{saved['id']}-*.json"))
    duplicate = library.PRESETS_DIR / "old-generation.json"
    duplicate.write_text(tracked.read_text(encoding="utf-8"), encoding="utf-8")

    library.refresh_preset_catalog()

    current = library.preset_get_by_id(saved["id"])
    assert current is not None
    assert current["name"] == "duplicate-name"
    assert not duplicate.exists()
    quarantined = list(
        (library.PRESETS_DIR / ".quarantine").glob("old-generation*.json"))
    assert len(quarantined) == 1
    assert json.loads(quarantined[0].read_text(encoding="utf-8"))["name"] == (
        "duplicate-name")


def test_reconcile_restores_quarantine_when_a_later_publish_fails(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("duplicate-name", set_active=False)
    tracked = next(library.PRESETS_DIR.glob(f"{saved['id']}-*.json"))
    duplicate = library.PRESETS_DIR / "a-duplicate.json"
    duplicate.write_text(tracked.read_text(encoding="utf-8"), encoding="utf-8")
    new_file = library.PRESETS_DIR / "b-new.json"
    new_document = json.loads(tracked.read_text(encoding="utf-8"))
    new_document["name"] = "new-name"
    new_file.write_text(json.dumps(new_document), encoding="utf-8")
    original_link = preset_editable.os.link

    def fail_new_preset(source, target, *args, **kwargs):
        if Path(target).name.endswith("-new-name.json"):
            raise OSError("injected later Preset publish failure")
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(preset_editable.os, "link", fail_new_preset)

    with pytest.raises(OSError, match="injected later Preset publish failure"):
        library.refresh_preset_catalog()

    assert duplicate.is_file()
    assert new_file.is_file()
    assert not list(
        (library.PRESETS_DIR / ".quarantine").glob("a-duplicate*.json"))
    assert library.preset_get("duplicate-name") is not None
    assert library.preset_get("new-name") is None


@pytest.mark.parametrize("delete_by", ["id", "name"])
def test_delete_cleanup_failure_does_not_revive_preset(
        tmp_path, monkeypatch, delete_by):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("delete me", set_active=False)
    original_unlink = Path.unlink
    cleanup_attempted = False

    def fail_preset_json_cleanup(path, *args, **kwargs):
        nonlocal cleanup_attempted
        if path.suffix == ".json" and f"{saved['id']}-delete-me" in path.name:
            cleanup_attempted = True
            raise OSError("injected Preset cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_preset_json_cleanup)

    with pytest.warns(RuntimeWarning, match="Could not clean committed Preset"):
        deleted = (
            library.preset_delete_by_id(saved["id"])["deleted"]
            if delete_by == "id"
            else library.preset_delete("delete me")
        )

    assert cleanup_attempted is True
    assert deleted is True
    assert library.preset_get_by_id(saved["id"]) is None
    assert not list(library.PRESETS_DIR.glob(f"{saved['id']}-*.json"))

    library.refresh_preset_catalog()

    assert library.preset_get("delete me") is None


def test_committed_delete_does_not_fail_when_cleanup_warning_is_an_error(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("delete me", set_active=False)
    original_unlink = Path.unlink

    def fail_preset_json_cleanup(path, *args, **kwargs):
        if path.suffix == ".json" and f"{saved['id']}-delete-me" in path.name:
            raise OSError("injected Preset cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_preset_json_cleanup)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = library.preset_delete_by_id(saved["id"])

    assert result["deleted"] is True
    assert library.preset_get_by_id(saved["id"]) is None
    library.refresh_preset_catalog()
    assert library.preset_get("delete me") is None


def test_unexpected_prepare_failure_finishes_as_unavailable(tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.sync_bundled_presets(
        quiet=True, download=False, mark_preparing=True)
    assert library.preset_get("starter")["availability"] == "PREPARING"
    original_probe = library._installed_model_ids

    def fail_availability_probe(_model_ids):
        raise RuntimeError("injected availability probe failure")

    monkeypatch.setattr(
        library, "_installed_model_ids", fail_availability_probe)

    with pytest.raises(RuntimeError, match="injected availability probe failure"):
        library.sync_bundled_presets(quiet=True, download=True)

    monkeypatch.setattr(library, "_installed_model_ids", original_probe)
    assert library.preset_get("starter")["availability"] == "UNAVAILABLE"


def test_external_rename_publish_failure_is_recoverable(tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", set_active=False)
    old_path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    new_path = library.PRESETS_DIR / f"{saved['id']}-after.json"
    document = json.loads(old_path.read_text(encoding="utf-8"))
    document["name"] = "after"
    old_path.write_text(json.dumps(document), encoding="utf-8")
    original_link = preset_editable.os.link

    def fail_new_document(source, target, *args, **kwargs):
        if Path(target) == new_path:
            raise OSError("injected external rename publish failure")
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(preset_editable.os, "link", fail_new_document)

    with pytest.raises(OSError, match="injected external rename"):
        library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "before"
    assert json.loads(old_path.read_text(encoding="utf-8"))["name"] == "after"
    assert not new_path.exists()

    monkeypatch.setattr(preset_editable.os, "link", original_link)
    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["name"] == "after"
    assert new_path.is_file()
    assert not old_path.exists()


def test_reconcile_does_not_overwrite_an_edit_arriving_after_parse(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("edited", note="original", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-edited.json"
    first = json.loads(path.read_text(encoding="utf-8"))
    first["note"] = "first edit"
    path.write_text(json.dumps(first), encoding="utf-8")
    store = library._PRESET_CATALOG._editable
    original_parse = store.parse_editable_document
    injected = False

    def edit_after_parse(candidate, *, scan_local=True):
        nonlocal injected
        result = original_parse(candidate, scan_local=scan_local)
        if Path(candidate) == path and not injected:
            injected = True
            second = json.loads(path.read_text(encoding="utf-8"))
            second["note"] = "second edit"
            path.write_text(json.dumps(second), encoding="utf-8")
        return result

    monkeypatch.setattr(store, "parse_editable_document", edit_after_parse)

    library.refresh_preset_catalog()

    assert json.loads(path.read_text(encoding="utf-8"))["note"] == "second edit"
    monkeypatch.setattr(store, "parse_editable_document", original_parse)
    library.refresh_preset_catalog()
    assert library.preset_get_by_id(saved["id"])["note"] == "second edit"


def test_reconcile_detects_same_size_edit_with_restored_mtime_after_parse(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("edited", note="original-a", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-edited.json"
    first = json.loads(path.read_text(encoding="utf-8"))
    first["note"] = "first-edit"
    path.write_text(json.dumps(first), encoding="utf-8")
    parsed_stat = path.stat()
    parsed_token = preset_editable.preset_file_token(path)
    store = library._PRESET_CATALOG._editable
    original_parse = store.parse_editable_document
    injected = False

    def edit_after_parse(candidate, *, scan_local=True):
        nonlocal injected
        result = original_parse(candidate, scan_local=scan_local)
        if Path(candidate) == path and not injected:
            injected = True
            second = json.loads(path.read_text(encoding="utf-8"))
            second["note"] = "later-edit"
            path.write_text(json.dumps(second), encoding="utf-8")
            os.utime(
                path,
                ns=(parsed_stat.st_atime_ns, parsed_stat.st_mtime_ns),
            )
            assert path.stat().st_size == parsed_stat.st_size
            assert path.stat().st_mtime_ns == parsed_stat.st_mtime_ns
            assert preset_editable.preset_file_token(path) != parsed_token
        return result

    monkeypatch.setattr(store, "parse_editable_document", edit_after_parse)

    library.refresh_preset_catalog()

    assert json.loads(path.read_text(encoding="utf-8"))["note"] == "later-edit"
    monkeypatch.setattr(store, "parse_editable_document", original_parse)
    library.refresh_preset_catalog()
    assert library.preset_get_by_id(saved["id"])["note"] == "later-edit"


def test_reconcile_migrates_legacy_token_before_using_ctime(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save(
        "legacy-token", note="first-note", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-legacy-token.json"
    initial_stat = path.stat()
    legacy_token = (
        f"{initial_stat.st_ino}:{initial_stat.st_size}:"
        f"{initial_stat.st_mtime_ns}"
    )
    with library.connect() as conn:
        conn.execute(
            "UPDATE settings SET value=? WHERE key=?",
            (json.dumps({"file": path.name, "token": legacy_token}),
             preset_editable.preset_file_key(saved["id"])),
        )
        conn.commit()

    library.refresh_preset_catalog()

    with library.connect() as conn:
        migrated = preset_editable.tracked_preset_files(conn)[saved["id"]]
    assert migrated["token"] == preset_editable.preset_file_token(path)
    assert preset_editable.preset_file_token_is_current(migrated["token"])

    before_edit = path.stat()
    document_text = path.read_text(encoding="utf-8")
    assert '"note": "first-note"' in document_text
    path.write_text(
        document_text.replace(
            '"note": "first-note"', '"note": "other-note"'),
        encoding="utf-8",
    )
    os.utime(
        path,
        ns=(before_edit.st_atime_ns, before_edit.st_mtime_ns),
    )
    assert path.stat().st_size == before_edit.st_size
    assert path.stat().st_mtime_ns == before_edit.st_mtime_ns
    assert preset_editable.preset_file_token(path) != migrated["token"]

    library.refresh_preset_catalog()

    assert library.preset_get_by_id(saved["id"])["note"] == "other-note"


def test_reconcile_does_not_overwrite_a_file_recreated_before_publish(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("edited", note="original", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-edited.json"
    first = json.loads(path.read_text(encoding="utf-8"))
    first["note"] = "first edit"
    path.write_text(json.dumps(first), encoding="utf-8")
    original_link = preset_editable.os.link
    injected = False

    def recreate_before_publish(source, target, *args, **kwargs):
        nonlocal injected
        if Path(target) == path and not injected:
            injected = True
            second = dict(first)
            second["note"] = "second edit"
            path.write_text(json.dumps(second), encoding="utf-8")
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(preset_editable.os, "link", recreate_before_publish)

    library.refresh_preset_catalog()

    assert json.loads(path.read_text(encoding="utf-8"))["note"] == "second edit"
    monkeypatch.setattr(preset_editable.os, "link", original_link)
    library.refresh_preset_catalog()
    assert library.preset_get_by_id(saved["id"])["note"] == "second edit"


def test_reconcile_tracks_its_publication_not_a_later_external_write(
        tmp_path, monkeypatch):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("edited", note="original", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-edited.json"
    first = json.loads(path.read_text(encoding="utf-8"))
    first["note"] = "first edit"
    path.write_text(json.dumps(first), encoding="utf-8")
    original_link = preset_editable.os.link
    injected = False

    def edit_after_publish(source, target, *args, **kwargs):
        nonlocal injected
        result = original_link(source, target, *args, **kwargs)
        if Path(target) == path and not injected:
            injected = True
            second = dict(first)
            second["note"] = "second edit"
            path.write_text(json.dumps(second), encoding="utf-8")
        return result

    monkeypatch.setattr(preset_editable.os, "link", edit_after_publish)

    library.refresh_preset_catalog()

    assert json.loads(path.read_text(encoding="utf-8"))["note"] == "second edit"
    monkeypatch.setattr(preset_editable.os, "link", original_link)
    library.refresh_preset_catalog()
    assert library.preset_get_by_id(saved["id"])["note"] == "second edit"


@pytest.mark.parametrize("mutation", ["rename", "delete"])
def test_mutation_rejects_an_external_edit_after_its_preparation(
        tmp_path, monkeypatch, mutation):
    _configure_catalog(tmp_path, monkeypatch)
    library.chain_set({"slots": []})
    saved = library.preset_save("before", note="original", set_active=False)
    path = library.PRESETS_DIR / f"{saved['id']}-before.json"
    store = library._PRESET_CATALOG._editable
    original_prepare = store._prepare_mutation
    injected = False

    def prepare_then_edit():
        nonlocal injected
        original_prepare()
        if not injected:
            injected = True
            document = json.loads(path.read_text(encoding="utf-8"))
            document["note"] = "external edit"
            path.write_text(json.dumps(document), encoding="utf-8")

    monkeypatch.setattr(store, "_prepare_mutation", prepare_then_edit)

    with pytest.raises(
            library.PresetConflictError, match="changed externally"):
        if mutation == "rename":
            library.preset_rename_by_id(saved["id"], "after")
        else:
            library.preset_delete_by_id(saved["id"])

    current = library.preset_get_by_id(saved["id"])
    assert current["name"] == "before"
    assert current["note"] == "original"
    assert json.loads(path.read_text(encoding="utf-8"))["note"] == \
        "external edit"

    monkeypatch.setattr(store, "_prepare_mutation", original_prepare)
    library.refresh_preset_catalog()
    assert library.preset_get_by_id(saved["id"])["note"] == "external edit"
