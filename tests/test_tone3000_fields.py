"""Regression tests for the official TONE3000 Tone/Model field semantics."""

import urllib.error

import tone3000


def test_tone_format_is_canonical_and_explicit_format_wins():
    assert tone3000.tone_format({"format": "nam", "gear": "space"}) == "nam"
    assert not tone3000.is_ir_tone({"format": "nam", "gear": "space"})
    assert tone3000.tone_format({"format": "ir", "gear": "amp"}) == "ir"
    assert tone3000.is_ir_tone({"format": "ir", "gear": "amp"})


def test_space_and_deprecated_gear_ir_route_to_ir_without_format():
    assert tone3000.is_ir_tone({"gear": "space"})
    assert tone3000.tone_format({"gear": "space"}) == "ir"
    normalized = tone3000.normalize_tone({"gear": "ir"})
    assert normalized["gear"] == "cab"
    assert normalized["format"] == "ir"


def test_deprecated_tone_aliases_are_retained_for_old_callers():
    normalized = tone3000.normalize_tone({"gear": "full-rig", "platform": "nam"})
    assert normalized["gear"] == "amp-cab"
    assert normalized["format"] == "nam"
    assert normalized["platform"] == "nam"


def test_model_architecture_version_is_canonical():
    assert tone3000.normalize_architecture("WaveNet") == "1"
    assert tone3000.normalize_architecture("SlimmableContainer") == "2"
    assert tone3000.normalize_architecture("custom") == "custom"
    a2 = tone3000.normalize_model({"architecture": "SlimmableContainer"})
    assert a2["architecture_version"] == "2"
    assert a2["architecture"] == "SlimmableContainer"
    ir = tone3000.normalize_model({"architecture_version": None, "architecture": "IR"})
    assert ir["architecture_version"] is None
    assert tone3000.is_ir_model(ir)


def test_explicit_null_architecture_without_legacy_label_stays_null():
    model = tone3000.normalize_model({"architecture_version": None})
    assert model["architecture_version"] is None
    assert tone3000.model_architecture_version(model) is None


def test_explicit_architecture_wins_over_stale_ir_marker():
    model = tone3000.normalize_model({
        "architecture_version": "2",
        "architecture": "IR",
    })
    assert not tone3000.is_ir_model(model, {"format": "ir"})


def test_official_non_nam_model_uses_parent_format_for_ir_routing():
    model = tone3000.normalize_model({"architecture_version": None})
    assert tone3000.is_ir_model(model, {"format": "ir", "gear": "space"})
    assert not tone3000.is_ir_model(model, {"format": "nam", "gear": "space"})


def test_official_object_taxonomy_fields_are_normalized_to_local_names():
    tone = tone3000.normalize_tone({
        "tags": [{"id": 1, "name": "clean"}],
        "makes": [{"id": 2, "name": "VOX AC30"}],
        "sizes": ["standard"],
    })
    assert tone["tags"] == ["clean"]
    assert tone["makes"] == ["VOX AC30"]
    assert tone["sizes"] == ["standard"]


def test_official_embedded_user_is_preserved_and_flattened():
    tone = tone3000.normalize_tone({
        "user": {"id": 7, "username": "artist", "avatar_url": None,
                 "url": "https://www.tone3000.com/artist"},
    })
    assert tone["user"]["id"] == 7
    assert tone["username"] == "artist"
    assert tone["user_url"].endswith("/artist")


def test_non_nam_formats_are_not_engine_compatible():
    assert tone3000.is_engine_compatible_model(
        {"architecture_version": None, "name": "capture.aida"},
        {"format": "aida-x"}) is False
    assert tone3000.is_engine_compatible_model(
        {"architecture_version": None, "name": "cab.wav"},
        {"format": "ir"}) is True


def test_local_model_suffix_must_match_engine_format():
    assert not tone3000.is_engine_compatible_model(
        {"architecture_version": "1", "local_path": "capture.wav"})
    assert tone3000.is_engine_compatible_model(
        {"architecture_version": "1", "local_path": "capture.NAM"})
    assert not tone3000.is_engine_compatible_model(
        {"architecture_version": None, "local_path": "cab.nam"},
        {"format": "ir"})
    assert tone3000.is_engine_compatible_model(
        {"architecture_version": None, "local_path": "cab.WAV"},
        {"format": "ir"})
    assert tone3000.is_engine_compatible_model(
        {"architecture": "IR", "local_path": "cab.wav"},
        {"format": "nam"})


def test_legacy_unknown_model_stays_loadable_when_parent_format_is_unknown():
    # The old RPC omitted both Model.name and architecture for some NAM rows.
    assert tone3000.is_engine_compatible_model(
        {"architecture_version": None}, {"gear": "amp"}) is True


