r"""安全测试：_safe_child 的越界防护。

asset.name / file_ref 来自消息文本，可被伪造。这里验证：
  - `..\..\x`、`/etc/passwd`、`C:\Windows\x` 一律拒绝
  - 正常文件名（含中文、空格）正常通过
  - 返回值必定落在 base 目录内

为导入 plugin.py，先塞一个最小的 maibot_sdk 占位模块（本机没装麦麦）。
"""
import sys
import tempfile
import types
from pathlib import Path

# ---- 最小 SDK 占位（只为让 plugin.py 能 import）----
_sdk = types.ModuleType("maibot_sdk")


class _Base:
    pass


def _field(default=None, **_kw):
    return default


_sdk.Field = _field
_sdk.HookHandler = lambda *a, **k: (lambda f: f)
_sdk.MaiBotPlugin = _Base
_sdk.PluginConfigBase = _Base
_sdk.Tool = lambda *a, **k: (lambda f: f)
sys.modules["maibot_sdk"] = _sdk

_t = types.ModuleType("maibot_sdk.types")
_t.ErrorPolicy = type("ErrorPolicy", (), {"SKIP": 1})
_t.HookMode = type("HookMode", (), {"BLOCKING": 1, "NON_BLOCKING": 2})
_t.HookOrder = type("HookOrder", (), {"NORMAL": 1})
_t.ToolParameterInfo = lambda **kw: kw
_t.ToolParamType = type("ToolParamType", (), {
    "STRING": "string", "FLOAT": "float",
    "INTEGER": "integer", "BOOLEAN": "boolean",
})
sys.modules["maibot_sdk.types"] = _t

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import plugin as P  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {name} {extra}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


REJECT = ["", "   ", ".", ".."]

# 路径穿越类：实现是「取 basename 后拼回 base」，所以会收敛到 base 内，
# 不返回 None。关键是不能逃出 base。
CONVERGE = [
    (r"..\..\..\Windows\System32\config\SAM", "SAM"),
    ("../../etc/passwd", "passwd"),
    (r"..\video.mp4", "video.mp4"),
    ("/etc/passwd", "passwd"),
    (r"C:\Windows\notepad.exe", "notepad.exe"),
    ("sub/dir/video.mp4", "video.mp4"),
]

GOOD = [
    "video.mp4",
    "用一首歌的时间，带你看看不一样的新余四中_114278778672044.mp4",
    "a b c.mp4",
]

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp) / "videos"
    base.mkdir()

    print("[1] 空/点路径必须被拒绝")
    for name in REJECT:
        got = P.VideoUnderstandPlugin._safe_child(base, name)
        check(f"拒绝 {name!r}", got is None, f"-> {got}")

    print("\n[2] 路径穿越必须收敛到 base 内（取 basename）")
    for raw, expect_leaf in CONVERGE:
        got = P.VideoUnderstandPlugin._safe_child(base, raw)
        inside = bool(got) and got.parent == base.resolve()
        check(f"收敛 {raw[:24]!r}", inside and got.name == expect_leaf,
              f"-> {got.name if got else None}")

    print("\n[3] 正常文件名必须通过，且落在 base 内")
    for name in GOOD:
        got = P.VideoUnderstandPlugin._safe_child(base, name)
        inside = bool(got) and base.resolve() in got.parents
        check(f"通过 {name[:20]!r}", got is not None and inside,
              f"-> {got.name if got else None}")

    print("\n[4] 关键断言：任何输入都不得逃出 base")
    escaped = []
    for name in REJECT + [r for r, _ in CONVERGE] + GOOD:
        got = P.VideoUnderstandPlugin._safe_child(base, name)
        if got is not None and base.resolve() not in got.parents:
            escaped.append((name, str(got)))
    check("无越界", not escaped, f"-> {escaped}")

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
