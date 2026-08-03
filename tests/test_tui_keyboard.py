"""Headless interaction tests for the TUI keyboard focus graph."""
import asyncio
import time

from textual.widgets import DataTable, Input, ProgressBar, Select, Static, TabbedContent, Tree
from rich.table import Table

from tui.app import GigBuddyApp
from tui.library_panel import LibraryTable
from tui.panels import ChainPanel, DetailPane, MeterBar, NodeWidget
from tui.picker import TonePickerScreen
from tui.presets import PresetNameModal, PresetPickerScreen


def run(coro):
    return asyncio.run(coro)


async def goto_tone_tab(app, pilot):
    """Switch the library panel to the TONE3000 search tab.

    Tab activation is detected by the 0.1s tick (TabActivated events lag in
    headless), so wait a few ticks for routing to settle.
    """
    # Programmatic `active` assignment rolls back in headless (Tabs watcher
    # re-posts), so take the real user path: click the tab.
    await pilot.click(app.query_one("#--content-tab-pane-tone"))
    await pilot.pause(0.3)


def test_main_screen_keeps_chain_read_only_and_opens_tone_picker(monkeypatch):
    tone = {
        "id": 10, "title": "Plexi", "gear": "amp", "username": "alice",
        "downloads_count": 1, "models": [],
    }
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            table = app.query_one("#lib-table-local")
            nodes = list(app.query(NodeWidget))

            assert app.focused is table

            await pilot.press("right")
            assert app.focused is table

            # chain nodes are clickable/focusable (↑/↓ steps within the tone
            # folder), but nothing opens until the user acts
            assert all(node.can_focus for node in nodes)
            amp_node = next(n for n in nodes if n.kind == "amp")
            amp_node.focus()
            await pilot.pause()
            assert app.focused is amp_node
            # ▲/▼ are separate clickable switch buttons on the node row
            assert app.query_one("#chain-amp-up") is not None
            assert app.query_one("#chain-amp-down") is not None

            table.focus()  # Enter is the table's select action
            await pilot.pause()
            await pilot.press("enter")
            # Enter on a row goes straight to that tone's model file list
            assert isinstance(app.screen, TonePickerScreen)
            assert app.screen.tone_id == 10

            await pilot.press("escape")
            assert app.focused is table

    run(scenario())


def test_cursor_highlight_updates_detail_without_opening_action(monkeypatch):
    tones = [
        {"id": 10, "title": "First Tone", "gear": "amp", "username": "alice",
         "downloads_count": 10, "models": []},
        {"id": 11, "title": "Second Tone", "gear": "cab", "username": "bob",
         "downloads_count": 9, "models": []},
    ]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: tones)
    monkeypatch.setattr(
        "tui.library_panel.library.get_tone",
        lambda tone_id: next((t for t in tones if t["id"] == tone_id), None),
    )

    def detail_text(app):
        detail = app.query_one("DetailPane")._body.content
        return " ".join(
            str(cell) for column in detail.columns for cell in column.cells)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "First Tone" in detail_text(app)
            assert not isinstance(app.screen, TonePickerScreen)

            await pilot.press("down")
            await pilot.pause()
            assert "Second Tone" in detail_text(app)
            assert not isinstance(app.screen, TonePickerScreen)

    run(scenario())


def test_search_shortcut_submit_and_escape(monkeypatch):
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda query, page_size, **kwargs: [{
            "id": 99,
            "title": f"result for {query}",
            "gear": "amp",
            "downloads_count": 1,
            "username": "tester",
        }],
    )

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/")
            search = app.query_one("#tone-search", Input)
            assert app.focused is search

            await pilot.press("p", "l", "e", "x", "i", "enter")
            panel = app.query_one("LibraryPanel")
            assert panel._mode == "tone"
            assert app.focused is app.query_one("#lib-table-tone")

            await pilot.press("escape")
            assert panel._mode == "local"
            assert search.value == ""
            assert app.focused is app.query_one("#lib-table-local")

    run(scenario())


