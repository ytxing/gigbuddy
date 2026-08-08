import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Select

from tui.panels import DEFAULT_DEVICE_LABEL, DeviceBar, DeviceChanged


class DeviceHost(App):
    def __init__(self):
        super().__init__()
        self.changes = []

    def compose(self) -> ComposeResult:
        yield DeviceBar(ins=["USB Mic"], outs=["USB Out"], cur_in="")

    def on_device_changed(self, event: DeviceChanged) -> None:
        self.changes.append((event.kind, event.name))


def test_system_default_is_first_and_can_be_restored():
    async def scenario():
        app = DeviceHost()
        async with app.run_test() as pilot:
            select = app.query_one("#dev-in", Select)
            assert select._options[0] == (DEFAULT_DEVICE_LABEL, "")
            assert select.value == ""

            select.value = "USB Mic"
            await pilot.pause()
            select.value = ""
            await pilot.pause()

            assert app.changes == [("in", "USB Mic"), ("in", "")]

    asyncio.run(scenario())
