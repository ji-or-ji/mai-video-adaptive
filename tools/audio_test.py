# -*- coding: utf-8 -*-
"""音频链路测试：静音分析 + 去静音 + 转录（含超时降级）"""
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
OUT = ROOT / "out_audio"

print("超时阈值 30s，超时即降级为「只交帧」")
print("-" * 72)
for name in ["video1.mp4", "video2.mp4", "loop5min.mp4"]:
    v = ASSETS / name
    if not v.exists():
        continue
    t0 = time.time()
    r = af.audio_transcript(v, out_dir=OUT, api_key=SF_KEY, timeout=30.0)
    dt = time.time() - t0
    print(f"{name:<16}{dt:>6.1f}s  has_audio={r.get('has_audio')}  "
          f"skipped={r.get('skipped')}  ratio={r.get('speech_ratio', -1):.2f}  "
          f"reason={r.get('reason') or '-'}")
    t = (r.get("text") or "").strip()
    if t:
        print(f"{'':<16}text: {t[:110]}")
