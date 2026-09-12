# -*- coding: utf-8 -*-
"""素材识别测试（不依赖宿主 SDK）"""
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))
import media as media_mod  # noqa: E402

CASES = [
    ("NapCat 视频占位",
     {"processed_plain_text": "[视频] 文件: 10767117487679816252150470293.mp4，大小: 332019"}),
    ("NapCat 文件占位（视频）",
     {"processed_plain_text": "[文件] Video_1746429489877.mp4，大小: 5694751，链接: https://gzc-download.ftn.qq.com/x.mp4"}),
    ("文件占位（非视频）",
     {"processed_plain_text": "[文件] 报告.pdf，大小: 1234"}),
    ("结构化 video 段",
     {"raw_message": [{"type": "video", "data": {"url": "https://example.com/a.mp4", "name": "a.mp4"}}]}),
    ("结构化 file 段",
     {"raw_message": [{"type": "file", "data": {"name": "clip.mov", "file_id": "abc123"}}]}),
    ("纯聊天无视频",
     {"processed_plain_text": "今天天气不错啊"}),
]

for label, msg in CASES:
    assets = media_mod.extract_assets(msg)
    desc = ", ".join(f"{a.name or a.file_ref or a.url}(src={a.source})" for a in assets) or "无"
    print(f"{label:<22} -> {len(assets)} 个: {desc}")

# 顺带确认 key 稳定
m = {"processed_plain_text": "[视频] 文件: x.mp4"}
a1 = media_mod.extract_assets(m)
a2 = media_mod.extract_assets(m)
print()
print("同一消息两次提取 key 一致:", a1[0].key == a2[0].key)
