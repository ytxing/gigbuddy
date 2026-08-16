"""Unit tests for src/library.py: schema, upsert, queries, chain file, import, CLI.

Network-free: tone3000 access is mocked; DB and chain file point at tmp dirs.
Run: .venv/bin/python -m pytest tests/ -q
"""
import json
import sqlite3
import sys
import tomllib
import urllib.error
from pathlib import Path

import pytest

import library
import tone3000


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point DB + chain file at a tmp dir for every test."""
    monkeypatch.setattr(library, "ROOT", tmp_path)
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(library, "TONES_DIR", tmp_path / "data" / "tones")
    monkeypatch.setattr(library, "PRESETS_DIR", tmp_path / "data" / "presets")
    (tmp_path / "data" / "tones").mkdir(parents=True)
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
    assert captured["architecture_filter"] == "2"


def test_tone3000_model_id_lookup_returns_its_parent_tone(monkeypatch):
    monkeypatch.setattr(
        tone3000, "_get",
        lambda _url, **_kwargs: [{"id": 123, "tone_id": 19,
                                  "architecture_version": "2"}],
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
            {"id": 1, "model_url": "http://x/Original%20Name_a2.nam",
             "architecture_version": "2",
             "model_json": {"metadata": {"name": "Eq Flat, Vol 3!"}}},
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
    got = tone3000.download(
        99, tmp_path, tag="my-tone-slug", a2_only=False, return_paths=True)
    names = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert names == ["9f8e7d.wav", "Original Name_a2.nam"], names
    assert got[1]["local_path"].endswith("9f8e7d.wav")


def test_download_reports_file_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(tone3000, "models", lambda *a, **kw: [
        {"id": 1, "model_url": "http://x/one.nam",
         "architecture_version": "2", "model_json": {}},
        {"id": 2, "model_url": "http://x/two.nam",
         "architecture_version": "2", "model_json": {}},
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
         "architecture_version": "2",
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
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == [
        "JCM 800 P5.nam"
    ]


def test_download_filters_extra_rows_from_a2_only_call(monkeypatch, tmp_path):
    monkeypatch.setattr(tone3000, "models", lambda *a, **kw: [
        {"id": 1, "model_url": "http://x/one.nam",
         "architecture_version": "2", "name": "one"},
        {"id": 2, "model_url": "http://x/two.wav",
         "architecture": "IR", "name": "two.wav"},
    ])

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"x"

    monkeypatch.setattr(tone3000.urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResp())

    records = tone3000.download(
        99, tmp_path, a2_only=True, ext="wav",
        return_paths=True, quiet=True)

    assert [record["id"] for record in records] == [1]
    assert (tmp_path / "one.nam").is_file()
    assert not (tmp_path / "two.wav").exists()


def test_download_uses_model_extension_inside_mixed_pack(monkeypatch, tmp_path):
    monkeypatch.setattr(tone3000, "models", lambda *a, **kw: [
        {"id": 1, "model_url": "http://x/amp",
         "architecture_version": "2", "name": "amp"},
        {"id": 2, "model_url": "http://x/cab",
         "architecture": "IR", "name": "cab"},
    ])

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"x"

    monkeypatch.setattr(tone3000.urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResp())

    records = tone3000.download(
        99, tmp_path, a2_only=False, ext="wav",
        return_paths=True, quiet=True)

    assert [Path(record["local_path"]).name for record in records] == [
        "amp.nam", "cab.wav"
    ]


def test_download_sanitizes_remote_names_to_the_destination_directory(
        monkeypatch, tmp_path):
    monkeypatch.setattr(tone3000, "models", lambda *a, **kw: [
        {"id": 1, "model_url": "http://x/one.nam",
         "architecture_version": "2",
         "name": "../../escape.nam", "model_json": {}},
        {"id": 2, "model_url": "http://x/two.wav",
         "name": r"nested\evil.wav", "model_json": {}},
    ])

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"x"

    monkeypatch.setattr(tone3000.urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResp())
    monkeypatch.setattr(tone3000.time, "sleep", lambda _seconds: None)
    tone3000.download(
        99, tmp_path, a2_only=False, return_paths=True, quiet=True)

    assert sorted(path.name for path in tmp_path.iterdir() if path.is_file()) == [
        "escape.nam", "evil.wav"
    ]
    assert not (tmp_path.parent / "escape.nam").exists()


def test_download_reuses_only_a_verified_existing_file(monkeypatch, tmp_path):
    payload = b"verified cached model"
    path = tmp_path / "cached.nam"
    path.write_bytes(payload)
    model = {"id": 1, "model_url": "http://x/cached.nam",
             "name": "cached.nam", "architecture_version": "2",
             "model_json": {}}
    monkeypatch.setattr(tone3000, "models", lambda *a, **kw: [model])
    calls = []

    def fail_urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("verified cache should not download")

    monkeypatch.setattr(tone3000.urllib.request, "urlopen", fail_urlopen)
    records = tone3000.download(
        99, tmp_path, return_paths=True, quiet=True,
        existing_records=[{
            "id": 1, "model_url": model["model_url"],
            "name": model["name"], "local_path": str(path),
            "local_size": len(payload),
            "local_sha256": tone3000._sha256_file(path),
        }],
    )

    assert calls == []
    assert path.read_bytes() == payload
    assert records[0]["local_size"] == len(payload)
    assert records[0]["local_sha256"] == tone3000._sha256_file(path)


def test_download_redownloads_when_cached_hash_is_stale(monkeypatch, tmp_path):
    path = tmp_path / "cached.nam"
    path.write_bytes(b"old")
    model = {"id": 1, "model_url": "http://x/cached.nam",
             "name": "cached.nam", "architecture_version": "2",
             "model_json": {}}
    monkeypatch.setattr(tone3000, "models", lambda *a, **kw: [model])

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"new"

    monkeypatch.setattr(tone3000.urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResp())
    monkeypatch.setattr(tone3000.time, "sleep", lambda _seconds: None)
    tone3000.download(
        99, tmp_path, return_paths=True, quiet=True,
        existing_records=[{
            "id": 1, "model_url": model["model_url"],
            "name": model["name"], "local_path": str(path),
            "local_size": 3, "local_sha256": "not-the-file",
        }],
    )

    assert path.read_bytes() == b"new"


def test_schema_created():
    with library.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tones", "models"} <= tables


def test_connection_context_closes_connection():
    with library.connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


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
        assert "architecture_version" in columns
        tone_columns = {row[1] for row in conn.execute("PRAGMA table_info(tones)")}
        assert "format" in tone_columns
        conn.execute("INSERT INTO tones (id) VALUES (1)")
        library.upsert_model(conn, {
            "id": 1, "tone_id": 1, "model_url": "u",
            "architecture": "IR", "local_path": "x.wav",
        })


def test_schema_reinitializes_after_the_database_is_recreated():
    with library.connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='presets'").fetchone()
    library.DB_FILE.unlink()

    with library.connect() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tones", "models", "presets", "settings"} <= tables


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
        conn.execute("UPDATE tones SET imported_at = CASE id "
                     "WHEN 19 THEN '2026-01-01T00:00:00+00:00' "
                     "WHEN 2 THEN '2026-02-01T00:00:00+00:00' END")
        conn.commit()
    assert [t["id"] for t in library.list_tones()] == [2, 19]  # dl desc: both equal, id order
    assert [t["id"] for t in library.list_tones(sort_by="title")] == [2, 19]
    assert [t["id"] for t in library.list_tones(sort_by="added-desc")] == [2, 19]
    assert [t["id"] for t in library.list_tones(sort_by="added-asc")] == [19, 2]
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
    amp_path = library.ROOT / "data" / "tones" / "19-01.nam"
    ir_path = library.ROOT / "data" / "tones" / "19-01.wav"
    custom_path = library.ROOT / "data" / "tones" / "19-custom.nam"
    amp_path.parent.mkdir(parents=True, exist_ok=True)
    amp_path.write_bytes(b"amp")
    ir_path.write_bytes(b"ir")
    custom_path.write_bytes(b"custom")
    with library.connect() as conn:
        library.upsert_tone(conn, SAMPLE)
        library.upsert_model(conn, {"id": 51, "tone_id": 19, "model_url": "u1",
                                    "architecture": "SlimmableContainer",
                                    "local_path": "data/tones/19-01.nam"})
        library.upsert_model(conn, {"id": 52, "tone_id": 19, "model_url": "u2",
                                    "architecture": "IR", "local_path": "data/tones/19-01.wav"})
        library.upsert_model(conn, {"id": 53, "tone_id": 19, "model_url": "u3",
                                    "architecture": "custom", "local_path": "data/tones/19-custom.nam"})
    t = library.get_tone(19)
    assert len(t["models"]) == 2
    assert [m["id"] for m in library.list_local_models("amp")] == [51]
    assert [m["id"] for m in library.list_local_models("ir")] == [52]
    assert library.list_local_models("amp")[0]["title"] == SAMPLE["title"]
    # upsert idempotent on model id
    with library.connect() as conn:
        library.upsert_model(conn, {"id": 51, "tone_id": 19, "model_url": "u1",
                                    "architecture": "SlimmableContainer", "local_path": None})
        assert conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 3


def test_local_tone_list_excludes_custom_only_files():
    custom_tone = {**SAMPLE, "id": 88, "title": "Custom Only",
                   "a1_models_count": 0, "a2_models_count": 1,
                   "custom_models_count": 1, "models_count": 1}
    with library.connect() as conn:
        library.upsert_tone(conn, custom_tone)
        library.upsert_model(conn, {
            "id": 8801, "tone_id": 88, "model_url": "u-custom",
            "name": "custom.nam",
            "architecture": "custom", "local_path": "data/tones/custom.nam",
        })

    assert library.get_tone(88)["models"] == []
    assert library.list_tones() == []
    assert library.list_tones(has_files=True) == []
    assert library.downloaded_model_ids_by_tone() == {}


def test_unsupported_model_paths_are_not_resolved_into_chain_metadata(tmp_path):
    tone = {**SAMPLE, "id": 89, "a1_models_count": 0,
            "a2_models_count": 1, "models_count": 1}
    path = library.TONES_DIR / "custom-only.nam"
    path.write_bytes(b"custom")
    with library.connect() as conn:
        library.upsert_tone(conn, tone)
        library.upsert_model(conn, {
            "id": 8901, "tone_id": 89, "model_url": "u",
            "name": "custom-only.nam", "architecture": "custom",
            "local_path": str(path),
        })

    assert library._model_id_for_path(str(path)) is None
    assert library._model_path(8901) is None
    assert library.tone_title_for_path(str(path)) is None
    assert library.local_models_by_tone(str(path)) is None


def test_local_listing_infers_architectureless_ir_and_keeps_a1_amp():
    """Picker/list tables must not lose old IR rows with a null architecture."""
    cab = {**SAMPLE, "id": 20, "gear": "cab", "platform": "ir",
           "title": "Blue Cabinet"}
    amp = {**SAMPLE, "id": 21, "gear": "amp", "platform": "nam",
           "title": "Legacy Amp"}
    (library.ROOT / "data" / "tones" / "20-blue").mkdir(parents=True)
    (library.ROOT / "data" / "tones" / "20-blue" / "Blue 1.wav").write_bytes(b"ir")
    (library.ROOT / "data" / "tones" / "21-legacy").mkdir(parents=True)
    (library.ROOT / "data" / "tones" / "21-legacy" / "Legacy.nam").write_bytes(b"a1")
    with library.connect() as conn:
        library.upsert_tone(conn, cab)
        library.upsert_tone(conn, amp)
        library.upsert_model(conn, {
            "id": 601, "tone_id": 20, "model_url": "https://cdn/blue-1.wav",
            "name": "Blue 1", "architecture": None,
            "local_path": "data/tones/20-blue/Blue 1.wav",
        })
        library.upsert_model(conn, {
            "id": 602, "tone_id": 21, "model_url": "https://cdn/legacy.nam",
            "name": "Legacy", "architecture": "WaveNet",
            "local_path": "data/tones/21-legacy/Legacy.nam",
        })

    assert [m["id"] for m in library.list_local_models("ir")] == [601]
    # A1 (WaveNet) 是废弃架构：amp picker 不列出、默认选中不落到旧模型。
    assert [m["id"] for m in library.list_local_models("amp")] == []
    assert library._first_local_model(20, ir=True) == 601
    assert library._first_local_model(21, ir=False) is None


def test_space_tone_is_classified_as_ir():
    assert library.model_is_ir(
        {"architecture": None, "name": "space capture.wav"},
        {"gear": "space"},
    )


def test_canonical_format_overrides_space_gear_fallback():
    """The documented format field wins over the legacy SPACE IR heuristic."""
    assert not library.model_is_ir(
        {"architecture_version": None, "name": "space capture.nam"},
        {"gear": "space", "format": "nam"},
    )
    assert library.model_is_ir(
        {"architecture_version": None, "name": "space capture.nam"},
        {"gear": "space", "format": "ir"},
    )


def test_architecture_version_is_canonical_for_model_classification():
    assert not library.model_is_ir(
        {"architecture_version": "1", "name": "capture.wav"},
        {},
    )


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


def test_uninstall_plan_refreshes_untracked_preset_dependencies(tmp_path):
    (tmp_path / "tones").mkdir()
    amp, _ir = _put_models(tmp_path / "tones")
    library.PRESETS_DIR.mkdir(parents=True)
    (library.PRESETS_DIR / "external.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-preset",
        "name": "external dependency",
        "chain": {
            "slots": [{
                "model_id": amp["id"],
                "path": amp["local_path"],
            }],
            "gain": 1.0,
            "master": 1.0,
            "quality": 1.0,
        },
    }), encoding="utf-8")

    plan = library.local_uninstall_plan([amp["tone_id"]])

    assert plan["preset_names"] == ["external dependency"]


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


def test_local_uninstall_clears_missing_external_paths(tmp_path):
    (tmp_path / "external").mkdir()
    amp, ir = _put_models(tmp_path / "external")
    Path(amp["local_path"]).unlink()
    Path(ir["local_path"]).unlink()
    library.chain_set({})

    plan = library.local_uninstall_plan([19])
    assert plan["outside_paths"] == []

    result = library.local_uninstall_tones([19])

    assert result["removed"] == 0
    assert result["missing"] == 2
    assert all(model["local_path"] is None
               for model in library.get_tone(19)["models"])


def test_local_uninstall_rechecks_external_paths_before_moving(
        monkeypatch, tmp_path):
    (tmp_path / "external").mkdir()
    amp, ir = _put_models(tmp_path / "external")
    Path(amp["local_path"]).unlink()
    Path(ir["local_path"]).unlink()
    library.chain_set({})

    real_plan = library.local_uninstall_plan
    calls = 0

    def plan_with_external_race(ids):
        nonlocal calls
        plan = real_plan(ids)
        calls += 1
        if calls == 2:
            Path(amp["local_path"]).write_bytes(b"reappeared")
        return plan

    monkeypatch.setattr(library, "local_uninstall_plan", plan_with_external_race)

    with pytest.raises(ValueError, match="outside"):
        library.local_uninstall_tones([19])
    assert Path(amp["local_path"]).read_bytes() == b"reappeared"
    models = {model["id"]: model for model in library.get_tone(19)["models"]}
    assert models[amp["id"]]["local_path"] == amp["local_path"]
    assert models[ir["id"]]["local_path"] is None


def test_local_uninstall_never_moves_external_path_after_check(
        monkeypatch, tmp_path):
    (tmp_path / "external").mkdir()
    amp, ir = _put_models(tmp_path / "external")
    Path(amp["local_path"]).unlink()
    Path(ir["local_path"]).unlink()
    library.chain_set({})

    real_assert = library._assert_no_existing_external_paths
    calls = 0

    def assert_then_recreate(models):
        nonlocal calls
        real_assert(models)
        calls += 1
        if calls == 1:
            Path(amp["local_path"]).write_bytes(b"recreated")

    monkeypatch.setattr(library, "_assert_no_existing_external_paths",
                        assert_then_recreate)

    with pytest.raises(ValueError, match="outside"):
        library.local_uninstall_tones([19])
    assert Path(amp["local_path"]).read_bytes() == b"recreated"
    assert not list((library.TONES_DIR.parent / ".trash").glob("*"))


def test_local_uninstall_preserves_path_replaced_during_operation(
        monkeypatch, tmp_path):
    (tmp_path / "tones").mkdir()
    amp, ir = _put_models(tmp_path / "tones")
    library.chain_set({})
    replacement = library.TONES_DIR / "redownloaded.nam"
    real_assert = library._assert_no_existing_external_paths
    calls = 0

    def assert_and_redownload(models):
        nonlocal calls
        real_assert(models)
        calls += 1
        if calls == 2:
            replacement.write_bytes(b"new amp")
            with library.connect() as conn:
                library.upsert_model(conn, {**amp, "local_path": str(replacement)})

    monkeypatch.setattr(library, "_assert_no_existing_external_paths",
                        assert_and_redownload)

    result = library.local_uninstall_tones([19])

    assert result["removed"] == 2
    models = {model["id"]: model for model in library.get_tone(19)["models"]}
    assert models[amp["id"]]["local_path"] == str(replacement)
    assert models[ir["id"]]["local_path"] is None
    assert replacement.read_bytes() == b"new amp"


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
    model = library.ROOT / "data" / "tones" / "19-01.nam"
    model.write_bytes(b"amp")
    with library.connect() as conn:
        library.upsert_tone(conn, {**SAMPLE, "id": 19})
        library.upsert_model(conn, {
            "id": 1901, "tone_id": 19, "model_url": "u1901",
            "name": model.name, "architecture": "SlimmableContainer",
            "local_path": str(model),
        })
    library.chain_set({"master": 0.4, "model": "data/tones/19-01.nam"})
    # REQ-035 portable：读返回绝对路径（相对根解析）
    cfg = library.chain_get()
    assert cfg["master"] == 0.4
    assert cfg["slots"] == [{"path": str(model)}]
    assert not library.CHAIN_FILE.with_suffix(".json.tmp").exists()  # no leftover tmp


def test_chain_set_rejects_known_unsupported_model():
    model = library.ROOT / "data" / "tones" / "legacy.nam"
    model.write_bytes(b"legacy")
    with library.connect() as conn:
        library.upsert_tone(conn, {"id": 29, "title": "Legacy", "gear": "amp"})
        library.upsert_model(conn, {
            "id": 2901, "tone_id": 29, "name": model.name,
            "model_url": None,
            "architecture": "custom", "local_path": str(model),
        })

    with pytest.raises(ValueError, match="supported A2/IR"):
        library.chain_set({"slots": [{"path": str(model)}]})


def test_chain_set_allows_unregistered_tone_asset():
    model = library.ROOT / "data" / "tones" / "unknown.nam"
    model.write_bytes(b"unknown")
    library.chain_set({"slots": [{"path": str(model)}]})
    assert library.chain_get()["slots"] == [{"path": str(model)}]


def test_import_does_not_persist_when_download_has_no_supported_records(
        monkeypatch):
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))
    monkeypatch.setattr(tone3000, "download", lambda *args, **kwargs: [{
        "id": 1902, "tone_id": 19, "model_url": "custom",
        "name": "custom.nam", "model_json": {"architecture": "custom"},
        "local_path": str(library.TONES_DIR / "custom.nam"),
    }])

    assert library.import_tone(19, quiet=True) is None
    assert library.list_tones() == []


def test_import_does_not_reuse_stale_local_path_when_download_is_empty(
        monkeypatch):
    missing = library.TONES_DIR / "19-stale" / "stale.nam"
    with library.connect() as conn:
        library.upsert_tone(conn, {**SAMPLE, "local_dir": str(missing.parent)},
                             commit=False)
        library.upsert_model(conn, {
            "id": 1901, "tone_id": 19, "model_url": "stale",
            "name": missing.name, "architecture": "SlimmableContainer",
            "local_path": str(missing),
        }, commit=False)
        conn.commit()

    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))
    monkeypatch.setattr(tone3000, "download", lambda *args, **kwargs: [])

    assert library.import_tone(19, quiet=True) is None


def test_get_tone_and_public_views_hide_missing_local_paths(capsys):
    missing = library.TONES_DIR / "19-stale" / "stale.nam"
    with library.connect() as conn:
        library.upsert_tone(conn, {**SAMPLE, "local_dir": str(missing.parent)},
                             commit=False)
        library.upsert_model(conn, {
            "id": 1901, "tone_id": 19, "model_url": "https://example/stale",
            "name": missing.name, "architecture": "SlimmableContainer",
            "local_path": str(missing),
        }, commit=False)
        conn.commit()

    tone = library.get_tone(19)
    assert tone["models"][0]["local_path"] is None
    assert tone["local_dir"] is None
    assert tone["model_name"] is None

    public = library._public_tone(tone)
    assert "_models_source" not in public
    assert public["models"][0]["local_path"] is None
    assert str(missing) not in library._fmt_show(tone)

    library.main(["tone", "show", "19"])
    assert str(missing) not in capsys.readouterr().out


def test_preset_draft_rejects_unsupported_model_reference(tmp_path):
    amp, _ir = _put_models(tmp_path)
    custom = library.TONES_DIR / "custom.nam"
    custom.write_bytes(b"custom")
    with library.connect() as conn:
        library.upsert_model(conn, {
            "id": 1003, "tone_id": 19, "model_url": "custom",
            "name": custom.name, "architecture": "custom",
            "local_path": str(custom),
        })
    library.chain_set({"model": amp["local_path"]})
    preset = library.preset_save("draft-boundary")

    with pytest.raises(ValueError, match="unsupported A2/IR"):
        library.preset_update_draft_by_id(
            preset["id"],
            {"slots": [{"model_id": 1003, "path": str(custom)}]},
        )


def test_legacy_preset_with_unsupported_registered_model_is_hidden(tmp_path):
    _put_models(tmp_path)
    custom = library.TONES_DIR / "custom.nam"
    custom.write_bytes(b"custom")
    with library.connect() as conn:
        library.upsert_model(conn, {
            "id": 1003, "tone_id": 19, "model_url": "custom",
            "name": custom.name, "architecture": "custom",
            "local_path": str(custom),
        }, commit=False)
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, created_at, updated_at) "
            "VALUES (?, ?, ?, 'now', 'now')",
            ("legacy-unsupported", "", json.dumps({
                "slots": [{"model_id": 1003, "path": str(custom)}],
                "gain": 1.0, "master": 1.0, "quality": 1.0,
            })),
        )
        conn.commit()

    assert library.preset_get("legacy-unsupported") is None
    assert all(p["name"] != "legacy-unsupported"
               for p in library.preset_list())


def test_import_tone_mocked(monkeypatch, capsys):
    downloaded = [
        {"id": 51, "tone_id": 19, "model_url": "u1", "model_json": {"architecture": "SlimmableContainer"},
         "local_path": "data/tones/19-01.nam"},
        {"id": 52, "tone_id": 19, "model_url": "u2", "model_json": None,
         "local_path": "data/tones/19-01.wav"},
        {"id": 53, "tone_id": 19, "model_url": "u3",
         "model_json": {"architecture": "custom"},
         "local_path": "data/tones/19-custom.nam"},
    ]
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))

    def fake_download(_tone_id, dest, **kwargs):
        if not kwargs.get("return_paths"):
            return len(downloaded)
        records = []
        for record in downloaded[:2]:
            record = dict(record)
            target = dest / Path(record["local_path"]).name
            target.write_bytes(b"model")
            record["local_path"] = str(target)
            records.append(record)
        return records + [dict(downloaded[2])]

    monkeypatch.setattr(tone3000, "download", fake_download)
    t = library.import_tone(19)
    assert t["local_dir"] is not None
    arch = {m["id"]: m["architecture"] for m in t["models"]}
    assert arch == {51: "SlimmableContainer", 52: "IR"}
    # idempotent: second import keeps one tone row
    library.import_tone(19)
    assert len(library.list_tones()) == 1


def test_remote_import_writes_a_portable_tone_pack_manifest(monkeypatch):
    remote = {**SAMPLE, "url": "https://www.tone3000.com/tones/canonical-19"}
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(remote))

    def fake_download(_tone_id, dest, **kwargs):
        assert kwargs["return_paths"] is True
        amp = dest / "Clean Capture.nam"
        ir = dest / "V30.wav"
        amp.write_bytes(b"nam")
        ir.write_bytes(b"ir")
        return [
            {"id": 51, "tone_id": 19, "model_url": "https://x/amp",
             "name": amp.name,
             "model_json": {"architecture": "SlimmableContainer"},
             "local_path": str(amp)},
            {"id": 52, "tone_id": 19, "model_url": "https://x/ir",
             "name": ir.name, "model_json": None,
             "local_path": str(ir)},
        ]

    monkeypatch.setattr(tone3000, "download", fake_download)
    imported = library.import_tone(19, quiet=True)

    pack = library.TONES_DIR / "19-fender-super-reverb-1977"
    manifest_path = pack / library.PACK_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert imported["local_dir"] == str(pack)
    assert sorted(path.name for path in pack.iterdir()) == [
        "Clean Capture.nam", "V30.wav", "gigbuddy.json"
    ]
    assert manifest["kind"] == "gigbuddy-tone-pack"
    assert manifest["pack"]["source"] == {
        "kind": "tone3000",
        "url": "https://www.tone3000.com/tones/canonical-19",
        "tone_id": 19,
    }
    assert [(model["file"], model["format"]) for model in manifest["models"]] == [
        ("Clean Capture.nam", "nam"), ("V30.wav", "ir")
    ]


def test_remote_import_preserves_user_manifest_fields(monkeypatch):
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))

    def fake_download(_tone_id, dest, **_kwargs):
        path = dest / "Clean Capture.nam"
        path.write_bytes(b"nam")
        return [{
            "id": 51, "tone_id": 19, "model_url": "https://x/amp",
            "name": path.name,
            "model_json": {"architecture": "SlimmableContainer"},
            "local_path": str(path),
        }]

    monkeypatch.setattr(tone3000, "download", fake_download)
    library.import_tone(19, quiet=True)
    manifest_path = library.TONES_DIR / "19-fender-super-reverb-1977" / "gigbuddy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pack"]["name"] = "My renamed pack"
    manifest["pack"]["description"] = "My notes"
    manifest["metadata"] = {"favorite": True}
    manifest["models"][0]["metadata"] = {"mic": "SM57", "setting": "clean"}
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    library.import_tone(19, quiet=True)
    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert refreshed["pack"]["name"] == "My renamed pack"
    assert refreshed["pack"]["description"] == "My notes"
    assert refreshed["metadata"] == {"favorite": True}
    assert refreshed["models"][0]["metadata"] == {
        "mic": "SM57", "setting": "clean"
    }


def test_remote_import_does_not_overwrite_foreign_manifest(monkeypatch):
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))
    destination = library.TONES_DIR / "19-fender-super-reverb-1977"
    destination.mkdir(parents=True)
    foreign = destination / library.PACK_MANIFEST_NAME
    foreign.write_text('{"kind": "other-tool", "keep": true}\n', encoding="utf-8")

    def fake_download(_tone_id, dest, **_kwargs):
        path = dest / "Clean Capture.nam"
        path.write_bytes(b"nam")
        return [{
            "id": 51, "tone_id": 19, "model_url": "https://x/amp",
            "name": path.name,
            "model_json": {"architecture": "SlimmableContainer"},
            "local_path": str(path),
        }]

    monkeypatch.setattr(tone3000, "download", fake_download)
    library.import_tone(19, quiet=True)

    assert json.loads(foreign.read_text(encoding="utf-8")) == {
        "kind": "other-tool", "keep": True
    }


def test_import_staging_copy_does_not_share_destination_inode(tmp_path):
    source_dir = library.TONES_DIR / "existing"
    source_dir.mkdir()
    source = source_dir / "model.nam"
    source.write_bytes(b"original")
    staging = tmp_path / "staging"
    staging.mkdir()

    library._seed_import_directory(source_dir, staging)
    (staging / "model.nam").write_bytes(b"redownloaded")

    assert source.read_bytes() == b"original"


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


def test_import_rolls_back_new_files_when_db_persistence_fails(monkeypatch):
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))

    downloaded_path = {}

    def fake_download(_tone_id, dest, **_kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "new.nam"
        path.write_bytes(b"nam")
        downloaded_path["path"] = path
        return [{
            "id": 51, "tone_id": 19, "model_url": "u1",
            "model_json": {"architecture": "SlimmableContainer"},
            "local_path": str(path),
        }]

    monkeypatch.setattr(tone3000, "download", fake_download)
    real_upsert_model = library.upsert_model

    def fail_after_db_write(conn, model, *, commit=True):
        real_upsert_model(conn, model, commit=commit)
        raise RuntimeError("simulated model write failure")

    monkeypatch.setattr(library, "upsert_model", fail_after_db_write)
    with pytest.raises(RuntimeError, match="simulated model write failure"):
        library.import_tone(19, quiet=True)

    assert not downloaded_path["path"].exists()
    assert library.get_tone(19) is None


def test_import_restores_replaced_file_when_db_persistence_fails(monkeypatch):
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))
    destination = library.TONES_DIR / "19-fender-super-reverb-1977"
    destination.mkdir(parents=True)
    target = destination / "existing.nam"
    target.write_bytes(b"old model")

    def fake_download(_tone_id, staging, **_kwargs):
        staged = staging / target.name
        staged.write_bytes(b"new model")
        return [{
            "id": 51, "tone_id": 19, "model_url": "u1",
            "model_json": {"architecture": "SlimmableContainer"},
            "local_path": str(staged),
        }]

    monkeypatch.setattr(tone3000, "download", fake_download)
    real_upsert_model = library.upsert_model

    def fail_after_db_write(conn, model, *, commit=True):
        real_upsert_model(conn, model, commit=commit)
        raise RuntimeError("simulated replacement persistence failure")

    monkeypatch.setattr(library, "upsert_model", fail_after_db_write)
    with pytest.raises(RuntimeError, match="replacement persistence"):
        library.import_tone(19, quiet=True)

    assert target.read_bytes() == b"old model"
    assert not (destination / library.PACK_MANIFEST_NAME).exists()
    assert library.get_tone(19) is None


def test_import_failure_does_not_remove_another_task_file(monkeypatch):
    monkeypatch.setattr(tone3000, "tone_by_id", lambda tid: dict(SAMPLE))

    def fake_download(_tone_id, dest, **_kwargs):
        # Simulate a concurrent importer publishing into the shared final
        # directory while this task is still downloading.
        foreign = library.TONES_DIR / "19-fender-super-reverb-1977" / "other.nam"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"other task")
        own = dest / "failed.nam"
        own.write_bytes(b"failed task")
        raise RuntimeError("simulated download failure")

    monkeypatch.setattr(tone3000, "download", fake_download)
    with pytest.raises(RuntimeError, match="simulated download failure"):
        library.import_tone(19, quiet=True)

    assert (library.TONES_DIR / "19-fender-super-reverb-1977" / "other.nam").read_bytes() == b"other task"


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
    def fake_download(*_args, **kwargs):
        if not kwargs.get("return_paths"):
            return 1
        return [{
            "id": 1903, "tone_id": 19, "model_url": "u1903",
            "name": "roundtrip.nam",
            "model_json": {"architecture": "SlimmableContainer"},
            "local_path": "data/tones/roundtrip.nam",
        }]

    monkeypatch.setattr(tone3000, "download", fake_download)
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


def test_cli_reports_the_frozen_release_version(capsys):
    metadata = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"))
    with pytest.raises(SystemExit) as exc_info:
        library.main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == \
        f"gigbuddy {metadata['project']['version']}"


def test_cli_search_json(capsys, monkeypatch):
    hit = dict(SAMPLE)
    hit.update({
        "a1_models_count": 4,
        "a2_models_count": 2,
        "custom_models_count": 3,
        "irs_count": 1,
        "models_count": 10,
        "models": [
            {"id": 1, "architecture_version": "2", "name": "good.nam"},
            {"id": 2, "architecture": "custom", "name": "bad.nam"},
        ],
    })
    monkeypatch.setattr(tone3000, "search", lambda q, **kw: [hit])
    library.main(["tone", "search", "fender", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)[0]
    assert payload["id"] == 19
    assert payload["supported_models_count"] == 3
    assert "a1_models_count" not in payload
    assert "custom_models_count" not in payload
    assert "models_count" not in payload
    assert [model["id"] for model in payload["models"]] == [1]


# ---- presets --------------------------------------------------------------

def _put_models(tmp_path):
    """Two library models (amp .nam + IR .wav) with real files on disk."""
    base = tmp_path if tmp_path.name == "external" else library.TONES_DIR
    base.mkdir(parents=True, exist_ok=True)
    with library.connect() as conn:  # tone row first (FK enabled)
        library.upsert_tone(conn, dict(SAMPLE))
    amp = {"id": 1001, "tone_id": 19, "model_url": "u1", "name": "SR AKG 414",
           "architecture": "SlimmableContainer", "local_path": str(base / "SR AKG 414.nam")}
    ir = {"id": 1002, "tone_id": 19, "model_url": "u2", "name": "DR Oxford Big",
          "architecture": "IR", "local_path": str(base / "DR Oxford Big.wav")}
    (base / "SR AKG 414.nam").write_bytes(b"amp")
    (base / "DR Oxford Big.wav").write_bytes(b"ir")
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
    assert p["chain"]["slots"] == [
        {"model_id": 1001, "path": "data/tones/SR AKG 414.nam"},
        {"model_id": 1002, "path": "data/tones/DR Oxford Big.wav"},
    ]
    # load resolves ids to paths and writes the live chain
    cfg = library.preset_load("clean-rig")
    assert cfg["slots"] == [
        {"path": amp["local_path"]}, {"path": ir["local_path"]}
    ]
    assert cfg["gain"] == 0.8 and cfg["master"] == 0.65
    assert library.chain_get()["slots"][0]["path"] == amp["local_path"]


def test_shareable_preset_export_uses_slot_model_ids_without_local_paths(tmp_path):
    amp, ir = _put_models(tmp_path)
    library.chain_set({"slots": [{"path": amp["local_path"]},
                                  {"path": ir["local_path"]}],
                       "gain": 0.8, "master": 0.65})
    library.preset_save("share-rig", note="portable")

    destination = tmp_path / "share-rig.json"
    exported = library.preset_export("share-rig", destination)
    document = json.loads(exported.read_text(encoding="utf-8"))

    assert document["kind"] == "gigbuddy-shareable-preset"
    assert document["provider"] == "tone3000"
    assert "model_ids" not in document
    assert document["chain"]["slots"] == [
        {"model_id": 1001, "output_gain_db": 0.0},
        {"model_id": 1002, "output_gain_db": 0.0},
    ]
    assert "path" not in document["chain"]["slots"][0]


def test_shareable_preset_export_rejects_local_pack_asset(tmp_path):
    local = library.TONES_DIR / "local-pack" / "pedal.nam"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"local")
    library.scan_local_packs(force=True)
    library.chain_set({"slots": [{"path": str(local)}]})
    library.preset_save("local-only")

    with pytest.raises(ValueError, match="local Pack"):
        library.preset_export("local-only", tmp_path / "local.json")


def test_shareable_preset_export_rejects_bypassed_local_pack_asset(tmp_path):
    local = library.TONES_DIR / "local-pack" / "pedal.nam"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"local")
    library.scan_local_packs(force=True)
    library.chain_set({"slots": [{"path": str(local)}]})
    library.chain_set({"slots": [{"path": None, "candidate": str(local)}]})
    library.preset_save("local-bypass")

    with pytest.raises(ValueError, match="local Pack"):
        library.preset_export("local-bypass", tmp_path / "local-bypass.json")


def test_shareable_preset_import_reuses_installed_model_and_can_load(tmp_path):
    amp, _ir = _put_models(tmp_path)
    source = tmp_path / "shared.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "downloaded-rig",
        "note": "from a friend",
        # A stale field from the previous share format must be ignored.
        "model_ids": [999999],
        "chain": {"slots": [{"model_id": 1001}],
                  "gain": 0.7, "master": 0.9, "quality": 0.8},
    }), encoding="utf-8")

    imported = library.preset_import(source, load=True, quiet=True)

    assert imported["name"] == "downloaded-rig"
    assert imported["note"] == "from a friend"
    assert imported["chain"]["slots"] == [
        {"model_id": 1001, "path": "data/tones/SR AKG 414.nam"},
    ]
    assert library.chain_get()["slots"] == [{"path": amp["local_path"]}]
    assert library.preset_current() == "downloaded-rig"


def test_shareable_preset_import_downloads_missing_models_by_tone(
        tmp_path, monkeypatch):
    _put_models(tmp_path)
    source = tmp_path / "shared.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "remote-rig",
        "chain": {"slots": [{"model_id": 2001}, {"model_id": 2002}]},
    }), encoding="utf-8")
    calls = []

    monkeypatch.setattr(
        tone3000, "tones_for_model_ids",
        lambda ids: [{"id": 77, "matched_model_ids": list(ids)}],
    )

    def fake_import(tone_id, *, model_ids, quiet):
        calls.append((tone_id, model_ids, quiet))
        with library.connect() as conn:
            library.upsert_tone(conn, {**SAMPLE, "id": tone_id}, commit=False)
            conn.commit()
        for index, model_id in enumerate(model_ids):
            path = library.TONES_DIR / f"remote-{model_id}.nam"
            path.write_bytes(b"remote")
            with library.connect() as conn:
                library.upsert_model(conn, {
                    "id": model_id, "tone_id": tone_id,
                    "model_url": f"https://example/{model_id}",
                    "name": path.name,
                    "architecture": "SlimmableContainer",
                    "local_path": str(path),
                }, commit=False)
                conn.commit()
        return {"id": tone_id}

    monkeypatch.setattr(library, "import_tone", fake_import)

    imported = library.preset_import(source, quiet=True)

    assert calls == [(77, [2001, 2002], True)]
    assert [slot["model_id"] for slot in imported["chain"]["slots"]] == [2001, 2002]
    assert [slot["path"] for slot in imported["chain"]["slots"]] == [
        "data/tones/remote-2001.nam", "data/tones/remote-2002.nam",
    ]


def test_shareable_file_in_preset_directory_is_not_auto_imported(tmp_path):
    library.PRESETS_DIR.mkdir(parents=True)
    source = library.PRESETS_DIR / "shared-rig.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "shared-rig",
        "chain": {"slots": []},
    }), encoding="utf-8")

    assert library.preset_list() == []
    assert source.is_file()


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

    moved = library.TONES_DIR / "renamed.nam"
    moved.write_bytes(b"amp")
    with library.connect() as conn:
        conn.execute("UPDATE models SET local_path = ? WHERE id = ?", (str(moved), amp["id"]))
        conn.commit()

    assert library.preset_resolved_chain("moving")["slots"] == [
        {"model_id": 1001, "path": str(moved)}
    ]


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


def test_preset_draft_rejects_a_stale_updated_at(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    original = library.preset_save("draft")
    replacement = {"slots": [], "gain": 0.5, "master": 1.0, "quality": 1.0}

    updated = library.preset_update_draft_by_id(
        original["id"], replacement,
        expected_updated_at=original["updated_at"])
    assert updated["chain"]["slots"] == []

    with pytest.raises(library.PresetConflictError, match="changed externally"):
        library.preset_update_draft_by_id(
            original["id"], {"slots": [{"path": None}]},
            expected_updated_at=original["updated_at"])
    assert library.preset_get_by_id(original["id"])["chain"]["slots"] == []


def test_preset_save_external_path_kept_verbatim(tmp_path):
    """The v0.2 live protocol rejects files outside data/tones."""
    ext = tmp_path / "external.nam"
    ext.write_bytes(b"x")
    with pytest.raises(ValueError, match="outside data/tones"):
        library.chain_set({"model": str(ext), "gain": 1.0})


def test_preset_load_missing_file_raises(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    library.preset_save("rig")
    p = library.preset_get("rig")
    p["chain"]["slots"][0]["model_id"] = 999999999  # unresolved id
    p["chain"]["slots"][0]["path"] = "data/tones/missing.nam"
    with library.connect() as conn:
        conn.execute("UPDATE presets SET chain_json=? WHERE name=?",
                     (json.dumps(p["chain"]), "rig"))
        conn.commit()
    with pytest.raises(ValueError, match="model file missing"):
        library.preset_load("rig")


def test_preset_list_and_delete(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    library.preset_save("a")
    library.preset_save("b")
    names = {p["name"] for p in library.preset_list()}
    assert names == {"a", "b"}
    assert library.preset_delete("a") is True
    assert library.preset_delete("a") is False  # already gone
    assert library.preset_get("a") is None


def test_preset_save_writes_editable_json_and_external_edit_reconciles(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})

    saved = library.preset_save("Clean Rig", note="original")
    preset_path = library.PRESETS_DIR / f"{saved['id']}-clean-rig.json"
    document = json.loads(preset_path.read_text(encoding="utf-8"))

    assert document == {
        "schema_version": 1,
        "kind": "gigbuddy-preset",
        "id": saved["id"],
        "name": "Clean Rig",
        "note": "original",
        "chain": saved["chain"],
        "created_at": saved["created_at"],
        "updated_at": saved["updated_at"],
    }

    document["note"] = "edited by hand"
    document["chain"]["gain"] = 0.25
    preset_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    library.refresh_preset_catalog()
    reconciled = library.preset_get_by_id(saved["id"])
    assert reconciled["note"] == "edited by hand"
    assert reconciled["chain"]["gain"] == 0.25
    assert reconciled["updated_at"] != saved["updated_at"]


def test_preset_mutations_keep_json_filename_and_content_in_sync(tmp_path):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    saved = library.preset_save("Old Name", note="old")
    old_path = library.PRESETS_DIR / f"{saved['id']}-old-name.json"

    renamed = library.preset_rename_by_id(saved["id"], "New Name")
    new_path = library.PRESETS_DIR / f"{saved['id']}-new-name.json"
    assert not old_path.exists()
    assert new_path.is_file()
    assert json.loads(new_path.read_text(encoding="utf-8"))["name"] == "New Name"

    updated = library.preset_update_note_by_id(saved["id"], "new note")
    document = json.loads(new_path.read_text(encoding="utf-8"))
    assert updated["note"] == "new note"
    assert document["note"] == "new note"

    result = library.preset_delete_by_id(saved["id"])
    assert result["deleted"] is True
    assert not new_path.exists()


def test_preset_reconcile_imports_new_json_and_tracks_external_delete(tmp_path):
    library.PRESETS_DIR.mkdir(parents=True)
    source = library.PRESETS_DIR / "my-rig.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-preset",
        "name": "My Rig",
        "note": "local file",
        "chain": {"slots": [], "gain": 0.5, "master": 1.0, "quality": 1.0},
    }), encoding="utf-8")

    library.refresh_preset_catalog()
    imported = library.preset_get("My Rig")
    assert imported is not None
    assert imported["chain"]["gain"] == 0.5
    tracked_path = library.PRESETS_DIR / f"{imported['id']}-my-rig.json"
    assert tracked_path.is_file()
    assert not source.exists()

    tracked_path.unlink()
    library.refresh_preset_catalog()
    assert library.preset_get("My Rig") is None


def test_legacy_sqlite_preset_is_exported_without_rewriting_chain(tmp_path):
    legacy_chain = {"model_id": None, "model_path": None, "gain": 0.4}
    with library.connect() as conn:
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, created_at, updated_at) "
            "VALUES ('legacy', 'keep', ?, 'created', 'updated')",
            (json.dumps(legacy_chain),),
        )
        conn.commit()

    library.refresh_preset_catalog()
    preset = library.preset_get("legacy")
    preset_path = library.PRESETS_DIR / f"{preset['id']}-legacy.json"
    assert preset_path.is_file()
    with library.connect() as conn:
        stored = conn.execute(
            "SELECT chain_json FROM presets WHERE id = ?", (preset["id"],)
        ).fetchone()[0]
    assert json.loads(stored) == legacy_chain


def test_preset_group_is_derived_from_name_only():
    assert library.preset_group("band-guitar-rhcp") == ("Band Gear", "Guitar")
    assert library.preset_group("classic-bass-ampeg-svt") == ("Classic Pairing", "Bass")
    assert library.preset_group("fender-super-reverb-ts9") == ("Classic Amplifiers", "Guitar")
    assert library.preset_group("darkglass-alpha-omega") == ("Classic Amplifiers", "Bass")
    assert library.preset_group("my-tone") == ("Custom", "Other")


def test_shareable_import_fills_missing_nam_calibration(tmp_path):
    amp, _ir = _put_models(tmp_path)
    (library.TONES_DIR / "SR AKG 414.nam").write_text(
        json.dumps({"version": "0.7.0",
                    "metadata": {"loudness": -23.0}}),
        encoding="utf-8")
    source = tmp_path / "shared.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "legacy-calibrated",
        "chain": {"slots": [{"model_id": 1001}]},
    }), encoding="utf-8")

    imported = library.preset_import(source, quiet=True)

    assert imported["chain"]["slots"] == [
        {"model_id": 1001, "output_gain_db": 5.0,
         "path": "data/tones/SR AKG 414.nam"},
    ]


def test_shareable_import_respects_explicit_calibration(tmp_path):
    amp, _ir = _put_models(tmp_path)
    (library.TONES_DIR / "SR AKG 414.nam").write_text(
        json.dumps({"version": "0.7.0",
                    "metadata": {"loudness": -23.0}}),
        encoding="utf-8")
    source = tmp_path / "shared.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "explicit-calibrated",
        "chain": {"slots": [{"model_id": 1001, "output_gain_db": -2.0}]},
    }), encoding="utf-8")

    imported = library.preset_import(source, quiet=True)

    assert imported["chain"]["slots"] == [
        {"model_id": 1001, "output_gain_db": -2.0,
         "path": "data/tones/SR AKG 414.nam"},
    ]


def test_shareable_import_respects_explicit_zero_calibration(tmp_path):
    amp, _ir = _put_models(tmp_path)
    (library.TONES_DIR / "SR AKG 414.nam").write_text(
        json.dumps({"version": "0.7.0",
                    "metadata": {"loudness": -23.0}}),
        encoding="utf-8")
    source = tmp_path / "shared.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "explicit-zero",
        "chain": {"slots": [{"model_id": 1001, "output_gain_db": 0.0}]},
    }), encoding="utf-8")

    imported = library.preset_import(source, quiet=True)

    slot = imported["chain"]["slots"][0]
    assert slot.get("output_gain_db", 0.0) == 0.0
    assert slot.get("output_gain_db") != 5.0


def test_shareable_export_import_roundtrip_preserves_zero_output_gain(tmp_path):
    amp, _ir = _put_models(tmp_path)
    (library.TONES_DIR / "SR AKG 414.nam").write_text(
        json.dumps({"version": "0.7.0",
                    "metadata": {"loudness": -23.0}}),
        encoding="utf-8")
    library.chain_set({
        "slots": [{"path": amp["local_path"], "output_gain_db": 0.0}],
    })
    library.preset_save("zero-roundtrip")
    destination = library.preset_export(
        "zero-roundtrip", tmp_path / "zero-roundtrip.json")
    document = json.loads(destination.read_text(encoding="utf-8"))

    assert document["chain"]["slots"][0]["output_gain_db"] == 0.0

    imported = library.preset_import(
        destination, name="zero-roundtrip-copy", quiet=True)
    slot = imported["chain"]["slots"][0]
    assert slot.get("output_gain_db", 0.0) == 0.0
    assert slot.get("output_gain_db") != 5.0


def test_cli_preset_roundtrip(tmp_path, capsys):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    assert library.main(["preset", "save", "cli-rig", "--note", "n"]) == 0
    assert library.main(["preset", "list"]) == 0
    assert "cli-rig" in capsys.readouterr().out
    assert library.main(["preset", "show", "cli-rig"]) == 0
    assert "#1001" in capsys.readouterr().out
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


def test_cli_shareable_preset_export_and_import(tmp_path, capsys):
    amp, _ir = _put_models(tmp_path)
    library.chain_set({"model": amp["local_path"]})
    assert library.main(["preset", "save", "cli-share"]) == 0
    capsys.readouterr()

    share_path = tmp_path / "preset-name.json"
    assert library.main(["preset", "export", "cli-share", str(share_path)]) == 0
    assert "Shareable Preset written" in capsys.readouterr().out
    document = json.loads(share_path.read_text(encoding="utf-8"))
    assert document["kind"] == "gigbuddy-shareable-preset"
    assert "model_ids" not in document
    assert "path" not in json.dumps(document)

    assert library.main(["preset", "delete", "cli-share"]) == 0
    capsys.readouterr()
    library.chain_set({"slots": []})

    assert library.main([
        "preset", "import", str(share_path), "--name", "cli-imported", "--load",
    ]) == 0
    assert "cli-imported" in capsys.readouterr().out
    assert library.preset_current() == "cli-imported"
    assert library.chain_get()["slots"] == [{"path": amp["local_path"]}]


def test_cli_shareable_preset_import_confirms_missing_models_before_download(
        tmp_path, monkeypatch, capsys):
    _put_models(tmp_path)
    source = tmp_path / "shared.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "remote-rig",
        "chain": {"slots": [{"model_id": 2001}]},
    }), encoding="utf-8")
    monkeypatch.setattr(
        library, "_shareable_model_tones",
        lambda ids: {2001: {"id": 77, "title": "Classic Amp"}},
    )
    calls = []

    def fake_import(tone_id, *, model_ids, quiet):
        calls.append((tone_id, model_ids, quiet))
        path = library.TONES_DIR / "remote-2001.nam"
        path.write_bytes(b"remote")
        with library.connect() as conn:
            library.upsert_tone(conn, {**SAMPLE, "id": tone_id}, commit=False)
            library.upsert_model(conn, {
                "id": 2001, "tone_id": tone_id,
                "model_url": "https://example/2001",
                "name": path.name,
                "architecture": "SlimmableContainer",
                "local_path": str(path),
            }, commit=False)
            conn.commit()
        return {"id": tone_id}

    monkeypatch.setattr(library, "import_tone", fake_import)
    answers = iter(["yes"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert library.main(["preset", "import", str(source), "--load"]) == 0

    output = capsys.readouterr().out
    assert "Loading shareable Preset 'remote-rig'." in output
    assert "Model 2001 from Tone 77: Classic Amp" in output
    assert "The Preset will be loaded into the live chain after download." in output
    assert "Preset 'remote-rig' imported and loaded." in output
    assert calls == [(77, [2001], True)]


def test_cli_shareable_preset_import_decline_does_not_write_or_load(
        tmp_path, monkeypatch, capsys):
    _put_models(tmp_path)
    source = tmp_path / "shared.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "kind": "gigbuddy-shareable-preset",
        "provider": "tone3000",
        "name": "remote-rig",
        "chain": {"slots": [{"model_id": 2001}]},
    }), encoding="utf-8")
    monkeypatch.setattr(
        library, "_shareable_model_tones",
        lambda ids: {2001: {"id": 77, "title": "Classic Amp"}},
    )
    monkeypatch.setattr(
        library, "import_tone",
        lambda *_args, **_kwargs: pytest.fail("download must not start after decline"),
    )
    prompts = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or "n")

    assert library.main(["preset", "import", str(source), "--load"]) == 1

    output = capsys.readouterr().out
    assert prompts == ["Download these model(s) now? [y/N]: "]
    assert "Preset import cancelled." in output
    assert library.preset_get("remote-rig") is None
    assert library.preset_current() is None


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
        {"id": 19, "gear": "amp", "models_count": 6},   # local A2, remote A1+A2
        {"id": 999, "gear": "amp", "models_count": 3},  # nothing local
        {"id": 123, "gear": "cab", "models_count": 2},  # local, partial IR
    ]
    remote_models = {
        19: [{"id": 1001, "architecture": "SlimmableContainer"},
             {"id": 9000, "architecture": "WaveNet"}],             # A1 filtered out
        123: [{"id": 9002, "architecture": "IR"},                  # have it
              {"id": 9001, "architecture": "IR"}],                 # missing
    }
    monkeypatch.setattr(
        "tone3000.models",
        lambda tid, a2_only=False: remote_models.get(tid, []))

    out = library.mark_download_state(hits)
    by_id = {t["id"]: t for t in out}
    # A1（WaveNet）是废弃架构，产品过滤：A2 已全下即 all，不再因缺 A1
    # 显示 partial。
    assert by_id[19]["download_state"] == "all"
    assert by_id[19]["downloaded"] == 2
    assert by_id[999]["download_state"] == "none"  # no local models, no API call
    assert by_id[123]["download_state"] == "partial"  # 1 of 2 IRs
    assert by_id[123]["downloaded"] == 1


def test_top_favorites_attaches_usernames(monkeypatch):
    """The compatibility name reads the current user's official favorites."""
    import tone3000

    def fake_get(url, **params):
        assert url == f"{tone3000.API}/tones/favorited"
        assert params == {"page": 1, "page_size": 50}
        return {"data": [
            {"id": 1, "title": "T1", "user": {
                "id": "u1", "username": "alice", "avatar_url": "a"},
             "a2_models_count": 1},
            {"id": 2, "title": "T2", "user": {
                "id": "u2", "username": "bob", "avatar_url": "b"},
             "irs_count": 1},
        ], "total": 2, "total_pages": 1}

    monkeypatch.setattr("tone3000._get", fake_get)
    rows = tone3000.top_favorites(2)
    assert [r["username"] for r in rows] == ["alice", "bob"]
    assert rows[0]["avatar_url"] == "a"


