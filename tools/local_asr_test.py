# -*- coding: utf-8 -*-
"""本地 ASR 测试：sherpa-onnx + SenseVoice（纯本地，无网络）"""
import sys
import io
import time
import wave
import array
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "assets" / "models"
OUT = ROOT / "out_audio"

import sherpa_onnx  # noqa: E402


def mem_mb():
    """当前进程工作集内存（MB）。"""
    import os
    import subprocess as sp
    try:
        r = sp.run(["powershell", "-NoProfile", "-Command",
                    f"[math]::Round((Get-Process -Id {os.getpid()}).WorkingSet64/1MB,1)"],
                   capture_output=True, text=True, timeout=30)
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return -1.0


def read_wav(path: Path):
    with wave.open(str(path), "rb") as f:
        assert f.getnchannels() == 1, "需要单声道"
        sr = f.getframerate()
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return sr, [s / 32768.0 for s in a]


t0 = time.time()
rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=str(MODELS / "model.int8.onnx"),
    tokens=str(MODELS / "tokens.txt"),
    num_threads=4,
    use_itn=True,
    language="auto",
    debug=False,
)
print(f"模型加载耗时 {time.time()-t0:.2f}s  进程内存 {mem_mb():.0f} MB")
print("-" * 70)

targets = sorted(OUT.glob("*.lean.wav")) + sorted(OUT.glob("video?.wav"))
for wav in targets:
    try:
        sr, samples = read_wav(wav)
        s = rec.create_stream()
        s.accept_waveform(sr, samples)
        t0 = time.time()
        rec.decode_stream(s)
        dt = time.time() - t0
        dur = len(samples) / sr
        print(f"{wav.name:<22} 音频{dur:6.1f}s  解码{dt:5.2f}s  "
              f"实时倍速 {dur/max(dt,1e-6):4.1f}x")
        print(f"{'':<22} -> {s.result.text[:130]}")
    except Exception as e:
        print(f"{wav.name:<22} FAIL {type(e).__name__}: {e}")
