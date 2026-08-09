"""Fast, Textual-free tests for the v0.2 ordered Slot state seam."""

from copy import deepcopy

import pytest

from tui.chain_state import (
    ChainState,
    ChainStateError,
    CommitReceipt,
    PreparedCommit,
    SlotOverlay,
    SlotStatus,
    chain_fingerprint,
)
from tui.presets import PresetEditModal


def _state(*paths: str | None) -> ChainState:
    return ChainState({
        "slots": [{"path": path} for path in paths],
        "gain": 1.0,
        "master": 1.0,
        "quality": 1.0,
        "revision": 3,
    })


def test_preset_edit_modal_captures_optional_updated_at_cas_token():
    current = PresetEditModal({
        "name": "current",
        "id": 7,
        "updated_at": "2026-08-08T00:00:00+00:00",
        "chain": {},
    })
    legacy = PresetEditModal({"name": "legacy", "chain": {}})

    assert current._preset_updated_at == "2026-08-08T00:00:00+00:00"
    assert current._preset_has_updated_at is True
    assert legacy._preset_updated_at is None
    assert legacy._preset_has_updated_at is False


def test_bypass_restore_and_different_file_are_target_local():
    state = _state("a.nam", "shared.nam")
    state.focus_slot(0)

    assert state.toggle_bypass(0) is True
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.slot(0).candidate == "a.nam"
    assert state.slot(1).status is SlotStatus.ACTIVE

    state.load_file(0, "b.nam")
    assert state.slot(0).status is SlotStatus.ACTIVE
    assert state.slot(0).path == "b.nam"
    assert state.slot(0).candidate is None

    state.toggle_bypass(0)
    state.load_file(0, "b.nam")
    assert state.slot(0).status is SlotStatus.ACTIVE
    assert state.slot(0).path == "b.nam"


def test_target_follows_reorder_and_delete_prefers_new_position():
    state = _state("a", "b", "c")
    state.focus_slot(1)
    assert state.move_slot(1, -1) is True
    assert state.target_index == 0
    assert state.slot(0).path == "b"

    state.delete_slot(0)
    assert state.target_index == 0
    assert state.slot(0).path == "a"  # new item at the deleted position

    state.delete_slot(0)
    assert state.target_index == 0
    assert state.slot(0).path == "c"
    state.delete_slot(0)
    assert state.slot_count == 0
    assert state.target_index is None


def test_slot_gains_follow_reorder_and_survive_bypass():
    state = _state("a.nam", "b.wav")
    state.set_slot_gain(0, "input_gain_db", 3.5)
    state.set_slot_gain(0, "output_gain_db", -2.0)
    state.focus_slot(0)

    assert state.move_slot(0, 1) is True
    assert state.slot(1).input_gain_db == 3.5
    assert state.slot(1).output_gain_db == -2.0
    assert state.toggle_bypass(1) is True
    assert state.slot(1).status is SlotStatus.BYPASS
    assert state.to_chain()["slots"][1] == {
        "path": None,
        "candidate": "a.nam",
        "input_gain_db": 3.5,
        "output_gain_db": -2.0,
    }

    with pytest.raises(ChainStateError):
        state.set_slot_gain(1, "output_gain_db", 24.1)


def test_apply_candidate_refreshes_gains_when_paths_are_unchanged():
    state = _state("a.nam")
    state.apply_candidate({
        "slots": [{"path": "a.nam", "input_gain_db": 2.5,
                    "output_gain_db": -1.5}],
    })

    assert state.slot(0).input_gain_db == 2.5
    assert state.slot(0).output_gain_db == -1.5


def test_last_target_delete_falls_back_to_previous_slot():
    state = _state("a", "b", "c")
    state.focus_slot(2)
    state.delete_slot(2)
    assert state.target_index == 1
    assert state.slot(1).path == "b"


def test_duplicate_paths_do_not_share_candidate_or_target():
    state = _state("same.nam", "same.nam")
    state.focus_slot(0)
    state.toggle_bypass(0)
    state.focus_slot(1)

    assert state.target_index == 1
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.slot(0).candidate == "same.nam"
    assert state.slot(1).status is SlotStatus.ACTIVE
    assert state.slot(1).candidate is None

    state.focus_slot(0)
    state.move_slot(0, 1)
    assert state.slot(0).status is SlotStatus.ACTIVE
    assert state.slot(1).status is SlotStatus.BYPASS
    assert state.target_index == 1


