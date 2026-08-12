"""Make src/ importable for tests (same bootstrap as bin/gigbuddy)."""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import tone3000  # noqa: E402
import library  # noqa: E402
from tui.app import GigBuddyApp  # noqa: E402
from tui.presets import PresetPanel  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_tone3000_credentials(monkeypatch, tmp_path):
    """Never let UI tests discover a developer's real TONE3000 session."""
    monkeypatch.delenv("TONE3000_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        tone3000, "TOKEN_FILE", tmp_path / "tone3000_tokens.json")


@pytest.fixture(autouse=True)
def isolated_preset_documents(monkeypatch, tmp_path):
    """Never let a test export or import the developer's real Preset files."""
    monkeypatch.setattr(library, "PRESETS_DIR", tmp_path / "data" / "presets")


@pytest.fixture(autouse=True)
def isolated_local_pack_scan(monkeypatch, tmp_path):
    """Keep repository-managed downloaded Packs out of isolated UI tests."""
    monkeypatch.setattr(library, "TONES_DIR", tmp_path / "data" / "tones")


@pytest.fixture(autouse=True)
def no_live_creator_leaderboard(monkeypatch):
    """Tests opt into creator rows explicitly; never hit the live leaderboard."""
    monkeypatch.setattr(tone3000, "top_creators", lambda **_kwargs: [])


@pytest.fixture(autouse=True)
def no_audio_device_probe(monkeypatch):
    """Keep UI tests independent of the optional realtime_cli binary."""
    async def skip_device_probe(_self, _generation=None):
        return None

    monkeypatch.setattr(GigBuddyApp, "_load_devices", skip_device_probe)


@pytest.fixture(autouse=True)
def no_remote_starter_preset_bootstrap(monkeypatch):
    """Tests seed the preset database explicitly; never download starters."""
    monkeypatch.setattr(PresetPanel, "_bootstrap_starter_if_empty",
                        lambda _self: None)
