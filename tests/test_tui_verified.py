"""Background author-verification: opening a tone detail for an unknown author
probes the TONE3000 website (mirrored via tone3000.verify_username) and
re-renders the summary with the verified checkmark when it lands, without
clobbering a newer selection."""
import asyncio
import threading
import time

import library

from tui.app import GigBuddyApp
from tui.panels import DetailPane


def run(coro):
    return asyncio.run(coro)


def test_unknown_author_verified_badge_appears_after_probe(monkeypatch):
    """Opening a detail for an author outside the mirror list probes once and
    shows the ✓ when the probe succeeds."""
    tone = {"id": 10, "title": "Plexi", "gear": "amp",
            "username": "brandnewauthor", "downloads_count": 1, "models": []}
    calls: list[str] = []

    def fake_verify(name):
        calls.append(name)
        # mimic the real function's side effect: a True verdict persists
        # into the mirror cache (without touching data/verified_users.json)
        library.tone3000.verified_users().add(name)
        return True  # tone3000.com says this author is verified

    monkeypatch.setattr("tui.library_panel.library.list_tones",
                        lambda **kw: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("library.tone3000.verify_username", fake_verify)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            pane = app.query_one(DetailPane)
            pane.show(tone)
            await pilot.pause(0.1)
            # probe fired on a background thread for the unknown author
            assert calls == ["brandnewauthor"]
            await pilot.pause(0.4)  # thread answers + call_from_thread lands
            assert "✓" in str(pane._summary.render())

    run(scenario())


def test_known_verified_author_skips_probe(monkeypatch):
    """Authors already in the mirror list render the badge without a network
    probe."""
    tone = {"id": 10, "title": "Plexi", "gear": "amp",
            "username": "amalgamaudio", "downloads_count": 1, "models": []}
    calls: list[str] = []

    def fake_verify(name):
        calls.append(name)
        return True

    monkeypatch.setattr("tui.library_panel.library.list_tones",
                        lambda **kw: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("library.tone3000.verify_username", fake_verify)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            pane = app.query_one(DetailPane)
            pane.show(tone)
            await pilot.pause(0.5)
            assert calls == [], "mirror hit must not re-probe"
            assert "✓" in str(pane._summary.render())

    run(scenario())


def test_creator_verified_badge_is_parsed_as_markup(monkeypatch):
    """Creator titles must render the badge, not expose Rich tags literally."""
    monkeypatch.setattr(
        "library.tone3000.verified_users",
        lambda: {"deathblossomaudio"},
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one(DetailPane)
            pane._set_marquee(pane._creator_title("deathblossomaudio"),
                              markup=True)
            await pilot.pause()
            rendered = pane._marquee.render()
            assert rendered.plain == "@deathblossomaudio ✓"
            assert "[b $success]" not in rendered.plain
            assert any(span.start == rendered.plain.index("✓")
                       for span in rendered.spans)

    run(scenario())


def test_late_probe_answer_does_not_cover_newer_selection(monkeypatch):
    """A slow probe for tone A must not overwrite the summary when the user
    already moved on to tone B."""
    tone_a = {"id": 10, "title": "A", "gear": "amp",
              "username": "aaa", "downloads_count": 1, "models": []}
    tone_b = {"id": 11, "title": "B", "gear": "cab",
              "username": "bbb", "downloads_count": 1, "models": []}

    def fake_slow_verify(name):
        time.sleep(0.4)
        library.tone3000.verified_users().add(name)
        return True

    monkeypatch.setattr("tui.library_panel.library.list_tones",
                        lambda **kw: [tone_a])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone_a)
    monkeypatch.setattr("library.tone3000.verify_username", fake_slow_verify)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            pane = app.query_one(DetailPane)
            pane.show(tone_a)
            pane.show(tone_b)  # user switches before the probe answers
            await pilot.pause(0.9)
            text = str(pane._summary.render())
            assert "✓" in text, "current tone's probe must still land"
            assert "@bbb" in text
            assert "@aaa" not in text, "stale probe must not overwrite"

    run(scenario())


def test_failed_probe_keeps_plain_summary(monkeypatch):
    """A probe failure (offline/blocked) leaves the summary without a badge
    and does not crash the pane."""
    tone = {"id": 10, "title": "Plexi", "gear": "amp",
            "username": "flakyauthor", "downloads_count": 1, "models": []}

    def fake_fail(name):
        return None  # fetch failed

    monkeypatch.setattr("tui.library_panel.library.list_tones",
                        lambda **kw: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("library.tone3000.verify_username", fake_fail)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            pane = app.query_one(DetailPane)
            pane.show(tone)
            await pilot.pause(0.6)
            assert "✓" not in str(pane._summary.render())

    run(scenario())


def test_verified_cache_updates_loaded_library_author_cell(monkeypatch):
    """A successful detail probe also badges the already loaded library row."""
    tone = {"id": 10, "title": "Plexi", "gear": "amp",
            "username": "brandnewauthor", "downloads_count": 1,
            "a2_models_count": 1, "description": "desc"}
    verified: set[str] = set()
    started = threading.Event()
    release = threading.Event()

    def fake_verify(name):
        started.set()
        release.wait(2)
        verified.add(name)
        return True

    monkeypatch.setattr("tui.library_panel.library.tone3000.search",
                        lambda *args, **kwargs: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("library.tone3000.verified_users",
                        lambda: verified)
    monkeypatch.setattr("library.tone3000.verify_username", fake_verify)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            table = app.query_one("#lib-table-tone")
            assert table.get_cell("remote:10", "author") == "@brandnewauthor"
            if not started.is_set():
                app.query_one(DetailPane).show(tone)
            await pilot.pause(0.1)
            assert started.is_set()
            release.set()
            await pilot.pause(0.5)
            assert table.get_cell("remote:10", "author") == "@brandnewauthor ✓"

    run(scenario())