def test_whole_replacement_clears_target_and_candidates():
    state = _state("a")
    state.focus_slot(0)
    state.toggle_bypass(0)

    state.replace_chain({"slots": [{"path": None}], "revision": 9})

    assert state.target_index is None
    assert state.slot(0).status is SlotStatus.EMPTY
    assert state.slot(0).candidate is None
    assert state.managed_fingerprint is None


def test_same_tui_fingerprint_and_revision_preserve_candidates():
    state = _state("a")
    state.focus_slot(0)
    state.toggle_bypass(0)
    state.mark_managed_write("tui-write", 4)

    polled = state.to_chain()
    assert state.reconcile(polled, fingerprint="tui-write", revision=4) is False
    assert state.target_index == 0
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.slot(0).candidate == "a"


def test_exact_managed_poll_rehydrates_a_persisted_candidate():
    state = _state("a")
    state.toggle_bypass(0)
    state.mark_managed_write("old-write", 4)

    # Simulate the race window: an earlier poll already discarded the local
    # candidate, then the delayed UI callback identifies the same write.
    state.reconcile(
        {"slots": [{"path": None}], "revision": 4},
        fingerprint="external-write", revision=4)
    assert state.slot(0).status is SlotStatus.EMPTY

    incoming = {"slots": [{"path": None, "candidate": "a"}], "revision": 5}
    state.mark_managed_write("new-write", 5)
    assert state.reconcile(
        incoming, fingerprint="new-write", revision=5) is False
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.slot(0).candidate == "a"


def test_invalid_poll_keeps_last_valid_state_and_sets_recoverable_error():
    state = _state("a")
    state.focus_slot(0)
    state.toggle_bypass(0)
    before = state.snapshot()

    assert state.reconcile({"slots": [{"path": 17}]}) is False
    assert state.target_index == before.target_index
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.slot(0).candidate == "a"
    assert state.chain_error == "slot 0 path must be a string or null"

    state.reconcile(state.to_chain(), fingerprint="external", revision=5)
    assert state.chain_error is None
    # 本会话从未写过链（无 managed 记录）：磁盘的 bypass candidate 是
    # 持久状态，poll 保留 BYPASS——只有 TUI 写过之后的未知改写才降级。
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.slot(0).candidate == "a"


@pytest.mark.parametrize(
    "fingerprint,revision",
    [("external-write", 5), (None, 4), ("tui-write", None)],
)
def test_external_or_unknown_poll_downgrades_null_to_empty(fingerprint, revision):
    state = _state("a")
    state.focus_slot(0)
    state.toggle_bypass(0)
    state.mark_managed_write("tui-write", 4)
    polled = state.to_chain()

    assert state.reconcile(polled, fingerprint=fingerprint, revision=revision) is True
    assert state.target_index is None
    assert state.slot(0).status is SlotStatus.EMPTY
    assert state.slot(0).candidate is None


def test_loading_and_error_are_overlays_over_the_base_state():
    state = _state("a")
    loading = state.begin_loading(0, "load-a")
    assert loading.status is SlotStatus.ACTIVE
    assert loading.operation_id == "load-a"
    assert state.slot(0).overlay is SlotOverlay.LOADING
    assert state.slot(0).display_state == "loading"
    assert state.begin_loading(0, "load-b").operation_id == "load-a"
    assert state.finish_loading(0, "stale") is False
    assert state.finish_loading(0, "load-a", error="decoder failed") is True
    assert state.set_error(0, "decoder failed").status is SlotStatus.ACTIVE
    assert state.slot(0).overlay is SlotOverlay.ERROR
    assert state.slot(0).error == "decoder failed"
    assert state.clear_overlay(0).overlay is None
    assert state.slot(0).status is SlotStatus.ACTIVE


