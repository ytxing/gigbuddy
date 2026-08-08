import json
from pathlib import Path

import pytest

import chain_protocol


def _root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "tones").mkdir(parents=True)
    (tmp_path / "data" / "dry_inputs").mkdir(parents=True)
    (tmp_path / "data" / "tones" / "amp.nam").write_text("nam")
    (tmp_path / "data" / "tones" / "cab.wav").write_bytes(b"wav")
    (tmp_path / "data" / "dry_inputs" / "dry.wav").write_bytes(b"wav")
    return tmp_path


def test_legacy_chain_normalizes_to_ordered_slots(tmp_path):
    root = _root(tmp_path)
    got = chain_protocol.normalize_chain(
        {"model": "data/tones/amp.nam", "ir": "data/tones/cab.wav"}, root=root
    )
    assert [Path(s["path"]).name for s in got["slots"]] == ["amp.nam", "cab.wav"]
    assert "model" not in got and "ir" not in got
    assert got["gain"] == got["master"] == got["quality"] == 1.0
    assert got["mute"] is False and got["revision"] == 0


def test_explicit_slots_take_precedence_and_malformed_slots_are_rejected(tmp_path):
    root = _root(tmp_path)
    with pytest.warns(RuntimeWarning, match="slots take precedence"):
        got = chain_protocol.normalize_chain(
            {"slots": [], "model": "data/tones/amp.nam"}, root=root
        )
    assert got["slots"] == []
    with pytest.raises(chain_protocol.ChainProtocolError):
        chain_protocol.normalize_chain({"slots": None}, root=root)
    with pytest.raises(chain_protocol.ChainProtocolError, match="contain path"):
        chain_protocol.normalize_chain({"slots": [{}]}, root=root)


def test_legacy_master_zero_is_read_as_mute_and_restores_parameter(tmp_path):
    root = _root(tmp_path)
    got = chain_protocol.normalize_chain(
        {"slots": [], "master": 0}, root=root
    )
    assert got["master"] == 1.0
    assert got["mute"] is True

    explicit = chain_protocol.normalize_chain(
        {"slots": [], "master": 0, "mute": False}, root=root
    )
    assert explicit["master"] == 0
    assert explicit["mute"] is False


@pytest.mark.parametrize("count", [0, 1, 6])
def test_slot_count_round_trips_with_order_and_duplicates(tmp_path, count):
    root = _root(tmp_path)
    raw = {"slots": [{"path": "data/tones/amp.nam"} for _ in range(count)]}
    got = chain_protocol.normalize_chain(raw, root=root)
    assert len(got["slots"]) == count
    assert all(Path(slot["path"]).name == "amp.nam" for slot in got["slots"])


@pytest.mark.parametrize(
    "legacy", [{"model": None, "ir": None},
                {"model": "data/tones/amp.nam"},
                {"ir": "data/tones/cab.wav"}],
)
def test_legacy_optional_paths_normalize_without_implicit_empty_slots(tmp_path, legacy):
    root = _root(tmp_path)
    got = chain_protocol.normalize_chain(legacy, root=root)
    expected = [name for name in ("amp.nam", "cab.wav")
                if (name == "amp.nam" and legacy.get("model"))
                or (name == "cab.wav" and legacy.get("ir"))]
    assert [Path(slot["path"]).name for slot in got["slots"]] == expected


def test_canonical_write_is_relative_atomic_and_increments_revision(tmp_path):
    root = _root(tmp_path)
    chain_file = root / "data" / "live_chain.json"
    first = chain_protocol.write_chain_file(
        chain_file, {"slots": [{"path": "data/tones/amp.nam"}]}, root=root
    )
    assert first["revision"] == 1
    stored = json.loads(chain_file.read_text())
    assert stored["slots"] == [{"path": "data/tones/amp.nam"}]
    assert "model" not in stored and "ir" not in stored
    second = chain_protocol.write_chain_file(chain_file, first, root=root)
    assert second["revision"] == 2
    assert not list(chain_file.parent.glob(".*.tmp"))


