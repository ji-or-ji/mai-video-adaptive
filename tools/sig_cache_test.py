# -*- coding: utf-8 -*-
"""签名缓存验证：同内容命中、无关内容不命中"""
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

ASSETS = ROOT / "assets"
STORE = ROOT / "out_e2e" / "sig_cache.json"

cache = af.VideoSignatureCache(STORE, match_threshold=0.9)


def sig(name):
    v = ASSETS / name
    info = af.probe_media(v)
    return cache.signature(v, info["duration"])


def check(label, s):
    r = cache.lookup(s)
    if r:
        print(f"{label:<28} 命中  相似度={r['similarity']:.2f}  来源={r['source']}")
    else:
        print(f"{label:<28} 未命中")


print("=== 签名缓存测试 ===")
s1 = sig("video1.mp4")
print(f"video1 签名帧数: {len(s1)}")
check("查 video1（自己）", s1)          # 未存入前应不命中

cache.remember(s1, "《暗区突围》实战：搜刮物资后撤离，收益 76 万。", "video1.mp4")
print("已存入 video1 的描述")
print()

check("查 video1（自己）", sig("video1.mp4"))        # 应命中
check("查 video1 重编码副本", sig("video1_recode.mp4"))  # 应命中
check("查 video2（无关）", sig("video2.mp4"))          # 应不命中
check("查循环视频（无关）", sig("loop5min.mp4"))        # 应不命中

print()
print(f"缓存条目数: {len(cache.entries)}")
print(f"缓存文件大小: {STORE.stat().st_size // 1024 if STORE.exists() else 0} KB")
