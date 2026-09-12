# -*- coding: utf-8 -*-
"""三档模式测试：off / local / api（素材用 TTS 合成的中文讲解视频）"""
import sys
import io
import subprocess
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

SF_KEY = "sk-qdhlistemmaiyugdwoolswsamxgepmcqxlswhibytsjcgwid"
OUT = ROOT / "out_audio"
MODELS = ROOT / "assets" / "models"
ASSETS = ROOT / "assets"
OUT.mkdir(exist_ok=True)

SPEECH = ("这个副本的机制是这样的，首领第一次狂暴的时候，所有人要躲到柱子后面，"
          "等它砸完地面再出来输出。")


def make_speech_video(out_video: Path) -> Path:
    wav = OUT / "speech_src.wav"
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$s.SelectVoice('Microsoft Huihui Desktop'); "
          f"$s.SetOutputToWaveFile('{wav}'); "
          f"$s.Speak('{SPEECH}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   timeout=180, check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=12",
                    "-i", str(wav), "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(out_video)], check=True)
    return out_video


v = make_speech_video(ASSETS / "speech_demo.mp4")
print(f"素材: {v.name}")
print(f"期望文本: {SPEECH}")
print("-" * 72)

for mode in ["off", "local", "api"]:
    t0 = time.time()
    r = af.audio_transcript(v, out_dir=OUT, mode=mode, api_key=SF_KEY,
                            local_model=MODELS / "model.int8.onnx",
                            local_tokens=MODELS / "tokens.txt", timeout=30.0)
    dt = time.time() - t0
    print(f"[{mode:<5}] {dt:6.2f}s  skipped={r.get('skipped')}  "
          f"reason={r.get('reason') or '-'}")
    if r.get("text"):
        print(f"          -> {r['text'][:130]}")
