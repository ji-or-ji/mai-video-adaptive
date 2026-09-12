# -*- coding: utf-8 -*-
"""
自适应抽帧核心模块（原型 v1）

思路
----
1. ffprobe 读出视频每个 packet 的时间戳与字节数
2. 按时间分桶，用桶内码率作为「画面信息密度」的代理指标
3. 目标帧数由时长决定，并夹在 [min_frames, max_frames] 内（成本护栏）
4. 把密度曲线归一化成累积分布 (CDF)，在累积轴上均匀撒 N 个点
   -> 密度高处占据更宽的累积区间，自然被更多点命中
5. 得到一串不均匀的抽帧时间点，交给 ffmpeg 逐点抽取

对外主入口：plan_frames() / adaptive_extract()
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


# ---------------- 基础执行 ----------------

def _run(cmd, timeout=180):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout, errors="replace")
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "").strip()[:400])
    return p.stdout


# ---------------- 探测 ----------------

def probe_media(video: Path) -> dict:
    """取时长 / 帧率 / 尺寸等基础信息。"""
    out = _run([
        FFPROBE, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", "-select_streams", "v:0", str(video),
    ])
    data = json.loads(out)
    fmt = data.get("format", {}) or {}
    st = (data.get("streams") or [{}])[0]
    duration = float(fmt.get("duration") or st.get("duration") or 0.0)
    fps_raw = st.get("avg_frame_rate") or "0/0"
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps = 0.0
    return {
        "duration": duration,
        "fps": fps,
        "width": int(st.get("width") or 0),
        "height": int(st.get("height") or 0),
    }


def probe_packets(video: Path) -> list:
    """返回 [(pts_time, size), ...]，仅视频流，按时间排序。"""
    out = _run([
        FFPROBE, "-v", "quiet", "-print_format", "json",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,size", str(video),
    ])
    data = json.loads(out)
    res = []
    for pk in data.get("packets", []):
        t, s = pk.get("pts_time"), pk.get("size")
        if t is None or s is None:
            continue
        try:
            res.append((float(t), int(s)))
        except (TypeError, ValueError):
            continue
    res.sort(key=lambda x: x[0])
    return res


# ---------------- 密度 ----------------

def build_density(packets, duration: float, bucket_s: float = 0.5):
    """按时间分桶，返回 (桶中心时间, 密度)。密度 = 桶内平均码率(B/s)。"""
    if duration <= 0 or not packets:
        return [], []
    n = max(1, int(math.ceil(duration / bucket_s)))
    acc = [0.0] * n
    for t, size in packets:
        idx = min(n - 1, max(0, int(t / bucket_s)))
        acc[idx] += size
    centers = [(i + 0.5) * bucket_s for i in range(n)]
    dens = [a / bucket_s for a in acc]
    return centers, dens


def smooth(dens, win: int = 3):
    """滑动平均，压掉单帧噪声。"""
    if win <= 1 or len(dens) <= 2:
        return list(dens)
    half = win // 2
    out = []
    for i in range(len(dens)):
        lo = max(0, i - half)
        hi = min(len(dens), i + half + 1)
        seg = dens[lo:hi]
        out.append(sum(seg) / len(seg))
    return out


# ---------------- 帧数决策 ----------------

def decide_frame_count(duration: float, min_frames: int, max_frames: int,
                       seconds_per_frame: float = 4.0) -> int:
    """时长决定基础帧数，再夹进上下限。"""
    base = int(round(duration / max(0.5, seconds_per_frame)))
    return int(max(min_frames, min(max_frames, base)))


# ---------------- 采样 ----------------

def pick_times(centers, dens, n_frames: int, duration: float):
    """CDF 均匀撒点。返回升序、去重后的时间点。"""
    if not centers or n_frames <= 0:
        return []
    total = sum(dens)
    if total <= 0:
        return [duration * (i + 0.5) / n_frames for i in range(n_frames)]
    cum, s = [], 0.0
    for d in dens:
        s += d
        cum.append(s / total)
    times = []
    for k in range(n_frames):
        q = (k + 0.5) / n_frames
        lo, hi = 0, len(cum) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cum[mid] < q:
                lo = mid + 1
            else:
                hi = mid
        times.append(min(centers[lo], max(0.0, duration - 0.02)))
    uniq = []
    for t in times:
        if not uniq or t - uniq[-1] > 1e-6:
            uniq.append(t)
    return uniq


# ---------------- 抽帧 ----------------

def extract_at(video: Path, times, out_dir: Path,
               max_height: int = 720, quality: int = 4):
    """按时间点逐点抽帧并等比缩小（不放大）。返回图片路径列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"scale=-2:'min({max_height},ih)'"
    paths = []
    for i, t in enumerate(times):
        out = out_dir / f"f{i:03d}_{t:07.2f}s.jpg"
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{t:.3f}", "-i", str(video),
               "-frames:v", "1", "-vf", vf,
               "-q:v", str(quality), str(out)]
        subprocess.run(cmd, capture_output=True, timeout=90)
        if out.exists() and out.stat().st_size > 0:
            paths.append(out)
    return paths


