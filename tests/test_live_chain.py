import hashlib
import json

import pytest

from tui import live
from tui.app import _ManagedChainAdapter
from tui.chain_state import ChainState, SlotStatus


@pytest.fixture
def chain_file(tmp_path, monkeypatch):
    path = tmp_path / "live_chain.json"
    monkeypatch.setattr(live, "CHAIN_FILE", path)
    return path


def write_chain_file(path, **values):
    payload = {"slots": [], **values}
    path.write_text(json.dumps(payload))


def test_read_chain_returns_a_valid_chain(chain_file):
    write_chain_file(chain_file, gain=0.5)

    chain = live.read_chain()

    assert chain["slots"] == []
    assert chain["gain"] == 0.5
    assert live.consume_chain_error() is None


def test_chain_snapshot_pairs_chain_with_its_exact_file_fingerprint(chain_file):
    payload = b'{"slots": [], "gain": 0.5, "revision": 4}\n'
    chain_file.write_bytes(payload)

    chain, fingerprint = live.read_chain_snapshot()

    assert chain["gain"] == 0.5
    assert chain["revision"] == 4
    assert fingerprint == hashlib.sha256(payload).hexdigest()


def test_corrupt_chain_keeps_the_last_valid_value(chain_file):
    write_chain_file(chain_file, gain=0.5)
    previous = live.read_chain()
    chain_file.write_text("{not valid json")

    assert live.read_chain() == previous
    assert "invalid chain file" in live.consume_chain_error()


def test_chain_error_is_one_shot_and_cleared_by_valid_read(chain_file):
    write_chain_file(chain_file)
    previous = live.read_chain()
    chain_file.write_text("{not valid json")

    assert live.read_chain() == previous
    assert live.consume_chain_error()
    assert live.consume_chain_error() is None
    assert live.read_chain() == previous
    assert live.consume_chain_error() is None

    chain_file.write_text("{different invalid json")
    assert live.read_chain() == previous
    write_chain_file(chain_file, gain=0.7)
    assert live.read_chain()["gain"] == 0.7
    assert live.consume_chain_error() is None


def test_chain_cache_isolated_by_chain_file_path(chain_file, tmp_path, monkeypatch):
    write_chain_file(chain_file, gain=0.5)
    assert live.read_chain()["gain"] == 0.5

    other_path = tmp_path / "other-live-chain.json"
    other_path.write_text("{not valid json")
    monkeypatch.setattr(live, "CHAIN_FILE", other_path)

    assert live.read_chain() == {}
    assert live.consume_chain_error()


def test_runtime_wait_ignores_stale_rejected_report_and_level_ticks(
        tmp_path, monkeypatch):
    level_file = tmp_path / "level.json"
    monkeypatch.setattr(live, "LEVEL_FILE", level_file)
    level_file.write_text(json.dumps({
        "in": 0.1,
        "out": 0.2,
        "runtime_revision": 7,
        "runtime_status": "rejected",
        "runtime_transaction_id": "old-7",
        "runtime_ack_seq": 4,
    }))

    with pytest.raises(TimeoutError):
        live.wait_for_runtime_revision(
            8,
            transaction_id="new-8",
            previous=(live.read_runtime_report(), live.level_file_fingerprint()),
            timeout=0.02,
            poll_interval=0.001,
        )


def test_runtime_wait_requires_a_fresh_ack_for_same_revision(tmp_path, monkeypatch):
    level_file = tmp_path / "level.json"
    monkeypatch.setattr(live, "LEVEL_FILE", level_file)
    previous = {
        "revision": 8,
        "status": "applied",
        "transaction_id": "old-8",
        "ack_seq": 10,
    }
    level_file.write_text(json.dumps({
        "runtime_revision": 8,
        "runtime_status": "applied",
        "runtime_transaction_id": "old-8",
        "runtime_ack_seq": 10,
    }))

    with pytest.raises(TimeoutError):
        live.wait_for_runtime_revision(
            8,
            transaction_id="new-8",
            previous=(previous, None),
            timeout=0.02,
            poll_interval=0.001,
        )


def test_runtime_wait_accepts_matching_fresh_ack(tmp_path, monkeypatch):
    level_file = tmp_path / "level.json"
    monkeypatch.setattr(live, "LEVEL_FILE", level_file)
    previous = {
        "revision": 8,
        "status": "rejected",
        "transaction_id": "old-8",
        "ack_seq": 10,
    }
    level_file.write_text(json.dumps({
        "runtime_revision": 8,
        "runtime_status": "applied",
        "runtime_transaction_id": "new-8",
        "runtime_ack_seq": 11,
    }))

    live.wait_for_runtime_revision(
        8,
        transaction_id="new-8",
        previous=(previous, None),
        timeout=0.02,
        poll_interval=0.001,
    )


