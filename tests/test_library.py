"""Unit tests for src/library.py: schema, upsert, queries, chain file, import, CLI.

Network-free: tone3000 access is mocked; DB and chain file point at tmp dirs.
Run: .venv/bin/python -m pytest tests/ -q
"""
import json
import sqlite3
import sys
import urllib.error
from pathlib import Path

import pytest

import library
import tone3000


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point DB + chain file at a tmp dir for every test."""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", tmp_path / "tones")
    yield


SAMPLE = {
    "id": 19, "title": "Fender Super Reverb 1977", "gear": "amp-cab", "platform": "nam",
    "username": "tone3000", "avatar_url": "http://a", "user_id": "u1",
    "description": "vintage clean", "tags": ["tube", "clean"], "makes": ["AKG c414"],
    "images": ["http://i1"],
    "downloads_count": 135824, "favorites_count": 1463,
    "a1_models_count": 3, "a2_models_count": 3, "custom_models_count": 0,
    "models_count": 6, "irs_count": 0, "has_model_with_url": 1,
    "model_name": "EQ Flat", "created_at": "2025-03-14", "updated_at": "2025-07-15",
    "published_at": "2025-03-14",
}


def test_slugify():
    assert tone3000.slugify("Fender Super Reverb 1977") == "fender-super-reverb-1977"
    assert tone3000.slugify("  A  B!! C__D  ") == "a-b-c-d"
    assert tone3000.slugify("") == "tone"
    assert tone3000.slugify("x" * 100) == "x" * 48
    assert tone3000.slugify(None) == "tone"


def test_tone3000_api_retries_a_dropped_tls_connection(monkeypatch):
    attempts = 0

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'[{"id": 1}]'

    def fake_urlopen(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.URLError("SSL: unexpected EOF")
        return Response()

    monkeypatch.setattr(tone3000.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tone3000.time, "sleep", lambda _seconds: None)
    assert tone3000._get("https://example.invalid") == [{"id": 1}]
    assert attempts == 2


def test_tone3000_search_forwards_make_filters(monkeypatch):
    captured = {}
    monkeypatch.setattr(tone3000, "_post",
                        lambda _url, body: captured.update(body) or [])

    assert tone3000.search(
        "two rock", usernames=["coretonecaptures"],
        tag_names=["clean"], make_names=["Two Rock Traditional Clean"]) == []
    assert captured["query_term"] == "two rock"
    assert captured["usernames"] == ["coretonecaptures"]
    assert captured["tag_names"] == ["clean"]
    assert captured["make_names"] == ["Two Rock Traditional Clean"]


def test_tone3000_model_id_lookup_returns_its_parent_tone(monkeypatch):
    monkeypatch.setattr(
        tone3000, "_get",
        lambda _url, **_kwargs: [{"id": 123, "tone_id": 19}],
    )
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tone_id: {"id": tone_id, "title": "Plexi"})

    assert tone3000.tones_for_model_ids([123]) == [
        {"id": 19, "title": "Plexi", "matched_model_ids": [123]}
    ]


def test_download_keeps_original_remote_names(monkeypatch, tmp_path):
    """Download filenames are the exact basenames supplied by TONE3000."""
    calls = {}

    def fake_models(tid, a2_only=True):
        return [
            {"id": 1, "model_url": "http://x/Original%20Name_a2.nam", "model_json": {"metadata": {"name": "Eq Flat, Vol 3!"}}},
            {"id": 2, "model_url": "http://x/9f8e7d.wav", "model_json": None},
        ]

    def fake_urlopen(req, timeout=60):
        import urllib.request as ur
        return ur.urlopen("data:text/plain,hi") if False else _FakeResp()

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"x"

    monkeypatch.setattr(tone3000, "models", fake_models)
    monkeypatch.setattr(tone3000.urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResp())
    got = tone3000.download(99, tmp_path, tag="my-tone-slug", return_paths=True)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["9f8e7d.wav", "Original Name_a2.nam"], names
    assert got[1]["local_path"].endswith("9f8e7d.wav")


def test_download_reports_file_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(tone3000, "models", lambda *a, **kw: [
        {"id": 1, "model_url": "http://x/one.nam", "model_json": {}},
        {"id": 2, "model_url": "http://x/two.nam", "model_json": {}},
    ])

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"x"

    monkeypatch.setattr(tone3000.urllib.request, "urlopen", lambda *a, **kw: _FakeResp())
    events = []
    tone3000.download(99, tmp_path, progress=lambda *args: events.append(args))
    assert events == [
        (0, 2, "one.nam"), (1, 2, "one.nam"),
        (1, 2, "two.nam"), (2, 2, "two.nam"),
    ]


def test_download_prefers_semantic_name_without_duplicate_extension(monkeypatch, tmp_path):
    monkeypatch.setattr(tone3000, "models", lambda *a, **kw: [
        {"id": 1, "model_url": "http://x/encoded-one.nam",
         "name": "JCM 800 P5", "model_json": {}},
        {"id": 2, "model_url": "http://x/encoded-two.wav?download=1",
         "name": "Greenback.wav", "model_json": {}},
    ])

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"x"

    monkeypatch.setattr(tone3000.urllib.request, "urlopen", lambda *a, **kw: _FakeResp())
    tone3000.download(99, tmp_path, ext="wav")
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "Greenback.wav", "JCM 800 P5.wav"
    ]


def test_schema_created():
    with library.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tones", "models"} <= tables


def test_existing_schema_gets_semantic_name_column():
    raw = sqlite3.connect(library.DB_FILE)
    raw.executescript("""
        CREATE TABLE tones (
            id INTEGER PRIMARY KEY,
            title TEXT,
            gear TEXT,
            downloads_count INTEGER
        );
        CREATE TABLE models (
            id INTEGER PRIMARY KEY,
            tone_id INTEGER NOT NULL REFERENCES tones(id),
            model_url TEXT,
            architecture TEXT,
            local_path TEXT
        );
    """)
    raw.close()

    with library.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
        assert "name" in columns
        conn.execute("INSERT INTO tones (id) VALUES (1)")
        library.upsert_model(conn, {
            "id": 1, "tone_id": 1, "model_url": "u",
            "architecture": "IR", "local_path": "x.wav",
        })


def test_connection_enables_integrity_pragmas():
    with library.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        with pytest.raises(sqlite3.IntegrityError):
            library.upsert_model(conn, {
                "id": 999, "tone_id": 404, "model_url": "u",
                "architecture": "IR", "local_path": None,
            })


def test_upsert_idempotent():
    with library.connect() as conn:
        library.upsert_tone(conn, SAMPLE)
        library.upsert_tone(conn, {**SAMPLE, "title": "Renamed"})
        n = conn.execute("SELECT COUNT(*) FROM tones").fetchone()[0]
        title = conn.execute("SELECT title FROM tones WHERE id=19").fetchone()[0]
    assert n == 1
    assert title == "Renamed"  # upsert updated, no duplicate row


def test_json_columns_roundtrip():
    with library.connect() as conn:
        library.upsert_tone(conn, SAMPLE)
        row = library.get_tone(19)
    assert row["tags"] == ["tube", "clean"]
    assert row["makes"] == ["AKG c414"]
    assert row["images"] == ["http://i1"]


def test_list_filters():
    cab = {**SAMPLE, "id": 2, "gear": "cab", "title": "Deluxe Reverb IR", "username": "v"}
    with library.connect() as conn:
        library.upsert_tone(conn, SAMPLE)
        library.upsert_tone(conn, cab)
    assert [t["id"] for t in library.list_tones()] == [2, 19]  # dl desc: both equal, id order
    assert [t["id"] for t in library.list_tones(gear="cab")] == [2]
    assert [t["id"] for t in library.list_tones(query="super")] == [19]
    assert [t["id"] for t in library.list_tones(gear="amp")] == []
    assert [t["id"] for t in library.list_tones(limit=1, offset=1)] == [19]


def test_list_filters_by_model_id():
    with library.connect() as conn:
        library.upsert_tone(conn, SAMPLE)
        library.upsert_model(conn, {
            "id": 123, "tone_id": 19, "model_url": "u", "name": "Plexi.nam",
            "architecture": "SlimmableContainer", "local_path": "Plexi.nam",
        })
    assert [t["id"] for t in library.list_tones(model_ids=[123])] == [19]
    assert library.list_tones(model_ids=[999]) == []


def test_list_filters_accept_multi_value_search_spec():
    other = {
        **SAMPLE,
        "id": 2,
        "title": "Plexi Clean",
        "username": "amalgamaudio",
        "tags": ["plexi", "clean"],
        "makes": ["Marshall Plexi"],
    }
    with library.connect() as conn:
        library.upsert_tone(conn, SAMPLE)
        library.upsert_tone(conn, other)

    assert {t["id"] for t in library.list_tones(
        authors=["tone3000", "amalgamaudio"])} == {2, 19}
    assert {t["id"] for t in library.list_tones(
        tags=["clean", "plexi"])} == {2, 19}
    assert [t["id"] for t in library.list_tones(
        makes=["Marshall Plexi"])] == [2]


def test_models_and_local_listing():
    with library.connect() as conn:
        library.upsert_tone(conn, SAMPLE)
        library.upsert_model(conn, {"id": 51, "tone_id": 19, "model_url": "u1",
                                    "architecture": "SlimmableContainer",
                                    "local_path": "data/tones/19-01.nam"})
        library.upsert_model(conn, {"id": 52, "tone_id": 19, "model_url": "u2",
                                    "architecture": "IR", "local_path": "data/tones/19-01.wav"})
    t = library.get_tone(19)
    assert len(t["models"]) == 2
    assert [m["id"] for m in library.list_local_models("amp")] == [51]
    assert [m["id"] for m in library.list_local_models("ir")] == [52]
    assert library.list_local_models("amp")[0]["title"] == SAMPLE["title"]
    # upsert idempotent on model id
    with library.connect() as conn:
        library.upsert_model(conn, {"id": 51, "tone_id": 19, "model_url": "u1",
                                    "architecture": "SlimmableContainer", "local_path": None})
        assert conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 2


def test_local_uninstall_blocks_active_chain_and_preserves_metadata(tmp_path):
    (tmp_path / "tones").mkdir()
    amp, ir = _put_models(tmp_path / "tones")
    library.chain_set({"model": amp["local_path"], "ir": ir["local_path"]})
    library.preset_save("dependent")

    plan = library.local_uninstall_plan([19])
    assert {m["id"] for m in plan["models"]} == {1001, 1002}
    assert plan["active_paths"] == sorted([amp["local_path"], ir["local_path"]])
    assert plan["preset_names"] == ["dependent"]
    with pytest.raises(ValueError, match="active chain"):
        library.local_uninstall_tones([19])

    library.chain_set({})
    with pytest.raises(ValueError, match="referenced by presets"):
        library.local_uninstall_tones([19])
    result = library.local_uninstall_tones([19], allow_preset_references=True)
    assert result["removed"] == 2
    assert (library.get_tone(19) or {})["title"] == SAMPLE["title"]
    assert all(model["local_path"] is None for model in library.get_tone(19)["models"])
    assert library.preset_get("dependent") is not None
    trash = Path(result["trash_dir"])
    assert (trash / "manifest.json").is_file()
    assert sorted(p.name for p in trash.iterdir() if p.name != "manifest.json") == [
        "1001-SR AKG 414.nam", "1002-DR Oxford Big.wav",
    ]


def test_local_uninstall_refuses_unmanaged_paths(tmp_path):
    (tmp_path / "external").mkdir()
    amp, _ir = _put_models(tmp_path / "external")
    library.chain_set({})
    plan = library.local_uninstall_plan([19])
    assert amp["local_path"] in plan["outside_paths"]
    with pytest.raises(ValueError, match="outside"):
        library.local_uninstall_tones([19])


def test_local_uninstall_reports_missing_files_separately(tmp_path):
    (tmp_path / "tones").mkdir()
    amp, ir = _put_models(tmp_path / "tones")
    Path(ir["local_path"]).unlink()
    library.chain_set({})

    result = library.local_uninstall_tones([19])

    assert result["removed"] == 1
    assert result["missing"] == 1
    manifest = json.loads(
        (Path(result["trash_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert [item["model_id"] for item in manifest["files"]] == [amp["id"]]
    assert [item["model_id"] for item in manifest["missing"]] == [ir["id"]]
    assert all(model["local_path"] is None for model in library.get_tone(19)["models"])


def test_local_uninstall_models_uninstalls_subset(tmp_path):
    """REQ-038：模型粒度卸载——只卸选中的模型，tone 其余模型保留；
    全部卸空时 local_dir 一并清空。"""
    (tmp_path / "tones").mkdir()
    amp, ir = _put_models(tmp_path / "tones")
    library.chain_set({})

    plan = library.local_uninstall_models_plan([amp["id"]])
    assert {m["id"] for m in plan["models"]} == {1001}
    assert plan["tone_ids"] == [19]

    result = library.local_uninstall_models([amp["id"]])
    assert result["removed"] == 1
    models = library.get_tone(19)["models"]
    assert {m["id"]: m["local_path"] for m in models} == {
        1001: None, 1002: ir["local_path"]}

    # 剩余模型不受影响，仍可再卸；卸空后 tone.local_dir 清空
    result = library.local_uninstall_models([ir["id"]])
    assert result["removed"] == 1
    assert all(m["local_path"] is None for m in library.get_tone(19)["models"])
    with library.connect() as conn:
        row = conn.execute("SELECT local_dir FROM tones WHERE id = 19").fetchone()
    assert row["local_dir"] is None

    # 空选择/未知 id 安全返回
    assert library.local_uninstall_models([])["removed"] == 0
    assert library.local_uninstall_models([99999])["removed"] == 0


def test_chain_get_set_atomic():
    assert library.chain_get() == {}
    library.chain_set({"master": 0.4, "model": "data/tones/19-01.nam"})
    # REQ-035 portable：读返回绝对路径（相对根解析）
    cfg = library.chain_get()
    assert cfg["master"] == 0.4
    assert cfg["model"] == str(library.ROOT / "data/tones/19-01.nam")
    assert not library.CHAIN_FILE.with_suffix(".json.tmp").exists()  # no leftover tmp


def test_import_tone_mocked(monkeypatch, capsys):
    downloaded = [
        {"id": 51, "tone_id": 19, "model_url": "u1", "model_json": {"architecture": "SlimmableContainer"},
         "local_path": "data/tones/19-01.nam"},
        {"id": 52, "tone_id": 19, "model_url": "u2", "model_json": None,
         "local_path": "data/tones/19-01.wav"},
    ]
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))
    monkeypatch.setattr(tone3000, "download",
                        lambda tid, dest, **kw: list(downloaded) if kw.get("return_paths") else len(downloaded))
    t = library.import_tone(19)
    assert t["local_dir"] is not None
    arch = {m["id"]: m["architecture"] for m in t["models"]}
    assert arch == {51: "SlimmableContainer", 52: "IR"}
    # idempotent: second import keeps one tone row
    library.import_tone(19)
    assert len(library.list_tones()) == 1


def test_import_persists_tone_and_models_as_one_transaction(monkeypatch):
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))
    monkeypatch.setattr(tone3000, "download", lambda *a, **kw: [
        {"id": 51, "tone_id": 19, "model_url": "u1",
         "model_json": {"architecture": "SlimmableContainer"},
         "local_path": "data/tones/19/one.nam"},
        {"id": 52, "tone_id": 19, "model_url": "u2",
         "model_json": {"architecture": "SlimmableContainer"},
         "local_path": "data/tones/19/two.nam"},
    ])
    real_upsert_model = library.upsert_model
    calls = 0

    def fail_on_second(conn, model, *, commit=True):
        nonlocal calls
        calls += 1
        real_upsert_model(conn, model, commit=commit)
        if calls == 2:
            raise RuntimeError("simulated model write failure")

    monkeypatch.setattr(library, "upsert_model", fail_on_second)
    with pytest.raises(RuntimeError, match="simulated"):
        library.import_tone(19, quiet=True)
    assert library.get_tone(19) is None


def test_import_ir_downloads_wav(monkeypatch):
    ir_row = {**SAMPLE, "gear": "cab"}
    calls = {}
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(ir_row))
    monkeypatch.setattr(tone3000, "download",
                        lambda tid, dest, **kw: calls.update(kw) or [])
    library.import_tone(19)
    assert calls["a2_only"] is False
    assert calls["ext"] == "wav"


def test_cli_roundtrip(capsys, monkeypatch):
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))
    monkeypatch.setattr(tone3000, "download", lambda *a, **kw: [])
    assert library.main(["tone", "list"]) == 0
    assert "No imported tones" in capsys.readouterr().out
    library.main(["tone", "import", "19"])
    assert "Fender Super Reverb" in capsys.readouterr().out
    library.main(["tone", "show", "19"])
    assert "Fender Super Reverb" in capsys.readouterr().out
    library.main(["tone", "list", "--gear", "cab"])
    assert "No imported tones" in capsys.readouterr().out
    library.main(["chain", "set", '{"master": 0.4}'])
    library.main(["chain", "get"])
    assert '"master": 0.4' in capsys.readouterr().out
    assert library.main(["chain", "set", "not json"]) == 1
    assert library.main(["chain", "set", "[1,2]"]) == 1
    assert library.main(["tone", "show", "99999"]) == 1


def test_cli_search_json(capsys, monkeypatch):
    monkeypatch.setattr(tone3000, "search", lambda q, **kw: [dict(SAMPLE)])
    library.main(["tone", "search", "fender", "--json"])
    out = capsys.readouterr().out
    assert json.loads(out)[0]["id"] == 19


# ---- presets --------------------------------------------------------------

def _put_models(tmp_path):
    """Two library models (amp .nam + IR .wav) with real files on disk."""
    with library.connect() as conn:  # tone row first (FK enabled)
        library.upsert_tone(conn, dict(SAMPLE))
    amp = {"id": 1001, "tone_id": 19, "model_url": "u1", "name": "SR AKG 414",
           "architecture": "SlimmableContainer", "local_path": str(tmp_path / "SR AKG 414.nam")}
    ir = {"id": 1002, "tone_id": 19, "model_url": "u2", "name": "DR Oxford Big",
          "architecture": "IR", "local_path": str(tmp_path / "DR Oxford Big.wav")}
    (tmp_path / "SR AKG 414.nam").write_bytes(b"amp")
    (tmp_path / "DR Oxford Big.wav").write_bytes(b"ir")
    with library.connect() as conn:
        library.upsert_model(conn, amp)
        library.upsert_model(conn, ir)
    return amp, ir


def test_preset_save_load_roundtrip(tmp_path):
    amp, ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"], "ir": ir["local_path"],
                       "gain": 0.8, "master": 0.65})
    p = library.preset_save("clean-rig", note="测试")
    assert p["name"] == "clean-rig"
    assert p["note"] == "测试"
    # paths resolved back to logic references
    assert p["chain"]["model_id"] == 1001
    assert p["chain"]["ir_model_id"] == 1002
    # load resolves ids to paths and writes the live chain
    cfg = library.preset_load("clean-rig")
    assert cfg["model"] == amp["local_path"]
    assert cfg["ir"] == ir["local_path"]
    assert cfg["gain"] == 0.8 and cfg["master"] == 0.65
    assert library.chain_get()["model"] == amp["local_path"]


def test_preset_active_dirty_and_quality_roundtrip(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"], "gain": 0.8,
                       "master": 0.65, "quality": 0.7})
    library.preset_save("working")
    assert library.preset_current() == "working"
    assert library.preset_is_dirty() is False
    assert library.preset_get("working")["chain"]["quality"] == 0.7

    library.chain_set({"model": amp["local_path"], "gain": 0.9,
                       "master": 0.65, "quality": 0.7})
    assert library.preset_is_dirty() is True
    loaded = library.preset_load("working")
    assert loaded["quality"] == 0.7
    assert library.preset_is_dirty() is False


def test_preset_resolved_chain_follows_model_path_migration(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    library.preset_save("moving")

    moved = tmp_path / "renamed.nam"
    moved.write_bytes(b"amp")
    with library.connect() as conn:
        conn.execute("UPDATE models SET local_path = ? WHERE id = ?", (str(moved), amp["id"]))
        conn.commit()

    assert library.preset_resolved_chain("moving")["model"] == str(moved)


def test_preset_manage_keeps_active_pointer_consistent(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    library.preset_save("old", note="keep me")

    renamed = library.preset_rename("old", "new")
    assert renamed["name"] == "new"
    assert library.preset_current() == "new"
    assert library.preset_get("old") is None

    updated = library.preset_update_note("new", "changed")
    assert updated["note"] == "changed"
    library.preset_save("new")
    assert library.preset_get("new")["note"] == "changed"

    assert library.preset_delete("new") is True
    assert library.preset_current() is None

    with pytest.raises(ValueError, match="cannot be empty"):
        library.preset_save("   ")


def test_preset_save_external_path_kept_verbatim(tmp_path):
    """Non-library files are stored as plain paths (no model_id)."""
    ext = tmp_path / "external.nam"
    ext.write_bytes(b"x")
    library.chain_set({"model": str(ext), "gain": 1.0})
    p = library.preset_save("external")
    assert p["chain"]["model_id"] is None
    assert p["chain"]["model_path"] == str(ext)
    assert library.preset_load("external")["model"] == str(ext)


def test_preset_load_missing_file_raises(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    library.preset_save("rig")
    p = library.preset_get("rig")
    p["chain"]["model_id"] = 999999999  # unresolved id
    with library.connect() as conn:
        conn.execute("UPDATE presets SET chain_json=? WHERE name=?",
                     (json.dumps(p["chain"]), "rig"))
        conn.commit()
    with pytest.raises(ValueError, match="model file missing"):
        library.preset_load("rig")


def test_preset_list_and_delete(tmp_path):
    _put_models(tmp_path)
    library.chain_set({"model": str(tmp_path / "SR AKG 414.nam")})
    library.preset_save("a")
    library.preset_save("b")
    names = {p["name"] for p in library.preset_list()}
    assert names == {"a", "b"}
    assert library.preset_delete("a") is True
    assert library.preset_delete("a") is False  # already gone
    assert library.preset_get("a") is None


def test_preset_group_is_derived_from_name_only():
    assert library.preset_group("band-guitar-rhcp") == ("Band Gear", "Guitar")
    assert library.preset_group("classic-bass-ampeg-svt") == ("Classic Pairing", "Bass")
    assert library.preset_group("my-tone") == ("Custom", "Other")


def test_preset_seed_uses_library_models(tmp_path, monkeypatch):
    _put_models(tmp_path)
    monkeypatch.setattr(
        library, "SEED_CHAINS",
        [("test-chain", "note", 1001, 1002)])
    assert library.preset_seed() == 1
    p = library.preset_get("test-chain")
    assert p["chain"]["model_id"] == 1001
    assert p["chain"]["ir_model_id"] == 1002
    assert p["chain"]["gain"] == 0.8


def test_preset_seed_skips_missing_tones(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        library, "SEED_CHAINS",
        [("ghost", "note", 999999, None)])
    assert library.preset_seed() == 0
    assert "skipped" in capsys.readouterr().out
    assert library.preset_get("ghost") is None


def test_preset_seed_replace_deletes_existing_presets(tmp_path, monkeypatch):
    _put_models(tmp_path)
    library.chain_set({"model": str(tmp_path / "SR AKG 414.nam")})
    library.preset_save("old")
    monkeypatch.setattr(
        library, "SEED_CHAINS",
        [("new", "note", 1001, None)])

    assert library.preset_seed(replace=True) == 1
    assert library.preset_get("old") is None
    assert library.preset_current() is None
    assert [p["name"] for p in library.preset_list()] == ["new"]


def test_cli_preset_roundtrip(tmp_path, capsys):
    _put_models(tmp_path)
    library.chain_set({"model": str(tmp_path / "SR AKG 414.nam")})
    assert library.main(["preset", "save", "cli-rig", "--note", "n"]) == 0
    assert library.main(["preset", "list"]) == 0
    assert "cli-rig" in capsys.readouterr().out
    assert library.main(["preset", "show", "cli-rig"]) == 0
    assert "SR AKG 414.nam" in capsys.readouterr().out
    assert library.main(["preset", "current"]) == 0
    assert "cli-rig" in capsys.readouterr().out
    assert library.main(["preset", "rename", "cli-rig", "cli-new"]) == 0
    assert library.preset_current() == "cli-new"
    assert library.main(["preset", "note", "cli-new", "stage tone"]) == 0
    assert library.preset_get("cli-new")["note"] == "stage tone"
    assert library.main(["preset", "load", "cli-new"]) == 0
    assert library.main(["preset", "show", "missing"]) == 1
    assert library.main(["preset", "delete", "cli-new"]) == 0
    assert library.main(["preset", "delete", "cli-new"]) == 1


def test_mark_download_state(monkeypatch, tmp_path):
    """Search hits are tagged all/partial/none by comparing model ids."""
    _put_models(tmp_path)  # tone 19: local models 1001 (amp) + 1002 (IR)
    # tone 123: one locally downloaded IR of two
    with library.connect() as conn:
        library.upsert_tone(conn, {**SAMPLE, "id": 123, "gear": "cab"})
        library.upsert_model(conn, {"id": 9002, "tone_id": 123, "name": "IR1",
                                    "model_url": "u9002", "architecture": "IR",
                                    "local_path": str(tmp_path / "IR1.wav")},
                             commit=False)
        conn.commit()
    (tmp_path / "IR1.wav").write_bytes(b"x")
    hits = [
        {"id": 19, "gear": "amp", "models_count": 6},   # local + remote A2 ids
        {"id": 999, "gear": "amp", "models_count": 3},  # nothing local
        {"id": 123, "gear": "cab", "models_count": 2},  # local, partial IR
    ]
    remote_models = {
        19: [{"id": 1001, "architecture": "SlimmableContainer"},   # all A2 local
             {"id": 9000, "architecture": "WaveNet"}],             # A1, not compared
        123: [{"id": 9002, "architecture": "IR"},                  # have it
              {"id": 9001, "architecture": "IR"}],                 # missing
    }
    monkeypatch.setattr(
        "tone3000.models",
        lambda tid, a2_only=False: remote_models.get(tid, []))

    out = library.mark_download_state(hits)
    by_id = {t["id"]: t for t in out}
    assert by_id[19]["download_state"] == "all"   # A2 all downloaded (A1 ignored)
    assert by_id[19]["downloaded"] == 2
    assert by_id[999]["download_state"] == "none"  # no local models, no API call
    assert by_id[123]["download_state"] == "partial"  # 1 of 2 IRs
    assert by_id[123]["downloaded"] == 1


def test_top_favorites_attaches_usernames(monkeypatch):
    """REQ-023: favorites 排行端点按 user_id 批量联查 users 补 username，
    表格不再显示 @?。"""
    import tone3000

    def fake_get(url, **params):
        if "tones_counts" in url:
            return [{"id": 1, "title": "T1", "user_id": "u1"},
                    {"id": 2, "title": "T2", "user_id": "u2"}]
        if "users" in url:
            assert "in.(" in params.get("id", ""), "应使用批量 in 过滤"
            return [{"id": "u1", "username": "alice", "avatar_url": "a"},
                    {"id": "u2", "username": "bob", "avatar_url": "b"}]
        return []

    monkeypatch.setattr("tone3000._get", fake_get)
    rows = tone3000.top_favorites(2)
    assert [r["username"] for r in rows] == ["alice", "bob"]
    assert rows[0]["avatar_url"] == "a"


def test_backfill_tone_usernames(monkeypatch, tmp_path):
    """REQ-023: 历史占位 username（'tone3000'/空）回填真实作者名。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO tones (id, title, username) VALUES (1, 'T1', 'tone3000')")
        conn.execute(
            "INSERT INTO tones (id, title, username) VALUES (2, 'T2', NULL)")
        conn.execute(
            "INSERT INTO tones (id, title, username) VALUES (3, 'T3', 'real')")
        conn.commit()
    monkeypatch.setattr(
        "library.tone3000.tone_by_id",
        lambda tid: {"id": tid, "username": f"author{tid}",
                     "avatar_url": f"a{tid}", "user_id": f"u{tid}"})
    assert library.backfill_tone_usernames() == 2   # 只回填 1、2
    with library.connect() as conn:
        assert conn.execute(
            "SELECT username FROM tones WHERE id=1").fetchone()[0] == "author1"
        assert conn.execute(
            "SELECT username FROM tones WHERE id=2").fetchone()[0] == "author2"
        assert conn.execute(
            "SELECT username FROM tones WHERE id=3").fetchone()[0] == "real"