def test_legacy_unknown_model_still_checks_existing_file_suffix():
    # A missing format marker is only a metadata compatibility case; an
    # already-downloaded path must still match the engine's actual file type.
    assert not tone3000.is_engine_compatible_model(
        {"architecture_version": None, "local_path": "capture.aida"},
        {"gear": "amp"})
    assert tone3000.is_engine_compatible_model(
        {"architecture_version": None, "local_path": "capture.nam"},
        {"gear": "amp"})
    assert not tone3000.is_engine_compatible_model(
        {"architecture_version": None, "local_path": "cab.nam"},
        {"gear": "space"})
    assert tone3000.is_engine_compatible_model(
        {"architecture_version": None, "local_path": "cab.wav"},
        {"gear": "space"})


def test_search_forwards_official_architecture_filter(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        tone3000, "_post", lambda _url, body: captured.update(body) or [])

    tone3000.search("plexi")
    assert "architecture_filter" not in captured

    tone3000.search("plexi", architecture_filter="1")
    assert captured["architecture_filter"] == "1"

    try:
        tone3000.search("plexi", architecture_filter="all")
    except ValueError as exc:
        assert "1, 2, or custom" in str(exc)
    else:
        raise AssertionError("invalid architecture filter must be rejected")


def test_models_reads_top_level_architecture_version_and_preserves_official_default(
        monkeypatch):
    captured = {}

    def fake_get(_url, **params):
        captured.update(params)
        return [
            {"id": 1, "architecture_version": "1"},
            {"id": 2, "architecture_version": "2"},
            {"id": 3, "architecture_version": "custom"},
            {"id": 4, "architecture_version": None},
        ]

    monkeypatch.setattr(tone3000, "_get", fake_get)
    assert [m["id"] for m in tone3000.models(9)] == [1, 3, 4]
    assert captured["select"] == (
        "id,created_at,updated_at,user_id,model_url,name,size,tone_id,"
        "architecture_version")
    assert [m["id"] for m in tone3000.models(9, a2_only=True)] == [2]
    assert [m["id"] for m in tone3000.models(9, a2_only=False)] == [1, 2, 3, 4]


def test_top_preserves_format_and_handles_integer_user_ids(monkeypatch):
    captured = {}

    def fake_get(url, **params):
        captured[url] = params
        if url.endswith("/users"):
            return [{"id": 7, "username": "artist", "avatar_url": None}]
        return [{
            "id": 9, "title": "Outboard", "gear": "outboard",
            "format": "aida-x", "user_id": 7,
            "a1_models_count": 0, "a2_models_count": 0,
            "custom_models_count": 0, "irs_count": 0,
            "models_count": 1,
        }]

    monkeypatch.setattr(tone3000, "_get", fake_get)
    row = tone3000.top(1)[0]

    assert row["format"] == "aida-x"
    assert row["username"] == "artist"
    assert "format" in captured[f"{tone3000.API}/tones_counts"]["select"]
    assert "a1_models_count" in captured[f"{tone3000.API}/tones_counts"]["select"]


def test_top_falls_back_for_legacy_tones_counts_schema(monkeypatch):
    selects = []

    def fake_get(url, **params):
        if url.endswith("/tones_counts"):
            selects.append(params["select"])
            if len(selects) == 1:
                raise urllib.error.HTTPError(url, 400, "missing format", {}, None)
            return [{"id": 1, "gear": "amp", "platform": "nam",
                     "user_id": "u1"}]
        return [{"id": "u1", "username": "artist", "avatar_url": None}]

    monkeypatch.setattr(tone3000, "_get", fake_get)
    row = tone3000.top(1)[0]

    assert "format" in selects[0]
    assert "format" not in selects[1]
    assert row["format"] == "nam"


def test_download_rewrites_conflicting_processing_suffix(monkeypatch, tmp_path):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"model"

    monkeypatch.setattr(tone3000.urllib.request, "urlopen",
                        lambda *_args, **_kwargs: Response())

    monkeypatch.setattr(
        tone3000, "models",
        lambda *_args, **_kwargs: [{
            "id": 1, "model_url": "https://example.test/capture",
            "name": "Capture.wav", "architecture_version": "1",
        }])
    tone3000.download(1, tmp_path / "nam", quiet=True)
    assert (tmp_path / "nam" / "Capture.nam").exists()

    monkeypatch.setattr(
        tone3000, "models",
        lambda *_args, **_kwargs: [{
            "id": 2, "model_url": "https://example.test/cab",
            "name": "Cab.nam", "architecture_version": None,
        }])
    tone3000.download(2, tmp_path / "ir", ext="wav", quiet=True)
    assert (tmp_path / "ir" / "Cab.wav").exists()
