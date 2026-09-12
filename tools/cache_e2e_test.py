# -*- coding: utf-8 -*-
"""端到端 + 签名缓存：重复视频应秒回复用"""
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

DS_KEY = "sk-c64867e1d2b94fcbb07d7ebf523585c9"
OUT = ROOT / "out_e2e"
CACHE = OUT / "e2e_cache.json"
ASSETS = ROOT / "assets"
if CACHE.exists():
    CACHE.unlink()

CASES = [
    ("video1.mp4", "第1次 video1"),
    ("video1.mp4", "第2次 video1"),
    ("video1_recode.mp4", "video1 重编码副本"),
    ("video2.mp4", "video2（无关）"),
]

for name, label in CASES:
    v = ASSETS / name
    if not v.exists():
        print(f"{label:<22} 不存在")
        continue
    r = af.understand_video(v, out_dir=OUT, asr_mode="off",
                            vision_api_key=DS_KEY, cache_path=CACHE)
    if r.get("cached"):
        print(f"{label:<22} 复用缓存  相似度={r['similarity']:.2f}  "
              f"耗时 {r['elapsed']:.2f}s")
    else:
        print(f"{label:<22} 实际处理  帧数={r.get('frames')}  "
              f"耗时 {r['elapsed']:.1f}s")
        print(f"{'':<22} -> {r['description'][:90]}")

print()
print(f"缓存条目: {len(af.VideoSignatureCache(CACHE).entries)}  "
      f"文件 {CACHE.stat().st_size // 1024 if CACHE.exists() else 0} KB")
