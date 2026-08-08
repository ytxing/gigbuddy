#!/usr/bin/env python3
"""GigBuddy 渲染管道：链 JSON → nam_cli(amp 子进程) + IR 卷积(cab) → wav

MVP 链格式（简单版）:
    {"amp": "/path/model.nam", "ir": "/path/cab.wav", "ir_mix": 1.0}
amp 与 ir 都可选；ir_mix 默认 1.0（dry/wet）。

用法: render.py <chain.json> <dry.wav> <out.wav>
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
NAM_CLI = ROOT / "bin" / "nam_cli"


# ---------------- WAV I/O（支持 PCM16/24/32 与 float32，兼容 wave 模块不认 float 的问题） ----------------
def load_wav(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"not a WAV file: {path}")
    i, fmt, sr, channels, bits = 12, 0, 0, 1, 16
    while i < len(data):
        ck, sz = data[i:i + 4], int.from_bytes(data[i + 4:i + 8], "little")
        if ck == b"fmt ":
            fmt = int.from_bytes(data[i + 8:i + 10], "little")
            channels = int.from_bytes(data[i + 10:i + 12], "little")
            sr = int.from_bytes(data[i + 12:i + 16], "little")
            bits = int.from_bytes(data[i + 22:i + 24], "little")
        elif ck == b"data":
            raw = np.frombuffer(data[i + 8:i + 8 + sz], dtype=np.uint8)
            break
        i += 8 + sz + (sz & 1)
    else:
        raise ValueError(f"no data chunk: {path}")

    if fmt == 3 and bits == 32:          # float32
        x = raw.view(np.float32)
    elif fmt == 1:                       # PCM
        if bits == 16:
            x = raw.view("<i2").astype(np.float32) / 32768.0
        elif bits == 24:
            packed = raw.reshape(-1, 3).astype(np.int32)
            x = (packed[:, 0] | packed[:, 1] << 8 | packed[:, 2] << 16)
            x = np.where(x & 0x800000, x - 0x1000000, x)
            x = x.astype(np.float32) / 8388608.0
        elif bits == 32:
            x = raw.view("<i4").astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"unsupported PCM bits: {bits}")
    else:
        raise ValueError(f"unsupported wav format: {fmt}")

    if channels > 1:                     # 取第一声道
        x = x.reshape(-1, channels)[:, 0]
    return x.astype(np.float32), sr


def save_wav(path, samples, sr):
    p32 = np.clip(samples, -1.0, 1.0).astype(np.float32)
    with open(path, "wb") as f:
        def w32(v): f.write(int(v).to_bytes(4, "little"))
        def w16(v): f.write(int(v).to_bytes(2, "little"))
        n = len(p32)
        f.write(b"RIFF"); w32(36 + n * 4); f.write(b"WAVE")
        f.write(b"fmt "); w32(16); w16(3); w16(1); w32(sr); w32(sr * 4); w16(4); w16(32)
        f.write(b"data"); w32(n * 4)
        f.write(p32.tobytes())


# ---------------- 处理步骤 ----------------
def resample_to(x, x_sr, target_sr):
    """线性插值重采样（IR 与干音采样率不匹配时用，MVP 精度够）"""
    if x_sr == target_sr:
        return x
    n_out = int(round(len(x) * target_sr / x_sr))
    idx = np.linspace(0, len(x) - 1, n_out)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def run_amp(model_nam, dry_wav, out_wav, block=512):
    """amp 走 nam_cli 子进程（防模型/插件崩溃拖垮主进程）"""
    r = subprocess.run([str(NAM_CLI), str(model_nam), str(dry_wav), str(out_wav), str(block)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"nam_cli failed: {r.stderr.strip()}")
    return r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""


def apply_ir(x, ir, ir_mix=1.0):
    """FFT 卷积（离线渲染，长度无约束）。IR 按能量归一，保持响度一致性"""
    ir = ir / max(1e-9, np.sqrt(np.sum(ir ** 2)))
    n = len(x) + len(ir) - 1
    nfft = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft))[:len(x)]
    return (ir_mix * y + (1.0 - ir_mix) * x).astype(np.float32)


def _nodes(chain):
    """兼容 canonical slots、nodes DSL 与旧 amp/ir 字符串格式."""
    if "slots" in chain:
        nodes = []
        for index, slot in enumerate(chain.get("slots") or []):
            if not isinstance(slot, dict):
                raise ValueError(f"invalid slot {index}")
            path = slot.get("path")
            if path is None:
                continue
            suffix = Path(path).suffix.lower()
            if suffix == ".nam":
                nodes.append({"type": "amp", "model_file": path})
            elif suffix == ".wav":
                nodes.append({"type": "cab_ir", "model_file": path,
                              "params": {"mix": float(
                                  chain.get("ir_mix", 1.0))}})
            else:
                raise ValueError(f"unsupported slot format: {path}")
        return nodes
    if "nodes" in chain:
        return chain["nodes"]
    nodes = []
    if chain.get("amp"):
        nodes.append({"type": "amp", "model_file": chain["amp"]})
    if chain.get("ir"):
        nodes.append({"type": "cab_ir", "model_file": chain["ir"],
                      "params": {"mix": float(chain.get("ir_mix", 1.0))}})
    return nodes


def render_chain(chain, dry_wav, out_wav):
    out_wav = Path(out_wav)
    x, sr = load_wav(dry_wav)
    tmp = out_wav.with_name(out_wav.stem + "_tmp.wav")
    stage_in = dry_wav
    stage_out = tmp
    for i, node in enumerate(_nodes(chain)):
        f = node["model_file"]
        if not Path(f).exists():
            raise FileNotFoundError(f"节点 {i} ({node.get('type')}) 文件不存在: {f}")
        if node.get("type") == "amp":
            run_amp(f, stage_in, stage_out)
            x, sr = load_wav(stage_out)
            stage_in = stage_out
        elif node.get("type") == "cab_ir":
            ir, ir_sr = load_wav(f)
            ir = resample_to(ir, ir_sr, sr)
            x = apply_ir(x, ir, float((node.get("params") or {}).get("mix", 1.0)))
        else:
            raise ValueError(f"未知节点类型: {node.get('type')}")
    save_wav(out_wav, x, sr)
    tmp.unlink(missing_ok=True)
    return x, sr


def main():
    if len(sys.argv) < 4:
        print("usage: render.py <chain.json> <dry.wav> <out.wav>")
        return 1
    chain = json.loads(Path(sys.argv[1]).read_text())
    x, sr = render_chain(chain, sys.argv[2], sys.argv[3])
    print(f"rendered {sys.argv[3]}: {len(x)} samples @ {sr}Hz, peak={np.abs(x).max():.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
