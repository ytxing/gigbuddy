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
    got = chain_protocol.normalize_chain(
        {"slots": [], "model": "data/tones/amp.nam"}, root=root
    )
    assert got["slots"] == []
    with pytest.raises(chain_protocol.ChainProtocolError):
        chain_protocol.normalize_chain({"slots": None}, root=root)


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


def test_slots_are_limited_and_unknown_slot_fields_are_removed(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(chain_protocol.ChainProtocolError):
        chain_protocol.normalize_chain(
            {"slots": [{"path": None}] * 7}, root=root
        )
    got = chain_protocol.normalize_chain(
        {"slots": [{"path": None, "label": "AMP"}]}, root=root
    )
    assert got["slots"] == [{"path": None}]


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