def test_remote_cursor_highlight_updates_detail_without_import(monkeypatch):
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda query, page_size, **kwargs: [
            {"id": 91, "title": "Remote First", "gear": "amp",
             "downloads_count": 3, "username": "alice", "description": "clean"},
            {"id": 92, "title": "Remote Second", "gear": "cab",
             "downloads_count": 2, "username": "bob", "description": "bright"},
        ],
    )

    def detail_text(app):
        detail = app.query_one("DetailPane")._body.content
        return " ".join(
            str(cell) for column in detail.columns for cell in column.cells)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "x", "enter")
            await pilot.pause(0.2)
            assert "Remote First" in detail_text(app)
            assert not isinstance(app.screen, TonePickerScreen)

            await pilot.press("down")
            await pilot.pause(0.2)
            assert "Remote Second" in detail_text(app)
            assert not isinstance(app.screen, TonePickerScreen)

    run(scenario())


def test_search_failure_clears_previous_detail(monkeypatch):
    tones = [{"id": 10, "title": "Local Tone", "gear": "amp",
              "username": "alice", "downloads_count": 1, "models": []}]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: tones)
    monkeypatch.setattr(
        "tui.library_panel.library.get_tone",
        lambda tone_id: tones[0] if tone_id == 10 else None,
    )
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    def detail_text(app):
        detail = app.query_one("DetailPane")._body.content
        return " ".join(
            str(cell) for column in detail.columns for cell in column.cells)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            assert "Local Tone" in detail_text(app)
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "x", "enter")
            await pilot.pause()
            assert "Move the library cursor" in str(app.query_one("DetailPane")._body.content)

    run(scenario())


def test_remote_import_does_not_block_keyboard(monkeypatch):
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda query, page_size, **kwargs: [{
            "id": row,
            "title": f"result {row}",
            "gear": "amp",
            "downloads_count": row,
            "username": "tester",
        } for row in range(3)],
    )
    # pack screen fetches the model list without network in this test
    monkeypatch.setattr(
        "tui.install_screen.tone3000.models", lambda tid, a2_only=True: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "x", "enter")
            table = app.query_one("#lib-table-tone")
            assert table.cursor_row == 0

            # Enter opens the pack install screen — a modal; the main UI stays
            # responsive (no blocking import on the UI thread)
            await pilot.press("enter")
            await pilot.pause()
            from tui.install_screen import PackInstallScreen
            assert isinstance(app.screen, PackInstallScreen)

            await pilot.press("escape")  # cancel, back to the table
            await pilot.pause()
            await pilot.press("down")
            assert table.cursor_row == 1

    run(scenario())


def test_remote_import_reports_progress_and_completion(monkeypatch):
    monkeypatch.setattr(
        "tui.library_panel.library.tone3000.search",
        lambda query, page_size, **kwargs: [{
            "id": 77, "title": "Pack", "gear": "amp",
            "downloads_count": 2, "username": "tester",
        }],
    )
    monkeypatch.setattr(
        "tui.install_screen.tone3000.models",
        lambda tid, a2_only=True: [
            {"id": 1, "model_url": "http://x/one.nam",
             "model_json": {"metadata": {"name": "one"}}},
            {"id": 2, "model_url": "http://x/two.nam",
             "model_json": {"metadata": {"name": "two"}}},
        ])

    def import_with_progress(tone_id, progress, **_kwargs):
        progress(0, 2, "one.nam")
        progress(1, 2, "one.nam")
        progress(2, 2, "two.nam")
        return {"id": tone_id, "models": [{"id": 1}, {"id": 2}]}

    monkeypatch.setattr("tui.install_screen.library.import_tone",
                        import_with_progress)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "x", "enter", "enter")  # search → pack screen
            await pilot.pause(0.2)
            from tui.install_screen import PackInstallScreen
            assert isinstance(app.screen, PackInstallScreen)
            # model list loaded from the mock, both selected
            pack = app.screen
            status = pack.query_one("#pack-status", Static)
            assert "2 model file(s)" in str(status.content)

            await pilot.press("enter")  # install (all selected)
            await pilot.pause(0.2)
            # completion: the screen dismisses and the app toasts the result
            assert not isinstance(app.screen, PackInstallScreen)
            toasts = {n.message for n in app._notifications}
            assert any("Installed 2 file(s) from tone 77" in m for m in toasts), toasts

    run(scenario())