# ---------------- 探针指纹与新颖率 ----------------

def probe_thumbnails(video: Path, duration: float, samples: int = 32,
                     px: int = 8):
    """一次 ffmpeg 调用抽出 samples 张 px*px 的 RGB 原始图。

    均匀采样，用于估算「画面新颖率」。本地抽帧，零 API 成本。
    """
    rate = max(1e-6, samples / max(0.1, duration))
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(video),
           "-vf", f"fps={rate:.6f},scale={px}:{px}:flags=bilinear",
           "-frames:v", str(max(1, samples)),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=300)
    fb = px * px * 3
    data = p.stdout
    return [data[i:i + fb] for i in range(0, len(data) - fb + 1, fb)]


def fingerprint(raw: bytes, bins: int = 16) -> dict:
    """RGB 原始图 -> 指纹：灰度看构图，直方图看色彩分布。"""
    gray = []
    for i in range(0, len(raw) - 2, 3):
        gray.append(0.299 * raw[i] + 0.587 * raw[i + 1] + 0.114 * raw[i + 2])
    hist = [0.0] * (3 * bins)
    n = len(raw) // 3
    if n:
        for i in range(0, len(raw) - 2, 3):
            for c in range(3):
                hist[c * bins + min(bins - 1, raw[i + c] * bins // 256)] += 1
        hist = [x / n for x in hist]
    return {"gray": gray, "hist": hist}


def _cos(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


def fp_similarity(f1: dict, f2: dict, w_gray: float = 0.5,
                  w_hist: float = 0.5) -> float:
    """指纹相似度 0~1。"""
    return w_gray * _cos(f1["gray"], f2["gray"]) + w_hist * _cos(f1["hist"], f2["hist"])


def novelty_scan(video: Path, duration: float, samples: int = 32,
                 px: int = 8, threshold: float = 0.98):
    """估算内容新颖率。

    做法：把探针帧按时间对半切，看后半段的帧有多少能在前半段找到
    高度相似的匹配。循环重复的内容（如 5 秒循环播放），后半段几乎
    全部能被前半覆盖，新颖率趋近 0；内容一路推进的视频则接近 1。

    返回 (新颖率, 重复度, 全部探针指纹)。
    """
    raws = probe_thumbnails(video, duration, samples, px)
    all_fps = [fingerprint(r) for r in raws]
    n = len(all_fps)
    if n < 4:
        return 1.0, 0.0, all_fps
    half = n // 2
    front, back = all_fps[:half], all_fps[half:]
    matched = 0
    for b in back:
        if max(fp_similarity(b, f) for f in front) >= threshold:
            matched += 1
    repeat = matched / len(back)
    return 1.0 - repeat, repeat, all_fps


def adaptive_plan(duration: float, novelty: float, *,
                  seconds_per_frame: float = 4.0,
                  min_floor: int = 3, max_cap: int = 48,
                  novelty_power: float = 1.5):
    """由「时长 × 新颖率」推出帧数区间与目标帧数。

    有效内容量 eff = 时长 × 新颖率^power。
    重复内容（新颖率→0）无论多长，eff 都趋近 0，帧数被压到下限附近；
    内容丰富（新颖率→1）则随时长增长，最终由 max_cap 封顶。

    返回 (min_frames, max_frames, target)。
    """
    ratio = max(0.0, min(1.0, novelty)) ** novelty_power
    eff = duration * ratio
    raw = eff / max(0.5, seconds_per_frame)

    lo = max(min_floor, int(round(raw * 0.5)))
    hi = int(round(raw * 1.4)) if raw > 0 else min_floor + 2
    hi = min(max_cap, max(lo + 1, hi))
    lo = min(lo, hi - 1)
    target = max(lo, min(hi, int(round(raw)) if raw > 0 else lo))
    return lo, hi, target


# ---------------- 规划与主入口 ----------------

@dataclass
class Plan:
    duration: float
    frame_count: int
    times: list = field(default_factory=list)
    min_frames: int = 0
    max_frames: int = 0
    novelty: float = -1.0
    note: str = ""


def plan_frames(video: Path, *, min_frames: int = None, max_frames: int = None,
                bucket_s: float = 0.5, seconds_per_frame: float = 4.0,
                smooth_win: int = 3, adaptive: bool = True,
                novelty_samples: int = 32, novelty_px: int = 8,
                novelty_threshold: float = 0.97, max_cap: int = 48) -> Plan:
    """只做规划，不抽帧。

    min_frames / max_frames 为 None 时启用「时长 × 新颖率」自适应区间；
    显式传入则按传入值固定。
    """
    info = probe_media(video)
    duration = info["duration"]
    packets = probe_packets(video)
    centers, dens = build_density(packets, duration, bucket_s)
    dens = smooth(dens, smooth_win)

    novelty = -1.0
    if adaptive and min_frames is None and max_frames is None:
        novelty, _repeat, _allf = novelty_scan(video, duration, novelty_samples,
                                               novelty_px, novelty_threshold)
        lo, hi, n = adaptive_plan(duration, novelty,
                                  seconds_per_frame=seconds_per_frame,
                                  max_cap=max_cap)
    else:
        lo = 8 if min_frames is None else int(min_frames)
        hi = 24 if max_frames is None else int(max_frames)
        lo = max(1, min(lo, hi - 1))
        n = decide_frame_count(duration, lo, hi, seconds_per_frame)

    times = pick_times(centers, dens, n, duration)
    return Plan(duration=duration, frame_count=len(times), times=times,
                min_frames=lo, max_frames=hi, novelty=novelty,
                note=f"packets={len(packets)} buckets={len(centers)}")


def adaptive_extract(video: Path, out_dir: Path, *, min_frames: int = None,
                     max_frames: int = None, bucket_s: float = 0.5,
                     seconds_per_frame: float = 4.0, smooth_win: int = 3,
                     max_height: int = 720, adaptive: bool = True,
                     novelty_samples: int = 32, novelty_threshold: float = 0.97,
                     max_cap: int = 48):
    """规划 + 抽帧。返回 (Plan, [图片路径])。"""
    plan = plan_frames(video, min_frames=min_frames, max_frames=max_frames,
                       bucket_s=bucket_s, seconds_per_frame=seconds_per_frame,
                       smooth_win=smooth_win, adaptive=adaptive,
                       novelty_samples=novelty_samples,
                       novelty_threshold=novelty_threshold, max_cap=max_cap)
    frames = extract_at(video, plan.times, out_dir, max_height=max_height)
    return plan, frames


# ---------------- 音频轨道 ----------------

SF_ASR_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
DEFAULT_ASR_MODEL = "FunAudioLLM/SenseVoiceSmall"


def _probe_duration(path: Path) -> float:
    out = _run([FFPROBE, "-v", "quiet", "-print_format", "json",
                "-show_format", str(path)])
    return float((json.loads(out).get("format") or {}).get("duration") or 0.0)


def probe_audio(video: Path) -> dict:
    """探测音轨。无音轨返回 {'has_audio': False}。"""
    p = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-select_streams", "a:0", "-show_streams", str(video)],
        capture_output=True, text=True, timeout=60, errors="replace")
    try:
        streams = (json.loads(p.stdout or "{}").get("streams")) or []
    except Exception:
        streams = []
    if not streams:
        return {"has_audio": False}
    st = streams[0]
    return {
        "has_audio": True,
        "codec": st.get("codec_name"),
        "channels": st.get("channels"),
        "sample_rate": st.get("sample_rate"),
    }


def extract_audio(video: Path, out_wav: Path, sr: int = 16000) -> Path:
    """抽音轨为单声道 16k wav。"""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr),
                    "-c:a", "pcm_s16le", str(out_wav)],
                   check=True, timeout=900)
    return out_wav


