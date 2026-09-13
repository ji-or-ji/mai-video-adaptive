"""③ 级验证：重读某段时间区间。

模拟 read_video 工具的核心路径（不依赖麦麦 SDK）：
区间内固定密度补抽帧 → 交给视觉模型 → 得到该区间的细节描述。

用 loop5min.mp4（250 秒）模拟长视频，问它 100~108 秒发生了什么。
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

DS_KEY = "sk-be00893b96234425baae60b9148ef1ed"
OUT = ROOT / "tools" / "_reread_out"
VIDEO = ROOT / "assets" / "loop5min.mp4"


def extract_range(video: Path, start: float, end: float, n: int = 8) -> list:
    """区间内固定密度补抽帧（区间小，要精度不要效率）。"""
    out = OUT / f"{video.stem}_{int(start)}_{int(end)}"
    step = max(0.5, (end - start) / max(1, n))
    times = [start + i * step for i in range(n)]
    return af.extract_at(video, times, out, max_height=720)


def ask_frames(frames, prompt: str) -> str:
    import base64
    content = [{"type": "text", "text": prompt}]
    for f in frames:
        b = base64.b64encode(Path(f).read_bytes()).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b}})
    payload = {"model": "deepseek-flash",
               "messages": [{"role": "user", "content": content}],
               "max_tokens": 500, "temperature": 0.2,
               "thinking": {"type": "disabled"}}
    req = urllib.request.Request(
        af.DS_CHAT_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + DS_KEY})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = json.loads(r.read().decode("utf-8"))
    return str(body["choices"][0]["message"]["content"]).strip()


if __name__ == "__main__":
    print(f"视频：{VIDEO.name}  时长 {af.probe_media(VIDEO)['duration']:.1f}s\n")

    for (s, e) in [(100.0, 108.0), (200.0, 206.0)]:
        t0 = time.time()
        frames = extract_range(VIDEO, s, e, n=8)
        cost = time.time() - t0
        print(f"=== 重读 {af.fmt_ts(s)}~{af.fmt_ts(e)}：抽到 {len(frames)} 帧，"
              f"耗时 {cost:.1f}s ===")
        if not frames:
            print("  抽帧失败\n")
            continue
        prompt = (f"这是一段视频 {af.fmt_ts(s)}~{af.fmt_ts(e)} 之间的画面。"
                  "请完整描述这段区间里发生了什么：画面中的物体、文字、动作与变化。"
                  "只写能从画面确认的内容，不推测。")
        print("  " + ask_frames(frames, prompt).replace("\n", "\n  "))
        print()
