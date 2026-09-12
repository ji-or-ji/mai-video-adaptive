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
    config_version: str = Field(default="1.0.0", description="配置版本（版本策略要求，一般无需改）",
                                json_schema_extra={"label": "配置版本", "order": 20})


class ExtractSection(PluginConfigBase):
    __ui_label__ = "抽帧"
    __ui_order__ = 1

    ffmpeg_path: str = Field(
        default="",
        description="ffmpeg 可执行文件绝对路径；留空则用 PATH（麦麦进程可能读不到新 PATH）",
        json_schema_extra={"label": "ffmpeg 路径", "order": 5})
    ffprobe_path: str = Field(
        default="",
        description="ffprobe 可执行文件绝对路径；留空则用 PATH",
        json_schema_extra={"label": "ffprobe 路径", "order": 6})
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

    mode: str = Field(
        default="host", description="host=走主程序任务 / direct=直连接口",
        json_schema_extra={"label": "调用方式（host/direct）", "order": 10})
    host_task: str = Field(
        default="vlm", description="host 模式走的任务名",
        json_schema_extra={"label": "主程序任务名", "order": 20})
    api_url: str = Field(default=af.DS_CHAT_URL, description="direct 模式的接口地址",
                         json_schema_extra={"label": "接口地址", "order": 30})
    api_key: str = Field(default="", description="direct 模式的 API Key",
                         json_schema_extra={"label": "API Key", "order": 40})
    model: str = Field(default=af.DEFAULT_VISION_MODEL, description="direct 模式的模型名",
                       json_schema_extra={"label": "模型名", "order": 50})
    timeout_s: float = Field(default=120.0, description="direct 模式超时",
                             json_schema_extra={"label": "超时（秒）", "order": 60})


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
    fetch_dir: str = Field(
        default="",
        description="NapCat 取回插件（video-fetch）的下载目录；填了则优先从这里取视频",
        json_schema_extra={"label": "取回目录", "order": 40})
    fetch_wait_s: float = Field(default=60.0, description="等待取回目录出现文件的最长秒数",
                                json_schema_extra={"label": "等待秒数", "order": 50})


