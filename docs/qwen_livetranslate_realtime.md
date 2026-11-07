# Qwen3.8 LiveTranslate 实时同传 —— 问题分析与改造设计

> **状态**：**已实施（v2.2，2026-10-10）**——按 v2.1 方案全量落地（§6）+ 单测/真机验收通过，
> 记录见 §9。v1 的双读兼容设计作废（决策见 §5 D1/D5）。真机探针：2026-09-18（§4）+
> 2026-10-10 复跑（D4 兜底 + provider 路径验收）。
> **Date**: 2026-09-18（v1 探针同日实测）· 2026-10-10（v2：3.8-only 重写；v2.1：D2/D3/D4 定稿；
> v2.2：实施 + 验收）
> **Scope**: `services/omni/providers/alibaba_livetranslate.py`（核心）·
> `services/session/lecture_translate.py`（输出模态落点）· `configs/dev/server.yaml` ·
> `services/shared/config/models.py`（translate 配置项清理）· 单测（`test_alibaba_livetranslate.py`）·
> 真机脚本（`test_bailian_translate_real.py`）
> **上游文档**：[qwen3.8-livetranslate-flash-realtime 模型页](https://help.aliyun.com/zh/model-studio/qwen3-8-livetranslate-flash-realtime) ·
> [客户端事件参考](https://help.aliyun.com/zh/model-studio/live-translator-client-events)
> （2026-09-17 更新；控制台详情页需登录，本文以该公开页 + 真机探针为准）
> **关联**：`docs/fd_realtime_translation.md`（3.5 接入设计；实施时更新为 3.8 口径）·
> `docs/fd_pipeline_translate_lecture.md`（pipeline lecture 设计；text-only 定调 commit 30411b5）·
> `docs/translate_lecture_improvements.md`（2026-09-14 切换阻塞治理）
> **结论文先**：3.8 相对 3.5 有三处**破坏性改名**——`session.modalities` →
> `session.output_modalities`（旧字段被回显但**不生效**，§4.2 V1）；增量译文
> `response.audio_transcript.text` / `response.text.text` → `.delta`；增量原文
> `conversation.item.input_audio_transcription.text` → `.delta`。3.5 写法在 3.8 下实测后果：
> **① 流式译文/原文被全量丢弃（只在回合末一次性下发）；② text-only 模态请求被静默忽略、
> 回落到默认 `[text, audio]`，休眠的 ai_audio 路径被意外激活**。另有既有的「空闲 ~60s 服务端
> `session.finished` → 降级延迟」问题在 3.8 上复现（§3.6）。**本次决策（2026-10-10）：直接升级
> 3.8、3.5 不再支持 → 纯切换**——请求侧只发 `output_modalities`，接收侧只认 `.delta`（译文
> 增量直传；**input 侧累计回全量再上抛**，引擎 transcription 为覆盖语义，见 §6.2），删除
> `_confirmed` revision 机制与全部 3.5 事件分支；`input_audio_transcription` 及
> `enable_source_transcription` / `source_transcription_model` 配置链随 3.5 一并移除（3.8 的
> ASR 始终开启、不支持关闭、识别结果免费，源语种自动识别——官方客户端事件页原文，见 §5 D4）。
> 改动封闭在 provider + lecture 引擎内，对外 WS 协议 / 客户端 / orchestrator 不受影响。
> **决策已齐**（D1 纯切换 / D2=A text-only / D3=b / D4 移除 / D5 作废），实施按 §6、验收按 §7。

## 1. 背景与目标

- 3.8 模型（`qwen3.8-livetranslate-flash-realtime`）为 3.5 的后继：能力/价格/限流与 3.5
  完全一致（见 §2.2），但**协议参数与事件名有破坏性差异**（模型页明确标注）。
- 2026-09-18 真机验证（配置临时切 3.8 后直接启动 server，两段 lecture 会话，日志
  `~/temp/livachat.log`；**验证后配置已还原 3.5**）：连接与握手全部成功
  （`session.created → session.updated` 无 error，`voice: Tina` 被接受），但 lecture 同传的
  **流式文本完全失效、输出模态失控**。
- 同日完成**裸 WS 真机探针**（§4）：三个改名全部实测确认、字段名落定（v1 曾据此设计出
  「两字段同发」的版本无关写法，v2 决策 3.8-only 后作废，见 §5 D5）。
- 目标：
  1. lecture 在 3.8 上恢复流式译文 + 流式原文转写；
  2. 输出模态回到**显式可控**（已定 text-only，见 D2）；
  3. 空闲 `session.finished` 的降级时点明确化（见 D3）；
  4. 借 3.8-only 决策删除全部 3.5 兼容面——旧 `.text` 事件分支、`_confirmed` revision
     状态机、`input_audio_transcription` 请求字段与配套配置项。
- 非目标：不改对外 WS 协议、不改客户端、不动 dialogue translate（LLM 逐轮）与 omni 路径；
  不做双读兼容（3.5 弃用，决策 2026-10-10）。

## 2. 官方 API 差异（3.5 → 3.8）

### 2.1 差异表（模型页原文 + 代码现状对照；字段名已由 §4 探针证实）

> 原文：「本模型使用 `session.output_modalities` 配置输出模态。流式译文使用
> `response.text.delta` 或 `response.audio_transcript.delta` 事件返回。参数和事件与
> `qwen3.5-livetranslate-flash-realtime` 存在差异，详见客户端事件和服务端事件。」

| 项 | 3.5（现有代码实现） | 3.8（探针实测） | 代码位置 |
|---|---|---|---|
| 输出模态参数 | `session.modalities` | `session.output_modalities`（旧字段被回显但**不生效**，§4.2 V1） | `open()` session.update 构造 :539 |
| 增量译文（音+文） | `response.audio_transcript.text`（`text`+`stash`，累积语义；代码 `_confirmed` diff 转增量） | `response.audio_transcript.delta`（**`delta` 纯增量，无 stash**） | :191-205 |
| 增量译文（纯文本） | `response.text.text` | `response.text.delta`（`delta`） | :223-237 |
| 增量原文 | `conversation.item.input_audio_transcription.text`（`text`+`stash`+`emotion`） | `conversation.item.input_audio_transcription.delta`（`delta`+`language`+`obfuscation`） | :282-294 |
| 源文识别配置 | `input_audio_transcription{model,language}`（3.5 的启用开关；`model=null` 可关、源语种可钉定） | **不适用 3.8**：ASR 始终开启、不支持关闭、识别免费；源语种自动识别（官方客户端事件页；delta 事件带 `language`） | :552-556 |
| 完成事件 | `.audio_transcript.done` / `.text.done` / `.completed` | 同名，字段未变（`transcript` / `text`） | :207 / :239 / :296 |
| 音频输出 | `response.audio.delta`（`delta`） | 同名同形（实测正常解析） | :254 |
| 生命周期 | `speech_started/stopped`、`response.created/done`、`session.finished` | 全部同名（实测） | :275 / :307 / :336 |
| 空 voice 行为 | text-only 下 voice 无关紧要（脚本 `voice=""` 可用） | **默认 output_modalities=["text","audio"] 时强制要求非空 voice**，否则 1007 `Start requires non-empty payload.voice`（§4.4） | — |

### 2.2 能力、价格与限流（与 3.5 一致，均无变化）

| 项 | 3.8（华北2 北京） |
|---|---|
| 上下文 | 输入 49,152 / 输出 4,096 / 总 53,248 |
| 模态 | 输入 Audio+Image → 输出 Text+Audio |
| 语种 | 60 种源语言识别 / 29 种语音输出 |
| 价格 | 音频输入 ¥40/M · 图片输入 ¥3.3/M · 文本输出 ¥100/M · 音频输出 ¥160/M |
| 限流 | RPM 10 / TPM 100,000 |

## 3. 真机日志实证（2026-09-18，两段 lecture 会话）

会话窗口：session#1 `01:12:35–01:14:25`，session#2 `01:18:45–01:20:34`（UTC）。
两段均使用 3.8（`alibaba livetranslate open model=qwen3.8-livetranslate-flash-realtime`）。

### 3.1 握手正常（非问题）

- `session.created` → `session.update`（`modalities:["text"]`、`voice:Tina`、源 en→zh 等）→
  `session.updated`，全程无 `error` 帧；域名/鉴权/workspace 头/音色均被接受。
- 值得注意：不合法的**旧参数不会报错，只会被静默忽略**（§4.2 V1 证实），这是本次事故的放大器。

### 3.2 事件普查（全日志，两段会话合计）

| 模型 → 服务端事件 | 次数 | 现有代码处理 |
|---|---:|---|
| `response.audio_transcript.delta` | 82 | ❌ 只认 `.text`，全量丢弃 |
| `conversation.item.input_audio_transcription.delta` | 78 | ❌ 只认 `.text`，全量丢弃 |
| `response.audio_transcript.text` / `response.text.text`（代码等待的事件） | 0 | —（空转） |
| `response.audio.delta` | 202 | ✅ 全部转 ai_audio 下发 |
| `response.audio_transcript.done` / `input_audio_transcription.completed` | 4 / 4 | ✅（回合末整段） |
| `response.done` / `audio.done` / `content_part.done` / `output_item.done` | 各 4 | ✅ |
| `input_audio_buffer.speech_started` / `stopped` | 5 / 4 | ✅ |
| `session.created` / `updated` / `finished` | 2 / 2 / 2 | ✅ |

### 3.3 问题 1：流式译文被丢弃，只在回合末一次性下发（核心）

- session#2 第一轮：`01:18:45.843`（speech_started）→ `01:19:17.75`（`.done`），**31 秒内
  的译文 delta 全部被丢弃（本会话两轮合计 76 个），客户端 0 帧 ai_text**；`01:19:17.750/751`
  才一次性下发 2 帧（`.done` dump 210 字 + final）。
- 同一时刻 recording 落盘 `role=assistant len=210`（译文本）。
- 影响：字幕/译文不流式，同传的核心体验（同步性）丧失；客户端 THINKING 期间长时间空白。

### 3.4 问题 2：流式原文转写被丢弃，只在 `.completed` 收到整段

- 本会话 72 个 `conversation.item.input_audio_transcription.delta` 全部被丢弃；客户端仅在
  `01:19:17.746`（第一轮结束）收到 1 帧 final（601 字），第二轮直到会话终结都没有 final。
  客户端原文区同样不流式。

### 3.5 问题 3：text-only 模态失控，休眠的音频输出路径被意外激活

- 代码请求 `session.modalities=["text"]`（lecture 引擎 `modalities=("text",)`，设计即
  **text-only 输出**，见 `fd_pipeline_translate_lecture.md` 文首注：24k OpusEncoder、
  `AUDIO_DELTA→ai_audio`、SPEAKING 迁移均为「保留未启用」）。
- 3.8 忽略旧字段 → 默认 `[text, audio]` → 实测 **session#2 的 186 个 `response.audio.delta`
  被转成 56 帧 ai_audio 下发客户端**（全日志 202 个），并出现 `status speaking` 迁移
  （text-only 下不应出现）。
- 影响：客户端开始播放翻译语音。是否保留属于产品决策（见 D2）；但**参数被静默忽略**
  本身是不可接受的（无任何报错）。

### 3.6 问题 4：空闲 ~60s 服务端 `session.finished`，lecture 哑掉且降级延迟

| 会话 | 最后一次活动 | `session.finished` | 空闲 | 发现时点 |
|---|---|---|---|---|
| #1 | `01:13:28.7`（最后音频） | `01:14:25.16` | ≈57s | `01:16:39.99` 下一帧 feed 才失败 → 1008 降级（**延迟 2m14s**） |
| #2 | `01:19:35.6`（最后模型事件） | `01:20:34.65` | ≈59s | 会话结束前未再喂帧 |

- 该行为 3.5 时代已复现并修过（commit 94f5d86「流终结静默哑掉」哨兵处理），**非 3.8 新增**；
  但空闲阈值（3.8 是否为固定 60s、3.5 与之是否一致）未知，见 D3。
- 附带：session#2 第二轮因客户端停止音频输入，模型未等到 `speech_stopped`，整轮无
  `.done`/`response.done`，随后随空闲超时一并终结。

## 4. 真机探针结果（2026-09-18，裸 WS）

### 4.1 方法与工具

- **脚本**：`services/scripts/test_bailian_translate_real.py` 新增裸 WS 抓包模式（不经
  provider——被测对象正是 provider 的事件解析，必须绕开它）：
  `--raw-dump --session-field {modalities|output_modalities|both} --output-modalities {text|text,audio} --voice`，
  逐事件打印字段名 + 类型统计。
- **音频**：macOS `say` 生成 11.4s 英文语音（16 kHz PCM16 mono；中文系统语音为空占位，
  改用英文，与生产 en→zh 会话一致）。
- **环境**：workspace 专属域名 + `X-DashScope-WorkSpace` 头 + `voice=Tina`（生产同款）；
  每个场景一轮，realtime 节奏喂帧 + 2s 尾静音。

### 4.2 结果矩阵

| # | 模型 | 请求字段 | session.updated 回执 | 收到的增量事件 | 音频输出 |
|---|---|---|---|---|---|
| V1 | 3.8 | `modalities=["text"]` | `modalities=["text"]` **+ `output_modalities=["text","audio"]`（默认值）** | `response.audio_transcript.delta` ×17、`audio.delta` ×40 | ✅（text-only 被无视） |
| V2 | 3.8 | `output_modalities=["text"]` | `output_modalities=["text"]` | `response.text.delta` ×17、input `.delta` ×16 | ❌ |
| V3 | 3.8 | `output_modalities=["text","audio"]` | `output_modalities=["text","audio"]` | `response.audio_transcript.delta` ×16、`audio.delta` ×38 | ✅ |
| V4 | 3.8 | 两字段同发 `["text"]` | `modalities=["text"]` + `output_modalities=["text"]` | `response.text.delta` ×16 | ❌ |
| B1 | 3.5 | `modalities=["text"]` | `modalities=["text"]`（回执无 output_modalities 键） | `response.text.text` ×45（`text`+`stash`） | ❌ |
| B2 | 3.5 | 两字段同发 `["text"]` | `modalities=["text"]`（未知字段静默丢弃，无报错） | `response.text.text` ×45 | ❌ |

### 4.3 事件字段名（实测，取代文档推断）

**3.8**（`.delta` 系）：

| 事件 | 字段 |
|---|---|
| `response.audio_transcript.delta` / `response.text.delta` | `delta`（纯增量）、`item_id`、`output_index`、`content_index`、`response_id` |
| `conversation.item.input_audio_transcription.delta` | `delta`、`item_id`、`obfuscation`、`content_index`（`language` 2026-09-18 见、10-10 复跑未见——随服务端波动，provider 不依赖） |
| `response.audio_transcript.done` | `transcript`（整段） |
| `response.text.done` | `text`（整段） |
| `conversation.item.input_audio_transcription.completed` | `transcript`（2026-10-10 复跑另带 `item_id`；未见 `language`） |

**3.5**（现状代码对标，revision 语义）：`response.text.text` / `...input_audio_transcription.text`
均带 `text` + `stash`（input 侧另有 `emotion`）——与现有 `_confirmed` diff 实现吻合。

### 4.4 关键结论

1. **旧字段被回显但不生效**（V1 铁证：请求 `["text"]`，回执中 `output_modalities` 仍为默认
   `["text","audio"]`）——"静默忽略"实锤。
2. **新字段生效**（V2/V3），回执直接携带 `output_modalities`。
3. **两字段同值同发在 3.5 / 3.8 都成立**（V4/B2）：3.5 丢弃未知字段、无报错，3.8 以
   `output_modalities` 为准。（v2：3.5 不再支持 → 该「版本无关写法」作废，仅发
   `output_modalities`，见 §5 D5。）
4. 3.8 delta 为**纯增量**（无 `stash`）→ `_confirmed` diff 逻辑整体删除（v2：3.5 弃用后无保留必要，见 §5 D1）。
5. **空 voice + 3.8 会硬失败**：默认双模态下 voice 必填，空串触发
   1007 `Start requires non-empty payload.voice`（探针首跑即复现）。生产配置 `voice: Tina`
   无碍；真机脚本原 `voice=""` 已修（默认 Tina）。
6. **生产 provider 路径基线**（3.8、text-only 请求）：`src deltas 0/1`、`text deltas 1`、
   `audio bytes 541440`、`errors 0` —— 与 §3 日志症状一致，且**全程零报错**（静默退化），
   改造后的验收可用同口径对比（§7）。

## 5. 设计决策（v2，2026-10-10）

### D1 事件处理策略：纯切换（已定）——删除 3.5 路径，不做双读

- 决策：**直接升级 3.8、3.5 不再支持**。接收循环只保留 3.8 `.delta` 路径；删除 3.5 的
  `.text` + `_confirmed` diff 及其全部重置点。
- 探针依据（§4.2）：两版**增量事件名**完全不重叠（3.8 只发 `.delta`，3.5 只发 `.text`），纯切换无歧义。
- 回滚：revert 实施提交（不再有「改配置即回 3.5」路径——3.5 配置随本次弃用）。

### D2 输出模态：**已定 A. text-only**（2026-10-10）

- 决策：`output_modalities=["text"]`，恢复 commit 30411b5 定调——客户端只显示字幕；引擎音频
  路径（`AUDIO_DELTA→ai_audio`、24k Opus、SPEAKING 迁移）继续休眠不启用。3.8 下走
  `response.text.delta`，验收口径 `audio bytes=0`。
- 曾评估的 B（`["text","audio"]`，客户端播翻译语音）不在本次范围：休眠音频路径需单独真机验证、
  成本上升（输出音频 ≈ ¥7.2/小时·会话 + 输入音频 ≈ ¥1/小时，3.5 计费口径推算）；留作后续独立决策。
- 备注：3.8 双模态下 `voice` 必填（§4.4-5）——生产 `voice: Tina` 已满足，与 text-only 决策无冲突。

### D3 空闲 `session.finished` 的降级时点：**已定 b**（2026-10-10）

- 现状：事件泵退出后标记死流，**等到下一帧 feed_pcm 才抛错降级**（最坏延迟 = 用户下次说话的间隔；§3.6 实测 2m14s）。
- 决策（b）：事件泵退出（非 CancelledError）时立即执行与 feed 失败相同的降级动作——关闭
  lecture 引擎 + 下发 `TRANSLATE_UNAVAILABLE` 帧（现状 orchestrator.py:1275-1284 同款，
  fatal=false）→ 回落逐轮翻译；用户下一次说话时已就绪。`_stream_ended` 守卫保留为竞态兜底。
- c（查 3.8 空闲超时配置项并调大）降为后续可选优化，不在本次范围。
- **修订（2026-10-10 晚，mute 调查后；以修订为准）**：降级部分改为**仅报错不降级**——流终结/
  喂帧失败只下发 `TRANSLATE_UNAVAILABLE`（1008, fatal=false）一次，**不关引擎、不回落逐轮**；
  另新增**断流守护**（客户端断帧 ≥1.5s + THINKING → 向云端补 1s raw 全零 PCM，mute 收尾）。
  见 [`mute_mic.md`](mute_mic.md) §8。

### D4 源文识别配置：随 3.5 一并移除（已定；探针兜底）

- 官方客户端事件页原文：`input_audio_transcription` 标注「适用于 qwen3.5-livetranslate-flash-realtime
  等模型」；「**qwen3.8-livetranslate-flash-realtime 的 ASR 始终开启，不支持关闭，识别结果免费**」。
- 3.8 下该字段（含 `model` / `language` 子字段）不再承担「启用」或「源语种钉定」职责：识别
  无条件返回、源语种自动识别（delta 事件自带 `language` 字段，§4.3）。
- 结论：请求侧移除 `input_audio_transcription`；配置链 `enable_source_transcription` /
  `source_transcription_model`（models.py / server.yaml / provider ctor / lecture 透传）一并删除。
  `source_language` 保留（provider 日志用途，不再进请求）。
- 兜底验证（✅ 2026-10-10 实测通过）：3.8 **不带**该字段发起会话，仍收 input `.delta` ×17 +
  `.completed` ×2，零 `audio.delta`。
- 备选（不推荐）：字段原样保留发送（3.8 实测收下无报错），但会留下「`enable=false` 实际
  关不掉」的死开关。

### D5 两字段同发的「版本无关写法」—— **已作废**

- 3.5 不再支持后，请求侧直接只发 `output_modalities`，不引入任何双字段兼容（原 D4 结论退役）。

## 6. 改动清单（v2，3.8-only）

### 6.1 provider：session.update 构造（`alibaba_livetranslate.py:538-547`）

- `session_cfg["modalities"]` → `session_cfg["output_modalities"]`（仅此一个字段），取值
  `["text"]`（D2=A text-only）；
- 删除 `input_audio_transcription` 构造分支（:552-556）及 provider ctor 的
  `enable_source_transcription` / `source_transcription_model` 参数（:467-468 / :482-483）；
- 保留：`voice`（3.8 必填非空；生产 Tina）、`input_audio_format` / `output_audio_format` /
  `sample_rate`（3.8 实测接受，与默认值一致）、`translation`（含 corpus）、`enable_voice_clone`。

### 6.2 provider：接收循环（`:191-252`、`:282-294`）——纯 3.8 路径

- 译文（response 侧，保留 `response_id == _current_response_id` 守卫）：
  - `response.audio_transcript.delta` → `TEXT_DELTA`（读 `delta`，直传）；
  - `response.text.delta` → `TEXT_DELTA`（读 `delta`，直传）；
  - `.done`（事件名不变，字段 `transcript` / `text`）→ **兜底**：与本回合已直传文本长度比对，
    若 done 整段更长（增量缺失/截断）补发差额，否则不下发（避免重复上屏）；
- 原文（input 侧；`.delta` 事件无 `response_id` 字段，§4.3，不做守卫——同 3.5 现状）：
  - `conversation.item.input_audio_transcription.delta` → **累计后上抛全量**：
    `self._input_text += delta`，`INPUT_TRANSCRIPTION_DELTA`（text=累计全文）。**不可纯增量直传**——
    lecture 引擎对 transcription 是 **revision/覆盖语义**（`lecture_translate.py:203-209` 直传
    `ev.text`、不累计；3.5 下服务端本就发全量 `text+stash`，见 `fd_realtime_translation.md`
    源文映射注），直传会把客户端原文区打碎成"只剩最后一截"；
  - 累计器重置：`.completed`、`item_id` 变化（新句，§4.3 事件带 `item_id`）、`reset()`/重连
    （沿用既有重置位点）；
  - `.completed`（字段 `transcript`）→ `INPUT_TRANSCRIPTION_DONE`（final，整段，语义同 3.5）；
    **空 transcript 不下发**（3.8 对静默微回合会发空 completed，2026-10-10 实测——避免客户端
    清空原文区/建空转写组；累计器仍重置；审查修正）；
- 删除 3.5 分支：`response.audio_transcript.text`（:191-205）、`response.text.text`（:223-237）、
  `input_audio_transcription.text`（:282-294），及 `_confirmed` 状态机与全部重置点
  （:69/:140/:186/:431）；
- 新增「本回合已直传文本长度」计数（替代 `_confirmed` 的角色），在 `response.created` / 回合
  边界重置（沿用原重置位点）。

### 6.3 lecture 引擎（`lecture_translate.py`）

- `modalities=("text",)` 维持（D2=A）；休眠音频路径（`_event_pump` 的 AUDIO_DELTA/
  RESPONSE_DONE 分支、24k 编码、SPEAKING 迁移）保留不启用，无需补验证；
- 删除 `enable_source_transcription` / `source_transcription_model` 透传（:95-96）；
- D3-b：事件泵退出路径（:347-355）由「标记死流、等下一帧 feed」改为主动触发降级——与
  orchestrator 侧 feed 失败同动作（关引擎 + `TRANSLATE_UNAVAILABLE` 帧 + 回落逐轮）；
  实现注意泵内自调 `close()` 的自取消问题（经调度触发或 orchestrator 侧关闭）；
  降级后**状态归一 LISTENING**（审查修正）：流可能在响应中途死亡（无 RESPONSE_DONE），
  状态机停在 THINKING，不归一则回落逐轮的状态门会持续丢弃用户音频——两条降级路径同改，
  口径同 `session.change` 切出 lecture（orchestrator.py:752-754）。

### 6.4 测试（`test_alibaba_livetranslate.py`，现 843 行）

- 新增 3.8 用例：`response.audio_transcript.delta` / `response.text.delta` 直传（纯增量、不经
  diff）；input `.delta` **累计全量上抛**（多 delta 累计、`item_id` 变化重置、`.completed` 清空）；
  `.done` 兜底（无缺失不重复下发 / 有缺失补差额）；session.update 断言（:417-505）改为含
  `output_modalities`、不含 `modalities`、不含 `input_audio_transcription`；
- 删除 3.5 用例：revision 语义组（:113-177：first_delta / incremental_confirmed /
  no_growth_skipped / rollback_protected）、`response.text.text`（:212）、transcription
  `text+stash`（:73/:86）、`test_source_transcription_disabled_omits_field`（:479）、
  `_confirmed` 重置（:388；改为新计数重置或删除）；
- 保留：`.done` / `.completed` / 生命周期 / reconnect / workspace 头 / 音频解码等（字段未变）。
- `test_lecture_translate.py` / `test_lecture_switch.py`：mock provider，随构造参数删除同步小改；
  新增事件泵退出即主动降级的用例（D3-b）。

### 6.5 配置与脚本

- `configs/dev/server.yaml:216,218`：两处 `qwen3.5-livetranslate-flash-realtime` →
  `qwen3.8-livetranslate-flash-realtime`（base_url 查询参数 + model）；删除 :221-222
  （`enable_source_transcription` / `source_transcription_model`）；`voice: Tina` 保留；
- `services/shared/config/models.py:239-240`：删除同名两个配置项（`source_language` /
  `target_language` 保留）；
- `services/scripts/test_bailian_translate_real.py`：provider 路径构造删 `enable_source_transcription`
  参数（:151；脚本未传 `source_transcription_model`）；新增「不带 `input_audio_transcription`」
  探针场景（D4 兜底）；`--session-field both` 分支可删（工具收敛）；
- staging / production 无 translate 段（已核实），无改动；
- 文档：`fd_realtime_translation.md` 更新为 3.8 口径（或标注 3.5 历史）；
  `fd_pipeline_translate_lecture.md` 更新模型名 / 事件名 / ASR 配置描述。

## 7. 验收标准（v2）

1. **单测**：全绿；3.8 `.delta` 用例（译文直传 + input 累计全量）+ `.done` 兜底用例；
   3.5 revision 用例已全部移除。
2. **真机探针**（同口径对比 §4.4-6 基线）：
   - `--raw-dump`（只发 `output_modalities=["text"]`、不带 `input_audio_transcription`）：收
     `response.text.delta`、**零 `audio.delta`**；仍收 `input_audio_transcription.delta`（D4 实证）；
   - 生产 provider 路径：`text deltas > 1`（流式）、`src deltas > 1`（interim，全量递增）、
     `audio bytes = 0`。
3. **端到端 lecture**：客户端字幕/译文流式出现（不再回合末一次性）；原文区逐步累计显示
   （interim 全量覆盖、不碎片）；无 ai_audio / SPEAKING 迁移（text-only）；空闲
   `session.finished` 后立即降级、用户下次说话即已就绪（D3-b）。
4. **回归**：dialogue translate / chat / omni 不受影响（本次改动不触及这些路径）。

## 8. 风险与回滚（v2）

| 风险 | 状态/说明 | 缓解 |
|---|---|---|
| 字段名不确定性 | ✅ 已消除（§4.3 实测） | — |
| 静默失配复发 | 未知事件只打 debug 日志，不报错 | 接收循环对"已知前缀但未处理"的事件（如 `response.*`）加 warn 级日志（可选加固） |
| `input_audio_transcription` 移除后 ASR 是否仍返回 | 官方明文「始终开启」，探针尚无「不带该字段」对照 | 实施前补探针场景（D4）；异常则回退为保留字段发送（实测无报错） |
| 3.8 ASR 不可关闭 | 若后续要「不显示原文」，API 层关不掉 | 消费端过滤（本次不做；现生产 lecture 需要原文） |
| 空 voice 硬失败 | 3.8 默认双模态下实测必填（§4.4-5）；text-only 下是否同样要求未单独验证 | 生产恒发 `voice: Tina`，不受影响；provider 可加空 voice 兜底（可选） |
| 多 item 交错（3.8 speaker_detection 默认开启） | 输入累计器按单 item 维护；交错出现可能串扰 | 单讲解场景预期单 item；实测观察，必要时按 `item_id` 分桶或配置 `audio.input.turn_detection`（3.8 新字段） |
| 空闲超时参数未知 | 3.5/3.8 阈值未对比（3.8 实测 ~60s） | D3-b 主动降级使影响有界；保留日志观测（c 项后续可选） |
| D3-b 自取消竞态 | 事件泵内触发降级可能涉及关闭自身任务 | 实现经调度（`call_soon`/`create_task`）触发，或由 orchestrator 侧执行 close |
| 回滚 | 3.5 配置随本次弃用，无「改配置回 3.5」路径 | revert 实施提交（代码层面一次性） |

## 9. 实施与验收记录（2026-10-10）

**实施**（按 §6 全量落地）：
- provider：session.update 发 `output_modalities=["text"]`；删 `input_audio_transcription` 构造与
  ctor 两参数；接收循环 `.delta` 直传（译文）+ 累计全量上抛（源文，item_id/completed/reset 重置）
  + `.done` 兜底补差；`_confirmed` 机器删除。
- lecture 引擎：删两参数透传；新增 `on_stream_ended` 回调——泵退出主动降级（D3-b）；
  orchestrator 经调度任务执行（关引擎 + `TRANSLATE_UNAVAILABLE` 帧 + 回落逐轮，防双降级）。
- 配置：`configs/dev/server.yaml` 两处切 3.8、删两键；`models.py::TranslateConfig` 删对应字段。
- 脚本：探针增 `--no-input-transcription`；`--session-field` 收敛为 output_modalities/modalities。
- 测试：provider 3.8 用例重写（累计/兜底/守卫）+ D3-b 引擎与 orchestrator 用例 +
  `test_lecture_switch.py` 假流改挂起（空流在 D3-b 下即降级，与切换延迟断言打架）。

**测试结果**：单测 1535 passed（1 skipped）+ integration 138 passed（1 skipped）；ruff clean。

**审查修正（实施后 review，2026-10-10）**：
- **降级路径补状态归一 LISTENING**（D3-b 回调 + feed 失败 catch 两处）：流可能在响应中途
  死亡（无 `RESPONSE_DONE`，§3.6 session#2 第二轮即此场景），状态机停在 THINKING；不归一时
  回落逐轮后状态门会持续丢弃用户音频，"下次说话即就绪"不成立。切出 lecture 路径本有此惯例
  （orchestrator.py:752-754），降级路径补齐。
- **空 transcript 的 `.completed` 不再下发空 final**（3.8 对静默微回合会发；实测两轮
  provider probe 各出现一次），避免客户端清空原文区/建空转写组；累计器仍重置。
- 复核通过项：`.done` 兜底前缀假设（真实输出里增量拼接与 done 整段一致、无误触发）、
  源文累计（17 条 interim 逐级增至全文）、`enqueue_outbound` 收尾竞态（无界队列 put 不抛）、
  闲时接收循环崩溃不发 SENTINEL 系有意设计（留给 feed 路径自动重连）。

**真机验收**：
- D4 兜底（raw-dump，不带 ASR 字段）：input `.delta` ×17、`.completed` ×2、零 audio delta ✅
- provider 路径（同口径对比 §4.4-6 基线）：`src deltas 14/2`（基线 0/1）、`text deltas 20`
  （基线 1）、`audio bytes 0`（基线 541440）、`errors 0` ✅

**待人工验收**：设备端 lecture 端到端——客户端字幕/原文区流式显示（原文非碎片）、text-only
无 ai_audio/SPEAKING、空闲 ~60s `session.finished` 后立即降级（下次说话即已就绪，D3-b）。

> **修订（2026-10-10 晚，以修订为准）**：D3-b 降级移除——流失败改为**仅报错不降级**
> （`TRANSLATE_UNAVAILABLE` 一次，不关引擎、不回落逐轮，见 §5 D3 修订注）；新增**断流守护**
> （mute/断帧 ≥1.5s + THINKING → 补 1s raw 全零 PCM，收尾闭环）。本节 D3-b 相关记述不再生效；
> 完整实验与决策见 [`mute_mic.md`](mute_mic.md) §8。

## 附录 A：日志证据摘录（生产 server 日志，2026-09-18）

```
# 3.8 握手（session#2）
01:18:45.645  alibaba livetranslate open model=qwen3.8-livetranslate-flash-realtime workspace=llm-1tmsqifo51cwfadr
01:18:45.649  send session.update  modalities=["text"] voice=Tina  (source=en, target=zh)
01:18:45.691  recv session.updated            # 无 error —— 旧参数被静默接受

# 流式译文被丢弃：31s 窗口内译文 delta 全部忽略
01:18:46.709  recv response.audio_transcript.delta   ← 首个译文增量（被忽略）
01:19:17.749  recv response.audio_transcript.done    ← 直到此刻才 dump
01:19:17.750  send ai_text（210 字，一次性）
01:19:17.751  send ai_text（final）

# 流式原文被丢弃
01:19:17.745  recv conversation.item.input_audio_transcription.completed（601 字）
01:19:17.746  send transcription（唯一下发，此前 72 个 .delta 全部忽略）

# text-only 失控：音频输出路径被激活
01:18:46.878  recv response.audio.delta ×186（本会话；全日志 202）
01:18:46.883  send status speaking   ← text-only 设计下不应出现的迁移
01:18:46.884  send ai_audio ×56

# 空闲终结与降级延迟
01:14:25.162  recv session.finished        （session#1，空闲 ≈57s）
01:14:25.163  lecture translate stream ended; next feed will trigger fallback
01:16:39.993  lecture feed_pcm failed, closing engine   （延迟 2m14s 才降级）
```

## 附录 B：关键代码位置与探针用法

> 下表为 v2.1 实施前的代码位置快照（行号已随实施变动，现状以代码为准）。

| 位置 | 说明 |
|---|---|
| `services/omni/providers/alibaba_livetranslate.py:539` | `session_cfg["modalities"]`（→ v2：改 `output_modalities` 单字段） |
| 同上 `:191-252` | 译文 `.text` / `.done` 处理与 `_confirmed` diff（→ v2：删 `.text`，`.delta` 直传 + `.done` 兜底） |
| 同上 `:282-294` | 原文 `.text` 分支（→ v2：删，改 `.delta`） |
| 同上 `:69,:140,:186,:431` | `_confirmed` 声明 / 重置点（→ v2：删） |
| `services/session/lecture_translate.py:105` | `modalities=("text",)`（text-only 落点，按 D2 定值） |
| `services/tests/unit/omni/providers/test_alibaba_livetranslate.py:113-242` | revision 语义用例（→ v2：删除；新增 3.8 `.delta` 用例） |
| `services/scripts/test_bailian_translate_real.py` | 探针（`--raw-dump --session-field output_modalities --output-modalities text`） |
| `configs/dev/server.yaml:216,218` | `base_url` 查询参数 + `model`（当前 3.5；迁移时同改为 3.8） |

```bash
# 探针复跑（参考 §4；key 在仓库根 .env，从 services/ 目录运行）
cd services && set -a && source ../.env && set +a
.venv/bin/python scripts/test_bailian_translate_real.py <audio.wav> \
  --raw-dump --session-field output_modalities --output-modalities text \
  --base-url "wss://${DASHSCOPE_API_HOST}/api-ws/v1/realtime" \
  --workspace-id "${DASHSCOPE_WORKSPACE_ID}" --source-language en --target-language zh --realtime
```