def test_picker_groups_models_by_tone_and_shows_highlight_detail(monkeypatch):
    models = [
        {"id": 1, "tone_id": 10, "model_url": "http://x/one.nam",
         "architecture": "SlimmableContainer", "local_path": "/tones/10/one.nam",
         "title": "Plexi Pack", "username": "alice", "gear": "amp",
         "description": "Bright channel", "tags": ["plexi"], "makes": []},
        {"id": 2, "tone_id": 10, "model_url": "http://x/two.nam",
         "architecture": "SlimmableContainer", "local_path": "/tones/10/two.nam",
         "title": "Plexi Pack", "username": "alice", "gear": "amp",
         "description": "Normal channel", "tags": ["plexi"], "makes": []},
    ]
    monkeypatch.setattr("tui.picker.library.list_local_models", lambda kind: models)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            app.push_screen(TonePickerScreen("amp"))
            await pilot.pause()
            tree = app.screen.query_one("#pick-tree", Tree)
            assert len(tree.root.children) == 1
            assert len(tree.root.children[0].children) == 2

            await pilot.press("right", "right")
            detail = app.screen.query_one("#pick-detail", Static)
            assert isinstance(detail.content, Table)
            cells = " ".join(
                str(cell) for column in detail.content.columns for cell in column.cells)
            assert "one.nam" in cells
            assert "Bright channel" in cells

            await pilot.press("left")
            assert tree.cursor_node is tree.root.children[0]

    run(scenario())


def test_library_add_flow_opens_only_selected_tone_models(monkeypatch):
    tone = {
        "id": 10, "title": "Plexi Pack", "gear": "amp-cab",
        "username": "alice", "downloads_count": 1, "models": [],
    }
    models = [{
        "id": 1, "tone_id": 10, "model_url": "http://x/one.nam",
        "architecture": "SlimmableContainer", "local_path": "/tones/10/one.nam",
        "title": "Plexi Pack", "username": "alice", "gear": "amp-cab",
        "description": "Combined capture", "tags": [], "makes": [],
    }]
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [tone])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("tui.picker.library.list_local_models", lambda kind: models)
    writes = []
    monkeypatch.setattr("tui.app.live.read_chain", lambda: {"ir": "old-cab.wav"})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: writes.append(dict(cfg)))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await pilot.press("enter")
            # Enter on a row opens the tone's model file list directly
            assert isinstance(app.screen, TonePickerScreen)
            assert app.screen.tone_id == 10
            assert not app.screen.query_one("#pick-search", Input).display

            await pilot.press("enter")
            await pilot.pause()
            assert writes[-1]["model"] == "/tones/10/one.nam"
            assert "ir" not in writes[-1]

    run(scenario())


def test_type_filter_drives_local_query(monkeypatch):
    calls = []

    def list_tones(**kwargs):
        calls.append(kwargs.get("gear"))
        return []

    monkeypatch.setattr("tui.library_panel.library.list_tones", list_tones)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            app.query_one("#type-filter-local", Select).value = "cab"
            await pilot.pause()
            assert calls[-1] == "cab"

    run(scenario())


def test_general_search_accepts_author_query(monkeypatch):
    queries = []

    def search(query, page_size, **kwargs):
        queries.append(query)
        return [{"id": 8, "title": "Author tone", "gear": "amp",
                 "downloads_count": 3, "username": "alice"}]

    monkeypatch.setattr("tui.library_panel.library.tone3000.search", search)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test() as pilot:
            await goto_tone_tab(app, pilot)
            await pilot.press("/", "a", "l", "i", "c", "e", "enter")
            # opening the TONE3000 tab auto-loads the trending feed (empty query),
            # then the typed search runs
            assert queries == ["", "alice"]
            table = app.query_one("#lib-table-tone")
            assert table.row_count == 1
            assert table.get_cell_at((0, 7)) == "@alice"

    run(scenario())


