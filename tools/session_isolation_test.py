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

_PENDING_MARK = P._M_PENDING

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

    class source:
        auto_process = True
        max_videos_per_message = 3

    class timeline:
        inject_fresh_seconds = 300.0


def make_plugin():
    inst = P.VideoUnderstandPlugin.__new__(P.VideoUnderstandPlugin)
    inst.ctx = _Ctx()
    inst.config = _Cfg()
    inst._session_latest = {}
    return inst


def inject(inst, sid, history=None):
    """跑一次注入钩子，返回注入的文本（未注入返回 None）。

    history 可传若干条模拟上下文（用于测关键词闸门）。
    """
    items = list(history or [])
    res = asyncio.run(P.VideoUnderstandPlugin.on_before_model_request(
        inst, None, items=items, session_id=sid))
    if not res or res.get("action") != "continue":
        return None
    new_items = res["modified_kwargs"]["items"]
    if not new_items:
        return None
    parts = new_items[-1].get("parts") or [{}]
    return str(parts[0].get("text") or "")


def user_msg(text):
    return {"item_type": "UserMessageItem",
            "meta": {}, "parts": [{"type": "text", "text": text}]}


GROUP_A = "3aeb34ec86d3ec02ee18b52a91a7bd73"
PRIVATE_B = "5f2b1d04aa19bb77cc33ee11ff22aa33"

inst = make_plugin()

print("[1] 群里发视频 -> 群会话能拿到")
inst._session_latest[GROUP_A] = {
    "text": "00:02 游戏内商店界面…", "ts": time.time(),
    "key": "b04c3c4507f2e37d", "segments": [], "summary": "00:02 游戏内商店界面…"}
out = inject(inst, GROUP_A)
check("同会话注入成功", out is not None, f"-> {str(out)[:40]}")
check("带视频标识", out is not None and "b04c3c4507f2e37d" in out)
check("自报家门（明说已看过）", out is not None and "你已经看过" in out, f"-> {str(out)[:40]}")

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

print("\n[6] 相关性闸门：过「强制注入窗口」后不再无脑重注")
inst3 = make_plugin()
STALE = time.time() - 900          # 15 分钟前理解完，已过 300s 窗口
inst3._session_latest[GROUP_A] = {
    "text": "00:02 游戏内商店界面…", "ts": STALE,
    "key": "b04c3c4507f2e37d", "segments": [], "summary": "00:02 游戏内商店界面…"}
out = inject(inst3, GROUP_A)
check("无关上下文不重注", out is None, f"-> {str(out)[:40]}")
out = inject(inst3, GROUP_A, history=[user_msg("晚点吃什么"), user_msg("随便")])
check("无关聊天不重注", out is None, f"-> {str(out)[:40]}")
out = inject(inst3, GROUP_A, history=[user_msg("刚才那个视频里是啥")])
check("提到视频则照常注入", out is not None, f"-> {str(out)[:40]}")

print("\n[7] 刚理解完的窗口内，即使没提视频也注入")
inst4 = make_plugin()
inst4._session_latest[GROUP_A] = {
    "text": "00:02 游戏内商店界面…", "ts": time.time(),
    "key": "x", "segments": [], "summary": "00:02 游戏内商店界面…"}
out = inject(inst4, GROUP_A, history=[user_msg("哈哈")])
check("窗口内注入", out is not None, f"-> {str(out)[:40]}")

print("\n[8] 原片被清理后，按文件名复用已有时间轴")
import tempfile  # noqa: E402
import timeline_store as TS  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    store = TS.TimelineStore(tmp)
    store.save("reusekey12345678", segments=[{"start": 0, "end": 5, "text": "画面"}],
               summary="00:00 复用摘要", group_id=GROUP_A,
               file_name="abc123.mp4")

    class _Asset:
        name = "abc123.mp4"
        file_ref = ""

    inst5 = make_plugin()
    inst5._store = store
    ok_reuse = inst5._reuse_by_filename(_Asset(), GROUP_A, "999888")
    check("命中文件名则复用", ok_reuse is True)
    check("复用后会话记录已写入", GROUP_A in inst5._session_latest)
    check("补记了新消息 id", store.by_message("999888") == "reusekey12345678",
          f"-> {store.by_message('999888')}")
    out = inject(inst5, GROUP_A)
    check("复用后能正常注入", out is not None and "复用摘要" in out,
          f"-> {str(out)[:40]}")

    class _Asset2:
        name = "never-seen.mp4"
        file_ref = ""

    check("未命中则不复用", inst5._reuse_by_filename(_Asset2(), GROUP_A) is False)

def after_process(inst, msg):
    """跑一次接收钩子（把 _handle 换成空操作，只为看消息文本改成了什么）。"""
    async def _noop(*_a, **_k):
        return None

    inst._handle = _noop
    inst._bg = set()
    return asyncio.run(
        P.VideoUnderstandPlugin.on_after_process(inst, msg))


def video_msg():
    return {
        "raw_message": [{"type": "video",
                         "data": {"file": "3ac0795f.mp4",
                                  "url": "https://x/y.mp4", "file_size": 8558780}}],
        "processed_plain_text": "[视频] 文件: 3ac0795f.mp4，大小: 8558780",
        "message_info": {"group_info": {"group_id": "1076711748"}},
        "message_id": "123",
        "session_id": "sess-1",
    }


print("\n[9] 消息文本里的状态标记：不得带时态（写完就改不了）")
inst6 = make_plugin()
msg = video_msg()
after_process(inst6, msg)
txt = msg["processed_plain_text"]
check("标记已写入", _PENDING_MARK in txt, f"-> {txt[-40:]!r}")
for bad in ("正在", "进行中", "处理中", "稍后"):
    check(f"不含「{bad}」", bad not in txt, f"-> {txt[-30:]!r}")
check("标记不得包含 _M_DONE（会永久关掉注入）",
      P._M_DONE not in txt, f"-> {txt[-30:]!r}")

print("\n[10] 重复处理同一条消息，不叠加标记")
after_process(inst6, msg)
check("只标记一次", msg["processed_plain_text"].count(_PENDING_MARK) == 1,
      f"-> {msg['processed_plain_text'].count(_PENDING_MARK)}")

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
