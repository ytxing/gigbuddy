from types import SimpleNamespace

from tui.library_panel import LibraryPanel


def test_tone_worker_identity_requires_the_current_pane_and_query():
    panel = SimpleNamespace(
        is_mounted=True,
        _screen_generation=3,
        _tone_request_id=8,
        _active_pane="pane-tone",
        _tone_request_view=("pane-tone", "clean", "trending", "all"),
        _view_states={
            "pane-tone": {
                "query": "clean", "sort": "trending", "type_filter": "all",
            },
        },
    )
    panel._screen_alive = lambda generation: (
        panel.is_mounted and generation == panel._screen_generation)
    panel._view_identity = lambda pane_id: (
        pane_id,
        str(panel._view_states.get(pane_id, {}).get("query", "")),
        str(panel._view_states.get(pane_id, {}).get("sort", "")),
        str(panel._view_states.get(pane_id, {}).get("type_filter", "all")),
    )
    panel._request_view_alive = (
        lambda pane_id, expected: LibraryPanel._request_view_alive(
            panel, pane_id, expected))

    assert LibraryPanel._tone_alive(panel, 3, 8)

    panel._view_states["pane-tone"]["query"] = "metal"
    assert not LibraryPanel._tone_alive(panel, 3, 8)

    panel._view_states["pane-tone"]["query"] = "clean"
    panel._active_pane = "pane-creators"
    assert not LibraryPanel._tone_alive(panel, 3, 8)


def test_silent_favorites_render_does_not_publish_visible_state():
    class Table:
        row_count = 0

        def clear(self):
            self.rows = []

        def add_row(self, *cells, key=None):
            self.rows.append((cells, key))
            self.row_count = len(self.rows)

    table = Table()
    calls = []
    panel = SimpleNamespace(
        _favorites_tones={}, _active_pane="pane-favorites",
        query_one=lambda *_args: table,
        _row_cells=lambda _tone: ("title",),
        _sync_legacy_type_options=lambda *_args: None,
        _update_favorites_subtitle=lambda **_kwargs: calls.append("subtitle"),
        _restore_view_anchor=lambda *_args: calls.append("anchor"),
        _publish_highlight=lambda *_args: calls.append("highlight"),
        _focus_if_pane_active=lambda *_args: calls.append("focus"),
        _status_row=lambda *_args: calls.append("status"),
    )

    LibraryPanel._render_favorites_entry(
        panel, {"tones": {1: {"id": 1}}}, silent=True)

    assert panel._favorites_tones[1]["id"] == 1
    assert calls == []
