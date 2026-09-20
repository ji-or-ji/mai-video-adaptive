# 开发说明

面向**改这个插件的人**。使用者只需要看 `README.md`。

这里放技术细节、内部机制、踩过的坑。设计层面的取舍记录在 `DESIGN.md`，
未验证项与后续计划在 `TODO.md`。

---

## 目录结构

```
<仓库根>                     ← 仓库根就是插件本体
├── _manifest.json           ← 麦麦插件清单（市场校验器只认根目录这一份）
├── plugin.py                ← 主逻辑：钩子、配置、取回、注入、工具
├── adaptive_frames.py       ← 抽帧 / 音频 / 视觉理解（核心，与宿主解耦）
├── media.py                 ← 从消息里识别视频素材、落盘
├── timeline_store.py        ← 时间轴落盘与检索
├── napcat-plugin/video-fetch/   ← 配套 NapCat 插件（取回原片）
└── tools/                   ← 测试脚本
```

`adaptive_frames.py` 不依赖麦麦，可单独跑；`plugin.py` 只做接线。

---

## 注入契约（改之前必读）

注入点是 `maisaka.replyer.before_model_request`。它给的是麦麦自己的
**ContextItem 列表**（`kwargs["items"]`），**不是** OpenAI 风格的 messages。

**必须追加在列表末尾。** 原因：模型开了 prompt 前缀缓存
（`cache_price_in` 远低于普通输入价），缓存命中要求前缀逐字节一致。
插在前面会让每次请求的前缀都变，缓存全废、按全价重算。

两个硬性要求：

1. **返回值必须带 `item_schema_version`**（当前 `1`），否则麦麦整包忽略，
   日志会报「items 无法反序列化」。
2. item 结构：

```python
{"item_type": "SystemMessageItem",
 "meta": {"item_id": uuid, "logical_turn_id": None, "timestamp": iso},
 "parts": [{"type": "text", "text": ...}]}
```

### 注入是「单次请求有效」的

麦麦**每次请求都重建 `items`**，所以上下文里看不到上一次注入的内容。
后果：`_M_DONE` 那个「已在上下文里就不重复注入」的检查**永不命中**。

实测代价：10 分钟内把同一份摘要重注了 7 次。
现在用相关性闸门控制（`timeline.inject_fresh_seconds`）——刚理解完的窗口内每条都注，
过窗口后只在最近消息提到视频时才注。

### 消息状态标记不能带时态

`on_after_process` 会改写 `processed_plain_text`（bot 眼里的用户消息文本），追加一句状态。

**这句写进去就冻结了**——理解完成后没有任何代码回去改它。
所以绝不能写「正在理解」，否则 bot 的历史里永远挂着一条没完成的进度条，
被问到时倾向回答「还没好 / 加载不出来」。实测就是这样踩的。

另一个雷：**标记文本绝不能包含 `_M_DONE`**。去重检查是扫全部 items 找 `_M_DONE`，
一旦消息文本里出现它，注入会被**永久静默关掉**。测试里已钉死这条。

---

## 组件启用态（`read_video` 怎么开关）

`@Tool` 是**导入期**装饰器，只能在类定义时决定注册与否，运行期删不掉。
所以「不开保留原片就不提供工具」不能靠不注册实现。

做法：**保持注册，在 `get_components` 里把它的 `metadata["enabled"]` 置 False**。
不进 planner 视野，模型看不到，也就不会白调。

**不要改成从声明里剔除组件**：组件没进注册表时，运行期再启用会因「未找到组件」失败，
只能重载插件才能恢复。保留注册、只改启用态才能热切换。

配置热改时由 `_sync_read_video_state` 调 `enable_component` / `disable_component` 同步
（需要在 `_manifest.json` 声明 `component.enable` / `component.disable` 能力，
少了会报 `E_CAPABILITY_DENIED`）。

---

## 取回链路（为什么要一个 NapCat 插件）

**QQ 不下群视频原片。** 实测服务器上 181 个视频一个 mp4 都没有，只有封面 png；
napcat-adapter 又把视频段压成纯文本、丢掉下载地址。OneBot 的 `get_file` /
`get_msg` / `download_file` / `get_group_file_url` **全部拿不到原片**。

唯一可靠路径：在 NapCat 插件里监听**原始事件**，从中取真实 CDN 地址
（`multimedia.nt.qq.com.cn/...&rkey=...`）再下载。

用 `node:https` 下载，不用 `fetch`——实测该环境 `fetch` 报 `fetch failed`。

### 安全

- 拦截私网/元数据地址（硬底线，默认永远生效）
- 域名白名单是**可选**收紧项（环境变量 `VIDEO_FETCH_ALLOWED_HOSTS`），不作默认——
  它依赖 QQ 当前的 CDN 域名，对方一换域名就误杀，属于会随外部变化失效的约束
- 内容大小上限 500MB

### 暂存区必须按时间清

NapCat 取回目录只是**中转站**。原来只靠 `cleanup_after` 删，不够：

实测 7 天，检测到视频 **158** 次，清理只触发 **18** 次，暂存目录堆到 **46 个文件 / 667MB**。

漏点不止一个：重启会 `cancel` 掉后台任务（`finally` 来不及跑）、合并转发里
NapCat 下了但 MaiBot 认不出、`asset.name` 与落盘名可能对不上。
**堵单个漏点不可靠**，所以改成按时间清（`napcat.fetch_keep_hours`，默认 24h），
对所有漏点都成立。`latest.json` 台账不删。

