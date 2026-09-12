# -*- coding: utf-8 -*-
"""音频链路分步计时，用于定位卡点"""
import sys
import io
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

SF_KEY = "sk-qdhlistemmaiyugdwoolswsamxgepmcqxlswhibytsjcgwid"
ASSETS = ROOT / "assets"
OUT = ROOT / "out_diag"
OUT.mkdir(exist_ok=True)


def step(label, fn):
    t0 = time.time()
    try:
        r = fn()
        print(f"  {label:<26} {time.time()-t0:6.2f}s  -> {r}")
        return r
    except Exception as e:
        print(f"  {label:<26} {time.time()-t0:6.2f}s  FAIL {type(e).__name__}: {e}")
        return None


def diag(name):
    v = ASSETS / name
    if not v.exists():
        print(f"[{name}] 不存在")
        return
    print(f"=== {name} ===")
    info = step("probe_audio", lambda: af.probe_audio(v))
    if not info or not info.get("has_audio"):
        print("  无音轨，结束")
        return
    wav = step("extract_audio", lambda: af.extract_audio(v, OUT / (v.stem + ".wav")))
    if wav is None:
        return
    step("detect_silence", lambda: len(af.detect_silence(wav)[0]))
    step("audio_speech_ratio", lambda: round(af.audio_speech_ratio(wav)[0], 3))
    lean = step("strip_silence", lambda: af.strip_silence(wav, OUT / (v.stem + ".lean.wav")))
    if lean is None:
        return
    step("transcribe_audio", lambda: (af.transcribe_audio(lean, api_key=SF_KEY) or "")[:60])


for n in ["video2.mp4", "video1.mp4", "loop5min.mp4"]:
    diag(n)
    print()
