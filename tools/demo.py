# -*- coding: utf-8 -*-
"""开发演示：规划 + 抽帧 + 密度图。

用法：python tools/demo.py [视频文件名]
不传参数则跑 assets/ 下所有 mp4。
"""
import sys
import io
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

ASSETS = ROOT / "assets"


def run_one(video: Path):
    info = af.probe_media(video)
    print("=" * 72)
    print(f"{video.name}: 时长 {info['duration']:.2f}s  "
          f"{info['width']}x{info['height']}  {info['fps']:.1f}fps")

    packets = af.probe_packets(video)
    centers, dens = af.build_density(packets, info["duration"], 0.5)
    sm = af.smooth(dens, 3)

    plan = af.plan_frames(video, min_frames=8, max_frames=24)
    print(f"规划帧数 = {plan.frame_count}   ({plan.note})")
    print("时间点: " + " ".join(f"{t:.1f}" for t in plan.times))

    out = ROOT / "frames" / video.stem
    t0 = time.time()
    frames = af.extract_at(video, plan.times, out)
    print(f"实际抽出 {len(frames)} 张，耗时 {time.time()-t0:.2f}s")

    print("密度曲线（^ 为抽帧位置）:")
    print(af.ascii_density(centers, sm, plan.times, rows=8))


def main():
    if len(sys.argv) > 1:
        targets = [ASSETS / sys.argv[1]]
    else:
        targets = sorted(ASSETS.glob("*.mp4"))
    if not targets:
        print("assets/ 下没有视频")
        return
    for v in targets:
        if v.exists():
            run_one(v)
        else:
            print(f"[跳过] {v} 不存在")


if __name__ == "__main__":
    main()