class SourceSection(PluginConfigBase):
    __ui_label__ = "视频源"
    __ui_order__ = 6

    auto_process: bool = Field(default=True, description="自动处理入站视频",
                               json_schema_extra={"label": "自动处理", "order": 10})
    cleanup_after: bool = Field(
        default=True,
        description="理解完成后删除视频与中间文件（帧/音频）；描述已入缓存，不影响复用",
        json_schema_extra={"label": "完成后删除文件", "order": 15})
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
        # 指定 ffmpeg / ffprobe（进程继承的 PATH 可能不含它们）
        ff = str(self.config.extract.ffmpeg_path or "").strip()
        fp = str(self.config.extract.ffprobe_path or "").strip()
        af.set_tools(ff or None, fp or None)
        import shutil as _sh
        self.ctx.logger.info(
            "视频理解插件已加载 extract=%s/s audio=%s cache=%s ffmpeg=%s",
            self.config.extract.seconds_per_frame,
            self.config.audio.mode,
            self.config.cache.enabled,
            af.FFMPEG if not ff else ff)

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
        """模型请求前注入最近一次视频理解结果。

        注意：这个钩子给的是麦麦自己的 ContextItem 列表（kwargs['items']），
        不是 OpenAI 风格的 messages。
        """
        if not self.config.plugin.enabled:
            return None

        items = kwargs.get("items")
        if not isinstance(items, list):
            return None

        sid = str(kwargs.get("session_id") or kwargs.get("stream_id") or "").strip()
        record = self._session_latest.get(sid) if sid else None
        if record is None and self._session_latest:
            record = max(self._session_latest.values(), key=lambda x: float(x.get("ts") or 0.0))
        if not record or not str(record.get("text") or "").strip():
            return None

        text = str(record["text"]).strip()

        # 已在上下文里则不重复注入
        for it in items:
            if not isinstance(it, dict):
                continue
            for part in (it.get("parts") or []):
                if isinstance(part, dict) and _M_DONE in str(part.get("text") or ""):
                    return None

        import uuid as _uuid
        from datetime import datetime as _dt

        block = f"{_M_DONE} {text}"
        item = {
            "item_type": "SystemMessageItem",
            "meta": {
                "item_id": _uuid.uuid4().hex,
                "logical_turn_id": None,
                "timestamp": _dt.now().isoformat(),
            },
            "parts": [{"type": "text", "text": block}],
        }
        new_items = list(items)
        # 追加到末尾：保持前缀（人设 + 历史）不变，命中 prompt 缓存
        new_items.append(item)
        # 整包回传：只替换 items，其余参数（含 item_schema_version）原样带回去
        new_kwargs = dict(kwargs)
        new_kwargs["items"] = new_items
        if not new_kwargs.get("item_schema_version"):
            new_kwargs["item_schema_version"] = 1
        self.ctx.logger.info("已注入视频内容（末尾，schema=%s）：%s",
                             new_kwargs.get("item_schema_version"), block[:120])
        return {"action": "continue", "modified_kwargs": new_kwargs}

    # ---- 处理流水线 ----

    async def _handle(self, asset: media_mod.VideoAsset, stream_id: str) -> None:
        assert self._sem is not None
        video_path: Path | None = None
        async with self._sem:
            try:
                video_path = await self._materialize(asset)
                prep = await asyncio.to_thread(self._prepare, video_path)
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
                import traceback
                self.ctx.logger.warning("视频理解失败 name=%s err=%s\n%s",
                                        asset.name or asset.file_ref, exc,
                                        traceback.format_exc()[-800:])
            finally:
                if bool(self.config.source.cleanup_after):
                    await asyncio.to_thread(self._cleanup, asset, video_path)

    def _cleanup(self, asset: media_mod.VideoAsset, video_path: Path | None) -> None:
        """清理视频本体与中间产物（帧 / 音频）。描述已入签名缓存，删除不影响复用。"""

        import shutil as _sh
        removed = []

        # 1) 取回的视频本体（可能在 NapCat 下载目录，也可能在本地运行时目录）
        cands = []
        if video_path is not None:
            cands.append(Path(video_path))
        fetch_dir = str(self.config.napcat.fetch_dir or "").strip()
        if fetch_dir:
            for name in (asset.name, asset.file_ref):
                if name:
                    cands.append(Path(fetch_dir) / name)
        for p in cands:
            try:
                if p.is_file():
                    p.unlink()
                    removed.append(str(p))
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("删除视频失败 %s: %s", p, exc)

        # 2) 中间产物目录（frame/audio/孪生副本）
        base = Path(self.ctx.paths.runtime_dir) / "videos"
        targets = []
        if video_path is not None:
            targets.append(base / Path(video_path).stem)
        targets.append(base / asset.key[:16])
        for target in targets:
            try:
                if target.is_dir():
                    _sh.rmtree(target, ignore_errors=True)
                    removed.append(str(target))
            except Exception:  # noqa: BLE001
                pass

        if removed:
            self.ctx.logger.info("已清理视频文件 %d 项", len(removed))

    async def _materialize(self, asset: media_mod.VideoAsset) -> Path:
        """落盘；优先用 NapCat 取回插件的下载目录，其次直取，最后 NapCat get_file。"""

        cfg = self.config
        runtime = Path(self.ctx.paths.runtime_dir) / "videos" / asset.key[:16]
        max_bytes = int(float(cfg.source.max_video_mb) * 1024 * 1024)
        timeout_s = max(5.0, float(cfg.audio.timeout_s))

        # 1) NapCat 取回插件的下载目录（video-fetch 插件把真实视频存这里）
        fetch_dir = str(cfg.napcat.fetch_dir or "").strip()
        if fetch_dir:
            got = await self._wait_fetched(fetch_dir, asset)
            if got is not None and got.stat().st_size <= max_bytes:
                return got

        # 2) 自带来源（url / base64 / 本地路径）
        if asset.url or asset.base64_data or asset.local_path:
            try:
                return await media_mod.materialize(
                    asset, target_dir=runtime, timeout_s=timeout_s, max_bytes=max_bytes)
            except Exception as exc:  # noqa: BLE001
                if not (asset.file_ref or asset.name):
                    raise
                self.ctx.logger.info("直接落盘失败，尝试 NapCat：%s", exc)

        # 3) NapCat OneBot get_file
        ref = str(asset.file_ref or asset.name or "").strip()
        if not ref:
            raise ValueError("素材缺少 url / base64 / local_path / file_ref")
        raw = await self._fetch_via_napcat(ref)
        if len(raw) > max_bytes:
            raise ValueError(f"NapCat 取回视频过大：{len(raw)} > {max_bytes}")
        name = asset.name or ref
        return await asyncio.to_thread(
            media_mod.save_bytes, raw, target_dir=runtime, name=name, key=asset.key)

    async def _wait_fetched(self, fetch_dir: str, asset: media_mod.VideoAsset) -> Path | None:
        """等待取回目录里出现目标视频（按 latest.json 的 done 状态或同名文件判定）。"""

        import json as _json

        names = [n for n in (asset.name, asset.file_ref) if n]
        deadline = time.time() + max(1.0, float(self.config.napcat.fetch_wait_s))
        dir_path = Path(fetch_dir)

        while time.time() < deadline:
            # a) 看 latest.json 是否已完成且对得上
            record = dir_path / "latest.json"
            if record.is_file():
                try:
                    info = _json.loads(record.read_text(encoding="utf-8"))
                except Exception:
                    info = {}
                if str(info.get("phase")) == "done":
                    saved = str(info.get("saved") or "")
                    if saved and Path(saved).is_file():
                        return Path(saved)
            # b) 直接找同名文件
            for name in names:
                cand = dir_path / name
                if cand.is_file() and cand.stat().st_size > 0:
                    return cand
            await asyncio.sleep(2.0)
        return None

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

        # NapCat 常把本地文件路径放在 file / path / url 里（url 未必是网络地址）
        for key in ("file", "path", "file_path", "url"):
            cand = str(data.get(key) or "").strip()
            if not cand or cand.lower().startswith(("http://", "https://")):
                continue
            p = Path(cand)
            if p.is_file():
                return p.read_bytes()
        return b""

    # ---- 同步部分（在线程里跑） ----

    def _prepare(self, video: Path) -> dict[str, Any]:
        cfg = self.config
        runtime = Path(self.ctx.paths.runtime_dir) / "videos" / video.stem
        import shutil as _sh
        self.ctx.logger.info(
            "工具检查 ffmpeg=%s ffprobe=%s FFMPEG=%s",
            _sh.which("ffmpeg"), _sh.which("ffprobe"), af.FFMPEG)

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
        cfg = self.config
        mode = str(cfg.vision.mode or "host").strip().lower()

        if mode == "direct":
            if not str(cfg.vision.api_key or "").strip():
                raise RuntimeError("direct 模式未配置 API Key")
            return await asyncio.to_thread(
                af.describe_video, frames, transcript,
                api_key=str(cfg.vision.api_key),
                url=str(cfg.vision.api_url),
                model=str(cfg.vision.model),
                timeout=float(cfg.vision.timeout_s))

        prompt = af.build_vision_prompt(transcript)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for frame in frames:
            b64 = base64.b64encode(Path(frame).read_bytes()).decode("ascii")
            content.append({"type": "image", "image_format": "jpeg",
                            "image_base64": b64})
        result = await self.ctx.llm.generate(
            [{"role": "user", "content": content}],
            model=str(cfg.vision.host_task or "vlm"))

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
