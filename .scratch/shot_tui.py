"""Headless screenshot of the GigBuddy TUI (SVG export)."""
import asyncio
import sys

from tui.app import GigBuddyApp


async def main() -> None:
    app = GigBuddyApp(spawn_engine=False)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.5)
        out = sys.argv[1] if len(sys.argv) > 1 else ".scratch/tui.svg"
        app.save_screenshot(out)
        print("saved", out)


asyncio.run(main())
