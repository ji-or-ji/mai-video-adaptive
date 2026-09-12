# mai-video-adaptive

麦麦视频理解的自适应抽帧与音频理解模块。

让视频按内容自动决定**抽多少帧、抽在哪**，配合语音转录，交给视觉模型理解。
成本压在分币级、耗时压在秒级，重复视频直接复用结果。

> 最终交付形态是麦麦插件（`plugin/`）+ 配套 NapCat 插件（`napcat-plugin/`）。

## ⚠️ 必装前置：配套的 NapCat 插件

**不装它，整条链路一行都跑不通。**

QQ 收到群视频**不会下载原片**，只存一张封面缩略图（实测 181 个视频，服务器上一个 mp4 都没有）。
而 napcat-adapter 又会把视频段压成纯文本，把下载地址丢掉。所以光靠 OneBot 接口拿不到视频。

配套的 NapCat 插件解决了这件事：监听视频消息 → 从**原始事件**里拿到真实 CDN 地址
（`multimedia.nt.qq.com.cn/...&rkey=...`）→ 用 `https` 下载到本地目录，供本插件取用。

安装步骤见 `plugin/README.md`，三步要点：放进 `napcat/plugins/`、
在 `napcat/config/plugins.json` 写 `{"video-probe": true}`、重启 NapCat。

## 流程

1. **签名查重**：先算视频签名，命中缓存则直接复用旧描述，跳过其余全部步骤
2. **探测**：ffprobe 读时长 + 每个 packet 的字节数（码率数据）
3. **侦察采样**：全片均匀抽一批缩略图（本地，零 API 成本）
4. **算指标**：
   - 新颖率：探针帧前后半段的覆盖度，专治重复内容
   - 码率波动：密度曲线起伏，决定帧位怎么分布
5. **定帧数**：时长 × 新颖率 → 有效内容量 → 帧数区间（含地板与硬顶）
6. **排布帧位**：按密度曲线做 CDF 撒点，密处密、疏处疏
7. **抽帧**：ffmpeg 按点抽取，等比缩小
8. **音频**：抽轨 → 静音分析 → 去静音 → 转录（三档：off / local / api）
9. **理解**：关键帧 + 语音转录 → 视觉模型 → 一段陈述式描述
10. **入库**：描述连同签名写入缓存

一句话分工：**签名管「要不要重做」，新颖率管「抽几张」，密度曲线管「抽在哪」，音频管「还说了什么」。**

## 模块速查

| 函数 | 作用 |
|---|---|
| `plan_frames(video)` | 只规划不抽帧，返回 `Plan` |
| `adaptive_extract(video, out_dir)` | 规划 + 抽帧 |
| `novelty_scan(video, duration)` | 新颖率与重复度 |
| `audio_transcript(video, out_dir, mode=...)` | 音频链路（三档） |
| `transcribe_local(wav, model, tokens)` | 本地 SenseVoice 转录 |
| `describe_video(frames, transcript, api_key=...)` | 关键帧（+ 语音）→ 描述 |
| `understand_video(video, out_dir, ...)` | 完整链路（含查重） |
| `VideoSignatureCache(path)` | 签名缓存 |

## 依赖

- **ffmpeg / ffprobe**：必须在 PATH 中
- **Python 3.10+**
- 本地 ASR（可选）：`pip install sherpa-onnx` + SenseVoice 模型
- 视觉模型：DeepSeek API（`deepseek-flash`）

## 本地 ASR 模型

sherpa-onnx 的 SenseVoice（int8，约 228MB）：

- 模型 `model.int8.onnx`
- 词表 `tokens.txt`

放 `assets/models/` 下。实测：加载 1s，进程内存 322MB，速度 25~40 倍实时。

## 两条重要的工程约束

### 1. Prompt 缓存保护（注入位置）

视频理解的结果要注入到 bot 的模型请求里，**必须追加在上下文末尾，绝不能插在前面**。

模型开了 prompt 前缀缓存（`cache_price_in` 远低于普通输入价），命中前提是**前缀逐字节一致**。
插在前面会让每次请求的前缀都变，缓存全部失效，按全价重算、变慢；群聊消息频繁，累积开销可观。
放末尾则前缀（人设 + 历史）不动，缓存持续命中，描述照样被模型看到。

