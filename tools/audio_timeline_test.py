"""音频时间轴验证：按静音切分语音区间，逐段转录，输出带时间戳的文本。

现状问题：audio_transcript 返回整段文本，且转录的是去静音后的音频，
时间轴与原视频错位，导致「10:03 说了什么」答不了。

这里验证解法：silencedetect 反推语音区间 → 切段转录 → 每段带时间。
"""
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

MODEL = ROOT / "assets" / "models" / "model.int8.onnx"
TOKENS = ROOT / "assets" / "models" / "tokens.txt"
OUT = ROOT / "tools" / "_audio_tl_out"
FF = af.FFMPEG


def probe_silences(wav: Path, noise_db=-30.0, min_dur=0.35):
    """返回静音区间 [(start, end), ...]。"""
    p = subprocess.run(
        [FF, "-hide_banner", "-i", str(wav), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace", timeout=600)
    out = p.stderr or ""
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", out)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", out)]
    return list(zip(starts, ends))


def speech_spans(silences, total: float):
    """由静音区间反推语音区间。"""
    spans, cursor = [], 0.0
    for s, e in silences:
        if s > cursor + 0.05:
            spans.append((cursor, s))
        cursor = max(cursor, e)
    if total > cursor + 0.05:
        spans.append((cursor, total))
    return spans


def merge_spans(spans, max_len=60.0, gap=1.2):
    """把碎语音区间合并成块（减少 ASR 调用次数）。"""
    out = []
    for s, e in spans:
        if out and (e - out[-1][0]) <= max_len and (s - out[-1][1]) <= gap:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [(a, b) for a, b in out]


def cut_wav(src: Path, dst: Path, start: float, end: float):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
                   capture_output=True, timeout=300)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "speech_demo.mp4"
    video = ROOT / "assets" / name
    OUT.mkdir(parents=True, exist_ok=True)
    wav = OUT / (video.stem + ".wav")

    af.extract_audio(video, wav)
    info = af.probe_media(video)
    total = float(info["duration"])

    sil = probe_silences(wav)
    spans = speech_spans(sil, total)
    blocks = merge_spans(spans, max_len=60.0)
    print(f"{name}  时长 {total:.1f}s")
    print(f"静音 {len(sil)} 段 → 语音 {len(spans)} 段 → 合并成 {len(blocks)} 块\n")

    if MODEL.exists():
        t0 = time.time()
        af.load_local_recognizer(MODEL, TOKENS, 2)
        print(f"本地模型加载 {time.time() - t0:.2f}s")
    else:
        print("模型缺失，退出")
        sys.exit(1)

    for i, (a, b) in enumerate(blocks):
        seg = OUT / f"blk{i}_{a:.1f}_{b:.1f}.wav"
        cut_wav(wav, seg, a, b)
        t0 = time.time()
        txt = af.transcribe_local(seg, model=MODEL, tokens=TOKENS, num_threads=2)
        dt = time.time() - t0
        print(f"[{af.fmt_ts(a)}~{af.fmt_ts(b)}] ({dt:.2f}s) {txt.strip()}")

    af.unload_local_recognizer()