def test_mark_download_state_reports_unknown_when_model_probe_fails(monkeypatch):
    path = library.ROOT / "data" / "tones" / "probe.nam"
    path.write_bytes(b"nam")
    with library.connect() as conn:
        library.upsert_tone(conn, {**SAMPLE, "id": 201})
        library.upsert_model(conn, {
            "id": 2011, "tone_id": 201, "model_url": "u",
            "architecture": "SlimmableContainer", "local_path": str(path),
        })

    def fail_probe(*_args, **_kwargs):
        raise TimeoutError("network down")

    monkeypatch.setattr(tone3000, "models", fail_probe)
    rows = library.mark_download_state([{"id": 201, "a2_models_count": 1}])
    assert rows[0]["download_state"] == "unknown"
    assert rows[0]["downloaded"] == 1


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


def test_relative_library_paths_resolve_against_root(monkeypatch, tmp_path):
    root = tmp_path / "project"
    model = root / "data" / "tones" / "1-x" / "A.nam"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"x")
    monkeypatch.setattr(library, "ROOT", root)
    monkeypatch.setattr(library, "TONES_DIR", root / "data" / "tones")

    with library.connect() as conn:
        library.upsert_tone(conn, {
            "id": 1, "title": "T", "username": "u",
            "local_dir": "data/tones/1-x",
        })
        library.upsert_model(conn, {
            "id": 1, "tone_id": 1, "model_url": "u",
            "name": "A.nam", "architecture": "SlimmableContainer",
            "local_path": "data/tones/1-x/A.nam",
        })

    with library.connect() as conn:
        row = conn.execute(
            "SELECT local_dir FROM tones WHERE id=1").fetchone()
        stored = conn.execute(
            "SELECT local_path FROM models WHERE id=1").fetchone()
    assert row["local_dir"] == "data/tones/1-x"
    assert stored["local_path"] == "data/tones/1-x/A.nam"
    tone = library.get_tone(1)
    assert tone["local_dir"] == str(root / "data" / "tones" / "1-x")
    assert tone["models"][0]["local_path"] == str(model)


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
    p.parent.mkdir(parents=True)
    p.write_bytes(b"amp")
    with library.connect() as conn:
        library.upsert_tone(conn, {"id": 1, "title": "T", "gear": "amp"})
        library.upsert_model(conn, {
            "id": 1001, "tone_id": 1, "model_url": "u",
            "name": p.name, "architecture": "SlimmableContainer",
            "local_path": str(p),
        })
    library.chain_set({"model": str(p), "gain": 1.0})
    raw = json.loads((tmp_path / "live_chain.json").read_text())
    assert raw["slots"] == [{"path": "data/tones/1-x/A.nam"}]
    assert library.chain_get()["slots"] == [{"path": str(p)}]


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


