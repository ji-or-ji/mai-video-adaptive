"""演示：时间轴注入后，模型能否回答「某一时刻是什么」。

用 summary（实际注入上下文的那份）而非完整 segments，才是真实场景。
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

DS_KEY = "sk-be00893b96234425baae60b9148ef1ed"
URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
OUT = ROOT / "tools" / "_tl_out"
VIDEO = ROOT / "assets" / "video1.mp4"

QUESTIONS = [
    "52 秒那里是什么？",
    "视频里那个保险箱是怎么打开的？",
    "视频最后一秒是什么画面？",
    "视频里有人说话吗？",
]


def ask(context: str, question: str) -> str:
    prompt = (f"以下是群里某段视频的分段描述：\n{context}\n\n"
              f"有人问：{question}\n"
              "根据上面的描述回答，尽量简短。"
              "如果描述里没有能回答这个问题的信息，就直说不知道，不要猜。")
    payload = {"model": MODEL,
               "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 300, "temperature": 0.2,
               "thinking": {"type": "disabled"}}
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + DS_KEY})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.loads(r.read().decode("utf-8"))
    return str(body["choices"][0]["message"]["content"]).strip()


if __name__ == "__main__":
    print("理解中 ...")
    res = af.understand_video(VIDEO, out_dir=OUT, asr_mode="off",
                              vision_api_key=DS_KEY, use_cache=False)
    summary = res["summary"]
    print(f"\n===== 实际注入上下文的内容（{len(summary)} 字）=====")
    print(summary)
    print("\n===== 问答 =====")
    for q in QUESTIONS:
        print(f"\n[问] {q}")
        print(f"[答] {ask(summary, q)}")
