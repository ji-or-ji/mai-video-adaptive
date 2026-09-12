# 视频理解插件（自适应抽帧）

麦麦插件。检测群里的视频，按内容自适应抽帧、可选转录语音，交给视觉模型理解，
把结果注入 bot 可见上下文。**不直接对用户发言**。

## 它和普通视频插件的区别

- **抽多少帧是算出来的**：按画面信息密度（码率曲线）分配帧位，重复内容自动压低帧数。
- **重复视频不重复干活**：内容相近的视频（同一条被多人转发、不同压缩版本）直接复用旧描述。
- **音频可选三档**：不读 / 本地模型 / 云端接口，默认不读。
- **慢不阻塞**：视频在后台处理，聊天照常走，处理完再补进上下文。

## 安装

1. 把整个 `plugin/` 目录放到麦麦的 `plugins/` 下（目录名建议 `video-understanding`）。
2. 确保 **ffmpeg / ffprobe** 在系统 PATH 中。
3. 在 WebUI 插件管理页启用「视频理解（自适应抽帧）」。
4. **把主程序的 `vlm` 任务指向支持图片输入的模型**（例如 `deepseek-flash`）。
   插件的描述请求走这个任务。
5. 若走 QQ 群视频：NapCat 需单独开一个 HTTP Server（默认 `127.0.0.1:3002`），
   填进插件配置的 `napcat.http_base_url`。

## 配置

| 分组 | 字段 | 默认 | 说明 |
|---|---|---|---|
| 插件 | enabled | true | 总开关 |
| 抽帧 | seconds_per_frame | 4.0 | 每几秒一帧 |
| 抽帧 | min_frames / max_frames | 0 / 0 | 0 表示自适应 |
| 音频 | mode | off | off / local / api |
| 音频 | api_key / api_url / api_model | 空 | api 模式用 |
| 音频 | local_model / local_tokens | 空 | local 模式用（见下） |
| 视觉 | host_task | vlm | 主程序任务名 |
| 缓存 | enabled / match_threshold | true / 0.9 | 相似视频复用 |
| NapCat | enabled / http_base_url | true / 127.0.0.1:3002 | 取回群视频 |
| 视频源 | max_video_mb / 单条上限 / 并发 | 80 / 3 / 1 | 体积与并发护栏 |

## 本地音频模式（可选）

需要 `pip install sherpa-onnx`，并准备 SenseVoice 模型：

- `model.int8.onnx`（约 228MB）
- `tokens.txt`

把两个路径填进 `audio.local_model` / `audio.local_tokens`。

实测：加载 1s，内存 322MB，速度 25~40 倍实时。**内存吃紧的机器慎用**，
可保持 `audio.mode = off` 或改用 `api`。

## 处理流程

```
消息到达
 ├─ 识别视频素材（结构化段 / NapCat 文本占位）
 ├─ 写入「理解中」占位
 └─ 后台：取视频 → 查签名缓存 → 自适应抽帧 → 可选转录 → 模型理解
      └─ 结果在模型请求前注入上下文
```

## 依赖

- ffmpeg / ffprobe（必须）
- 主程序：1.0.0+，SDK 2.0.0+
- 可选：sherpa-onnx（本地音频模式）

## 许可

MIT