---

## 音频链路

抽轨 → 静音分析 → **按静音切段** → 小段合并成块 → 逐块转录。

**不能全局去静音**（旧实现）：那样时间轴与原视频错位，转录出来的时间对不上。

块合并的两个旋钮：

| 参数 | 作用 |
|---|---|
| `block_seconds` | 单块最长秒数 |
| `gap_seconds` | 停顿短于此值就并成一块 ← 这个才是省调用的关键 |

实测：40 秒三段话，`gap_seconds` 从 1.5 提到 6.0，ASR 调用从 **3 次降到 1 次**。

### 内存护栏

| 机制 | 配置 | 行为 |
|---|---|---|
| 内存闸门 | `audio.min_free_mb` | 加载前查可用内存，不达标就不加载，直接降级 |
| 失败回退 | `audio.fallback` | api ↔ local 自动切换，每次切换重新过闸门 |
| 用完即卸 | `audio.unload_after` | 转录完立即释放模型 |

### 本地 ASR 卡的是 CPU，不是内存

`audio.mode=local` 走 sherpa-onnx SenseVoice。模型占 322MB 内存，
但真正的门槛是 CPU：桌面级 CPU 上 25~40 倍实时，**赛扬 G1840 这类双核只有 3~8 倍**,
一段 60 秒语音要啃 8~20 秒 CPU，还要和 ffmpeg 抢那两个核。

**内存小可以开本地，CPU 弱反而应该用 api。** 两者别搞反。

（sherpa-onnx 未装时给的是可操作报错，不是裸 `ImportError`。）

---

## 时间轴与存储

```
<麦麦数据目录>/data/plugins/<插件id>/
├── timeline/<标识>.json     # 完整时间轴（分段明细），总是写
├── timeline_index.json      # 标识 → 群 / 消息id / 文件名 / 压缩摘要
├── kept_videos/<标识>.mp4   # 仅 keep_video = true
└── video_sig_cache.json     # 签名缓存
```

注意 `<ctx.paths.data_dir>` **不是** `plugins/data/`，而是 `data/plugins/<插件id>/`
（这个我按错的路径找了两轮）。

**标识** = `sha1(视频签名)[:16]`。描述、时间轴、原片三份数据共用它，不另造索引。

### 摘要的两条轨道

视觉与音频**分开存**，只在摘要层合并，**共用同一份预算**
（最多 6 行 / 282 字符）：单边有内容时独占整行，两边都有各分一半。
所以黑屏但有说话的视频不会丢内容，也不会因为多了音频让常驻体积翻倍。

### 按文件名复用

`cleanup_after` 删掉原片后，同一条视频被转发/引用再来会取回失败。
所以进 `_handle` **先按文件名查已有时间轴**，命中直接复用——放在取回之前，
否则要先傻等 `fetch_wait_s`（60 秒）超时。

`read_video` 的 `video_id` 要**宽松解析**：模型眼前有两个标识
（适配器写在消息里的文件名、注入里的 `视频#编号`），实测它挑了文件名。
现在四种形态都认（key / 带#编号 / 文件名 / 文件名去扩展名）。

---

## 会话隔离：不许兜底

注入时**严格按 session 匹配，拿不到就不注入**。

曾经的写法是「本会话找不到记录时取全局最新那条」，本意是提高注入成功率，
后果是 **A 群的视频被注入到 B 群甚至私聊**，bot 在私聊里评论群里的视频内容。

**隔离是底线，注入失败只是这次没有视频信息。** 别为了可用性牺牲正确性。

---

## 踩过的坑（清单）

| 坑 | 现象 | 解法 |
|---|---|---|
| ffmpeg 不在 PATH | 抽帧报 `WinError 2` | 配绝对路径（麦麦进程继承的 PATH 可能是旧的） |
| 半截文件喂 ffprobe | 返回空 JSON `{}` | 等文件大小连续两次采样一致 |
| items 被整包丢弃 | 日志报无法反序列化 | 回传 `item_schema_version` |
| 注入被静默丢弃 | 描述进不了上下文 | 追加末尾 + 正确的 item 结构 |
| 群号取不到 | `message.get("group_id")` 是空 | 在 `message.message_info.group_info.group_id` |
| 本地 ASR 依赖缺失 | 裸 `ImportError` | 给可操作报错 |
| 默认端口凭空写 | `http_base_url` 默认 3002 | 改 3000（NapCat 标准端口） |
| 文档路径写错 | 按文档找不到文件 | 见上文 data_dir 那条 |
| 状态标记带时态 | bot 永远以为在处理中 | 改成中性描述 |
| 标记含 `_M_DONE` | 注入被永久关掉 | 测试里钉死 |

---

## 测试

```bash
python tools/session_isolation_test.py   # 会话隔离 / 注入 / 重读标识 / 暂存清理（34 项）
python tools/timeline_store_test.py      # 时间轴存储（12 项）
python tools/security_test.py            # 路径越界防护（14 项）
python tools/media_test.py               # 素材识别
python tools/novelty_test.py             # 新颖率与动态区间
python tools/sig_cache_test.py           # 签名缓存
```

需要真实模型的端到端脚本（`e2e_test.py` / `reread_test.py` / `qa_demo.py` 等）
会调外部 API，按需手动跑。
