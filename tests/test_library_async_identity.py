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


def test_tone_cache_is_fifo_bounded():
    panel = SimpleNamespace(
        _tone_cache={},
        _remote_tones={},
        _tone_total=None,
        _tone_page=0,
        _tone_has_more=False,
    )

    for index in range(21):
        panel._remote_tones = {index: {"id": index}}
        panel._tone_total = 21
        panel._tone_page = index + 1
        LibraryPanel._save_tone_cache(panel, (f"query-{index}", "all", "trending"))

    assert len(panel._tone_cache) == 20
    assert ("query-0", "all", "trending") not in panel._tone_cache
    assert ("query-20", "all", "trending") in panel._tone_cache
