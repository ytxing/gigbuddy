#!/usr/bin/env python3
"""生成测试信号 wav：吉他风格（低音 E 弦弹奏 + 颤音衰减）用于渲染链路验证。
用法: gen_test_wav.py <out.wav> [duration=3] [sr=48000]
"""
import sys, math, struct, wave

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "data/dry_test.wav"
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    sr = int(sys.argv[3]) if len(sys.argv) > 3 else 48000
    n = int(dur * sr)

    # 音符: E2 82.41, A2 110, D3 146.83, G3 196 (低音扫弦感), 每次拨弦带指数衰减
    notes = [(82.41, 0.0), (110.0, 0.6), (146.83, 1.2), (196.0, 1.8)]
    samples = [0.0] * n
    for freq, start in notes:
        s = int(start * sr)
        if s >= n: break
        remain = min(n - s, int(1.2 * sr))
        for i in range(remain):
            t = i / sr
            env = math.exp(-2.5 * t)          # 拨弦衰减
            vib = 1.0 + 0.004 * math.sin(2 * math.pi * 5 * t)  # 轻微颤音
            v = math.sin(2 * math.pi * freq * vib * t)
            v += 0.3 * math.sin(2 * math.pi * freq * 2 * vib * t)  # 泛音
            samples[s + i] += 0.8 * env * v

    peak = max(1e-9, max(abs(x) for x in samples))
    scale = 0.9 / peak
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, x * scale)) * 32767)) for x in samples))
    print(f"wrote {out}: {n} samples, {dur}s @ {sr}Hz, peak=0.9")

if __name__ == "__main__":
    main()
