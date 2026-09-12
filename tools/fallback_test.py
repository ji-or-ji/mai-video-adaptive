# -*- coding: utf-8 -*-
"""内存闸门与失败回退测试"""
import sys
import io
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

MODELS = ROOT / "assets" / "models"
SPEECH = ROOT / "assets" / "speech_demo.mp4"
OUT = ROOT / "out_audio"

free = af.available_memory_mb()
print(f"当前可用内存: {free:.0f} MB")
print("-" * 76)

CASES = [
    ("local 正常", dict(mode="local", min_free_mb=200.0)),
    ("local 被内存闸门拦下", dict(mode="local", min_free_mb=999999.0)),
    ("api 坏Key → 回退 local", dict(mode="api", api_key="bad-key", min_free_mb=200.0)),
    ("api 坏Key + 闸门 → 全失败", dict(mode="api", api_key="bad-key", min_free_mb=999999.0)),
]

for label, opts in CASES:
    t0 = time.time()
    r = af.audio_transcript(SPEECH, out_dir=OUT,
                            local_model=MODELS / "model.int8.onnx",
                            local_tokens=MODELS / "tokens.txt",
                            timeout=15.0, **opts)
    print(f"{label:<30} {time.time()-t0:5.1f}s  used={r.get('used') or '-':<5} "
          f"skipped={r.get('skipped')}  reason={r.get('reason') or '-'}")
    if r.get("tried"):
        print(f"{'':<30} tried={r['tried']}")
    if r.get("text"):
        print(f"{'':<30} text={r['text'][:60]}")
