# -*- coding: utf-8 -*-
"""新颖率与动态区间验证"""
import sys
import io
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

ASSETS = ROOT / "assets"

print(f"{'文件':<16}{'时长':>7}{'重复度':>9}{'新颖率':>9}{'区间':>12}{'探针耗时':>10}")
print("-" * 66)
for name in ["loop5min.mp4", "video1.mp4", "video2.mp4"]:
    v = ASSETS / name
    if not v.exists():
        print(f"{name:<16} 不存在")
        continue
    info = af.probe_media(v)
    t0 = time.time()
    novelty, repeat, fps_ = af.novelty_scan(v, info["duration"], samples=32)
    dt = time.time() - t0
    lo, hi, tgt = af.adaptive_plan(info["duration"], novelty)
    print(f"{name:<16}{info['duration']:>6.0f}s{repeat:>9.2f}{novelty:>9.2f}"
          f"{f'{lo}~{hi}':>12}{dt:>9.1f}s")

print()
print("=== 规划对比 ===")
for name in ["loop5min.mp4", "video1.mp4", "video2.mp4"]:
    v = ASSETS / name
    if not v.exists():
        continue
    p = af.plan_frames(v, adaptive=True)
    print(f"{name:<16} 新颖率={p.novelty:.2f}  区间={p.min_frames}~{p.max_frames}  "
          f"最终帧数={p.frame_count}")
