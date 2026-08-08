from rich.cells import cell_len
from rich.text import Text

from tui.marquee import (MARQUEE_ENDPOINT_PAUSE_TICKS, ellipsis_window,
                         marquee_text, marquee_window)


def test_marquee_window_moves_between_both_ends_without_wrapping():
    text = "0123456789"
    width = 4
    travel = cell_len(text) - width

    assert marquee_window(text, width, 0) == "0123"
    assert marquee_window(text, width, MARQUEE_ENDPOINT_PAUSE_TICKS + 1) == "1234"
    assert marquee_window(text, width, MARQUEE_ENDPOINT_PAUSE_TICKS + 2) == "2345"
    assert marquee_window(text, width, MARQUEE_ENDPOINT_PAUSE_TICKS + travel) == "6789"
    assert marquee_window(text, width, MARQUEE_ENDPOINT_PAUSE_TICKS + travel + 1) == "6789"
    assert marquee_window(text, width, MARQUEE_ENDPOINT_PAUSE_TICKS + travel + MARQUEE_ENDPOINT_PAUSE_TICKS + 1) == "5678"

    cycle = (MARQUEE_ENDPOINT_PAUSE_TICKS + travel) * 2
    assert marquee_window(text, width, cycle) == "0123"


def test_marquee_window_handles_fit_and_invalid_width():
    assert marquee_window("short", 5) == "short"
    assert marquee_window("short", 0) == ""
    assert marquee_window("short", -1) == ""


def test_ellipsis_window_uses_terminal_cell_width():
    assert ellipsis_window("长标题 amp model", 6) == "长标…"
    clipped = ellipsis_window("0123456789", 6)
    assert clipped == "01234…"
    assert cell_len(clipped) <= 6


def test_markup_marquee_preserves_rich_spans_while_clipping_cells():
    text = Text.from_markup("[bold]0123456789[/bold] · [dim]tail[/dim]")

    window = marquee_text(text, 5)

    assert window.plain == "01234"
    assert window.cell_len == 5
    assert window.spans
