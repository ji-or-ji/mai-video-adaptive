# -*- coding: utf-8 -*-
"""视频理解插件（自适应抽帧 + 语音转录）。

检测入站视频，按内容自适应抽帧、可选转录语音，交给视觉模型理解，
把结果注入 bot 可见上下文（不直接对用户发言）。

核心（抽帧 / 音频 / 签名去重）在 adaptive_frames 里，本文件只做接线。
"""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Any

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

try:
    from . import adaptive_frames as af
    from . import media as media_mod
except ImportError:  # PluginLoader 以文件方式加载
    import adaptive_frames as af  # type: ignore
    import media as media_mod  # type: ignore


_M_PENDING = "[视频理解中]"
_M_DONE = "[视频内容]"
_M_FAIL = "[视频理解失败]"


# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------

class PluginSection(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "video"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="插件总开关",
                          json_schema_extra={"label": "启用插件", "order": 10})


class ExtractSection(PluginConfigBase):
    __ui_label__ = "抽帧"
    __ui_order__ = 1

    seconds_per_frame: float = Field(
        default=4.0, description="有效内容的取样密度：每几秒一帧",
        json_schema_extra={"label": "每几秒一帧", "order": 10})
    min_frames: int = Field(
        default=0, description="帧数下限；0 表示由时长与新颖率自适应",
        json_schema_extra={"label": "最少帧数（0=自适应）", "order": 20})
    max_frames: int = Field(
        default=0, description="帧数上限；0 表示自适应（硬顶 48）",
        json_schema_extra={"label": "最多帧数（0=自适应）", "order": 30})
    max_height: int = Field(
        default=720, description="抽帧后的最大高度",
        json_schema_extra={"label": "最大高度", "order": 40})


class AudioSection(PluginConfigBase):
    __ui_label__ = "音频"
    __ui_order__ = 2

    mode: str = Field(
        default="off", description="off=不读音频 / local=本地模型 / api=云端接口",
        json_schema_extra={"label": "音频模式（off/local/api）", "order": 10})
    api_key: str = Field(default="", description="云端 ASR 的 API Key（api 模式用）",
                         json_schema_extra={"label": "ASR API Key", "order": 20})
    api_url: str = Field(default=af.SF_ASR_URL, description="云端 ASR 接口地址",
                         json_schema_extra={"label": "ASR 接口", "order": 30})
    api_model: str = Field(default=af.DEFAULT_ASR_MODEL, description="云端 ASR 模型名",
                           json_schema_extra={"label": "ASR 模型", "order": 40})
    local_model: str = Field(default="", description="本地模型路径（model.int8.onnx）",
                             json_schema_extra={"label": "本地模型路径", "order": 50})
    local_tokens: str = Field(default="", description="本地词表路径（tokens.txt）",
                              json_schema_extra={"label": "本地词表路径", "order": 60})
    timeout_s: float = Field(default=30.0, description="云端转录超时，超时即降级只交帧",
                             json_schema_extra={"label": "转录超时（秒）", "order": 70})
    min_free_mb: float = Field(
        default=500.0,
        description="加载本地模型前要求的最小可用内存（MB）；不达标就不加载",
        json_schema_extra={"label": "本地模式内存门槛（MB）", "order": 80})
    fallback: bool = Field(default=True, description="首选途径失败时回退另一种（API↔本地）",
                           json_schema_extra={"label": "失败回退", "order": 90})
    unload_after: bool = Field(default=True, description="转录后卸载本地模型，释放内存",
                               json_schema_extra={"label": "用完卸载", "order": 100})


class VisionSection(PluginConfigBase):
    __ui_label__ = "视觉模型"
    __ui_order__ = 3

    host_task: str = Field(
        default="vlm", description="走主程序的任务名；把该任务指向支持图片输入的模型",
        json_schema_extra={"label": "模型任务名", "order": 10})


class CacheSection(PluginConfigBase):
    __ui_label__ = "缓存"
    __ui_order__ = 4

    enabled: bool = Field(default=True, description="相似视频复用描述",
                          json_schema_extra={"label": "启用签名缓存", "order": 10})
    max_entries: int = Field(default=200, description="最多缓存条数",
                             json_schema_extra={"label": "最大条数", "order": 20})
    match_threshold: float = Field(default=0.9, description="签名命中阈值（越高越严）",
                                   json_schema_extra={"label": "命中阈值", "order": 30})


