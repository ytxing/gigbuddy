from pathlib import Path

import pytest

from tone3000 import _safe_filename


@pytest.mark.parametrize("value", ["../escape.nam", "/tmp/escape.nam", "..\\escape.nam"])
def test_remote_model_filename_falls_back_from_path_components(value):
    assert _safe_filename(value, "model-1.nam") == "model-1.nam"


def test_remote_model_filename_preserves_semantic_name():
    assert _safe_filename("Fender Super Reverb: Vol 3.nam", "model-1.nam") == (
        "Fender Super Reverb: Vol 3.nam"
    )


def test_remote_model_filename_rejects_missing_fallback():
    with pytest.raises(ValueError):
        _safe_filename("../escape.nam", "")
