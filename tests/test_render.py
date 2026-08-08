"""Regression tests for canonical offline rendering helpers."""

import struct

import numpy as np

import render


def _pcm24_wav(samples: list[int], sample_rate: int = 48000) -> bytes:
    payload = b"".join(
        int(sample).to_bytes(3, "little", signed=True) for sample in samples)
    fmt = struct.pack(
        "<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate,
        sample_rate * 3, 3, 24)
    data = struct.pack("<4sI", b"data", len(payload)) + payload
    riff_size = 4 + len(fmt) + len(data)
    return b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + fmt + data


def test_load_wav_decodes_signed_24_bit_pcm(tmp_path):
    path = tmp_path / "signed-24.wav"
    path.write_bytes(_pcm24_wav([-8388608, -1, 0, 1, 8388607]))

    samples, sample_rate = render.load_wav(path)

    assert sample_rate == 48000
    np.testing.assert_allclose(
        samples,
        np.array([-1.0, -1 / 8388608, 0.0, 1 / 8388608,
                  8388607 / 8388608], dtype=np.float32),
        rtol=0,
        atol=1e-7,
    )


def test_nodes_derives_processing_order_from_canonical_slots():
    chain = {
        "slots": [
            {"path": "/tones/amp.nam"},
            {"path": None, "candidate": "/tones/ignored.wav"},
            {"path": "/tones/cab.wav"},
        ],
        "ir_mix": 0.25,
    }

    assert render._nodes(chain) == [
        {"type": "amp", "model_file": "/tones/amp.nam"},
        {"type": "cab_ir", "model_file": "/tones/cab.wav",
         "params": {"mix": 0.25}},
    ]
