# mai-video-adaptive

麦麦视频理解的自适应抽帧与音频理解模块。

让视频按内容自动决定**抽多少帧、抽在哪**，配合语音转录，交给视觉模型理解。
成本压在分币级、耗时压在秒级，重复视频直接复用结果。

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

## 三档音频模式

| 模式 | 值 | 说明 |
|---|---|---|
| 不用 | `off` | **默认**，不读音频 |
| 本地 | `local` | sherpa-onnx SenseVoice，无网络、不外传 |
| API | `api` | 云端 ASR，带超时降级（超时即只交帧） |

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
- [x] 三档音频模式
- [x] 端到端理解与防幻觉约束
- [x] 签名缓存去重
- [ ] 接入麦麦插件