另外，麦麦这个钩子给的是它自己的 **ContextItem 列表**（不是 OpenAI messages），
返回值还必须带 `item_schema_version`，否则整包被丢弃。

### 2. 内存保护（本地音频模式的护栏）

本地推理约占 **322MB** 内存，对吃紧的机器（4G 老服务器）不友好。三道护栏，均可配：

| 机制 | 配置 | 默认 | 行为 |
|---|---|---|---|
| 内存闸门 | `audio.min_free_mb` | 500 | 加载模型前先查可用内存，不达标就不加载，直接降级 |
| 失败回退 | `audio.fallback` | true | api ↔ local 自动切换，两条都失败才放弃 |
| 用完即卸 | `audio.unload_after` | true | 转录完立即释放模型，不常驻 |

含义：即使配了本地模式，内存不够也不会把宿主挤崩；云端挂了能自动回本地。

## 三档音频模式

| 模式 | 值 | 说明 |
|---|---|---|
| 不用 | `off` | **默认**，不读音频 |
| 本地 | `local` | sherpa-onnx SenseVoice，无网络、不外传 |
| API | `api` | 云端 ASR，带超时降级 |

护栏机制见上节「内存保护」。

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `min_frames` / `max_frames` | `None` | 显式传入则固定；`None` 走自适应 |
| `seconds_per_frame` | 4.0 | 有效内容 → 帧数的基准（每几秒一帧） |
| `novelty_power` | 1.5 | 新颖率的倾斜力度 |
| `min_floor` / `max_cap` | 3 / 48 | 帧数地板与硬顶（成本护栏） |
| `bucket_s` | 0.5 | 密度曲线分桶粒度 |
| `max_height` | 720 | 抽帧后的最大高度 |
| `match_threshold` | 0.9 | 签名命中阈值 |
| `min_free_mb` | 500 | 本地模型内存门槛 |
| `fallback` | true | 首选途径失败时回退另一种 |
| `unload_after` | true | 转录后卸载本地模型 |

## 实测数据

| 场景 | 结果 |
|---|---|
| 74s 游戏视频 | 19 帧，描述含游戏名 / 地图 / 密码 / 收益 |
| 5 分钟循环视频 | 3 帧（新颖率 0.00），描述准确 |
| 57s 语音视频 | 12 帧 + 本地转录，全程 9s |
| 重复视频第二次 | 0.4~0.6s 复用，跳过 API |

## 开发

```bash
python tools/demo.py            # 抽帧演示（含密度图）
python tools/novelty_test.py    # 新颖率与动态区间
python tools/audio_test.py      # 音频链路
python tools/local_asr_test.py  # 本地 ASR
python tools/e2e_test.py        # 端到端
python tools/cache_e2e_test.py  # 签名缓存
```

## 状态

- [x] 自适应抽帧（码率密度 + CDF 撒点）
- [x] 新颖率与动态上下限
- [x] 音频链路（抽轨 / 静音分析 / 去静音 / 转录）
- [x] 本地 ASR（sherpa-onnx SenseVoice）
- [x] 三档音频模式（含内存闸门与失败回退）
- [x] 端到端理解与防幻觉约束
- [x] 签名缓存去重
- [x] 接入麦麦插件（`plugin/`）
- [x] 配套 NapCat 插件取回群视频（`napcat-plugin/`）
- [x] 服务器实测跑通

## 麦麦插件

`plugin/` 是可直接放进麦麦 `plugins/` 的插件。

**它有硬前置**：必须同时装一个配套的 NapCat 插件（`napcat-plugin/video-probe/`）。
因为 QQ 不下载群视频原片、适配器又把下载地址压成了纯文本，必须从 NapCat 的
原始事件里拿真实 CDN 地址并下载。安装步骤见 `plugin/README.md` 开头的「必装前置」。

插件还带三道内存护栏（内存闸门 / 失败回退 / 用完即卸），让本地音频模式
在内存吃紧的机器上也能安全启用。

注入上下文时**一律追加在末尾**：模型开了 prompt 前缀缓存，前缀一变缓存全废，
按全价重算。详见 `plugin/README.md` 的「缓存保护」。