class NapcatSection(PluginConfigBase):
    __ui_label__ = "NapCat"
    __ui_order__ = 5

    enabled: bool = Field(default=True, description="用 NapCat HTTP get_file 取回真实视频",
                          json_schema_extra={"label": "启用 NapCat 取回", "order": 10})
    http_base_url: str = Field(default="http://127.0.0.1:3002",
                               description="NapCat OneBot HTTP 地址",
                               json_schema_extra={"label": "HTTP Base URL", "order": 20})
    access_token: str = Field(default="", description="可选 access token",
                              json_schema_extra={"label": "Access Token", "order": 30})


class SourceSection(PluginConfigBase):
    __ui_label__ = "视频源"
    __ui_order__ = 6

    auto_process: bool = Field(default=True, description="自动处理入站视频",
                               json_schema_extra={"label": "自动处理", "order": 10})
    max_video_mb: float = Field(default=80.0, description="超过该大小跳过",
                                json_schema_extra={"label": "最大体积（MB）", "order": 20})
    max_videos_per_message: int = Field(default=3, description="单条消息最多处理几个视频",
                                        json_schema_extra={"label": "单条上限", "order": 30})
    concurrency: int = Field(default=1, description="同时处理数",
                             json_schema_extra={"label": "并发数", "order": 40})


class VideoUnderstandConfig(PluginConfigBase):
    __ui_label__ = "视频理解"

    plugin: PluginSection = Field(default_factory=PluginSection)
    extract: ExtractSection = Field(default_factory=ExtractSection)
    audio: AudioSection = Field(default_factory=AudioSection)
    vision: VisionSection = Field(default_factory=VisionSection)
    cache: CacheSection = Field(default_factory=CacheSection)
    napcat: NapcatSection = Field(default_factory=NapcatSection)
    source: SourceSection = Field(default_factory=SourceSection)


# ------------------------------------------------------------------
# 插件
# ------------------------------------------------------------------

