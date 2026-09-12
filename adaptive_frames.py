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
import subprocess
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
                       seconds_per_frame: float = 5.0) -> int:
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


# ---------------- 规划与主入口 ----------------

@dataclass
class Plan:
    duration: float
    frame_count: int
    times: list = field(default_factory=list)
    note: str = ""


def plan_frames(video: Path, *, min_frames: int = 8, max_frames: int = 24,
                bucket_s: float = 0.5, seconds_per_frame: float = 5.0,
                smooth_win: int = 3) -> Plan:
    """只做规划，不抽帧。"""
    info = probe_media(video)
    duration = info["duration"]
    packets = probe_packets(video)
    centers, dens = build_density(packets, duration, bucket_s)
    dens = smooth(dens, smooth_win)
    n = decide_frame_count(duration, min_frames, max_frames, seconds_per_frame)
    times = pick_times(centers, dens, n, duration)
    return Plan(duration=duration, frame_count=len(times), times=times,
                note=f"packets={len(packets)} buckets={len(centers)}")


def adaptive_extract(video: Path, out_dir: Path, *, min_frames: int = 8,
                     max_frames: int = 24, bucket_s: float = 0.5,
                     seconds_per_frame: float = 5.0, smooth_win: int = 3,
                     max_height: int = 720):
    """规划 + 抽帧。返回 (Plan, [图片路径])。"""
    plan = plan_frames(video, min_frames=min_frames, max_frames=max_frames,
                       bucket_s=bucket_s, seconds_per_frame=seconds_per_frame,
                       smooth_win=smooth_win)
    frames = extract_at(video, plan.times, out_dir, max_height=max_height)
    return plan, frames


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
