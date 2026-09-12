# -*- coding: utf-8 -*-
"""ASR 测试：抽音轨 + 硅基 SenseVoiceSmall 转录"""
import sys
import io
import time
import json
import subprocess
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
OUT = ROOT / "out_audio"
OUT.mkdir(exist_ok=True)

SF_KEY = "sk-qdhlistemmaiyugdwoolswsamxgepmcqxlswhibytsjcgwid"
SF_ASR = "https://api.siliconflow.cn/v1/audio/transcriptions"
MODEL = "FunAudioLLM/SenseVoiceSmall"


def extract_audio(video: Path, out_wav: Path, sr: int = 16000) -> float:
    t0 = time.time()
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr),
           "-c:a", "pcm_s16le", str(out_wav)]
    subprocess.run(cmd, check=True, timeout=600)
    return time.time() - t0


def transcribe(path: Path):
    boundary = "----maasrboundary"
    data = Path(path).read_bytes()
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{MODEL}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode(),
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(SF_ASR, data=body, headers={
        "Authorization": "Bearer " + SF_KEY,
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return time.time() - t0, resp


for name in ["video1.mp4", "video2.mp4"]:
    v = ASSETS / name
    if not v.exists():
        continue
    wav = OUT / (v.stem + ".wav")
    print("=" * 60)
    te = extract_audio(v, wav)
    print(f"{name}: 抽音轨 {te:.2f}s, wav {wav.stat().st_size // 1024} KB")
    try:
        tt, resp = transcribe(wav)
        txt = resp.get("text") or json.dumps(resp, ensure_ascii=False)[:300]
        print(f"  转录耗时 {tt:.2f}s")
        print(f"  文本: {str(txt)[:300]}")
    except Exception as e:
        print(f"  转录失败: {type(e).__name__}: {e}")