def test_runtime_prepare_round_trip_uses_transaction_identity(tmp_path, monkeypatch):
    control_file = tmp_path / "control.json"
    reply_file = tmp_path / "control.reply.json"
    monkeypatch.setattr(live, "CONTROL_FILE", control_file)
    monkeypatch.setattr(live, "CONTROL_REPLY_FILE", reply_file)
    monkeypatch.setattr(live, "wait_for_engine_ready", lambda **_: "session-1")
    reply_file.write_text(json.dumps({
        "status": "prepared",
        "session_id": "session-1",
        "transaction_id": "tx-1",
        "revision": 3,
    }))

    live.request_runtime_prepare(
        {"slots": [], "revision": 3}, "tx-1", timeout=0.02)

    request = json.loads(control_file.read_text())
    assert request["operation"] == "prepare"
    assert request["transaction_id"] == "tx-1"
    assert request["candidate"]["revision"] == 3


def test_engine_ready_accepts_prepare_reply_for_the_same_session(tmp_path, monkeypatch):
    reply_file = tmp_path / "control.reply.json"
    monkeypatch.setattr(live, "CONTROL_REPLY_FILE", reply_file)
    reply_file.write_text(json.dumps({
        "status": "prepared",
        "session_id": "session-1",
        "transaction_id": "tx-1",
        "revision": 3,
    }))

    assert live.wait_for_engine_ready(timeout=0.02, poll_interval=0.001) == "session-1"


def test_managed_adapter_rejects_a_stale_ui_chain_base(chain_file):
    write_chain_file(chain_file, revision=2)

    with pytest.raises(live.chain_protocol.ChainFileConflict):
        _ManagedChainAdapter(
            object(), expected_chain={"slots": [], "revision": 1})


def test_non_managed_write_rejects_a_stale_chain_base(chain_file):
    first = live.write_chain({"slots": []})
    fingerprint = live.chain_file_fingerprint()
    chain_file.write_text(json.dumps({"slots": [], "gain": 2.0,
                                      "revision": first["revision"]}))
    external = chain_file.read_bytes()

    with pytest.raises(live.chain_protocol.ChainFileConflict):
        live.write_chain(
            {"slots": [{"path": None}], "gain": 0.5},
            expected_fingerprint=fingerprint,
            expected_revision=first["revision"],
        )
    assert chain_file.read_bytes() == external


def test_managed_adapter_accepts_candidate_rehydrated_from_own_poll(
        tmp_path, monkeypatch):
    tones = tmp_path / "data" / "tones"
    tones.mkdir(parents=True)
    (tones / "amp.nam").write_bytes(b"amp")
    chain_file = tmp_path / "data" / "live_chain.json"
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "CHAIN_FILE", chain_file)
    chain_file.write_text(json.dumps({
        "slots": [{"path": None, "candidate": "data/tones/amp.nam"}],
        "gain": 1.0,
        "master": 1.35,
        "quality": 1.0,
        "revision": 6,
    }))

    state = ChainState(live.read_chain())
    state.reconcile(
        {"slots": [{"path": None}], "gain": 1.0,
         "master": 1.35, "quality": 1.0, "revision": 5},
        fingerprint="external-write", revision=5)
    assert state.slot(0).status is SlotStatus.EMPTY

    state.mark_managed_write("own-write", 6)
    assert state.reconcile(
        live.read_chain(), fingerprint="own-write", revision=6) is False
    assert state.slot(0).status is SlotStatus.BYPASS

    _ManagedChainAdapter(object(), expected_chain=state.to_chain())


def test_runtime_prepare_rejection_does_not_touch_chain_file(tmp_path, monkeypatch):
    control_file = tmp_path / "control.json"
    reply_file = tmp_path / "control.reply.json"
    chain_file = tmp_path / "live_chain.json"
    chain_file.write_text('{"slots": [], "revision": 2}\n')
    monkeypatch.setattr(live, "CONTROL_FILE", control_file)
    monkeypatch.setattr(live, "CONTROL_REPLY_FILE", reply_file)
    monkeypatch.setattr(live, "wait_for_engine_ready", lambda **_: "session-1")
    reply_file.write_text(json.dumps({
        "status": "rejected",
        "session_id": "session-1",
        "transaction_id": "tx-2",
        "revision": 3,
        "error": "bad NAM",
    }))

    with pytest.raises(RuntimeError, match="bad NAM"):
        live.request_runtime_prepare(
            {"slots": [], "revision": 3}, "tx-2", timeout=0.02)
    assert chain_file.read_text() == '{"slots": [], "revision": 2}\n'


def test_runtime_wait_accepts_ack_after_engine_restart(tmp_path, monkeypatch):
    level_file = tmp_path / "level.json"
    monkeypatch.setattr(live, "LEVEL_FILE", level_file)
    level_file.write_text(json.dumps({
        "runtime_revision": 4,
        "runtime_status": "applied",
        "runtime_transaction_id": "new-5",
        "runtime_session_id": "session-new",
        "runtime_ack_seq": 1,
    }))

    live.wait_for_runtime_revision(
        4,
        transaction_id="new-5",
        previous=({
            "revision": 3,
            "status": "applied",
            "transaction_id": "old-4",
            "session_id": "session-old",
            "ack_seq": 99,
        }, None),
        timeout=0.02,
        poll_interval=0.001,
    )


