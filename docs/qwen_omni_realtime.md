# Qwen3.5 → Qwen3.8 Omni Realtime 模型切换 —— 问题分析与改造设计

> **状态**：**已实施（2026-09-27）**——C1–C7 全部落地，服务端侧验证见 §10；
> **真机 App 端到端与 semantic_vad 抢答专项（§7.4/§7.5）待做**（`omni.enabled` 临时置 true 即可，见 §9.3）
> **Date**: 2026-09-27（§4 探针同日实测，workspace 专属域名 + 真实 API）
> **Scope**: `configs/dev/server.yaml`（omni 段）· `services/omni/providers/alibaba_omni.py`（核心）·
> `services/scripts/test_omni_real.py` · `services/tests/unit/omni/providers/test_alibaba_omni.py`
> **上游文档**：[实时调用指南](https://help.aliyun.com/zh/model-studio/realtime) ·
> [客户端事件](https://help.aliyun.com/zh/model-studio/client-events) ·
> [服务端事件](https://help.aliyun.com/zh/model-studio/server-events) ·
> [qwen3.8-omni-flash-realtime 模型页](https://help.aliyun.com/zh/model-studio/qwen3-8-omni-flash-realtime) ·
> [qwen3.5-omni-flash-realtime 模型页](https://help.aliyun.com/zh/model-studio/qwen3-5-omni-flash-realtime)
> （均为 2026-09-27 抓取；控制台页需登录，本文以公开页 + 真机探针为准）
> **关联**：`docs/fd_server_omni_model.md`（omni 接入设计）· `docs/fd_server_omni_model_dev.md`（实施记录）·
> `docs/qwen_livetranslate_realtime.md`（同族 3.8 迁移先例，方法论同款）·
> `docs/qwen_audio_analysis.md`（3.8-omni-flash 非实时版迁移，价格/思考模式结论勿混用）
>
> **结论文先**：
> 1. **协议层两代完全兼容：单论模型切换，改配置两行即可**——生产 payload（`modalities`/`voice`/`instructions`/
>    `turn_detection`/`enable_search`/flat 音频字段）原样直连 3.8，握手 `session.created → session.updated`
>    无 error，事件名/字段与 3.5 **逐一实测一致**（§4.2）。不存在 livetranslate 3.8 那样的破坏性改名。
>    （本次实际改动不止两行：另含 D1/D2 两个既有缺陷修复与 D3 VAD 切换，清单见 §6。）
> 2. 本次探针**同时暴露两个既有缺陷**，与 3.8 无关、但随本次迁移一并修（§4.3 / §4.4）：
>    - **输出音频 1.5 倍慢放**：`session.sample_rate`（flat）字段被服务端**回显但不生效**，omni 实际输出
>      24 kHz；服务端 Opus 编码器固定 16 kHz（`media.audio.default_sample_rate`）且 alibaba omni 链路
>      **无重采样**（`AudioFrameBuffer` 只重新分帧）。F0 实测已确证（§4.3）。**修法已拍板取 D1 方案 A**：
>      nested `audio.output.format.sample_rate=16000` 让模型直出 16 kHz（双端实测），并把请求侧的采样率
>      改为与 Opus 编码器同源（§5 D1 / C3）；服务端 soxr 重采样（方案 B）列为回退预案、本次不实施。
>    - **interim 转写自接入起一直为空**：真实事件名是 `conversation.item.input_audio_transcription.delta`
>      （字段 `text`+`stash`），provider 只认旧名 `.text` 且读 `delta` 字段——两层都不匹配（§4.4）。
>      **修法已拍板**（D2）：事件名双读 + 文本取 `text + stash`。
> 3. **VAD 按拍板切 `semantic_vad`**（D3，3.8 实测整轮可用）；真机重点验收「抢答/误打断」，回退为配置一行。
> 4. 3.8 的收益：价格大幅下降（音频入 ¥27→¥6、音频出 ¥107→¥12 每百万 tokens，§2.2）、限流 TPM 100K→2M、
>    历史限额放宽（音频 80 轮/480s → 100 轮/600s），能力/参数面基本平移（**realtime 族无思考模式**，
>    不存在 `docs/qwen_audio_analysis.md` 里 3.8 非实时版的「默认开思考」延迟陷阱）。

## 1. 背景与目标

- omni 模式的 provider 模型原为 `qwen3.5-omni-flash-realtime`（`configs/dev/server.yaml:176,178`），
  **本次迁移后为 `qwen3.8-omni-flash-realtime`**（回滚即改回这两行，见 §8）。
  `omni.enabled` 默认为 **false**（`:161`，仅三段式生效），omni 链路目前只在开图/真机联调时按需开启；
  `configs/production|staging` 为空占位，无 omni 段。
- 目标：切换至 `qwen3.8-omni-flash-realtime`（北京地域，业务空间 `llm-1tmsqifo51cwfadr` 已实测可用）。
- 非目标：不动三段式 ASR/LLM/TTS、不动 `translate`（其 3.8 迁移另见 `docs/qwen_livetranslate_realtime.md`，
  配置仍为 3.5）、不动对外 WS 协议与客户端、不启用 3.8 新增的 MCP / 多通道音频 / 视频聚合 / 声音复刻。

## 2. 官方 API 差异（3.5 → 3.8）

### 2.1 差异表（官方页原文 + 探针实测）

| 项 | 3.5-omni-flash-realtime（现状） | 3.8-omni-flash-realtime | 影响 / 代码位置 |
|---|---|---|---|
| 接入地址 | 公共域名与业务空间专属域名均可 | **必须使用绑定业务空间的 WS 地址**（模型页原文） | 现状已是 workspace 专属域名（`server.yaml:176`），探针实测连通 ✓ |
| 事件名/字段（WS） | `response.text.delta` / `response.audio_transcript.delta` / `response.audio.delta` / `response.created|done` / `input_audio_buffer.speech_started|stopped` / `conversation.item.input_audio_transcription.delta|completed` | **逐一实测同名同形**（§4.2） | provider 事件分支无需改动 |
| 输出模态 | `session.modalities` | 同名字段（**未改名**，与 livetranslate 3.8 的 `output_modalities` 不同） | `alibaba_omni.py:462` 不变 |
| 音频配置 | flat `input_audio_format`/`output_audio_format`/`sample_rate` 为兼容字段（回显可见）；nested `audio.*` 3.5 亦支持 | 官方建议 nested `audio.input.format`/`audio.output.format`；flat 保留兼容 | **flat `sample_rate` 两代都被回显但被忽略**（§4.3） |
| 音色 | 默认 Tina；flat `voice` | 默认 Tina；新增 nested `audio.output.voice`（优先于 `voice`），新增 `longanlingxin` 等 | 现配置 `voice: Tina` 继续可用 |
| VAD | `server_vad`（默认）/ `semantic_vad`；`idle_timeout_ms` 仅 3.5 支持 | `server_vad` / `semantic_vad`（3.8 实测接受且整轮可用，§4.6）；无 `idle_timeout_ms` | 本次随迁移切 `semantic_vad`（D3），现状未用 idle 字段 |
| 采样参数默认 | temperature 0.7 / top_p 0.8 / presence 1.5 / repetition 1.0 | temperature 0.6 / top_p 0.95 / presence 1.0 / repetition **0**（支持 0）；`max_tokens` 显式范围 [1, 65536] | 本项目显式传 0.8/0.9/1024（`generation_opts`），在 3.8 合法区间内 ✓ |
| 思考模式 | 无 | **无**（realtime 族文档无 `reasoning_effort`/`enable_thinking` 参数） | 无 3.8 非实时版那样的延迟陷阱 |
| 图片输入 | `input_image_buffer.append`（base64 JPEG；须先发过音频；随 `input_audio_buffer.commit` 提交） | 同（探针实测可用，§4.5） | 抓屏链路不受影响 |
| 历史限额 | 音频 80 轮 / 480s，视频 50 轮 / 120s | 音频 **100 轮 / 600s**，视频 50 轮 / **240s**；输入 token 上限 196608 一致 | 放宽 |
| 单会话时长 | 最长 120 分钟 | 同 | — |

> livetranslate 3.8 的「`modalities` → `output_modalities`、转写 `.text` → `.delta`」等破坏性改名
> **不适用于 omni 族**：omni 的转写增量事件在 **3.5 上就已经叫 `.delta`**（§4.4 实测），两代一致。

### 2.2 能力、价格与限流（华北2 北京）

| 项 | 3.5-omni-flash-realtime | 3.8-omni-flash-realtime |
|---|---|---|
| 输入模态 | 文本/图片/视频/音频 | 文本/流式音频/视频（连续图像帧）（模型页口径）+ 多通道 1/2/4 声道；静态图片经 `input_image_buffer.append` **探针实测可用**（§4.5） |
| 价格：输入音频 | ¥27 / M | **¥6 / M** |
| 价格：输出音频 | ¥107 / M（输出的文本**不**计费） | **¥12 / M**（音频与对应文本**分别**计费，文本出 ¥4.5 / M） |
| 价格：输入文本/图片/视频 | ¥3.3 / M | ¥1.5 / M |
| 价格：输出文本 | ¥20 / M（3.5 realtime 不单独计费） | ¥4.5 / M |
| 限流 | 60 RPM / 100,000 TPM | 60 RPM / **2,000,000 TPM** |
| 免费额度 | — | 100 万 token（北京，开通/发布起 90 天，以较晚者为准） |
| 新增能力 | — | 自定义 Function Calling、MCP、多通道音频、视频聚合（`session.video.input.representation_compact`，token 数降至 1/4）、声音复刻 |

> 计费口径变化：3.8 输出侧「音频 + 文本」双计费。按本场景（~12.5 token/s 音频输出、文本出百 token 级）
> 折算仍大幅低于 3.5（音频出单价 1/8.9，且文本量小）。实际单价以百炼控制台为准。

## 3. 调用链（代码现状）

| 环节 | 位置 | 说明 |
|---|---|---|
| 装配 | `services/gateway/endpoint.py:100` | `omni.enabled=true` 时选 `OmniOrchestrator`，否则三段式 |
| 采样率 | `endpoint.py:83-99`；`omni_orchestrator.py:202,286` | opus 编解码器固定用 `cfg.media.audio.default_sample_rate`（**16000**）；但 provider 是以 `ctx.audio_sample_rate`（会话声明值）打开的——二者不同源，见 §4.3 / D1 |
| 握手 | `services/omni/providers/alibaba_omni.py:416-503` | 等 `session.created` → 发 `session.update` → 等 `session.updated` |
| session.update 构造 | `alibaba_omni.py:461-478` | `modalities`/`voice`/`input_audio_format`/`output_audio_format`/**`sample_rate`（flat）**/`turn_detection: server_vad`/`instructions`/`enable_search`/`search_options` + `generation_opts` |
| 收流 | `alibaba_omni.py:145-287` | 事件 → `OmniEvent` 队列；`response.created` 作回合边界清陈旧输出事件 |
| 转写映射 | `alibaba_omni.py:235-253` | 只认 `conversation.item.input_audio_transcription.text`，取 `delta` 字段（**见 §4.4**） |
| 音频出 | `services/session/omni_orchestrator.py:576-586` | `AUDIO_DELTA` → `AudioFrameBuffer` 分帧 → `OpusEncoder` → `ai_audio`；**无重采样**（见 §4.3） |
| 图片入 | `alibaba_omni.py:320-345` | `input_image_buffer.append`；被 API 判非法图时 `_image_disabled` 关本会话图片 |

## 4. 真机探针结果（2026-09-27）

### 4.1 方法与工具

- **端到端（生产 payload）**：`services/scripts/test_omni_real.py`——走真实 provider，workspace 专属域名 +
  `X-DashScope-WorkSpace`，音频 `~/projects/asr_test1.wav`（16 kHz 单声道），`voice=Tina`。
  provider 的消息级 DEBUG 日志（`livachat.omni.alibaba.messages`）提供事件普查。
- **裸 WS（字段/参数级）**：临时脚本（`/tmp/raw_probe.py`、`/tmp/img_probe.py`，未入库）直连同一地址，
  文本模式用 `conversation.item.create(input_text)` + `response.create` 规避 ASR 内容差异；
  采样率用 **F0（基频）判定**：同一音色同一文本下，采样率误判会产生恰好 1.5 倍的基频偏移。
  估计器在已知 16 kHz 人声（`asr_test1.wav`）上标定为 127 Hz（合理男声/低音女声），可信。
- 全过程只读：不写库、不改配置；产生的 wav/pcm 落在 `/tmp`。

### 4.2 3.8 兼容性（核心结论）

`test_omni_real.py --model qwen3.8-omni-flash-realtime`（生产 payload 原样）：

```
[recv] session.created → [recv] session.updated（无 error，未改任何参数）
响应正常：full text='你好呀，测试听起来很顺利呢。'
事件普查与 3.5 基线逐项一致：
  response.created / response.text.delta / response.audio_transcript.delta|done / response.audio.delta|done
  response.content_part.* / response.output_item.* / response.done
  input_audio_buffer.speech_started|stopped|committed / conversation.item.created
  conversation.item.input_audio_transcription.delta ×22 + .completed ×1
```

- `session.updated` 回显：`modalities/instructions/voice/input_audio_format/output_audio_format/
  input_audio_transcription/turn_detection/sample_rate/enable_search/search_options` 全部被接受。
- 首包时延：`speech_stopped` → 首个文本/音频 delta，3.5 与 3.8 同秒（秒级粒度下无差异）。
- 空闲行为：会话建立后完全空闲 150s、以及**答完一轮再空闲 150s**，均未收到 `session.finished`
  （与 livetranslate 3.8 实测的 ~60s 空闲终结**不同**；官方上限是单会话 120 分钟）。
- 图片链路（§4.5）、`semantic_vad`（§4.6）在 3.8 上均实测可用。

### 4.3 发现 1（既有缺陷）：flat `sample_rate` 被忽略 → 输出 24 kHz，服务端按 16 kHz 编码 = 1.5 倍慢放

测法：**同一音色 + 同一文本**成对跑，F0 中位数一律按 16 kHz 假设解析——若真实采样率差一档，基频应整体差 1.5 倍。

| 文本 | 请求形态 | F0 中位数 | 判定 |
|---|---|---|---|
| 朗读句 | flat `sample_rate: 16000` 仅此（= 生产现状） | 147 Hz | **24 kHz** |
| 朗读句 | ＋nested `audio.output.format=24000` | 160 Hz | 24 kHz |
| 朗读句 | ＋nested `audio.output.format=16000` | **239 Hz** | 16 kHz |
| 长元音「啊」 | flat `sample_rate: 16000` 仅此（= 生产现状） | 163 / 165 Hz（两次） | **24 kHz** |
| 长元音「啊」 | ＋nested `audio.output.format=24000` | 155 Hz | 24 kHz |
| 长元音「啊」 | ＋nested `audio.output.format=16000` | **232 Hz** | 16 kHz |
| 长元音「啊」（只改 flat） | flat `sample_rate: 24000` | 170 Hz（对照 163 Hz） | flat **无效** |
| 数数句（3.8） | flat 现状 → ＋nested `output.format=16000` | 172 Hz → **281 Hz** | 3.8 同规律 |

- 结论：**同一文本**下 nested 16k 档恒为其余两档的 ≈1.5 倍（朗读句 239 vs 160/147，长元音 232 vs 155/163），
  证明 nested 字段生效；而**只改 flat 字段**（16000→24000）基频几乎不动（163→170 Hz，远非 1.5 倍），
  且 flat-only 与 nested-24000 同档（147 vs 160、163/165 vs 155，差 ≤9%，属跑间音高抖动）——证明 flat `sample_rate`
  **被回显但不生效**（实际输出 = 默认 24 kHz）。与 livetranslate 3.8 的 `modalities` 回显不生效是同一种坑
  （回显 ≠ 生效）。
- **语速对照（更直观的交叉验证，2026-09-27 补测）**：同一句 39 字回复，omni 3.5 现状输出按 16 kHz 标签为
  **9.72s（4.0 字/秒）**；同文本三段式 TTS（`test_bailian_tts_real.py`，16 kHz）为 **6.85s（5.7 字/秒）**。
  9.72s ÷ 1.5 = 6.48s（6.0 字/秒），与 TTS 基准吻合——1.5 倍关系独立复现。
  样本：`/tmp/before_35_omni.wav`（问题样本）、`/tmp/tts_ref_16k.wav`（语速基准）。
- 现状影响：模型产出 24 kHz PCM 被直接喂进 16 kHz `OpusEncoder`（`endpoint.py:88-94`，
  `media/buffer.py:AudioFrameBuffer` 只重分帧、不重采样），客户端按 16 kHz 解码 → **语速 1.5 倍慢、
  音调 1.5 倍低**。`openai_realtime` provider 有 `Resampler`（`openai_realtime.py:62`），alibaba 链路没有。
- 由于 `omni.enabled=false`（`server.yaml:161`）且 production/staging 无 omni 段，**该缺陷当前不在线上暴露**，
  但一旦启用 omni 即生效——已随本次迁移修复（D1）。

### 4.4 发现 2（既有缺陷）：interim 转写事件名不符，自接入起 interim 全程为空

真实帧（3.5 与 3.8 同形，已删 `obfuscation`）：

```json
{"type": "conversation.item.input_audio_transcription.delta",
 "item_id": "item_...", "content_index": 0,
 "text": "", "stash": "你好", "language": "zh", "emotion": "neutral"}
{"type": "conversation.item.input_audio_transcription.completed",
 "transcript": "你好，这是一个ASR的测试识别测试。你好，非常好，我很喜欢。", ...}
```

- 语义：`text` = 已确认前缀，`stash` = 待修正后缀，**当前最完整预览 = `text + stash`**（官方原文）。
- 现状代码 `alibaba_omni.py:235` 只认 `.text` 事件名并取 `delta` 字段——**事件名与字段两层都不存在**
  （实测两代都只发 `.delta` + `text`/`stash`），于是 **24 条真实增量事件被全部丢弃**（探针端到端输出里
  `input_transcription_delta` 一条都没有，只有 `.completed` 落成 final）。
  客户端 `transcription(is_final=false)` 路径因此从未收到过 interim。
- `.completed` 映射正确（`transcript` 字段），无需改动。

### 4.5 图片/抓屏链路（3.8 实测可用）

- `input_image_buffer.append`（base64 JPEG，30 KB）在 3.8 上被接受，无 error 帧；手工模式下
  `音频 → 图片 → input_audio_buffer.commit → conversation.item.create(文本提问) → response.create`
  顺序实测通过：640×360 红色圆形 → 模型答 **「圆形，红色」**。
- 反例与干扰项：1280×720、11 px 小字（"LIVA-CHAT-42"）的图，模型答非所问——属**图内文字过小**，
  不是协议问题（同款小字图在 3.5 上表现相同）。
- 官方图片约束（现行文档）：JPG/JPEG、建议 480p/720p、**最高不超过 1080p**、base64 后 **≤256 KB**
  （建议原始 ≤190 KB）、建议 1 张/秒、**发送前须已发过至少一次 `input_audio_buffer.append`**。
  抓屏当前长边上限 1920（`docs/android_screen_recording.md`）≈1080p，压到 ≤190 KB 需在真机回归项里确认。

### 4.6 semantic_vad（3.8 实测可用，本次采用 —— D3）

- **参数被接受并回显**：`turn_detection.type=semantic_vad` → 3.8 回显
  `{"type": "semantic_vad", "threshold": 0.5, "prefix_padding_ms": 300, "silence_duration_ms": 800,
  "create_response": true, "interrupt_response": true}`。官方对本族推荐 semantic_vad（按语义有效性判停，
  过滤附和声/背景音）。
- **整轮链路正常**：3.8 实测帧序为
  `speech_started → conversation.item.created → speech_stopped → committed → transcription.completed →
  response.created → … → response.done`；`input_audio_buffer.speech_started|stopped` 照常下发，
  orchestrator 的 `SPEECH_STARTED → user_voice 打断`（`omni_orchestrator.py:541-543`）与自动出音频均不受影响
  （`interrupt_response: false` 对照实验：3.8 音频输出完整，126 KB / 104 KB 两轮）。
- **打断灵敏度差异（需真机回归；样本小，仅作提示）**：同一路「持续人声 + 默认 `interrupt_response: true`」的
  探针里，3.8 三次**均在产出音频前被取消**（`response.audio.delta` 0 条，文本只剩「你好」），
  3.5 单次对照则先产出了音频才被打断。取消本身是 barge-in 的设计行为（用户说话即打断），
  但 3.8 上「一个字都没发出来就被打断」更容易出现——对应真机上「长句停顿/边说边听」的抢答场景，
  列为验收项（§7.5）；如体验不佳，可调 `silence_duration_ms`（默认 800，可降）/ `threshold`，
  或退回 `server_vad`（配置级）。

## 5. 设计决策

### D1 采样率口径：nested 直配 16 kHz（方案 A，已拍板 —— 2026-09-27）

**问题**：模型当前按默认出 24 kHz PCM，而 `AudioFrameBuffer` 只重分帧、`OpusEncoder` 固定 16 kHz，
两者之间没有重采样 → 1.5 倍慢放（§4.3）。两条修法：

| | **方案 A：nested 直配 16 kHz（✅ 采用）** | 方案 B：服务端重采样（回退预案，不实施） |
|---|---|---|
| 做法 | `session.update` 增补 nested `audio.input/output.format.sample_rate=16000`，模型直出 16 kHz | 输出维持 24 kHz，在 provider 收流处用 `media/resample.py:Resampler`（soxr）转 16 kHz 后再进 `AudioFrameBuffer` → Opus |
| 实测 | **3.5 / 3.8 双端已验证**：nested 被接受、`session.updated` 原样回显，F0 判定输出确为 16 kHz（§4.3） | 仓库已有同款模式可抄：`openai_realtime.py:62`（`Resampler(_OPENAI_OUTPUT_RATE, sample_rate)`） |
| 改动面 | `session_cfg` 加 ~6 行 + 采样率传参 2 处（C2/C3，无新逻辑） | ~20-30 行：resampler 实例化 + 重连 `reset()` + `RESPONSE_DONE` 时 flush 残样 + 单测 |
| 额外开销/风险 | 无（16 kHz 转换在厂商侧；采样率档位官方列于 3.5 系文档，3.8 由探针实测确认） | 每会话一次流式重采样（CPU 可忽略）；多一处状态需与重连/清队列路径对齐 |
| 对「厂商默认采样率变化」的稳健性 | 高（显式声明，不依赖默认值） | 低（硬编码 `24000` 假设） |
| 质量 | 16 kHz 即 Opus 16 kHz 带宽上限，无二次损失 | 同（终点一样是 16 kHz） |

**已拍板取方案 A**（2026-09-27）：零代码逻辑、双端实测、且不依赖默认值；与设计定调
`docs/fd_server_omni_model_dev.md:179`「Qwen 可配 16 kHz，优先直配免重采样」一致。
**方案 B 保留为回退预案（本次不实施）**：若真机验收发现厂商 16 kHz 转换质量不佳、或未来某代忽略 nested 字段，
再按 `openai_realtime` 模式补 resampler（届时 A 的 nested 字段保留无冲突）。

请求侧写法（nested 与 flat 同发，与 livetranslate D4「版本无关写法」同思路）：

```python
"audio": {
    "input":  {"format": {"type": "pcm", "sample_rate": sample_rate}},
    "output": {"format": {"type": "pcm", "sample_rate": sample_rate},   # 与 Opus 编码器同值
               "voice": voice or self._voice},                           # nested 优先于 flat
},
# flat 兼容字段（input_audio_format/output_audio_format/sample_rate/voice）保留：回显可见、老版兼容
```

⚠️ **取值来源（必做，见 C3）**：`sample_rate` 必须与 Opus 编解码器同源
（`cfg.media.audio.default_sample_rate=16000`，`endpoint.py:88-99`）。现状 provider 收到的是
`ctx.audio_sample_rate`（客户端 `session.start` 声明值），二者仅靠「客户端默认 16000」巧合一致；
实现上由 `endpoint.py` 把该值传给 `OmniOrchestrator`，orchestrator 打开 omni 流时用它，
保证「模型输入/输出采样率 == 编解码器采样率」。（客户端真声明 24 kHz 时输入链路同样会错配，
属既有约束，本次不扩面。）

### D2 interim 转写：改认 `.delta` + `text + stash`（已拍板 —— 2026-09-27，随本次一起修）

- 方案：事件名兼容双读（`.delta` 主用、`.text` 保留兜底），文本取 `text + stash`（若两者皆空再回退 `delta`）。
  参考实现：`openai_realtime.py:245-253` 已有双读写法（但其文本取 `delta` 字段，omni 需改取 `text + stash`）。
- 语义对齐：orchestrator 对 `INPUT_TRANSCRIPTION_DELTA` 只做「覆盖式 interim 上抛」（`is_final=false`），
  与官方「text+stash = 当前最完整预览」语义一致，无需在 provider 做 diff 转换。
- 连带：`test_alibaba_omni.py:91-100` 的用例当前断言的是**错误形态**（`.text` + `delta`），需重写。

### D3 VAD：切 `semantic_vad`（已拍板 —— 2026-09-27，随本次迁移一起做）

- 配置：`turn_detection: {"type": "semantic_vad"}`（`threshold` / `silence_duration_ms` 用服务端默认
  0.5 / 800，暂不新增配置项；真机若需调，先硬编码验证再决定是否透出）。
- 依据：官方对本族推荐（过滤附和声、背景音等无意义声音）；3.8 实测整轮可用（§4.6）。
- 连带验收：**不要**顺带设 `interrupt_response: false` —— 那会同时关掉服务端 barge-in；本链路的
  打断依赖两条路径（客户端 `interrupt` 消息 → `cancel_response`；`SPEECH_STARTED` → `user_voice` 打断，
  `omni_orchestrator.py:541-543`），semantic_vad 下 `SPEECH_STARTED` 照常下发（§4.6 实测）。
- 回退：真机若发现抢答/误打断变差，配置级改回 `server_vad`（一行）。

### D4 参数与能力：不动

- `generation_opts`（temperature 0.8 / max_tokens 1024 / top_p 0.9）落在 3.8 合法区间；
  `enable_search: true` + `search_options` 被 3.8 接受（与 `tools` 互斥，本项目未用 tools）。
- 3.8 新增的 MCP / 多通道 / 视频聚合 / 声音复刻本次**不启用**（无需求，且会引入新配置面）。

## 6. 改动清单

| # | 文件 | 位置 | 改动 |
|---|---|---|---|
| C1 | `configs/dev/server.yaml` | `:176`、`:178` | `qwen3.5-omni-flash-realtime` → `qwen3.8-omni-flash-realtime`（`base_url` 查询串 + `model` 两处；域名与 workspace_id 不变） |
| C2 | `services/omni/providers/alibaba_omni.py` | `:461-478` | `session_cfg`：①增补 nested `audio.input.format` / `audio.output.format` / `audio.output.voice`（D1）；②`turn_detection.type` → `semantic_vad`（D3）；flat 字段保留 |
| C3 | `services/gateway/endpoint.py` + `services/session/omni_orchestrator.py` | `endpoint.py:83`、`:103-118`；`omni_orchestrator.py:30-57`、`:202`、`:286` | 采样率同源（D1 前置）：`endpoint` 把 `output_sample_rate`（= `cfg.media.audio.default_sample_rate`）传入 `OmniOrchestrator`，orchestrator 打开/重开 omni 流时用它作 `sample_rate`（替换 `ctx.audio_sample_rate`）；无编码器时回退会话声明值 |
| C4 | `services/omni/providers/alibaba_omni.py` | `:235-243` | 转写增量：双读 `.delta`/`.text`，文本 = `text + stash`（D2） |
| C5 | `services/scripts/test_omni_real.py` | `:32` | `DEFAULT_MODEL` → 3.8（探针默认值与生产对齐） |
| C6 | `services/tests/unit/omni/providers/test_alibaba_omni.py`、`services/tests/integration/omni/` | `:44-56`（session fixture）、`:91-100`、`:212-245`；`conftest.py:57-80` | 转写用例改为 `.delta` + `text`/`stash`（含 `.text` 兼容用例）；`turn_detection` 断言 `:52`/`:242` 改 `semantic_vad`；握手 payload 断言增补 nested `audio.*`；orchestrator 采样率来源加断言。**注意 `test_openai_realtime.py` 的同名 `server_vad` 断言属另一 provider，不得连带修改** |
| C7 | `docs/fd_server_omni_model.md`、`docs/fd_server_omni_model_dev.md` | 前者 `:222`、`:490-492`；后者 `:137`、`:165` | 模型串同步为 3.8；后者把记录中的错误事件名 `conversation.item.input_audio_transcription.text` 改为实测的 `.delta`（`text`+`stash`）；实施后本文档状态改「已实施」 |

不改：`omni/base.py` 抽象、`OmniService`/factory、协议 schema、客户端；`omni_orchestrator.py`
除 C3 的采样率取值外无其他改动（事件泵、打断、图片、录制链路均不动）。

## 7. 验收标准

1. **单测**：`cd services && pytest tests/unit/omni tests/integration/omni -q` 全绿（含新转写用例、
   nested `audio.*` + `semantic_vad` 的 payload 断言、orchestrator 采样率取编码器口径的断言）。
2. **真机脚本**：`test_omni_real.py --model qwen3.8-omni-flash-realtime --base-url <workspace 域名> --workspace-id ...`
   ——握手无 error；回复文本正常；`input_transcription_delta` 事件**成串出现**且内容递进、与 final 一致。
3. **采样率**：同文本语速与三段式 TTS **同档**（对照基准 ≈5.7 字/秒，现状为 ≈4.0 字/秒）；F0 落在 Tina 常态
   区间（不再整体低 1.5 倍）。对照样本见 §4.3。
4. **真机 App（omni.enabled=true）**：语音对话音色/语速正常；抓屏图片问答正确；长会话/长时间空闲不哑掉。
   探针实测 3.8 空闲 150s 未触发 `session.finished`（§4.2），但若真机出现流终结，`omni_orchestrator`
   会在下一帧音频自动重开（泵退出重启逻辑，日志 `omni stream ended; will reopen on next audio`）——
   既有行为提示：重开是**全新 vendor 会话、模型上下文清空**（本地 `_history` 不回放），本次不修，
   验收只需确认「不哑掉/不报错」。
5. **打断（semantic_vad 专项，重点看「抢答」）**：长句中间停顿时模型不应过早开口；用户中途插话应能打断；
   模型不应「一个字都没发出来就被自己的尾音打断」（§4.6 探针里 3.8 更易出现）；不达标先调
   `silence_duration_ms`，再考虑退回 `server_vad`。
6. **回归**：`omni.enabled=false` 时三段式行为不变（本改动不触碰该路径）。

## 8. 风险与回滚

| 风险 | 等级 | 说明与对策 |
|---|---|---|
| 3.8 需业务空间专属地址 | 低 | 现状配置已是专属域名；探针同账号同域名实测连通（C1 不改域名） |
| D1 修复后**听感明显变化**（1.5 倍变快/变高） | 中 | 属**修复**而非回归；需真机听感验收（§7.3），避免被误判为新模型音色差异 |
| semantic_vad 打断灵敏度变化（抢答/误打断） | 中 | 探针里 3.8 更易「未出声即被打断」（§4.6）；真机专项验收（§7.5），可调 `silence_duration_ms`，或配置级回退 `server_vad` |
| D2 修复后客户端首次出现 interim 转写 | 中 | 客户端三段式路径已有 interim 处理，逻辑同构；真机确认 UI 不闪烁/不重复 |
| 3.8 输出文本单独计费 | 低 | 输出单价 1/8.9，文本量小；如需控成本可设 `max_tokens` |
| 图片 ≤190 KB / ≤1080p 约束 | 低 | 抓屏 1920 长边压到 190 KB 需真机验证（§4.5） |
| 回滚 | — | **配置级回滚**：C1 两行改回 3.5 即可，代码改动对 3.5 无副作用——已用新 payload（含 nested `audio.output.voice` + `semantic_vad`）直打 3.5 实测握手/回复正常（§10）；`.delta` 双读保留 `.text` 兜底、C3 采样率同源对两代同样正确 |

## 9. 实施前准备（开工顺序）

1. **记录改前基线 —— 已完成（2026-09-27）**：`tests/unit/omni + tests/integration/omni` = **123 passed / 0 failed**
   （`/tmp/omni_before.xml`；本仓 pytest 摘要行被包装、不可信，计数用 junitxml）。
2. **留「修复前」听感对照 —— 已采集（2026-09-27）**：问题样本 `/tmp/before_35_omni.wav`（3.5 现状，9.72s/39 字
   = 4.0 字/秒）、语速基准 `/tmp/tts_ref_16k.wav`（三段式 TTS 同文本，6.85s = 5.7 字/秒）。
   修复后按 §7.3 与**基准**对齐（不与问题样本比）。两个 wav 在 `/tmp`，需长期保留请先挪到 `~/temp/`。
3. **omni.enabled 策略**：dev 配置保持 `false` 交付；真机验收时临时置 `true`（沿用现有联调做法），
   避免改动默认路径影响他人三段式联调。
4. **C3 影响面自查**：`endpoint`→`orchestrator` 只多传一个采样率参数；无编码器时（集成测试 fake）
   回退会话声明值；`omni_orchestrator` 其余逻辑零改动。
5. **（可选）探针脚本入库**：附录 A 的裸 WS 探针目前在 `/tmp`；若希望作为长期回归工具，评审后移入
   `services/scripts/`（不阻塞本次实施）。

## 10. 实施与验证记录（2026-09-27）

**C1–C7 全部落地**（C6 探针脚本入库为可选项，未做）：

| 项 | 结果 |
|---|---|
| 单测 / omni 集成 | `tests/unit` 全量 **1121 passed / 1 skipped**；`tests/unit/omni + tests/integration/omni` = **128 passed**（改前 123，新增 5：`.delta`+`stash` 转写、stash-only 首帧、`.text` 旧形态兼容、nested `audio.*` payload、采样率同源 ×2） |
| 其它集成 | `tests/integration`（除 agent_relay）**84 tests / 2 failed** —— 失败为 `bff/test_bff_smoke`「真实数据出现契约外 kind」，属本地 lvdata 数据漂移，与本次无关；`agent_relay`（164 failed）为 Redis 未启动（`redis://localhost:6379`），环境依赖 |
| lint | `ruff check` 改动文件：All checks passed |
| 真机（3.8 + workspace 域名 + 新 payload） | 握手 `session.created → session.updated` 无 error；**interim 转写恢复**：23 条递进 `input_transcription_delta` + 2 条 final（改前为 0 条） |
| 语速（§7.3 验收） | 同一 39 字文本：修复前 **9.72s（4.0 字/秒）** → 修复后 **7.20s（5.4 字/秒）**；三段式 TTS 基准 6.85s（5.7 字/秒）→ 已对齐 ✓ |
| 音高（F0 交叉验证） | 3.8 修复后中位数 **232 Hz**、provider 全链路（`test_omni_real.py` 产出 wav）**256 Hz**，与 3.5 真实 16 kHz 档（232 / 239 Hz）一致 ✓ |
| 回滚路径复验 | 用**新 payload**（nested audio 含 nested voice + `semantic_vad`）直打 **3.5**：握手无 error、回复正常，输出 F0 **239 Hz** = 16 kHz、语速 5.9 字/秒 → 回滚安全，且采样率修复对 3.5 同样生效 ✓ |

**待做（需 Android 真机）**：§7.4 真机 App 端到端（语音/抓屏/长会话）、§7.5 semantic_vad 抢答专项。
服务端侧已就绪：`configs/dev/server.yaml` 的 `omni.enabled` 交付时保持 `false`，真机验证时临时置 `true`。

## 后续 TODO

- **「点按即回一句」需给 `OmniStream` 加文本入口**（provider 侧发 `conversation.item.create(input_text)` +
  `response.create` 直起一轮）：现状协议无文本输入、omni 走 semantic_vad 只认语音。方向与约束（manual 模式
  已验证可行 / VAD 下显式 commit 会报错）见 `docs/fd_pipeline_omni_mode.md` §10 TODO-1。**未实施。**

## 附录 A：探针命令

```bash
# 端到端（生产 payload；workspace 专属域名 + 头）
cd services && set -a && . ./.env && set +a
./.venv/bin/python scripts/test_omni_real.py \
  --model qwen3.8-omni-flash-realtime \
  --base-url wss://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime \
  --workspace-id llm-1tmsqifo51cwfadr
# 观察：握手 session.created/updated、事件普查、Transcriptions: ['[FINAL] ...']
```

采样率判定（F0）与图片验证用的裸 WS 脚本本次为临时脚本（`/tmp/raw_probe.py`、`/tmp/img_probe.py`），
如需入库可作为 `services/scripts/` 下的 probe 增补（待评审）。

## 附录 B：关键证据摘录（2026-09-27）

```
# 3.8 握手回显（session.updated，flat + 兼容字段被原样接受）
{"model": "qwen3.8-omni-flash-realtime", "modalities": ["text","audio"], "voice": "Tina",
 "input_audio_format": "pcm", "output_audio_format": "pcm",
 "input_audio_transcription": {"model": "qwen3-asr-flash-realtime"},
 "turn_detection": {"type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300,
                    "silence_duration_ms": 800, "create_response": true, "interrupt_response": true},
 "sample_rate": 16000,           <-- 回显但实测不生效（§4.3）
 "enable_search": true, "search_options": {"enable_source": true}}

# nested 音频配置回显（3.8 实测接受）
"audio": {"input": {"format": {"type": "pcm", "sample_rate": 16000}},
          "output": {"format": {"type": "pcm", "sample_rate": 16000}}}

# 转写增量真实帧（3.5/3.8 同形）——provider 现在不认
{"type": "conversation.item.input_audio_transcription.delta",
 "text": "", "stash": "你好", "language": "zh", "emotion": "neutral"}
```
