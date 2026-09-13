"""时间轴模式验证：跑一条视频，看模型输出的分段质量与时间戳是否靠谱。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

DS_KEY = "sk-be00893b96234425baae60b9148ef1ed"
OUT = ROOT / "tools" / "_tl_out"


def show(name: str):
    v = Path(name) if ("/" in name or "\\" in name) else (ROOT / "assets" / name)
    print("=" * 64)
    if not v.exists():
        print(f"[跳过] {name} 不存在")
        return
    print(f"{name}  {v.stat().st_size / 1024 / 1024:.2f}MB")
    r = af.understand_video(v, out_dir=OUT, asr_mode="off",
                            vision_api_key=DS_KEY, use_cache=False)
    print(f'帧数={r["frame_count"]}  新颖率={r["novelty"]:.3f}  '
          f'耗时={r["elapsed"]:.1f}s')
    print("--- 压缩摘要（注入上下文用的那份）---")
    print(r["summary"])
    print("--- 完整时间轴 ---")
    for s in r["segments"]:
        print(f'{af.fmt_ts(s["start"])}~{af.fmt_ts(s["end"])}  {s["text"]}')
    print("--- raw 开头 ---")
    print(r["raw"][:300])


if __name__ == "__main__":
    for n in (sys.argv[1:] or ["video1.mp4"]):
        show(n)
