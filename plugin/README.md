# 麦麦假装看视频

麦麦插件。检测群里的视频，按内容自适应抽帧、可选转录语音，交给视觉模型理解，
把结果注入 bot 可见上下文。**不直接对用户发言**。

## 关键前提：需要配套的 NapCat 插件

QQ 收到群视频**不会下载原片**，只存一张封面缩略图（实测 181 个视频、服务器上一个 mp4 都没有）。
而 napcat-adapter 又会把视频段压成纯文本，把下载地址丢掉。所以光靠 OneBot 接口拿不到视频。

本项目自带一个 NapCat 插件（见 `napcat-plugin/video-probe/`）解决这件事：

- 它监听视频消息，从**原始事件**里拿到真实 CDN 地址（`multimedia.nt.qq.com.cn/...&rkey=...`）
- 用 `https` 模块下载到本地（实测 `fetch` 在该环境报 `fetch failed`，`https` 稳定）
- 本插件配置 `napcat.fetch_dir` 指向那个下载目录，优先从那里取视频

完整链路：

```
视频消息
 → NapCat 插件：抓真实地址 + 下载到本地目录
 → 本插件：从下载目录取回
 → 签名查重 → 自适应抽帧 → 可选转录 → 视觉模型理解
 → 注入 bot 上下文
```

> NapCat 插件安装：目录放进 `napcat/plugins/`，并在 `napcat/config/plugins.json` 写
> `{"video-probe": true}`（NapCat 除内置插件外默认禁用），然后重启 NapCat。

## 它和普通视频插件的区别

- **抽多少帧是算出来的**：按画面信息密度（码率曲线）分配帧位，重复内容自动压低帧数。
- **重复视频不重复干活**：内容相近的视频（同一条被多人转发、不同压缩版本）直接复用旧描述。
- **音频可选三档**：不读 / 本地模型 / 云端接口，默认不读。
- **慢不阻塞**：视频在后台处理，聊天照常走，处理完再补进上下文。

## 安装

1. 把整个 `plugin/` 目录放到麦麦的 `plugins/` 下（目录名建议 `video-understanding`）。
2. 放好配套 NapCat 插件（见上）。
3. 在 WebUI 启用本插件。
4. **`extract.ffmpeg_path` / `extract.ffprobe_path` 填绝对路径**。
   麦麦进程可能继承的是很旧的 PATH，`shutil.which("ffmpeg")` 会返回 `None`，
   导致抽帧报 `WinError 2`。这一项别偷懒。

## 配置

| 分组 | 字段 | 默认 | 说明 |
|---|---|---|---|
| 插件 | enabled | true | 总开关 |
| 抽帧 | ffmpeg_path / ffprobe_path | 空 | **建议填绝对路径** |
| 抽帧 | seconds_per_frame | 4.0 | 每几秒一帧 |
| 抽帧 | min_frames / max_frames | 0 / 0 | 0 表示自适应 |
| 音频 | mode | off | off / local / api |
| 音频 | api_key / api_url / api_model | 空 | api 模式用 |
| 音频 | local_model / local_tokens | 空 | local 模式用（见下） |
| 音频 | min_free_mb / fallback / unload_after | 500 / true / true | 内存闸门与回退 |
| 视觉 | mode | host | host=走主程序任务 / direct=直连接口 |
| 视觉 | api_url / api_key / model | 空 | direct 模式用（不动主程序 vlm 任务） |
| 缓存 | enabled / match_threshold | true / 0.9 | 相似视频复用 |
| NapCat | fetch_dir | 空 | **NapCat 取回插件的下载目录** |
| NapCat | fetch_wait_s | 60 | 等下载完成的最长秒数 |
| 视频源 | cleanup_after | true | **理解完成后删除视频与中间文件**（描述已入缓存，不影响复用） |
| 视频源 | max_video_mb / 单条上限 / 并发 | 80 / 3 / 1 | 体积与并发护栏 |

## 本地音频模式（可选）

需要 `pip install sherpa-onnx`，并准备 SenseVoice 模型：

- `model.int8.onnx`（约 228MB）
- `tokens.txt`

把路径填进 `audio.local_model` / `audio.local_tokens`。

实测：加载 1s，内存 322MB，速度 25~40 倍实时。**内存吃紧的机器慎用**，
可保持 `audio.mode = off` 或改用 `api`。

三档之外还有两道保险：**内存闸门**（低于 `min_free_mb` 就不加载本地模型）与
**失败回退**（api ↔ local 自动切换）。

## 磁盘占用

默认 `cleanup_after = true`：理解完成后立即删除视频本体与中间产物（帧、音轨），
服务器不会因为跑视频理解而堆文件。描述已存签名缓存，删除不影响重复视频复用。
若要保留原始视频，把开关关掉即可。

## 处理流程

```
消息到达
 ├─ 识别视频素材（结构化段 / NapCat 文本占位）
 ├─ 写入「理解中」占位
 └─ 后台：取视频 → 查签名缓存 → 自适应抽帧 → 可选转录 → 模型理解
      └─ 结果在模型请求前注入上下文
```

## 依赖

- ffmpeg / ffprobe（必须，建议配绝对路径）
- 主程序：1.0.0+，SDK 2.0.0+
- 配套 NapCat 插件（本仓库 `napcat-plugin/video-probe`）
- 可选：sherpa-onnx（本地音频模式）

## 实测

- 群里发视频 → 约 6 秒后完成理解，描述含姿态变化（多帧生效）
- 重复视频第二次：0.4~0.6 秒复用，跳过模型调用
- 5 分钟循环视频：压到 3 帧，描述仍准确

## 许可

MIT
