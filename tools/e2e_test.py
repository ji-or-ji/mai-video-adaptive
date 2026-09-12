# -*- coding: utf-8 -*-
"""端到端测试：自适应抽帧 + 音频转录 → 视觉模型理解"""
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

DS_KEY = "sk-c64867e1d2b94fcbb07d7ebf523585c9"
OUT = ROOT / "out_e2e"
MODELS = ROOT / "assets" / "models"

TARGETS = [
    ROOT / "tools" / "2026-09-12 16-23-20.mp4",
    ROOT / "assets" / "video1.mp4",
    ROOT / "assets" / "loop5min.mp4",
]

for v in TARGETS:
    if not v.exists():
        print(f"[跳过] {v.name} 不存在")
        continue
    r = af.understand_video(v, out_dir=OUT, asr_mode="local",
                            local_model=MODELS / "model.int8.onnx",
                            local_tokens=MODELS / "tokens.txt",
                            vision_api_key=DS_KEY)
    print("=" * 72)
    print(f"{v.name}")
    print(f"  抽帧 {r['frames']} 张 (规划 {r['frame_count']}, 新颖率 {r['novelty']:.2f})  "
          f"全程 {r['elapsed']:.1f}s")
    if r["transcript"]:
        print(f"  语音: {r['transcript'][:160]}")
    print(f"  描述: {r['description']}")
