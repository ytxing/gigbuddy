"""Headless screenshots of modal screens + focused node state."""
import asyncio

from tui.app import GigBuddyApp
from tui.library_panel import LibraryTable
from tui.panels import NodeWidget


async def main() -> None:
    app = GigBuddyApp(spawn_engine=False)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.5)
        # focused amp node
        next(n for n in app.query(NodeWidget) if n.kind == "amp").focus()
        await pilot.pause(0.2)
        app.save_screenshot(".scratch/tui_node_focus.svg")
        # tone action modal (Enter on the library table)
        app.query_one(LibraryTable).focus()
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.3)
        app.save_screenshot(".scratch/tui_action.svg")
        await pilot.press("escape")
        await pilot.pause(0.2)
        # preset picker modal
        await pilot.press("p")
        await pilot.pause(0.3)
        app.save_screenshot(".scratch/tui_presets.svg")
        print("done")


asyncio.run(main())
