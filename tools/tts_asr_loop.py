# -*- coding: utf-8 -*-
"""本机 TTS -> 本地 ASR 闭环，验证中文识别准确度"""
import sys
import io
import subprocess
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

OUT = ROOT / "out_audio"
MODELS = ROOT / "assets" / "models"
OUT.mkdir(exist_ok=True)

TEXT = "今天群里有人发了一段视频，里面有个人在解释怎么打这个副本，讲得挺清楚的。"

raw = OUT / "tts_raw.wav"
ps = ("Add-Type -AssemblyName System.Speech; "
      "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
      "$s.SelectVoice('Microsoft Huihui Desktop'); "
      f"$s.SetOutputToWaveFile('{raw}'); "
      f"$s.Speak('{TEXT}'); "
      "$s.Dispose()")
t0 = time.time()
subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=180, check=True)
print(f"TTS 生成 {time.time()-t0:.2f}s   {raw.stat().st_size // 1024} KB")

wav16 = OUT / "tts_16k.wav"
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav16)], check=True)

t0 = time.time()
text = af.transcribe_local(wav16, model=MODELS / "model.int8.onnx",
                           tokens=MODELS / "tokens.txt")
print(f"本地转录 {time.time()-t0:.2f}s")
print(f"原文 : {TEXT}")
print(f"识别 : {text}")
