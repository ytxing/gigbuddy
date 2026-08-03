"""Unit tests for src/library.py: schema, upsert, queries, chain file, import, CLI.

Network-free: tone3000 access is mocked; DB and chain file point at tmp dirs.
Run: .venv/bin/python -m pytest tests/ -q
"""
import json
import sqlite3
import sys

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


def test_chain_get_set_atomic():
    assert library.chain_get() == {}
    library.chain_set({"master": 0.4, "model": "data/tones/19-01.nam"})
    assert library.chain_get() == {"master": 0.4, "model": "data/tones/19-01.nam"}
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


def test_preset_seed_uses_library_models(tmp_path, monkeypatch):
    _put_models(tmp_path)
    monkeypatch.setattr(
        library, "SEED_CHAINS",
        [("test-chain", "note", 19, 19)])  # tone 19 has amp model 1001 + IR 1002
    assert library.preset_seed() == 1
    p = library.preset_get("test-chain")
    assert p["chain"]["model_id"] == 1001
    assert p["chain"]["ir_model_id"] == 1002
    assert p["chain"]["gain"] == 0.8


def test_preset_seed_skips_missing_tones(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(library, "SEED_CHAINS", [("ghost", "note", 999999, None)])
    assert library.preset_seed() == 0
    assert "跳过" in capsys.readouterr().out
    assert library.preset_get("ghost") is None


def test_cli_preset_roundtrip(tmp_path, capsys):
    _put_models(tmp_path)
    library.chain_set({"model": str(tmp_path / "SR AKG 414.nam")})
    assert library.main(["preset", "save", "cli-rig", "--note", "n"]) == 0
    assert library.main(["preset", "list"]) == 0
    assert "cli-rig" in capsys.readouterr().out
    assert library.main(["preset", "show", "cli-rig"]) == 0
    assert "SR AKG 414.nam" in capsys.readouterr().out
    assert library.main(["preset", "load", "cli-rig"]) == 0
    assert library.main(["preset", "show", "missing"]) == 1
    assert library.main(["preset", "delete", "cli-rig"]) == 0
    assert library.main(["preset", "delete", "cli-rig"]) == 1


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