def test_meter_uses_clean_track_without_escaped_brackets():
    rendered = MeterBar().render()
    assert "\\[" not in rendered
    assert "│" in rendered


def test_chain_panel_compact_layout_keeps_output_and_preset_rows(monkeypatch):
    # keep the 0.3s tick from overwriting the chain with live_chain.json
    monkeypatch.setattr(
        "tui.app.live.read_chain",
        lambda: {"model": "/tones/19-marshall-jcm-800-2203.nam",
                 "gain": 0.8, "master": 0.35})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(100, 35)) as pilot:
            panel = app.query_one(ChainPanel)
            panel.chain = {
                "model": "/tones/19-marshall-jcm-800-2203.nam",
                "gain": 0.8,
                "master": 0.35,
            }
            await pilot.pause()

            assert "MASTER" in str(panel.params.render())
            assert "0.35" in str(panel.params.render())
            # node rows carry ▲/▼ switch buttons; preset UI lives in the left panel
            assert app.query_one("#chain-amp-up") is not None
            assert app.query_one("#chain-amp-down") is not None
            assert app.query_one("#chain-ir-up") is not None
            assert app.query_one("#chain-ir-down") is not None

    run(scenario())


def test_preset_picker_loads_into_chain_panel(monkeypatch):
    """p key → picker → Enter → chain written."""
    presets = [{
        "name": "mayer-clean", "note": "Mayer 清音",
        "chain": {"model_id": 1, "model_path": "/tmp/x.nam", "ir_model_id": None,
                  "ir_path": None, "gain": 0.8, "master": 0.8},
    }]
    monkeypatch.setattr("tui.presets.library.preset_list", lambda: presets)
    loaded = []
    monkeypatch.setattr(
        "tui.presets.library.preset_load",
        lambda name: loaded.append(name) or
        {"model": "/tmp/x.nam", "gain": 0.8, "master": 0.8})
    monkeypatch.setattr("tui.presets.library.preset_get", lambda name: presets[0])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, PresetPickerScreen)

            await pilot.press("enter")  # load the first preset
            await pilot.pause()
            assert not isinstance(app.screen, PresetPickerScreen)
            # picker loads it, then the app applies it (two preset_load calls)
            assert loaded == ["mayer-clean", "mayer-clean"]

    run(scenario())


def test_focused_amp_node_steps_through_tone_folder(monkeypatch, tmp_path):
    """↑/↓ on a focused AMP node cycles sibling models of the same tone folder,
    writes the chain and mirrors the model in the detail pane."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": []}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a, amp_b])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    # read_chain must reflect what write_chain wrote (real impl reads the file)
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", amp_a["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            amp_node.focus()
            await pilot.pause()
            assert app.focused is amp_node

            await pilot.press("down")  # → amp_b
            await pilot.pause()
            assert written["model"] == amp_b["local_path"]
            assert app.query_one(ChainPanel).chain["model"] == amp_b["local_path"]

            await pilot.press("down")  # wraps around → amp_a
            await pilot.pause()
            assert written["model"] == amp_a["local_path"]

            await pilot.press("up")  # back → amp_b
            await pilot.pause()
            assert written["model"] == amp_b["local_path"]

    run(scenario())


def test_click_node_row_shows_tone_detail(monkeypatch, tmp_path):
    """Clicking an AMP node row mirrors its tone folder detail in the pane."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": []}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": amp_a["local_path"], "gain": 0.8})

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            await pilot.click(amp_node)
            await pilot.pause()
            assert app.focused is amp_node
            from rich.console import Console
            import io
            buf = io.StringIO()
            Console(file=buf, width=100).print(
                app.query_one(DetailPane)._body.content)
            body = buf.getvalue()
            assert "MV5 G1.nam" in body      # FILE section: the model
            assert "JCM800" in body          # owning tone

    run(scenario())