def detect_silence(wav: Path, noise_db: float = -30.0, min_dur: float = 0.5):
    """返回 (静音区间列表, 总时长)。"""
    p = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(wav),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=900, errors="replace")
    silences, cur = [], None
    for line in (p.stderr or "").splitlines():
        if "silence_start:" in line:
            try:
                cur = float(line.split("silence_start:")[1].split()[0])
            except (IndexError, ValueError):
                cur = None
        elif "silence_end:" in line and cur is not None:
            try:
                silences.append((cur, float(line.split("silence_end:")[1].split()[0])))
            except (IndexError, ValueError):
                pass
            cur = None
    return silences, _probe_duration(wav)


def audio_speech_ratio(wav: Path, noise_db: float = -30.0,
                       min_dur: float = 0.5):
    """返回 (有声比例, 静音区间, 总时长)。静音占比越低，有声比例越高。"""
    silences, dur = detect_silence(wav, noise_db, min_dur)
    if dur <= 0:
        return 0.0, silences, dur
    silent = sum(max(0.0, e - s) for s, e in silences)
    return max(0.0, 1.0 - min(1.0, silent / dur)), silences, dur


def strip_silence(wav: Path, out_wav: Path, noise_db: float = -30.0,
                  min_dur: float = 0.5) -> Path:
    """去掉静音段，输出精简音频。"""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
         "-af", f"silenceremove=stop_periods=-1:stop_duration={min_dur}:"
                f"stop_threshold={noise_db}dB",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)],
        check=True, timeout=900)
    return out_wav


