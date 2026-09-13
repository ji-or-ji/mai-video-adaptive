"""对比：时间轴模式 vs 旧的「一段话概括」模式，token 消耗差多少。

用同一段视频、同一批帧，只换 prompt，看 usage 差异。
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

DS_KEY = "sk-be00893b96234425baae60b9148ef1ed"
VIDEO = ROOT / "assets" / "video1.mp4"
OUT = ROOT / "tools" / "_cost_out"


def measure(prompt: str, frames, tag: str, frame_times=None, max_tokens=600):
    content = [{"type": "text", "text": prompt}]
    for i, f in enumerate(frames):
        if frame_times and i < len(frame_times):
            content.append({"type": "text", "text": f"[{af.fmt_ts(frame_times[i])}]"})
        import base64
        b = base64.b64encode(Path(f).read_bytes()).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b}})
    payload = {"model": "deepseek-flash",
               "messages": [{"role": "user", "content": content}],
               "max_tokens": max_tokens, "thinking": {"type": "disabled"}}
    if frame_times:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        af.DS_CHAT_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + DS_KEY})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = json.loads(r.read().decode("utf-8"))
    u = body.get("usage") or {}
    text = body["choices"][0]["message"]["content"]
    print(f"[{tag}] prompt={u.get('prompt_tokens')} "
          f"completion={u.get('completion_tokens')} total={u.get('total_tokens')} "
          f"输出字符={len(text)}")
    return u, text


if __name__ == "__main__":
    plan, frames = af.adaptive_extract(VIDEO, OUT / "frames")
    print(f"帧数={len(frames)}  时长={plan.duration:.1f}s\n")

    old_p = af.build_vision_prompt("")
    old_u, old_text = measure(old_p, frames, "旧:整体概括", max_tokens=500)

    new_p = af.build_timeline_prompt(plan.times, "")
    new_u, new_text = measure(new_p, frames, "新:时间轴", frame_times=plan.times,
                              max_tokens=2660)

    parsed = af.parse_timeline(new_text, duration=plan.duration)
    summary = parsed["summary"]
    print(f"\n注入摘要字符数={len(summary)}  (旧模式注入={len(old_text)})")

    print("\n=== 折算（按每帧图约 300~400 token 计，仅看差额）===")
    d_out = (new_u.get("completion_tokens") or 0) - (old_u.get("completion_tokens") or 0)
    d_in = (new_u.get("prompt_tokens") or 0) - (old_u.get("prompt_tokens") or 0)
    print(f"理解阶段：输入 +{d_in} token，输出 +{d_out} token")
    print(f"注入阶段：{len(summary)} 字符 vs {len(old_text)} 字符")
