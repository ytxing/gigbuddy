"""Make src/ importable for tests (same bootstrap as bin/gigbuddy)."""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import tone3000  # noqa: E402


@pytest.fixture(autouse=True)
def no_live_creator_leaderboard(monkeypatch):
    """Tests opt into creator rows explicitly; never hit the live leaderboard."""
    monkeypatch.setattr(tone3000, "top_creators", lambda **_kwargs: [])