def test_paths_stored_relative_read_absolute_and_move_dir(monkeypatch, tmp_path):
    """REQ-035 portable: local_path 存相对项目根、读取还原绝对；
    模拟移动目录后相对路径解析到新根。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "ROOT", tmp_path)
    model = tmp_path / "data" / "tones" / "1-x" / "A.nam"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"x")
    with library.connect() as conn:
        library.upsert_tone(conn, {"id": 1, "title": "T", "username": "u"})
        library.upsert_model(conn, {"id": 1, "tone_id": 1,
                                    "model_url": "http://x/A.nam",
                                    "architecture": "SlimmableContainer",
                                    "local_path": str(model)})
    with library.connect() as conn:
        stored = conn.execute(
            "SELECT local_path FROM models WHERE id=1").fetchone()[0]
    assert stored == "data/tones/1-x/A.nam", f"DB 应存相对路径: {stored}"
    assert library.get_tone(1)["models"][0]["local_path"] == str(model)
    # 模拟移动目录：ROOT 换成新位置（文件拷贝过去）→ 相对路径仍解析
    new_root = tmp_path / "moved"
    (new_root / "data" / "tones" / "1-x").mkdir(parents=True)
    (new_root / "data" / "tones" / "1-x" / "A.nam").write_bytes(b"x")
    monkeypatch.setattr(library, "ROOT", new_root)
    assert library.get_tone(1)["models"][0]["local_path"] == str(
        new_root / "data" / "tones" / "1-x" / "A.nam")


def test_old_absolute_path_rebased_to_new_root(monkeypatch, tmp_path):
    """REQ-035 旧数据兼容：根外绝对路径（旧机器）按 data/tones/ 段重基。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    new_root = tmp_path / "moved"
    target = new_root / "data" / "tones" / "2-y" / "B.nam"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    monkeypatch.setattr(library, "ROOT", new_root)
    old_abs = "/Users/someone/old/path/data/tones/2-y/B.nam"
    with library.connect() as conn:
        library.upsert_tone(conn, {"id": 2, "title": "T2", "username": "u"})
        library.upsert_model(conn, {"id": 2, "tone_id": 2,
                                    "model_url": "http://x/B.nam",
                                    "architecture": "SlimmableContainer",
                                    "local_path": old_abs})
    assert library.get_tone(2)["models"][0]["local_path"] == str(target)