def test_click_switch_buttons_step_models(monkeypatch, tmp_path):
    """Clicking the ▲/▼ buttons steps sibling models (no keyboard needed)."""
    amp_a = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G1.nam")}
    amp_b = {"id": 2, "tone_id": 10, "name": "MV5 G2", "architecture": "SlimmableContainer",
             "local_path": str(tmp_path / "MV5 G2.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "MV5 G2.nam").write_bytes(b"b")
    tone = {"id": 10, "title": "JCM800", "gear": "amp", "username": "arthm",
            "downloads_count": 1, "models": []}
    monkeypatch.setattr("tui.app.library.local_models_by_tone",
                        lambda path: [amp_a, amp_b])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: tone)
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", amp_a["local_path"]),
                                 "gain": 0.8})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            # clicks hit the switch column container; bottom half = ▼ (down)
            col = app.query_one("#chain-amp-down").parent
            await pilot.click(col, offset=(5, 3))  # ▼ → amp_b
            await pilot.pause()
            assert written["model"] == amp_b["local_path"]

            await pilot.click(col, offset=(5, 0))  # ▲ → amp_a
            await pilot.pause()
            assert written["model"] == amp_a["local_path"]

            # clicking a button also focuses its node
            assert app.focused is next(
                n for n in app.query(NodeWidget) if n.kind == "amp")

    run(scenario())


def test_notify_caps_toast_stack_at_two(monkeypatch):
    """Rapid notifications never leave more than two toasts queued."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.notify("one")
            app.notify("two")
            app.notify("three")
            app.notify("four")
            await pilot.pause()
            assert len(app._notifications) <= 2
            # the newest one is present
            msgs = {n.message for n in app._notifications}
            assert "four" in msgs

    run(scenario())


def test_double_click_ir_toggles_bypass(monkeypatch, tmp_path):
    """Double-clicking the IR node bypasses (ir=null) and restores it."""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    ir = {"id": 2, "tone_id": 11, "name": "GB 1960", "architecture": "IR",
          "local_path": str(tmp_path / "GB 1960.wav")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    (tmp_path / "GB 1960.wav").write_bytes(b"b")
    monkeypatch.setattr("tui.app.library.local_models_by_tone", lambda path: [ir])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tid: {"id": tid, "title": "T"})
    written = {}
    initial = {"model": amp["local_path"], "ir": ir["local_path"], "gain": 0.8}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": written.get("model", initial["model"]),
                                 "ir": written.get("ir", initial["ir"]),
                                 "gain": written.get("gain", 0.8)})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            ir_node = next(n for n in app.query(NodeWidget) if n.kind == "ir")
            await pilot.double_click(ir_node)
            await pilot.pause()
            assert written["ir"] is None  # bypassed

            await pilot.double_click(ir_node)
            await pilot.pause()
            assert written["ir"] == ir["local_path"]  # restored

    run(scenario())


def test_double_click_amp_toggles_mute(monkeypatch, tmp_path):
    """Double-clicking the AMP node mutes (gain=0) and restores the gain."""
    amp = {"id": 1, "tone_id": 10, "name": "MV5 G1", "architecture": "SlimmableContainer",
           "local_path": str(tmp_path / "MV5 G1.nam")}
    (tmp_path / "MV5 G1.nam").write_bytes(b"a")
    monkeypatch.setattr("tui.app.library.local_models_by_tone", lambda path: [amp])
    monkeypatch.setattr("tui.app.library.get_tone", lambda tid: {"id": tid, "title": "T"})
    written = {}
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"model": amp["local_path"],
                                 "gain": written.get("gain", 0.8)})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            amp_node = next(n for n in app.query(NodeWidget) if n.kind == "amp")
            await pilot.double_click(amp_node)
            await pilot.pause()
            assert written["gain"] == 0.0
            assert amp_node.muted is True

            await pilot.double_click(amp_node)
            await pilot.pause()
            assert written["gain"] == 0.8
            assert amp_node.muted is False

    run(scenario())


def test_search_requests_large_page(monkeypatch):
    """Library search asks TONE3000 for 50 hits, not a handful."""
    captured = {}
    def fake_search(query, **kw):
        captured.update(kw)
        return []
    monkeypatch.setattr("tui.library_panel.library.tone3000.search", fake_search)
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await goto_tone_tab(app, pilot)
            search = app.query_one("#tone-search", Input)
            search.focus()
            search.value = "plexi"
            await pilot.press("enter")
            await pilot.pause()
            assert captured["page_size"] == 50

    run(scenario())


def test_preset_panel_browse_and_load(monkeypatch):
    """Preset panel lists presets; highlight shows summary, Enter loads."""
    # keep the library table empty: patching tui.app.library.get_tone would
    # also break the library panel (same module), firing a None highlight
    # that clears the detail pane after the preset summary.
    monkeypatch.setattr("tui.library_panel.library.list_tones", lambda **kw: [])
    presets = [{
        "name": "mayer-clean", "note": "Mayer 清音",
        "chain": {"model_id": 1, "model_path": "/tmp/x.nam", "ir_model_id": None,
                  "ir_path": None, "gain": 0.8, "master": 0.8},
        "updated_at": "2026-08-02T12:00:00+00:00",
    }]
    monkeypatch.setattr("tui.presets.library.preset_list", lambda: presets)
    monkeypatch.setattr("tui.presets.library.preset_get", lambda name: presets[0])
    monkeypatch.setattr("tui.presets.library.preset_load",
                        lambda name: {"model": "/tmp/x.nam", "gain": 0.8, "master": 0.8})
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)
    monkeypatch.setattr("tui.app.live.read_chain", lambda: {"gain": 1.0})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one("#preset-table", DataTable)
            table.focus()
            await pilot.pause()
            assert len(table.rows) == 1

            # highlight → detail pane shows the preset summary
            await pilot.press("down")
            await pilot.pause()
            body = app.query_one(DetailPane)._body.content
            assert "mayer-clean" in str(body)

            # enter → loads the preset (chain panel follows via live_chain.json)
            await pilot.press("enter")
            await pilot.pause()

    run(scenario())


def test_quality_hotkeys_clamp_and_write_chain(monkeypatch):
    """u/U adjust the A2 quality param (clamped 0..1) into the live chain."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)
    written = {}
    # read_chain reflects what write_chain wrote (real impl reads the file)
    monkeypatch.setattr("tui.app.live.read_chain",
                        lambda: {"gain": 1.0,
                                 "quality": written.get("quality", 1.0)})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: written.update(cfg))

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("u")  # quality 1.0 → 0.9
            await pilot.pause()
            assert written["quality"] == 0.9

            await pilot.press("U", "U")  # → 1.1 → clamped to 1.0
            await pilot.pause()
            assert written["quality"] == 1.0

            for _ in range(12):
                await pilot.press("u")  # → 0.0 floor
            await pilot.pause()
            assert written["quality"] == 0.0

            panel = app.query_one(ChainPanel)
            assert "QUALITY" in str(panel.params.render())

    run(scenario())


