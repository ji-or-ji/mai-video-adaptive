"""时间轴存储自测：落盘 / 反查 / 区间检索 / 淘汰。"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import timeline_store as ts  # noqa: E402

SEGS = [
    {"start": 0.0, "end": 14.8, "text": "一个人在厨房切菜"},
    {"start": 14.8, "end": 81.2, "text": "镜头切到户外，白色轿车驶过"},
    {"start": 600.0, "end": 620.0, "text": "PPT 页面，标题第三季度营收"},
]

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {name} {extra}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


with tempfile.TemporaryDirectory() as tmp:
    store = ts.TimelineStore(tmp, ttl_days=7, max_entries=3)
    sig = [{"gray": [1, 2, 3], "hist": [4, 5]}]
    key = ts.sig_key(sig)
    print(f"key = {key}  (len={len(key)})")

    print("\n[1] 落盘与读取")
    store.save(key, segments=SEGS, summary="00:00 厨房 → 00:15 户外 → 10:00 PPT",
               duration=634.5, group_id="1076711748", message_id="7684674063054906249",
               video_kept=True)
    check("文件已写", (Path(tmp) / "timeline" / f"{key}.json").exists())
    rec = store.load(key)
    check("segments 取回", rec and len(rec["segments"]) == 3)
    check("duration 保留", rec and abs(rec["duration"] - 634.5) < 0.01)
    check("摘要常驻", "厨房" in store.summary(key))

    print("\n[2] 按被引用消息反查")
    found = store.by_message("7684674063054906249")
    check("命中", found == key, f"-> {found}")
    check("未命中返回 None", store.by_message("000") is None)

    print("\n[3] 区间检索（按需查细节）")
    segs = store.find_segments(key, 595, 625)
    check("只取交集段", len(segs) == 1 and "PPT" in segs[0]["text"])
    check("空区间返回空", store.find_segments(key, 200, 300) == [])

    print("\n[4] 最近记录")
    time.sleep(0.01)
    key2 = ts.sig_key([{"gray": [9, 9], "hist": [1, 1]}])
    store.save(key2, segments=[], summary="第二条", group_id="1076711748",
               message_id="111")
    r = store.recent(group_id="1076711748", limit=1)
    check("返回最新那条", r and r[0]["key"] == key2)
    check("按群过滤生效", store.recent(group_id="other", limit=5) == [])

    print("\n[5] 条数上限淘汰（max_entries=3）")
    for i in range(4):
        k = ts.sig_key([{"gray": [i, i, i], "hist": [i]}])
        store.save(k, segments=[], summary=f"s{i}", group_id="g", message_id=f"m{i}")
    idx = store._load_index()
    check("索引不超上限", len(idx) <= 3, f"-> {len(idx)}")

    print("\n[6] TTL 淘汰")
    stale = ts.TimelineStore(tmp, ttl_days=0.00001, max_entries=99)
    stale.save("stalekey", segments=[], summary="old", now=time.time() - 99999)
    stale.purge()
    check("过期被清", stale.load("stalekey") is None)

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