def transcribe_audio(wav: Path, *, api_key: str, url: str = SF_ASR_URL,
                     model: str = DEFAULT_ASR_MODEL, timeout: float = 300.0) -> str:
    """调 ASR 接口转录，返回文本。"""
    boundary = "----maasrboundary"
    data = Path(wav).read_bytes()
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode(),
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return str(resp.get("text") or "")


def transcript_core(text: str) -> str:
    """提取转录里的实质字符（去掉音乐符号、标点、空白）。"""
    if not text:
        return ""
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))


def audio_transcript(video: Path, *, out_dir: Path, api_key: str,
                     noise_db: float = -30.0, min_silence: float = 0.5,
                     min_speech_ratio: float = 0.02, min_core_chars: int = 4,
                     timeout: float = 30.0,
                     model: str = DEFAULT_ASR_MODEL) -> dict:
    """完整音频链路：抽轨 → 静音分析 → 去静音 → 转录。

    返回 dict：has_audio / speech_ratio / text / skipped / reason。
    无音轨、几乎全静音、转录无实质内容时 skipped=True。
    """
    info = probe_audio(video)
    if not info.get("has_audio"):
        return {"has_audio": False, "skipped": True, "reason": "no_audio", "text": ""}

    out_dir.mkdir(parents=True, exist_ok=True)
    wav = extract_audio(video, out_dir / (video.stem + ".wav"))
    ratio, _, dur = audio_speech_ratio(wav, noise_db, min_silence)
    if ratio < min_speech_ratio:
        return {"has_audio": True, "speech_ratio": ratio, "skipped": True,
                "reason": "silent", "text": ""}

    lean = strip_silence(wav, out_dir / (video.stem + ".lean.wav"),
                         noise_db, min_silence)
    try:
        text = transcribe_audio(lean, api_key=api_key, model=model,
                                timeout=timeout)
    except Exception as e:  # 超时或上游异常：降级，不阻塞
        return {"has_audio": True, "speech_ratio": ratio, "skipped": True,
                "reason": "asr_failed", "error": type(e).__name__, "text": ""}
    core = transcript_core(text)
    if len(core) < min_core_chars:
        return {"has_audio": True, "speech_ratio": ratio, "skipped": True,
                "reason": "no_speech_content", "text": text}
    return {"has_audio": True, "speech_ratio": ratio, "skipped": False,
            "duration": dur, "text": text}


# ---------------- 本地 ASR（可选，无网络） ----------------

_LOCAL_ASR_CACHE = {}


def _read_wav_mono(path: Path):
    """读单声道 16bit wav，返回 (采样率, [-1,1] 浮点样本)。"""
    import wave
    import array
    with wave.open(str(path), "rb") as f:
        if f.getnchannels() != 1:
            raise ValueError("需要单声道 wav")
        sr = f.getframerate()
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return sr, [s / 32768.0 for s in a]


def load_local_recognizer(model: Path, tokens: Path, num_threads: int = 2):
    """加载并缓存 sherpa-onnx SenseVoice 识别器。"""
    key = (str(model), str(tokens), num_threads)
    rec = _LOCAL_ASR_CACHE.get(key)
    if rec is None:
        import sherpa_onnx
        rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model), tokens=str(tokens), num_threads=num_threads,
            use_itn=True, language="auto", debug=False)
        _LOCAL_ASR_CACHE[key] = rec
    return rec


def transcribe_local(wav: Path, *, model: Path, tokens: Path,
                     num_threads: int = 2) -> str:
    """本地 sherpa-onnx SenseVoice 转录（无网络）。"""
    rec = load_local_recognizer(model, tokens, num_threads)
    sr, samples = _read_wav_mono(wav)
    s = rec.create_stream()
    s.accept_waveform(sr, samples)
    rec.decode_stream(s)
    return str(s.result.text or "")


# ---------------- 调试可视化 ----------------

def ascii_density(centers, dens, times, width: int = 64, rows: int = 10):
    """把密度曲线画成文本柱状图，并标出抽帧位置。"""
    if not centers:
        return ""
    peak = max(dens) or 1.0
    n = len(dens)
    lines = []
    for r in range(rows, 0, -1):
        thr = peak * r / rows
        line = ""
        for i in range(n):
            line += "#" if dens[i] >= thr else " "
        lines.append(line)
    marks = [" "] * n
    for t in times:
        i = min(n - 1, max(0, int(t / (centers[1] - centers[0])) if n > 1 else 0))
        marks[i] = "^"
    lines.append("".join(marks))
    return "\n".join(lines)