class VideoUnderstandPlugin(MaiBotPlugin):
    """视频理解插件。"""

    config_model = VideoUnderstandConfig

    def __init__(self) -> None:
        super().__init__()
        self._sem: asyncio.Semaphore | None = None
        self._session_latest: dict[str, dict[str, Any]] = {}
        self._bg: set[asyncio.Task[Any]] = set()

    # ---- 生命周期 ----

    async def on_load(self) -> None:
        self._sem = asyncio.Semaphore(max(1, int(self.config.source.concurrency)))
        Path(self.ctx.paths.runtime_dir).mkdir(parents=True, exist_ok=True)
        self.ctx.logger.info(
            "视频理解插件已加载 extract=%s/s audio=%s cache=%s",
            self.config.extract.seconds_per_frame,
            self.config.audio.mode,
            self.config.cache.enabled,
        )

    async def on_unload(self) -> None:
        for task in list(self._bg):
            task.cancel()
        self._bg.clear()

    async def on_config_update(self, scope: str, config_data: dict[str, Any],
                               version: str) -> None:
        del scope, config_data, version
        self._sem = asyncio.Semaphore(max(1, int(self.config.source.concurrency)))

    # ---- Hook：检测入站视频 ----

    @HookHandler(
        "chat.receive.after_process",
        name="video_understand_after_process",
        description="检测视频并异步理解，结果注入上下文",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_after_process(self, message: Any = None, **kwargs: Any) -> dict[str, Any] | None:
        del kwargs
        if not self.config.plugin.enabled or not self.config.source.auto_process:
            return None
        if not isinstance(message, dict):
            return None

        assets = media_mod.extract_assets(message)
        if not assets:
            return None

        limit = max(1, int(self.config.source.max_videos_per_message))
        assets = assets[:limit]
        stream_id = self._stream_id(message)

        plain = str(message.get("processed_plain_text") or "").strip()
        line = f"{_M_PENDING} 检测到 {len(assets)} 个视频，正在理解"
        if _M_PENDING not in plain and _M_DONE not in plain:
            message["processed_plain_text"] = f"{plain}\n{line}".strip() if plain else line

        self.ctx.logger.info("检测到 %d 个视频 session=%s", len(assets), stream_id or "-")
        for asset in assets:
            task = asyncio.create_task(self._handle(asset, stream_id))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

        return {"action": "continue", "modified_kwargs": {"message": message}}

    # ---- Hook：模型请求前注入 ----

    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="video_understand_inject",
        description="在模型请求前注入最近一次视频理解结果",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_before_model_request(self, messages: Any = None,
                                      **kwargs: Any) -> dict[str, Any] | None:
        if not self.config.plugin.enabled:
            return None
        if not isinstance(messages, list):
            return None

        sid = str(kwargs.get("session_id") or kwargs.get("stream_id") or "").strip()
        record = self._session_latest.get(sid) if sid else None
        if record is None and self._session_latest:
            record = max(self._session_latest.values(), key=lambda x: float(x.get("ts") or 0.0))
        if not record or not str(record.get("text") or "").strip():
            return None

        if any(isinstance(m, dict) and isinstance(m.get("content"), str)
               and _M_DONE in str(m.get("content")) for m in messages):
            return None

        block = f"{_M_DONE} {str(record['text']).strip()}"
        new_messages = list(messages)
        pos = 0
        for index, msg in enumerate(messages):
            if isinstance(msg, dict) and msg.get("role") == "system":
                pos = index + 1
            else:
                break
        new_messages.insert(pos, {"role": "system", "content": block})
        return {"action": "continue", "modified_kwargs": {"messages": new_messages}}

    # ---- 处理流水线 ----

    async def _handle(self, asset: media_mod.VideoAsset, stream_id: str) -> None:
        assert self._sem is not None
        async with self._sem:
            try:
                video = await self._materialize(asset)
                prep = await asyncio.to_thread(self._prepare, video)
                if prep.get("cached"):
                    text = prep["text"]
                else:
                    text = await self._describe(prep["frames"], prep["transcript"])
                    if prep.get("cache") is not None and text:
                        prep["cache"].remember(prep["sig"], text, asset.name or asset.file_ref)
                self._session_latest[stream_id] = {"text": text, "ts": time.time()}
                self.ctx.logger.info("视频理解完成 name=%s text=%s",
                                     asset.name or asset.file_ref, text[:80])
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("视频理解失败 name=%s err=%s",
                                        asset.name or asset.file_ref, exc)

    async def _materialize(self, asset: media_mod.VideoAsset) -> Path:
        """落盘；只有文件名时经 NapCat 取回。"""

        cfg = self.config
        runtime = Path(self.ctx.paths.runtime_dir) / "videos" / asset.key[:16]
        max_bytes = int(float(cfg.source.max_video_mb) * 1024 * 1024)
        timeout_s = max(5.0, float(cfg.audio.timeout_s))

        if asset.url or asset.base64_data or asset.local_path:
            try:
                return await media_mod.materialize(
                    asset, target_dir=runtime, timeout_s=timeout_s, max_bytes=max_bytes)
            except Exception as exc:  # noqa: BLE001
                if not (asset.file_ref or asset.name):
                    raise
                self.ctx.logger.info("直接落盘失败，尝试 NapCat：%s", exc)

        ref = str(asset.file_ref or asset.name or "").strip()
        if not ref:
            raise ValueError("素材缺少 url / base64 / local_path / file_ref")
        raw = await self._fetch_via_napcat(ref)
        if len(raw) > max_bytes:
            raise ValueError(f"NapCat 取回视频过大：{len(raw)} > {max_bytes}")
        name = asset.name or ref
        return await asyncio.to_thread(
            media_mod.save_bytes, raw, target_dir=runtime, name=name, key=asset.key)

    async def _fetch_via_napcat(self, file_ref: str) -> bytes:
        cfg = self.config
        if not cfg.napcat.enabled:
            raise RuntimeError("NapCat 取回未启用")

        params = {"file_id": file_ref, "file": file_ref}

        # 优先走 adapter 能力
        try:
            result = await self.ctx.api.call("adapter.napcat.file.get_file", params=params)
            raw = self._bytes_from_napcat(response=result)
            if raw:
                return raw
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.info("adapter get_file 失败，回退 HTTP：%s", exc)

        # 回退裸 HTTP
        base = str(cfg.napcat.http_base_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("NapCat http_base_url 为空")
        import json as _json
        import urllib.request as _ur

        body = _json.dumps(params).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "User-Agent": "maivideo-plugin/0.1"}
        token = str(cfg.napcat.access_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        def _post() -> dict[str, Any]:
            req = _ur.Request(f"{base}/get_file", data=body, headers=headers)
            with _ur.urlopen(req, timeout=max(10.0, float(cfg.source.max_video_mb))) as r:
                return _json.loads(r.read().decode("utf-8"))

        payload = await asyncio.to_thread(_post)
        raw = self._bytes_from_napcat(response=payload)
        if not raw:
            raise RuntimeError("NapCat get_file 未返回可用数据")
        return raw

    @staticmethod
    def _bytes_from_napcat(*, response: Any) -> bytes:
        data = response
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict):
            return b""
        b64 = str(data.get("base64") or data.get("base64_data") or "").strip()
        if b64:
            return base64.b64decode(b64.split(",")[-1], validate=False)
        path = str(data.get("file") or data.get("path") or "").strip()
        if path and Path(path).is_file():
            return Path(path).read_bytes()
        return b""

    # ---- 同步部分（在线程里跑） ----

    def _prepare(self, video: Path) -> dict[str, Any]:
        cfg = self.config
        runtime = Path(self.ctx.paths.runtime_dir) / "videos" / video.stem

        cache = None
        sig = None
        if cfg.cache.enabled:
            store = Path(self.ctx.paths.data_dir) / "video_sig_cache.json"
            cache = af.VideoSignatureCache(
                store, match_threshold=float(cfg.cache.match_threshold),
                max_entries=int(cfg.cache.max_entries))
            info = af.probe_media(video)
            sig = cache.signature(video, info["duration"])
            hit = cache.lookup(sig)
            if hit:
                return {"cached": True, "text": hit["text"]}

        min_f = int(cfg.extract.min_frames) or None
        max_f = int(cfg.extract.max_frames) or None
        _plan, frames = af.adaptive_extract(
            video, runtime / "frames", min_frames=min_f, max_frames=max_f,
            seconds_per_frame=float(cfg.extract.seconds_per_frame),
            max_height=int(cfg.extract.max_height))

        mode = str(cfg.audio.mode or "off").strip().lower()
        audio = af.audio_transcript(
            video, out_dir=runtime / "audio", mode=mode,
            api_key=str(cfg.audio.api_key or ""),
            local_model=cfg.audio.local_model or None,
            local_tokens=cfg.audio.local_tokens or None,
            model=str(cfg.audio.api_model or af.DEFAULT_ASR_MODEL),
            timeout=float(cfg.audio.timeout_s),
            min_free_mb=float(cfg.audio.min_free_mb),
            fallback=bool(cfg.audio.fallback),
            unload_after=bool(cfg.audio.unload_after))
        transcript = "" if audio.get("skipped") else str(audio.get("text") or "")

        return {"cached": False, "frames": frames, "transcript": transcript,
                "sig": sig, "cache": cache}

    # ---- 异步：调用宿主模型 ----

    async def _describe(self, frames: list[Path], transcript: str) -> str:
        prompt = af.build_vision_prompt(transcript)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for frame in frames:
            b64 = base64.b64encode(Path(frame).read_bytes()).decode("ascii")
            content.append({"type": "image", "image_format": "jpeg",
                            "image_base64": b64})
        result = await self.ctx.llm.generate(
            [{"role": "user", "content": content}],
            model=str(self.config.vision.host_task or "vlm"))

        if isinstance(result, dict):
            if result.get("success") is False:
                raise RuntimeError(str(result.get("error") or "宿主模型调用失败"))
            text = result.get("response") or result.get("content") or result.get("text") or ""
        else:
            text = result or ""
        text = str(text).strip()
        if not text:
            raise RuntimeError("宿主模型返回空描述")
        return text

    # ---- 工具 ----

    @staticmethod
    def _stream_id(message: dict[str, Any]) -> str:
        for key in ("session_id", "stream_id", "chat_id"):
            value = message.get(key)
            if value:
                return str(value).strip()
        info = message.get("message_info") or {}
        if isinstance(info, dict):
            for key in ("session_id", "stream_id", "chat_id"):
                value = info.get(key)
                if value:
                    return str(value).strip()
        return ""


def create_plugin() -> VideoUnderstandPlugin:
    """插件工厂入口。"""

    return VideoUnderstandPlugin()
