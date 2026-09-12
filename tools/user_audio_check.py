# -*- coding: utf-8 -*-
"""用户录制的真实语音：本地 ASR 对比"""
import sys
import io
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

SRC = ROOT / "tools" / "2026-09-12 16-23-20.mp4"
OUT = ROOT / "out_audio"
MODELS = ROOT / "assets" / "models"

EXPECT = ("哎我跟你们说，昨天那个新副本我去试了一下，真的有点东西。"
          "P1 还好，主要就是躲绿圈和红圈，站错位置直接就没了。"
          "到 P2 的时候 BOSS 会分身，你要先打那个发光的本体，不然打半天不掉血。"
          "最后斩杀阶段 DPS 压力特别大，我那个装备才五百八，勉强压线过的。"
          "总的来说机制不难，就是容错率低。")

info = af.probe_media(SRC)
print(f"素材 {SRC.name}  时长 {info['duration']:.1f}s  {info['width']}x{info['height']}")

t0 = time.time()
r = af.audio_transcript(SRC, out_dir=OUT, mode="local",
                        local_model=MODELS / "model.int8.onnx",
                        local_tokens=MODELS / "tokens.txt")
dt = time.time() - t0
print(f"本地识别 {dt:.2f}s  skipped={r.get('skipped')}  "
      f"reason={r.get('reason') or '-'}  ratio={r.get('speech_ratio', -1):.2f}")
print("-" * 72)
print("原文 :", EXPECT)
print("识别 :", r.get("text", ""))