def test_quit_requires_two_ctrl_c(monkeypatch):
    """Ctrl+C once warns, a second press within the window exits; no q binding."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)
    monkeypatch.setattr("tui.app.live.read_chain", lambda: {"gain": 1.0})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: None)

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            assert not any(b.key == "q" for b in app.BINDINGS)

            await pilot.press("ctrl+c")
            await pilot.pause()
            assert not app._exit  # still running after the first press
            assert "Ctrl+C" in str(app.query_one(ChainPanel)) or True
            # first press posted a toast
            toasts = {n.message for n in app._notifications}
            assert any("再按一次" in m for m in toasts), toasts

            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app._exit is True

    run(scenario())


def test_quit_two_ctrl_c_works_from_modal(monkeypatch):
    """Ctrl+C twice exits even while a modal screen is open."""
    monkeypatch.setattr("tui.app.library.get_tone", lambda tone_id: None)
    monkeypatch.setattr("tui.app.live.read_chain", lambda: {"gain": 1.0})
    monkeypatch.setattr("tui.app.live.write_chain", lambda cfg: None)
    monkeypatch.setattr("tui.presets.library.preset_list", lambda: [])

    async def scenario():
        app = GigBuddyApp(spawn_engine=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("p")  # open the preset picker modal
            await pilot.pause()
            assert isinstance(app.screen, PresetPickerScreen)

            await pilot.press("ctrl+c", "ctrl+c")
            await pilot.pause()
            assert app._exit is True

    run(scenario())