def test_local_pack_scanner_indexes_files_without_manifest(tmp_path):
    pack_dir = library.TONES_DIR / "my-local-pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "clean.nam").write_bytes(b"nam")
    (pack_dir / "v30.wav").write_bytes(b"ir")
    (pack_dir / "notes.txt").write_text("ignored", encoding="utf-8")
    (pack_dir / ".hidden.nam").write_bytes(b"ignored")

    packs = library.scan_local_packs(force=True)

    assert len(packs) == 1
    pack = packs[0]
    assert pack["name"] == "my-local-pack"
    assert pack["source_kind"] == "local"
    assert [model["format"] for model in pack["models"]] == ["nam", "ir"]
    assert all(model["id"] is None for model in pack["models"])
    assert all(model["model_key"].startswith(pack["pack_id"] + ":")
               for model in pack["models"])
    assert len(library.list_local_models("amp")) == 1
    assert len(library.list_local_models("ir")) == 1
    assert library.local_models_by_tone(str(pack_dir / "clean.nam"))


def test_local_pack_manifest_fields_and_invalid_json_are_non_blocking(tmp_path):
    pack_dir = library.TONES_DIR / "manifest-pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "capture.nam").write_bytes(b"nam")
    manifest = {
        "kind": "gigbuddy-tone-pack",
        "pack": {"id": "local-manifest-pack", "name": "Edited Pack",
                 "author": "Me", "description": "notes"},
        "models": [{"file": "capture.nam", "name": "Clean capture",
                    "metadata": {"mic": "SM57"}}],
    }
    (pack_dir / library.PACK_MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8")
    pack = library.scan_local_packs(force=True)[0]
    assert pack["pack_id"] == "local-manifest-pack"
    assert pack["name"] == "Edited Pack"
    assert pack["models"][0]["name"] == "Clean capture"
    assert pack["models"][0]["metadata_json"] == json.dumps(
        {"mic": "SM57"}, ensure_ascii=False)

    (pack_dir / library.PACK_MANIFEST_NAME).write_text("{broken", encoding="utf-8")
    refreshed = library.scan_local_packs(force=True)[0]
    assert refreshed["manifest_status"] == "invalid"
    assert refreshed["models"][0]["name"] == "capture.nam"


def test_local_pack_preset_uses_pack_identity_and_resolves(tmp_path):
    pack_dir = library.TONES_DIR / "preset-pack"
    pack_dir.mkdir(parents=True)
    amp = pack_dir / "amp.nam"
    amp.write_bytes(b"nam")
    pack = library.scan_local_packs(force=True)[0]

    library.chain_set({"slots": [{"path": str(amp)}]})
    saved = library.preset_save("Local Rig")
    slot = saved["chain"]["slots"][0]
    assert slot["model_id"] is None
    assert slot["pack_id"] == pack["pack_id"]
    assert slot["relative_path"] == "amp.nam"
    assert slot["model_key"] == f"{pack['pack_id']}:amp.nam"
    assert library.preset_resolved_chain_by_id(saved["id"])["slots"][0]["path"] == str(amp)


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


def test_uninstall_plan_matches_legacy_preset_path_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr(library, "ROOT", tmp_path)
    monkeypatch.setattr(library, "TONES_DIR", tmp_path / "data" / "tones")
    model = tmp_path / "data" / "tones" / "10-x" / "A.nam"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"amp")
    with library.connect() as conn:
        library.upsert_tone(conn, {"id": 10, "title": "T", "gear": "amp",
                                   "username": "a"})
        library.upsert_model(conn, {
            "id": 1, "tone_id": 10, "model_url": "u", "name": "A.nam",
            "architecture": "SlimmableContainer", "local_path": str(model),
        })
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, created_at, updated_at) "
            "VALUES (?, ?, ?, 'now', 'now')",
            ("legacy", "", json.dumps({
                "model": "data/tones/10-x/A.nam",
                "gain": 1.0, "master": 1.0,
            })),
        )
        conn.commit()
    library.chain_set({"slots": []})

    plan = library.local_uninstall_models_plan([1])
    assert plan["preset_names"] == ["legacy"]
    with pytest.raises(ValueError, match="referenced by presets"):
        library.local_uninstall_models([1])