def test_runtime_wait_accepts_expected_session_after_ack_sequence_reset(
        tmp_path, monkeypatch):
    level_file = tmp_path / "level.json"
    monkeypatch.setattr(live, "LEVEL_FILE", level_file)
    level_file.write_text(json.dumps({
        "runtime_revision": 4,
        "runtime_status": "applied",
        "runtime_transaction_id": "new-5",
        "runtime_session_id": "session-new",
        "runtime_ack_seq": 1,
    }))

    live.wait_for_runtime_revision(
        4,
        transaction_id="new-5",
        expected_session_id="session-new",
        previous=({
            "revision": 3,
            "status": "applied",
            "transaction_id": "old-4",
            "session_id": "session-old",
            "ack_seq": 99,
        }, None),
        timeout=0.02,
        poll_interval=0.001,
    )


def test_missing_chain_rollback_acknowledges_then_removes_temporary_chain(
        tmp_path, monkeypatch):
    chain_file = tmp_path / "live_chain.json"
    level_file = tmp_path / "level.json"
    monkeypatch.setattr(live, "CHAIN_FILE", chain_file)
    monkeypatch.setattr(live, "LEVEL_FILE", level_file)

    class App:
        @staticmethod
        def _managed_engine_active():
            return True

    adapter = _ManagedChainAdapter(App())
    chain_file.write_text(json.dumps({
        "slots": [], "revision": 1, "_transaction_id": "candidate",
    }))
    adapter._candidate_fingerprint = live.chain_file_fingerprint()
    adapter._file_write_succeeded = True
    adapter._runtime_before = ({
        "revision": None,
        "status": "unknown",
        "transaction_id": None,
        "session_id": "session-1",
        "ack_seq": 3,
    }, None)
    seen = {}

    def prepare(chain, transaction_id, *, timeout):
        seen["prepare"] = {
            "chain": chain,
            "transaction_id": transaction_id,
            "timeout": timeout,
        }
        return "session-rollback"

    monkeypatch.setattr(live, "request_runtime_prepare", prepare)

    def acknowledge(revision, *, transaction_id, previous, timeout,
                    expected_session_id=None):
        seen.update({
            "revision": revision,
            "transaction_id": transaction_id,
            "previous": previous,
            "expected_session_id": expected_session_id,
        })
        temporary = json.loads(chain_file.read_text())
        assert temporary["slots"] == []
        assert temporary["revision"] == 0
        assert temporary["_transaction_id"] == transaction_id

    monkeypatch.setattr(live, "wait_for_runtime_revision", acknowledge)

    adapter.restore_file({})
    adapter.restore_runtime(None)

    assert seen["prepare"]["transaction_id"] == seen["transaction_id"]
    assert seen["prepare"]["chain"]["revision"] == 0
    assert seen["prepare"]["chain"]["_transaction_id"] == seen["transaction_id"]
    assert seen["expected_session_id"] == "session-rollback"
    assert seen["revision"] == 0
    assert seen["transaction_id"]
    assert not chain_file.exists()


def test_existing_chain_rollback_restores_original_bytes_after_ack(
        tmp_path, monkeypatch):
    chain_file = tmp_path / "live_chain.json"
    original = b'{\n  "slots": [],\n  "revision": 4,\n  "vendor": {"keep": true}\n}\n'
    chain_file.write_bytes(original)
    monkeypatch.setattr(live, "CHAIN_FILE", chain_file)

    class App:
        @staticmethod
        def _managed_engine_active():
            return True

    adapter = _ManagedChainAdapter(App())
    chain_file.write_text(json.dumps({
        "slots": [], "revision": 5, "_transaction_id": "candidate",
    }))
    adapter._candidate_fingerprint = live.chain_file_fingerprint()
    adapter._file_write_succeeded = True
    adapter._runtime_before = ({
        # The engine can still report the revision before the current file
        # while a commit is in flight; rollback must target the file base.
        "revision": 3,
        "status": "applied",
        "transaction_id": "old",
        "session_id": "session-1",
        "ack_seq": 8,
    }, None)

    monkeypatch.setattr(
        live, "request_runtime_prepare",
        lambda chain, transaction_id, *, timeout: "session-rollback",
    )

    def acknowledge(revision, *, transaction_id, previous, timeout,
                    expected_session_id=None):
        assert revision == 4
        assert expected_session_id == "session-rollback"
        temporary = json.loads(chain_file.read_text())
        assert temporary["_transaction_id"] == transaction_id
        assert temporary["revision"] == 4

    monkeypatch.setattr(live, "wait_for_runtime_revision", acknowledge)

    adapter.restore_file({})
    adapter.restore_runtime(None)

    assert chain_file.read_bytes() == original
