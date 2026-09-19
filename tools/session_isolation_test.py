r"""会话隔离测试：视频摘要只能注入本会话，绝不串台。

背景：早期实现里有个「全局兜底」——本会话找不到记录时取全局最新那条，
导致 A 群的视频被注入到 B 群甚至私聊。这里把它钉死。

为导入 plugin.py，先塞一个最小的 maibot_sdk 占位模块。
"""
import asyncio
import sys
import time
import types
from pathlib import Path

_sdk = types.ModuleType("maibot_sdk")


class _Base:
    pass


_sdk.Field = lambda default=None, **_kw: default
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


class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


class _Ctx:
    logger = _Logger()


class _Cfg:
    class plugin:
        enabled = True


def make_plugin():
    inst = P.VideoUnderstandPlugin.__new__(P.VideoUnderstandPlugin)
    inst.ctx = _Ctx()
    inst.config = _Cfg()
    inst._session_latest = {}
    return inst


def inject(inst, sid):
    """跑一次注入钩子，返回注入的文本（未注入返回 None）。"""
    items = []
    res = asyncio.run(P.VideoUnderstandPlugin.on_before_model_request(
        inst, None, items=items, session_id=sid))
    if not res or res.get("action") != "continue":
        return None
    new_items = res["modified_kwargs"]["items"]
    if not new_items:
        return None
    parts = new_items[-1].get("parts") or [{}]
    return str(parts[0].get("text") or "")


GROUP_A = "3aeb34ec86d3ec02ee18b52a91a7bd73"
PRIVATE_B = "5f2b1d04aa19bb77cc33ee11ff22aa33"

inst = make_plugin()

print("[1] 群里发视频 -> 群会话能拿到")
inst._session_latest[GROUP_A] = {
    "text": "00:02 游戏内商店界面…", "ts": time.time(),
    "key": "b04c3c4507f2e37d", "segments": [], "summary": "00:02 游戏内商店界面…"}
out = inject(inst, GROUP_A)
check("同会话注入成功", out is not None, f"-> {str(out)[:40]}")
check("带视频标识", out is not None and "视频#b04c3c4507f2e37d" in out)

print("\n[2] 关键：别的会话绝不能拿到")
out = inject(inst, PRIVATE_B)
check("私聊不被注入", out is None, f"-> {str(out)[:40]}")
out = inject(inst, "another-group-999")
check("别的群不被注入", out is None, f"-> {str(out)[:40]}")

print("\n[3] 空 session_id 也不得兜底")
out = inject(inst, "")
check("空 id 不注入", out is None, f"-> {str(out)[:40]}")

print("\n[4] 陈旧记录不注入（超 30 分钟）")
inst._session_latest[PRIVATE_B] = {
    "text": "旧内容", "ts": time.time() - 3600, "key": "old", "segments": [],
    "summary": "旧内容"}
out = inject(inst, PRIVATE_B)
check("过期不注入", out is None, f"-> {str(out)[:40]}")
check("过期记录被清掉", PRIVATE_B not in inst._session_latest)

print("\n[5] 条数上限")
inst2 = make_plugin()
for i in range(80):
    inst2._session_latest[f"s{i}"] = {"text": f"t{i}", "ts": time.time() + i,
                                      "key": "", "segments": [], "summary": ""}
inst2._trim_session_latest(keep=50)
check("裁到上限", len(inst2._session_latest) <= 50,
      f"-> {len(inst2._session_latest)}")

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