def test_invalid_slot_is_rejected_without_representing_unknown_type(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(chain_protocol.ChainProtocolError):
        chain_protocol.normalize_chain(
            {"slots": [{"path": "data/tones/unknown.txt"}]}, root=root
        )
    with pytest.raises(chain_protocol.ChainProtocolError):
        chain_protocol.normalize_chain(
            {"slots": [{"path": "../outside.nam"}]}, root=root
        )


def test_bypass_candidate_is_scoped_and_serialized_as_a_relative_tone_path(tmp_path):
    root = _root(tmp_path)
    got = chain_protocol.normalize_chain(
        {"slots": [{"path": None, "candidate": "data/tones/amp.nam"}]},
        root=root,
    )
    assert got["slots"] == [{
        "path": None,
        "candidate": str(root / "data" / "tones" / "amp.nam"),
    }]

    chain_file = root / "data" / "live_chain.json"
    chain_protocol.write_chain_file(chain_file, got, root=root)
    assert json.loads(chain_file.read_text())["slots"] == [{
        "path": None,
        "candidate": "data/tones/amp.nam",
    }]

    invalid = [
        {"path": "data/tones/amp.nam", "candidate": "data/tones/cab.wav"},
        {"path": None, "candidate": "../outside.nam"},
        {"path": None, "candidate": "data/tones/candidate.txt"},
        {"path": None, "candidate": 7},
    ]
    for slot in invalid:
        with pytest.raises(chain_protocol.ChainProtocolError):
            chain_protocol.normalize_chain({"slots": [slot]}, root=root)


def test_slots_are_limited_and_unknown_slot_fields_are_removed(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(chain_protocol.ChainProtocolError):
        chain_protocol.normalize_chain(
            {"slots": [{"path": None}] * 7}, root=root
        )
    got = chain_protocol.normalize_chain(
        {"slots": [{"path": None, "label": "AMP"}]}, root=root
    )
    assert got["slots"] == [{"path": None, "label": "AMP"}]
    chain_file = root / "data" / "live_chain.json"
    chain_protocol.write_chain_file(
        chain_file, {"slots": [{"path": None, "label": "AMP"}]}, root=root)
    assert json.loads(chain_file.read_text())["slots"] == [{"path": None}]


def test_file_input_is_normalized_and_instrument_state_is_restricted(tmp_path):
    root = _root(tmp_path)
    got = chain_protocol.normalize_chain(
        {"input": {"source": "file", "file": "data/dry_inputs/dry.wav",
                    "state": "playing", "loop": True}}, root=root
    )
    assert got["input"] == {"source": "file", "file": str(root.resolve() / "data/dry_inputs/dry.wav"),
                             "state": "playing", "loop": True}
    with pytest.raises(chain_protocol.ChainProtocolError):
        chain_protocol.normalize_chain(
            {"input": {"source": "instrument", "state": "playing"}}, root=root
        )


@pytest.mark.parametrize("field", ["source", "state"])
@pytest.mark.parametrize("value", [[], {}])
def test_invalid_non_string_input_enums_raise_protocol_error(
        tmp_path, field, value):
    root = _root(tmp_path)
    with pytest.raises(chain_protocol.ChainProtocolError):
        chain_protocol.normalize_chain(
            {"input": {field: value}}, root=root)


def test_unknown_input_and_slot_fields_survive_read_but_not_canonical_write(
        tmp_path):
    root = _root(tmp_path)
    candidate = {
        "external_note": "keep on read",
        "input": {"source": "instrument", "extra_input": "keep on read"},
        "slots": [{"path": None, "extra_slot": "keep on read"}],
    }
    normalized = chain_protocol.normalize_chain(candidate, root=root)
    assert normalized["external_note"] == "keep on read"
    assert normalized["input"]["extra_input"] == "keep on read"
    assert normalized["slots"][0]["extra_slot"] == "keep on read"

    chain_file = root / "data" / "live_chain.json"
    chain_protocol.write_chain_file(chain_file, candidate, root=root)
    stored = json.loads(chain_file.read_text())
    assert stored["external_note"] == "keep on read"
    assert stored["input"] == {
        "source": "instrument", "file": None,
        "state": "stopped", "loop": False,
    }
    assert stored["slots"] == [{"path": None}]


def test_missing_file_input_is_preserved_and_written_relative(tmp_path):
    root = _root(tmp_path)
    missing = "data/dry_inputs/not-downloaded.wav"
    got = chain_protocol.normalize_chain(
        {"slots": [], "input": {"source": "file", "file": missing}}, root=root
    )
    assert got["input"]["file"] == str(root / missing)

    chain_file = root / "data" / "live_chain.json"
    chain_protocol.write_chain_file(
        chain_file, {"slots": [], "input": {"source": "file", "file": missing}},
        root=root,
    )
    stored = json.loads(chain_file.read_text())
    assert stored["input"]["file"] == missing


def test_atomic_replace_failure_keeps_previous_chain(tmp_path, monkeypatch):
    root = _root(tmp_path)
    chain_file = root / "data" / "live_chain.json"
    chain_protocol.write_chain_file(
        chain_file, {"slots": [{"path": "data/tones/amp.nam"}]}, root=root
    )
    before = chain_file.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(chain_protocol.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        chain_protocol.write_chain_file(
            chain_file, {"slots": [{"path": "data/tones/cab.wav"}]}, root=root
        )

    assert chain_file.read_bytes() == before
    assert not list(chain_file.parent.glob(".*.tmp"))


def test_compare_and_swap_rejects_external_update_without_overwrite(tmp_path):
    root = _root(tmp_path)
    chain_file = root / "data" / "live_chain.json"
    first = chain_protocol.write_chain_file(
        chain_file, {"slots": []}, root=root)
    expected_fingerprint = chain_protocol.file_fingerprint(chain_file)
    expected_revision = first["revision"]

    chain_file.write_text(json.dumps({"slots": [], "gain": 2.0}))
    external_bytes = chain_file.read_bytes()

    with pytest.raises(chain_protocol.ChainFileConflict):
        chain_protocol.write_chain_file(
            chain_file,
            {"slots": [{"path": "data/tones/amp.nam"}]},
            root=root,
            expected_fingerprint=expected_fingerprint,
            expected_revision=expected_revision,
            revision=expected_revision + 1,
        )

    assert chain_file.read_bytes() == external_bytes


def test_prepared_revision_must_follow_cas_base(tmp_path):
    root = _root(tmp_path)
    chain_file = root / "data" / "live_chain.json"
    first = chain_protocol.write_chain_file(
        chain_file, {"slots": []}, root=root)
    fingerprint = chain_protocol.file_fingerprint(chain_file)

    with pytest.raises(chain_protocol.ChainFileConflict):
        chain_protocol.write_chain_file(
            chain_file,
            first,
            root=root,
            expected_fingerprint=fingerprint,
            expected_revision=first["revision"],
            revision=first["revision"],
        )