class _Adapter:
    def __init__(self, initial, *, fail_at=None):
        self.file = deepcopy(initial)
        self.runtime = {"paths": [slot["path"] for slot in initial["slots"]]}
        self.fail_at = fail_at
        self.calls = []

    def snapshot_runtime(self):
        self.calls.append("snapshot")
        return deepcopy(self.runtime)

    def prepare(self, chain):
        self.calls.append(("prepare", deepcopy(chain)))
        if self.fail_at == "prepare":
            raise RuntimeError("prepare failed")
        prepared_chain = deepcopy(chain)
        prepared_chain["revision"] = 4
        return PreparedCommit(
            prepared_chain,
            {"paths": [slot["path"] for slot in prepared_chain["slots"]]},
            4,
        )

    def write_file(self, chain):
        self.calls.append(("write", deepcopy(chain)))
        if self.fail_at == "file":
            raise RuntimeError("file failed")
        self.file = deepcopy(chain)
        return CommitReceipt("tui-write", 4)

    def apply_runtime(self, prepared):
        self.calls.append(("runtime", deepcopy(prepared.runtime)))
        if self.fail_at == "runtime":
            raise RuntimeError("runtime failed")
        self.runtime = deepcopy(prepared.runtime)

    def restore_file(self, chain):
        self.calls.append("restore-file")
        self.file = deepcopy(chain)

    def restore_runtime(self, snapshot):
        self.calls.append("restore-runtime")
        self.runtime = deepcopy(snapshot)


def test_commit_prepares_full_candidate_then_records_managed_write():
    state = _state("a", None)
    adapter = _Adapter(state.to_chain())

    committed = state.commit(adapter, lambda draft: draft.toggle_bypass(0))

    # 提交链持久化 bypass 的恢复候选（v0.2.14 语义：重启后可恢复原模型）
    assert committed["slots"] == [
        {"path": None, "candidate": "a"}, {"path": None}]
    assert adapter.calls[1][0] == "prepare"
    assert adapter.calls[1][1]["revision"] == 3
    assert adapter.calls[2][0] == "write"
    assert adapter.calls[2][1]["revision"] == 4
    assert adapter.calls[3][0] == "runtime"
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.slot(0).candidate == "a"
    assert state.managed_fingerprint == "tui-write"
    assert state.managed_revision == 4

    state.reconcile(adapter.file, fingerprint="tui-write", revision=4)
    assert state.slot(0).status is SlotStatus.BYPASS


def test_prepare_failure_does_not_write_or_change_state():
    state = _state("a")
    before = state.snapshot()
    adapter = _Adapter(state.to_chain(), fail_at="prepare")

    with pytest.raises(RuntimeError, match="prepare failed"):
        state.commit(adapter, lambda draft: draft.toggle_bypass(0))

    assert state.snapshot() == before
    assert adapter.file == before.chain
    assert "write" not in adapter.calls
    assert "restore-file" not in adapter.calls


@pytest.mark.parametrize("fail_at", ["file", "runtime"])
def test_file_or_runtime_failure_restores_file_runtime_and_state(fail_at):
    state = _state("a")
    state.focus_slot(0)
    before = state.snapshot()
    adapter = _Adapter(state.to_chain(), fail_at=fail_at)
    previous_runtime = deepcopy(adapter.runtime)

    with pytest.raises(RuntimeError, match=f"{fail_at} failed"):
        state.commit(adapter, lambda draft: draft.toggle_bypass(0))

    assert state.snapshot() == before
    assert adapter.file == before.chain
    assert adapter.runtime == previous_runtime
    assert "restore-file" in adapter.calls
    assert "restore-runtime" in adapter.calls


def test_invalid_transaction_or_index_is_rejected_without_textual_app():
    state = _state("a")
    with pytest.raises(ChainStateError):
        state.move_slot(0, 2)
    with pytest.raises(ChainStateError):
        state.load_file(0, "")
    with pytest.raises(ChainStateError):
        state.commit(_Adapter(state.to_chain()))
    with pytest.raises(ChainStateError, match="target slot"):
        state.load_target_file("b")


@pytest.mark.parametrize(
    "invalid",
    [{"slots": [{"path": "a"}], "revision": "4"},
     {"slots": [{"path": "a"}], "quality": 2.0},
     {"slots": [{"path": "a"}], "mute": "false"},
     {"slots": [{}]}],
)
def test_invalid_poll_fields_keep_last_valid_state(invalid):
    state = _state("a")
    state.focus_slot(0)
    state.toggle_bypass(0)
    assert state.reconcile(invalid) is False
    assert state.target_index == 0
    assert state.slot(0).status is SlotStatus.BYPASS
    assert state.chain_error


def test_missing_slot_path_is_rejected_at_construction():
    with pytest.raises(ChainStateError, match="slot 0 must contain path"):
        ChainState({"slots": [{}]})


def test_canonical_fingerprint_is_stable_for_key_order():
    first = {"slots": [{"path": "a"}], "gain": 1.0, "revision": 1}
    second = {"revision": 1, "gain": 1.0, "slots": [{"path": "a"}]}
    assert chain_fingerprint(first) == chain_fingerprint(second)
