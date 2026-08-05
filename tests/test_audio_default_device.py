"""REQ-005: the INPUT/OUTPUT pickers offer "System Default" (auto-detect).

The engine resolves an absent --in/--out flag to PortAudio's default device,
so the first pick of each picker carries the empty value and switching back to
it must forward DeviceChanged(kind, "") so the engine restarts on defaults.
"""
import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Select

from tui.panels import DEFAULT_DEVICE_LABEL, DeviceBar, DeviceChanged
from tui.app import GigBuddyApp
import library


def run(coro):
    return asyncio.run(coro)


class DeviceHost(App):
    """Mounts a DeviceBar and records the DeviceChanged messages it posts."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__()
        self.bar = DeviceBar(*args, **kwargs)
        self.changes: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield self.bar

    def on_device_changed(self, event: DeviceChanged) -> None:
        self.changes.append((event.kind, event.name))


def test_first_pick_is_system_default_and_blank_selection_kept():
    async def scenario():
        app = DeviceHost(ins=["USB Mic"], outs=["USB Out"], cur_in="",
                         cur_out="USB Out")
        async with app.run_test() as pilot:
            sel_in = app.query_one("#dev-in", Select)
            sel_out = app.query_one("#dev-out", Select)
            # System Default is the first pick of both pickers, value ""
            assert sel_in._options[0] == (DEFAULT_DEVICE_LABEL, "")
            assert sel_out._options[0] == (DEFAULT_DEVICE_LABEL, "")
            assert [v for _, v in sel_in._options][1:] == ["USB Mic"]
            # a blank current value stays on System Default instead of jumping
            # to the first concrete device
            assert sel_in.value == ""
            assert sel_out.value == "USB Out"

    run(scenario())


def test_no_devices_keeps_none_placeholder():
    async def scenario():
        app = DeviceHost(ins=[], outs=[])
        async with app.run_test() as pilot:
            sel_in = app.query_one("#dev-in", Select)
            assert sel_in._options == [("(none)", "")]
            assert sel_in.value == ""

    run(scenario())


def test_switching_back_to_system_default_posts_empty_value():
    async def scenario():
        app = DeviceHost(ins=["USB Mic", "Built-in"], outs=["USB Out"])
        async with app.run_test() as pilot:
            sel_in = app.query_one("#dev-in", Select)
            # user picks a concrete device...
            sel_in.value = "USB Mic"
            await pilot.pause()
            assert app.changes == [("in", "USB Mic")]
            # ...then switches back to System Default: "" is forwarded, not dropped
            sel_in.value = ""
            await pilot.pause()
            assert app.changes[-1] == ("in", "")
            # repeated selection of the same default is not re-sent
            sel_in.value = ""
            await pilot.pause()
            assert len(app.changes) == 2

    run(scenario())


def test_set_devices_keeps_system_default_selection():
    async def scenario():
        app = DeviceHost(ins=["USB Mic"], outs=["USB Out"])
        async with app.run_test() as pilot:
            sel_in = app.query_one("#dev-in", Select)
            sel_in.value = "USB Mic"  # user picked a concrete device
            await pilot.pause()
            # engine re-enumeration must not bounce the pick back to USB Mic
            app.bar.set_devices(["USB Mic", "Built-in"], ["USB Out"],
                                cur_in="", cur_out="USB Out")
            await pilot.pause()
            assert sel_in.value == ""
            assert sel_in._options[0] == (DEFAULT_DEVICE_LABEL, "")

    run(scenario())


def test_app_records_empty_device_as_default(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "DB_FILE", tmp_path / "gigbuddy.db")
    monkeypatch.setattr(library, "CHAIN_FILE", tmp_path / "live_chain.json")
    monkeypatch.setattr("tui.app.live.CHAIN_FILE", tmp_path / "live_chain.json")
    model = tmp_path / "amp.nam"
    model.write_bytes(b"amp")
    library.chain_set({"model": str(model), "gain": 0.8, "master": 1.0})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            # select a concrete device, then fall back to the system default
            app.on_device_changed(DeviceChanged("in", "USB Mic"))
            app.on_device_changed(DeviceChanged("out", "USB Out"))
            app.on_device_changed(DeviceChanged("in", ""))
            app.on_device_changed(DeviceChanged("out", ""))
            await pilot.pause()
            assert app._dev_in == ""
            assert app._dev_out == ""

    run(scenario())