def test_chain_paths_relative_on_write_absolute_on_read(monkeypatch, tmp_path):
    """REQ-035: chain 写入转相对、读取还原绝对。"""
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(library, "ROOT", tmp_path)
    p = tmp_path / "data" / "tones" / "1-x" / "A.nam"
    library.chain_set({"model": str(p), "gain": 1.0})
    raw = json.loads((tmp_path / "live_chain.json").read_text())
    assert raw["model"] == "data/tones/1-x/A.nam"
    assert library.chain_get()["model"] == str(p)


def test_old_absolute_local_path_rows_still_resolve(monkeypatch, tmp_path):
    """REQ-041：REQ-035 之前的旧库行 local_path 存绝对路径——路径反查必须
    两种存储格式都命中，否则链节点点击找不到模型 → detail 清成空态。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "ROOT", tmp_path)
    tones = tmp_path / "data" / "tones"
    abs_path = str(tones / "10-jcm800" / "MV5 G1.nam")
    with library.connect() as conn:
        library.upsert_tone(conn, {"id": 10, "title": "JCM800",
                                   "gear": "amp", "username": "a"})
        library.upsert_model(conn, {"id": 1, "tone_id": 10,
                                    "model_url": "u", "name": "MV5 G1",
                                    "architecture": "SlimmableContainer",
                                    "local_path": abs_path})
        # upsert_model 已转相对（REQ-035）；改回绝对模拟旧行
        conn.execute("UPDATE models SET local_path = ? WHERE id = 1",
                     (abs_path,))
        conn.commit()

    assert library.local_models_by_tone(abs_path) is not None
    assert library.tone_title_for_path(abs_path) == "JCM800"
    assert library._model_id_for_path(abs_path) == 1

    # 新格式（相对存储）同样命中——两种格式并存不冲突（不同路径避免
    # 同一路径双行匹配歧义）
    rel_path = str(Path(abs_path).with_name("MV5 G2.nam").relative_to(tmp_path))
    with library.connect() as conn:
        library.upsert_model(conn, {"id": 2, "tone_id": 10,
                                    "model_url": "u2", "name": "MV5 G2",
                                    "architecture": "SlimmableContainer",
                                    "local_path": rel_path})
    assert library.tone_title_for_path(str(tmp_path / rel_path)) == "JCM800"
    assert library._model_id_for_path(str(tmp_path / rel_path)) == 2


def test_uninstall_plan_matches_active_chain_for_relative_rows(monkeypatch, tmp_path):
    """REQ-041 伴随：新格式（相对存储）行的卸载 plan 必须识别活动链占用
    （此前 paths 未绝对化，相对行对不上链上的绝对路径 → 拦截漏判）。"""
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(library, "ROOT", tmp_path)
    f = tmp_path / "data" / "tones" / "10-x" / "A.nam"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"amp")
    with library.connect() as conn:
        library.upsert_tone(conn, {"id": 10, "title": "T", "gear": "amp",
                                   "username": "a"})
        library.upsert_model(conn, {"id": 1, "tone_id": 10,
                                    "model_url": "u", "name": "A.nam",
                                    "architecture": "SlimmableContainer",
                                    "local_path": str(f)})
    library.chain_set({"model": str(f), "gain": 1.0})
    plan = library.local_uninstall_models_plan([1])
    assert plan["active_paths"] == [str(f)]
    with pytest.raises(ValueError, match="active chain"):
        library.local_uninstall_models([1])
