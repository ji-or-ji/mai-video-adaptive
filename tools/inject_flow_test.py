"""集成交替测试：模拟 _handle → 落盘 → _session_latest → 注入 block。

不依赖麦麦 SDK：把 plugin.py 的接线逻辑用最小复刻跑一遍，
验证「完整时间轴落盘 + 摘要注入带标识」这条链路是通的。
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402
import timeline_store as tl_store  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {name} {extra}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


# 复刻 plugin._remember + 注入 block 拼接
def remember(store, desc, sig, duration, stream_id):
    if store is None or not sig:
        return ""
    key = tl_store.sig_key(sig)
    store.save(key, segments=desc.get("segments") or [],
               summary=desc.get("summary") or desc.get("text") or "",
               duration=float(duration or 0.0), group_id=stream_id)
    return key


def build_block(record):
    text = str(record["text"]).strip()
    ref = str(record.get("key") or "").strip()
    if ref:
        text = f"（视频#{ref}）\n{text}"
    return f"[视频内容] {text}"


with tempfile.TemporaryDirectory() as tmp:
    store = tl_store.TimelineStore(tmp, ttl_days=7, max_entries=50)
    sig = [{"gray": [0.1, 0.2], "hist": [0.3]}]
    segs_full = [
        {"start": float(i * 60), "end": float(i * 60 + 58),
         "text": f"第{i}分钟的画面描述，此处有较长的细节内容，用于对照压缩后的效果"}
        for i in range(12)
    ]
    segs_full[10] = {"start": 600.0, "end": 620.0,
                     "text": "PPT 页面，标题第三季度营收"}
    desc = {
        "segments": segs_full,
        "summary": "00:00 开头\n10:00 PPT 页面（第三季度营收）",
        "text": "00:00 开头\n10:00 PPT 页面（第三季度营收）",
    }

    print("[1] 落盘")
    key = remember(store, desc, sig, 634.5, "1076711748")
    check("返回短标识", len(key) == 16, f"-> {key}")
    check("时间轴文件已写", (Path(tmp) / "timeline" / f"{key}.json").exists())
    check("索引已写", (Path(tmp) / "timeline_index.json").exists())

    print("\n[2] 注入 block 形态")
    record = {"text": desc["text"], "key": key, "ts": time.time()}
    block = build_block(record)
    check("带视频标识", f"视频#{key}" in block)
    check("带摘要正文", "开头" in block)
    check("标识在正文之前", block.index("视频#") < block.index("开头"))

    print(f"\n注入内容预览（{len(block)} 字符）：")
    print("  " + block.replace("\n", "\n  "))

    print("\n[3] 按需查细节（省下的常驻，以一次查询还回来）")
    segs = store.find_segments(key, 595, 625)
    check("查到目标段", any("第三季度" in s["text"] for s in segs),
          f"-> 命中 {len(segs)} 段")
    full = store.load(key)
    check("完整时间轴比摘要长",
          sum(len(s["text"]) for s in full["segments"]) > len(desc["summary"]))

    print("\n[4] 引用消息反查")
    store.save("refkey00000000", segments=[], summary="x",
               message_id="7684674063054906249")
    check("按引用定位", store.by_message("7684674063054906249") == "refkey00000000")

    print("\n[5] 常驻体积（固定上限，与视频长度无关）")
    print(f"  摘要 {len(desc['summary'])} 字符 vs 完整时间轴 "
          f"{sum(len(s['text']) for s in full['segments'])} 字符")

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
