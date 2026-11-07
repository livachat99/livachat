# Bailian ASR 新模型接入 —— qwen-audio-3.0-asr-flash-streaming 调研与设计

> 状态：**已实施**（2026-09-13，代码 + 单测 979 用例绿；真机验证完成 §6.3
> #1/#2/#3a/#3b + 回滚/保活/空闲连接附加实测，见 §6.3 实测结果）。
> 追加（2026-09-13 晚）：**收尾时延实测与 `max_sentence_silence=600` 调优**，见 §6.4；
> 回归 1200 用例绿。
> 待办：§6.3 #4/#5（需 app+server 联调）、部署侧配置同步（§4.5，现含
> `max_sentence_silence: 600`）。
> 修订：2026-09-13 并入评审意见 A1/A2/A3/C2——heartbeat 两形态切分（§4.6-1）、
> 部署侧配置同步（§4.5）、改动顺序硬约束（§8）、open 日志含 model（§4.3-4）。
> 范围：`services/asr/providers/alibaba_bailian.py` + `services/asr/factory.py` +
> `services/shared/config/models.py` + `configs/dev/server.yaml` + 单测 + 真机脚本。
> 不动 orchestrator / 协议层 / 客户端（§6.4 例外：orchestrator 加一条 final 到达日志）。
> 结论先行：**新模型与现役 `paraformer-realtime-v2` 走同一套 DashScope duplex 协议，
> 现实现对协议已经就位；本设计只做三个小改动（workspace 头、心跳包跳过、config schema），
> 主体的收益来自配置切换与 `heartbeat` / `language_hints` 参数。**
> 后续（2026-10-10）：**已切换 `qwen-audio-3.1-asr-flash-streaming`**（仅 model 串，
> far+mss600 保守档），调研/实测/变更见 `docs/asr_qwen31_migration.md`；本文档保留为
> 3.0 迁移历史。

## 1. 背景与目标

- 百炼新一代实时识别模型 `qwen-audio-3.0-asr-flash-streaming`（官方文档与
  Fun-ASR-Realtime 同页合述）是当前"实时"场景的推荐模型：热词、Prompt 上下文、
  多语种及方言、音频时长无限制（选型页原文："推荐使用 `qwen-audio-3.0-asr-flash-streaming`"）。
- 现役 `configs/dev/server.yaml:59-64` 用的是 `paraformer-realtime-v2`，
  连 `wss://dashscope.aliyuncs.com/api-ws/v1/inference`。
- 新模型按官方要求（推荐）走**业务空间专属域名** `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`，
  与 TTS 的 `qwen-audio-3.0-tts-flash` 是同一编排方式——TTS 侧已有成熟先例
  （`configs/dev/server.yaml:127-135` + `services/tts/providers/alibaba_bailian.py:394-407`），
  本设计直接镜像它。

目标：

1. 服务端可切换到新模型（配置驱动，不新增 provider 类）；
2. ASR 支持 `workspace_id`（请求头），对齐 TTS 的既有做法；
3. 补齐新模型引入的两处协议差异（`heartbeat` 心跳包、可选保活参数）；
4. 降低长静默判死风险：新模型 `heartbeat` 参数覆盖"连接内持续发静音"形态；
   "帧流中断"形态（已发生 1001 故障的形态）维持既有内联重建兜底（见 §4.6-1）。

## 2. 调研（联网核对，2026-09-13）

来源见 §9。以下均为官方文档原文结论，非推测。

### 2.1 接口地址与请求头

- WebSocket URL（华北2 北京）：
  `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`
  （新加坡为 `...ap-southeast-1.maas.aliyuncs.com`；文档同时注明
  "现有域名仍可正常使用"，即 `dashscope.aliyuncs.com` 未被下线）。
- 请求头：

  | 参数 | 必选 | 说明 |
  |---|---|---|
  | `Authorization` | 是 | `Bearer <api_key>`，握手阶段校验，失败 401/403 |
  | `X-DashScope-WorkSpace` | 否 | 百炼业务空间 ID |
  | `user-agent` | 否 | 客户端标识 |
  | `X-DashScope-DataInspection` | 否 | 数据合规检测，默认不传 |

  与 TTS 的 yaml 注释（"业务空间模型必须连 workspace 专属域名；缺头会 401/411"）
  一致：域名与头是配套设施。

### 2.2 协议交互流程（与现役实现完全同构）

1. 建连 → 2. `run-task` → 收 `task-started` → 3. 发二进制音频（单声道）并持续收
   `result-generated` → 4. `finish-task` → 继续收 `result-generated` →
   收 `task-finished` → 5. 关连接。

即：**与现役 `_BailianStream` 的事件模型（task-started / result-generated /
task-finished / task-failed + 二进制帧）逐一对应**，`services/asr/providers/alibaba_bailian.py:31-100`
的接收循环不需要重构。

### 2.3 run-task 参数（payload.parameters）

| 参数 | 必选 | 说明 |
|---|---|---|
| `format` | 是 | `pcm`/`wav`/`mp3`/`opus`/`speex`/`aac`/`amr`（opus/speex 须 Ogg 封装） |
| `sample_rate` | 是 | Hz；8k 模型仅 8000，**其他模型任意采样率**（16k 合法） |
| `language_hints` | 否 | 语种数组；本模型**最多 4 个**，超出取前 4；不设=自动识别。码表：zh/en/ja/ko/vi/th/id/ms/tl/hi/ar/fr/de/es/pt/ru/it/nl/... |
| `vocabulary` | 否 | **即时热词**（`{"词": 权重}`，1-5 或 50），**仅本模型支持**；与 `vocabulary_id` 合并，上限 2000 |
| `vocabulary_id` | 否 | 预编译热词表 ID |
| `semantic_punctuation_enabled` | 否 | 默认 false（VAD 断句，低延迟，适合交互）；true=语义断句（会议场景） |
| `max_sentence_silence` | 否 | VAD 断句静音阈值 ms，默认 1300，范围 [200, 6000]（官方文档口径；**本模型实测默认 800**，见 §6.4） |
| `multi_threshold_mode_enabled` | 否 | 默认 false，防 VAD 段过长 |
| `heartbeat` | 否 | 默认 false；**true 时"持续发送静音音频也能保持连接不断"**，false 时"即使持续发送静音音频，连接也将在一定时间后因超时而断开" |
| `speech_noise_threshold` | 否 | [-1.0, 1.0]，VAD 灵敏度高级参数 |
| `special_word_filter` | 否 | 敏感词过滤 |

### 2.4 上下文增强（input.context，run-task 与 continue-task 均可携带）

`payload.input.context` 为消息数组：`role` ∈ {`user`(type=`input_text`),
`assistant`(type=`text`)}，须按轮次排列且 user 在 assistant 前；
各类型最多 5 条、每轮总长 ≤400 字符（超出截尾）。
用法示例即官方 run-task 报文（`model: qwen-audio-3.0-asr-flash-streaming`）。

### 2.5 服务端事件 result-generated

```json
{
  "header": {"task_id": "...", "event": "result-generated", "attributes": {}},
  "payload": {
    "output": {"sentence": {
      "begin_time": 170, "end_time": 920,
      "text": "好，我知道了",
      "heartbeat": false,
      "sentence_begin": false, "sentence_end": true,
      "sentence_id": 1,
      "words": [{"begin_time": 170, "end_time": 295, "text": "好", "punctuation": "，"}]
    }},
    "usage": {"duration": 12}
  }
}
```

要点：

- **最终/中间结果的判据是 `sentence_end`**（true=最终），`end_time` 在最终结果里非 null；
  句首包为 `begin_time:0, end_time:null, text:"", sentence_begin:true, sentence_end:false`。
- **心跳包：`heartbeat: true`，`sentence_id: 0`，文档明确"可跳过该结果"**——这是新模型
  引入的、现实现未处理的字段。
- `usage.duration` 仅在 `sentence_end=true` 时给出（计费时长）。

**实测确认（2026-09-13，探针抓原始帧）**：句首包 `sentence_begin=true, text="",
end_time=null`；中间结果包恒 `end_time=null`、`sentence_end` 为 false（**`is_final`
判据成立，无中间包误判为 final**）；最终包 `sentence_end=true, end_time=8680,
usage={"duration": 9}`；静音期间实际收到心跳包（`heartbeat=true, sentence_id=0`，
约 50s 间隔）——§4.3-3 的跳过是必要且生效的。服务端 VAD 在语音结束后约 2s 给出
final（`max_sentence_silence` 默认 1300ms 的体现）。

## 3. 现状核对（codebase，逐条可溯源）

### 3.1 已经就位的（不用改）

| 事项 | 证据 |
|---|---|
| 协议同构：run-task / 二进制 feed / result-generated / task-failed / finish-task 全链路已实现 | `services/asr/providers/alibaba_bailian.py:19-165`（`_BailianStream`）、`:166-225`（provider） |
| `result-generated` 解析已优先读 `output.sentence`（`transcription` 为兼容回退），与新模型结构匹配 | `alibaba_bailian.py:75-93` |
| `is_final` 判据 `end_time is not None or sentence_end` 对新模型两种包（句首/最终）判定正确 | `alibaba_bailian.py:80` |
| 段间不需要重建连接：orchestrator 每轮 `reset()` 只清本地队列并复用同一 WS | `alibaba_bailian.py:141-145`、`services/session/orchestrator.py:797-814` |
| 配置参数可透传任意 run-task 参数（`params` 合并） | `alibaba_bailian.py:214-215` |
| 错误自愈：task-failed 不产出文本、关 ws 让 feed 抛 ProviderError，orchestrator 有界重建 | `alibaba_bailian.py:56-72`、`orchestrator.py:1734-1832` |
| `sample_rate` 按会话配置动态传入（16k 合法，新模型任意采样率） | `orchestrator.py:422`、`:1761` |

### 3.2 缺口

1. **workspace 支持缺失**：
   - `AsrProviderConfig` 无 `workspace_id` 字段（`services/shared/config/models.py:68-73`），
     TTS 侧同位置有（`models.py:108-116`，`workspace_id: str | None = None`）。
   - provider `open()` 只带 `Authorization` 头（`alibaba_bailian.py:182`），无
     `X-DashScope-WorkSpace`；TTS 侧有 `_headers()` 先例
     （`services/tts/providers/alibaba_bailian.py:402-407`）与 factory 传参先例
     （`services/tts/factory.py` `workspace_id=cfg.get("workspace_id")`）。
2. **心跳包未处理**：`heartbeat: true` 的 result-generated 会走通用分支。
   现有判据下（`end_time` null、无 `sentence_end`）它只会产出空文本 interim——
   `orchestrator._poll_asr_results` 对空文本不动作（`orchestrator.py:1700`），
   实际无害；但语义上应显式跳过（`sentence_id: 0` 是保活包，不是识别结果）。
3. **`asr.language` 是死配置**：`configs/dev/server.yaml:46` 配了 `language: zh-CN`，
   全仓无读取方；provider 的 `language` 形参会拼 `language_hints: [language]`
   （`alibaba_bailian.py:210-211`），但 orchestrator 调 `open()` 从不传
   （`orchestrator.py:422/1761`），`AsrService.open` 的 `language` 形参同样无人传
   （`services/asr/service.py:59-65`）。新模型 `language_hints` 上限 4 个，
   本产品是中英双语场景，值得借机接线。
4. **yaml `params.sample_rate` 会覆盖传入值**（`configs/dev/server.yaml:64` +
   `alibaba_bailian.py:214-215` 的 update 顺序）。当前 ctx 采样率恒 16000 所以无感，
   但这是隐性双事实源，切换模型时顺手清掉。

### 3.3 现有测试与脚本

- 单测：`services/tests/unit/asr/providers/test_bailian.py`（4 用例，本地
  `websockets.serve` mock 全协议时序；含 task-failed 自愈锁）。
- 真机脚本：`services/scripts/test_bailian_asr_real.py`（已有 `--model` /
  `--base-url` / `--language` 参数，加 workspace 支持即可直接验新模型）；
  `services/scripts/compare_asr_realtime.py`（paraformer vs qwen-asr 的 A/B 对比，
  可用于新老模型对照）。
- TTS 侧可照抄的锁：`services/tests/unit/tts/providers/test_bailian.py:632-645`
  （`test_workspace_header`：断言 header 含 `X-DashScope-WorkSpace`，
  且 base_url 不派生不改写）。

## 4. 设计

### 4.1 方案总览

**不新增 provider 类**：新模型与 `alibaba_bailian` provider 的协议完全同构，
复用同一类（`active: alibaba_bailian`，改 model/base_url/workspace 即切换）。
新增 provider 类只会复制一份协议实现，且会让 `alibaba_qwen_asr`
（OpenAI-realtime 风格协议，`services/asr/providers/alibaba_qwen_asr.py`）的路由语义混乱——
新模型**不是**那条协议，不要误接到 `alibaba_qwen_asr`。

改动 = 3 处生产代码 + 1 处 yaml + 测试/脚本。

### 4.2 config schema（镜像 TTS）

`services/shared/config/models.py` `AsrProviderConfig` 追加：

```python
class AsrProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    params: dict[str, Any] | None = None
    workspace_id: str | None = None      # 新增：仅驱动 X-DashScope-WorkSpace 头
```

> 实施顺序硬约束：pydantic v2 默认 `extra="ignore"`（`models.py:255-260` 的
> `SettingsConfigDict` 未设 extra），schema 未落地前 yaml 里的 `workspace_id`
> 会被静默丢弃——正确顺序与后果见 §8。

### 4.3 provider（`services/asr/providers/alibaba_bailian.py`）

1. `__init__` 增 `workspace_id: str | None = None`，存字段；
2. 抽出 `_headers()`（与 TTS 同名同语义）：

   ```python
   def _headers(self) -> dict[str, str]:
       """workspace_id 存在时附加 X-DashScope-WorkSpace 头（业务空间模型缺头 401/411）。"""
       headers = {"Authorization": f"Bearer {self._api_key}"}
       if self._workspace_id:
           headers["X-DashScope-WorkSpace"] = self._workspace_id
       return headers
   ```

   `open()` 的 `additional_headers` 改用它（现 `alibaba_bailian.py:182`）。
   语义与 TTS 一致：**base_url 是实际连接端点（含 workspace 域名），
   workspace_id 只驱动头，两值配套、互不派生**。

3. `_receive_loop` 的 `result-generated` 分支最前面加保活包跳过：

   ```python
   sentence = output.get("sentence", output.get("transcription", {}))
   if sentence.get("heartbeat"):
       continue          # 保活包（sentence_id=0），不是识别结果
   ```

   放在取 `text`/`is_final` 之前：保证既不产出结果、也不触碰 `_final_text`。

4. `open()` 建连成功后补一条含 model 的日志：现 `asr provider opened`
   （`services/asr/service.py:74`）只带 provider 名，换模型后新旧不可区分。
   注意本仓已知坑：ASR provider 走 stdlib logging，服务端 structlog 渲染下
   `extra=` 字段会静默丢失（1001 排查时证实）——**关键值写进消息体**，如
   `logger.info("bailian asr open model=%s workspace=%s", self._model, self._workspace_id or "-")`。

### 4.4 factory（`services/asr/factory.py`）

`alibaba_bailian` 分支增参（对齐 TTS factory 的写法）：

```python
return AlibabaBailianProvider(
    base_url=cfg["base_url"],
    api_key=cfg["api_key"],
    model=cfg["model"],
    params=cfg.get("params"),
    workspace_id=cfg.get("workspace_id"),
)
```

`create_asr()` 其余逻辑（active/fallback 装配）不变。

### 4.5 yaml（`configs/dev/server.yaml`）

`asr.providers.alibaba_bailian` 改为（注释对齐 TTS 条目 `:127-135`）：

```yaml
    alibaba_bailian:
      enabled: true
      # base_url 为实际连接端点（业务空间模型必须连 workspace 专属域名）；
      # workspace_id 只驱动 X-DashScope-WorkSpace 请求头——两值配套，缺头会 401/411
      base_url: wss://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference
      api_key: "${DASHSCOPE_API_KEY}"
      model: qwen-audio-3.0-asr-flash-streaming
      workspace_id: llm-1tmsqifo51cwfadr
      params:
        heartbeat: true                  # 长静默保活（见 4.6-1）
        language_hints: ["zh", "en"]     # ≤4 个；不设=自动识别（见 4.6-2）
```

注意：`params` 会整表 merge 进 run-task parameters，**删掉原来的
`sample_rate: 16000`**（采样率以会话配置为单一事实源，见 §3.2-4）。
`workspace_id` 取值与 TTS 同一业务空间（同一账号下 TTS/ASR 共用 workspace；
若实际开通在不同空间，改这一行为准）。

`asr.active` 与 `fallback` 不变（`alibaba_bailian` / `[]`）；
`alibaba_qwen_asr` 条目保留现状（enabled 但非 active，不冲突）。

**部署侧同步（A3）**：仓库内仅 `configs/dev/server.yaml` 含 asr 段
（`configs/production/`、`configs/staging/` 均为占位 .gitkeep）。实施时须先确认
线上实际加载的配置来源，并把同一组键（base_url / model / workspace_id / params）
同步过去；若线上直接复用 dev 配置，则改此文件即生效。
回滚 = 改回 `model: paraformer-realtime-v2` **一行即可**，无需回滚代码（provider 类未变）。
实测（2026-09-13）：workspace 域名±头、以及本次新增的 params（`heartbeat`/`language_hints`）
对 paraformer 同样兼容，且 `heartbeat: true` 对其同样生效（保活不丢失）——
均无需一并回退，见 §6.3 回滚兼容性与 paraformer 保活对照实测。
注意**坏配置不会在启动时暴露**：ASR 是会话期懒连接（首个 `session.start` 才
`open()`，`orchestrator.py:420-428`），配置错误只在首个会话以 1001 体现——上线前先跑 §6.2 脚本。

### 4.6 可选增强（v1 建议做前两项，其余按需）

1. **`heartbeat: true`（建议启用；覆盖"持续静音"形态，不是已发生故障的复现）**：
   先分清两类长静默——它们是不同的风险路径：
   - **形态①「连接内持续发静音」**：客户端 SDK 不按 VAD 门控、推流期间持续
     50Hz 发帧（`apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatSDK.kt:156-164`），
     LISTENING 下 orchestrator 逐帧转发给 ASR（含静音帧，`orchestrator.py:782-795`）。
     这正是 `heartbeat: true` 的文档语义（"持续发送静音音频"不再因超时断开）覆盖的场景。
     **实测（2026-09-13）：无 heartbeat 时 300s（5min）判死
     （`Response stream timeout, timeout_seconds=300`），heartbeat=true 时 32min 存活
     ——heartbeat 是决定性保活手段**（§6.3-3a 含对照组）。
   - **形态②「帧流中断」**：麦克风不流/连接空转——**实测（2026-09-13）：停帧
     23s 即被上游判死**（`task-failed: request timeout after 23 seconds`，与 TTS 侧
     23s 空转阈值同源），且 `heartbeat: true` **不覆盖**该形态（实测带 heartbeat
     仍被 reap）。该形态由既有内联重建兜底（`orchestrator.py:1734-1832`，已实施），
     且属生产高频路径：THINKING/SPEAKING 门控期间不喂帧（`orchestrator.py:779-780`），
     超 23s 即触发（如长 TTS 播报），恢复靠下一帧 feed 的自愈重建。
     （复核 1001 排查记录：两种形态的实测判死阈值分别是 **23s**（停帧）与 **300s**
     （持续静音，§6.3-3a 对照组），都远小于"29 分钟"——29min 只能是 `recv audio`
     观测到的 gap 长度/恢复时刻，判死在 gap 早期已发生，1001 在恢复推流首帧才暴露
     （检测懒惰）。历史故障用的是 paraformer，其静音阈值是否同为 300s 见 §6.3。）
   配套的保活包跳过在 §4.3-3；THINKING/SPEAKING 门控期间不喂帧的秒级窗口
   （`orchestrator.py:779-780`）两种参数都不覆盖，窗口远小于 29min，可接受。
2. **`language_hints` 接线**：直接用 yaml `params` 传（零代码）。同时处置死配置
   `asr.language: zh-CN`——删除，或（更好）让 orchestrator 调 `open()` 时传
   `language`，由 provider 拼 `language_hints`。v1 取最简：yaml params + 删死配置；
   后者留作后续（涉及 orchestrator，扩了本设计边界）。
3. `vocabulary` 即时热词：yaml `params` 可直传，仅本模型支持；等有词表需求再配。
4. 上下文增强（`input.context`）：需要 orchestrator 把最近对话历史透传给
   provider（现 `open()`/`reset()` 无该通道），**属于新功能而非接入**，单独立项。
5. `usage.duration` 计费观测：可选加 debug 日志，不阻塞。
6. **（评估过，本期不实施）"每轮 finish-task + 轮间 idle 复用连接"**——从根上绕开
   形态②的 23s 任务级判死（§4.6-1）。**决策见文末结论：只存档为 option，不改代码。**
   - 动机：现设计是"一会话一长任务"，THINKING/SPEAKING 门控期间不喂帧
     （`orchestrator.py:779-780`）超 23s 即判死，下轮首帧走自愈重建（实为高频路径）；
     若改为每轮 turn-end 发 finish-task（provider 现有 `final()` 即此语义）、下轮
     首帧前重新 run-task，任务生命周期与轮次对齐，该判死路径不再发生。
   - 协议前提（2026-09-13 实测，两个前提全部成立）：
     ① 任务结束后**无任务 idle WS 至少 8 分钟不被服务端断开**（客户端 20s WS ping
     保活下；未探更长，8min 已远超轮间/思考窗口）；
     ② 同连接在 finish-task 后可直接再 run-task → `task-started` 并完整跑完第二轮
     （实测两轮 final 均正确、`task-finished` 正常）。
   - 代价：每轮多一次 run-task→task-started 握手（~1 RTT，几十 ms）；provider
     状态机改为"任务随轮而生"（对齐 TTS 侧形态），orchestrator 需新增每轮
     `final()` 调用点（现 orchestrator 从不调用 `final()`，只有脚本在用）。
   - 结论：**本期不做**。现状的"长任务 + 首帧自愈重建"已实测可用，且重建成本与
     握手同量级（同帧补喂、客户端无感）；收益（少一次重建）小于改动面（orchestrator +
     provider 双改）。若未来出现"重建对用户可感知"的实证，再按本节重启评估。

## 5. 已考虑并否决

- **新增 `alibaba_bailian_v3` provider 类**：协议同构，复制无收益；配置即可切换。
- **走 `alibaba_qwen_asr` provider**：那是 OpenAI-realtime 风格协议
  （`session.update`/`input_audio_buffer.append`），与新模型协议不同，接错必挂。
- **AOQ 协议接入**（文档提到新模型除 WebSocket 外支持 AOQ，需集成 AOQ 客户端 SDK）：
  面向客户端弱网/降噪场景；服务端已有 WS 链路与全部自愈逻辑，不引入新 SDK。
  留作未来客户端侧选型，不在本设计范围。
- **`open()` 等待 task-started 再返回**：语义上文档要求 task-started 后才能发音频，
  但 TCP 保序保证 run-task 先于音频到达服务端，且现役实现
  open→feed 背靠背路径在生产自愈场景已反复验证可用（`orchestrator.py:1734-1832`
  的重建即此路径）。改成等待会引入首包延迟，收益不明。**维持现状**，
  真机验证时确认首帧不丢（§6.3）。

## 6. 测试与验证

### 6.1 单测（`services/tests/unit/asr/providers/test_bailian.py`）

沿用本地 `websockets.serve` mock 的做法，新增：

1. **workspace header**：provider 传 `workspace_id="ws-test"` → mock 侧断言握手头含
   `X-DashScope-WorkSpace: ws-test`（对齐 TTS `test_workspace_header`）；
   不传时无该头（默认行为不回退）。
2. **心跳包跳过**：mock 在 interim 之前发一帧
   `{"sentence": {"text": "", "heartbeat": true, "sentence_id": 0, ...}}` →
   断言 `results()` 中不出现该包、`final_text` 不被污染。
3. （回归）现有 4 用例保持全绿——特别是 task-failed 自愈锁。

### 6.2 真机脚本（`services/scripts/test_bailian_asr_real.py`）

增 `--workspace-id`（透传 provider），默认 model 更新为新模型；运行：

```bash
export DASHSCOPE_API_KEY=sk-xxx
services/.venv/bin/python services/scripts/test_bailian_asr_real.py <音频> \
  --base-url wss://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference \
  --workspace-id <WorkspaceId> --model qwen-audio-3.0-asr-flash-streaming \
  --language zh --all-results
```

验收点：建连成功（无 401/411）、interim 与 final 文本正确、
`--all-results` 下无异常空包刷屏（心跳包已被跳过）。

### 6.3 真机验收清单

| # | 项 | 判据 |
|---|---|---|
| 1 | workspace 域名+头必需性 | 缺 `workspace_id` → 观察到 401/411（证实配套关系）；补上 → 通 |
| 2 | 首帧不丢 | 会话首句完整；服务端日志无首字缺失 |
| 3a | 长静默保活（形态①：持续发静音） | LISTENING 下静音音频持续 ≥30min，不出现 `task-failed` / 1001（heartbeat:true 生效） |
| 3b | 停推帧恢复（形态②：帧流中断） | 停发帧 ≥30min 后再发帧：首帧触发有界重建、无 1001 弹窗（既有行为回归；顺带观察 heartbeat 对该形态是否也有效） |
| 4 | 端到端回归 | app 全流程（chat/translate），`asr provider opened` 出现且会话可用；`compare_asr_realtime.py` 做新老模型时延/准确率对照 |
| 5 | 断句交错 | 无重复上屏/重复落行（见 §7-3） |

**实测结果（2026-09-13，本账号 + `llm-1tmsqifo51cwfadr` 空间；`test_bailian_asr_real.py` + 协议探针）**：

- **#1 与预期不符（重要）**：三种组合**全部连通**——workspace 域名±`X-DashScope-WorkSpace` 头、
  旧域名（`dashscope.aliyuncs.com`）无头，均 0.09–0.13s 建连并正确转录。
  TTS 的"缺头 401/411"经验**在 ASR 上未复现**（官方文档本就列该头为可选）。
  结论：域名+头按推荐路径保留（§4.5），但不构成硬依赖；§7-1 已按此更新。
- **#2 通过**：8.46s 音频最快速度喂入（0.13s 喂完）后转录含首词"你好"且全句正确——
  首帧未丢，open→feed 背靠背路径（§5 维持现状的设计）获实证。
  （附带修复：脚本 `--all-results` 原为无效开关（`show_interim` 形参未被使用，interim
  永不打印），§6.2 的验收点名存实亡；已修复并实测——interim 序列正常输出，含每句开头
  2 条空包（`sentence_begin`）而非刷屏，验收点现可执行。）
- **#3b 已实测（结果修正了对该形态的预期）**：停帧后 **23s** 收到
  `task-failed: request timeout after 23 seconds`、WS 以 1011 关闭；**`heartbeat: true`
  不覆盖该形态**（实测即带 heartbeat 发送 run-task）。既有内联重建是该形态的正确
  兜底，不需要也不应改为其他方案。
- **#3a 通过，因果已证（含对照组）**：
  - 实验组：持续静音 32 分钟（`heartbeat=true`）任务全程存活、无 `task-failed`，
    至主动 finish；期间收到 **35 个心跳包**（≈55s 一个，与 §2.5 实测一致）。
  - 对照组（`heartbeat=false`，其余条件相同）：**静音 4.9 分钟即判死**——
    `task-failed: Response stream timeout (timeout_seconds=300, elapsed_ms=310009)`。
  - 结论：**"连接内持续静音"的判死阈值是 300s（5min）；`heartbeat: true` 是真实
    有效的保活参数（非空转）**。生产含义：不启用时，LISTENING 下用户静默 >5min
    即触发一次判死+自愈重建；启用后该路径消失。此结果同时修正了形态①的阈值预期
    （此前按排查记录推测为 ~29min）。
- **#4/#5**：需 app+server 联调，未做。
- **附带实测（协议事实，供 §4.6-6 的 option 用）**：任务干净结束后（finish-task →
  task-finished），无任务 idle WS **8 分钟不被断开**，且同连接可直接再 run-task 完成
  第二轮（两轮 final 均正确）。
- **回滚兼容性实测（2026-09-13）**：`paraformer-realtime-v2` 在 5 种组合下全部正常
  转录——workspace 域名±`X-DashScope-WorkSpace` 头、旧域名、带/不带新增 params
  （`heartbeat`+`language_hints`）。**回滚 = 只改 `model` 一行**。
- **paraformer 保活对照实测（2026-09-13，回答回滚路径）**：同条件下
  `paraformer-realtime-v2` 无 heartbeat → **300s 判死**（`timeout_seconds=300`，
  与新模型完全一致）；带 `heartbeat: true` → **存活 8 分钟**、收 9 个心跳包（≈53s 一个）。
  → ①"静音 300s 判死"是该协议族的**通用行为（与模型无关）**；② **heartbeat 保活
  paraformer 同样支持**——回滚只改 `model` 一行时**保活能力不丢失**（前提：
  `params.heartbeat: true` 保留）。代价面：回滚失去新模型其余能力（热词、上下文、
  多语种 hints）。

### 6.4 收尾时延实测与 max_sentence_silence 调优（2026-09-13 晚）

**背景**：使用中感觉"人说完话，等 final 有延迟感"。逐段核对后，延迟大头在云端
VAD 断句窗口；本地链路（上行/服务端/回传）合计仅百毫秒级。

**方法**：新增两个探针脚本，均以实时节奏（20ms/50Hz）喂帧，测「最后一帧语音发出 →
final 到达」的墙钟差（实时喂帧下音频时间轴=墙钟，即用户感知）：

- `services/scripts/measure_asr_final_latency.py`：直连云端 provider，支持
  `--matrix` 对照矩阵与 `--param max_sentence_silence=N` 单配置扫描。
- `services/scripts/measure_e2e_asr_final_latency.py`：模拟设备连**本机服务端**
  （WS + opus 帧，走 gateway→orchestrator→云端全链路），用于验证 yaml 配置生效。

**核心结论（修正 §2.3 的"默认 1300"）**：本模型
`qwen-audio-3.0-asr-flash-streaming` 的 `max_sentence_silence` **实测默认值是 800ms**，
且收尾时延与阈值近似线性：

```
收尾时延 ≈ max_sentence_silence + 模型固化时延   （qwen ≈ 455ms；paraformer ≈ 205ms）
```

| 配置 | 收尾时延（直连，均值） | 说明 |
|---|---|---|
| qwen，不传（默认 800） | 1253ms | 与显式 800 完全一致 → 默认即 800 |
| qwen，600 | 1085ms | 本次采用值 |
| qwen，400 | 850ms | |
| qwen，200 | 707ms | |
| qwen，1300 | 1774ms | 印证线性（1300+455≈1755） |
| paraformer，默认 | 1000ms | 同阈值下比 qwen 快 ~250ms（模型固有差异） |
| paraformer，200 | 350ms | |

**断句代价（受控样本：句中 700ms 停顿）**：mss=800 → 1 个 final 完整；
mss=600/400 → 被切成两句（前句在用户尚未说完时就 final）。碎句在生产会触发
抢答：final 一到即 `force_turn_end` → `_start_thinking`，而 THINKING 态下
`_handle_audio` 丢弃音频帧（`orchestrator.py:779`），用户后续语句会丢。故 600 是
"与本地 TurnDetector(medium=600ms) 对齐、代价可控"的折中，不再下探。

**端到端验证（yaml 生效）**：真实服务端 + yaml 配置，同一音频 4 次：
yaml=600 → **1254ms**（1225–1302）；yaml=800 → **1395ms**（1327–1420）。
差 ~140ms，与直连差值同向 → 配置穿透生效。服务端链路附加约 100–200ms（相对直连）。

**探针环境坑（重要）**：尾随"静音"不能用全零帧。**全零帧经 Opus 往返后不是静音**
——编码器刚处理完语音时，20ms 全零帧编出的包解码后是上一帧语音的低电平残响
（实测 RMS≈365 恒定波形，本地可复现），云端 VAD 不判静音、句子永不闭合
（探针会卡在"未收到 final"）。e2e 探针因此改用低幅随机底噪（`--silence-amp`，
模拟真实房间底噪）。生产采集路径发的就是底噪，不受影响；但 `SilenceFlushGate`
的 mute 补发窗口（1s 全零）只有首帧带残响、其余为真静音，故仍能闭合句子。

**观测埋点（本次新增）**：`orchestrator.py` 的 `_poll_asr_results` 现对每个 final
打 `ASR final received len=%d text=%r`（INFO）——此前空文本 final 被静默丢弃，
"收尾为何慢"无从判读；provider 侧每个 result 包在 DEBUG 打
`heartbeat/sentence_id/sentence_end/end_time/text_len`。排查时用
`LIVACHAT__SERVER__LOG_LEVEL=DEBUG` 打开。

**未做/待办**：
- 客户端 App 真机（§6.3 #4/#5）仍未做；本机无 adb 设备，e2e 为服务端替代验证。
- 部署侧配置同步需带上 `max_sentence_silence: 600`（§4.5）。
- 若未来要再压收尾，唯一手段是轮次驱动 `finish-task`（§4.6-6 的 option，从延迟
  视角可省掉整个静音窗口等待 ~0.6–1.0s），但需改 orchestrator/provider 状态机，
  且碎句/抢答风险同 mss 下探，需先实测"finish-task → final"时延。

## 7. 风险与未决项

1. **域名与头的配套关系已实测（结论与预期不符）**：官方文档"现有域名仍可正常使用"
   成立；TTS 的"缺头 401/411"经验**在 ASR 上未复现**——本账号下 workspace 域名±头、
   旧域名无头三种组合全部连通（2026-09-13，见 §6.3 实测结果）。配置仍按推荐路径
   域名+头成对给（§4.5），但不构成硬依赖。
2. **`heartbeat` 的覆盖面以真机为准**：官方语义只覆盖"持续发静音"（形态①，
   §4.6-1）；"帧流中断"（形态②，已发生故障形态）是否也受保护未知，该形态由
   既有内联重建兜底。THINKING/SPEAKING 门控期间不喂帧的秒级窗口
   （`orchestrator.py:779-780`）不受保护，窗口远小于 29min，可接受。
3. **服务端 VAD 断句与本地轮次检测的交错**：新模型默认 `max_sentence_silence=1300`
   （服务端断句），与本地 `TurnDetector`（400-900ms，`turn_detector.py:18`）并存。
   现役 paraformer 同为服务端断句语义，双 final 去重（`_asr_final_sent`）、
   `force_turn_end`、final 送达 shield 等防御均已存在且被回归锁覆盖；
   换模型不改变该结构，但**真机需核对无重复上屏/落行**（§6.3-5）。若显著，另立项。
4. `params` 合并语义是"覆盖"而非"默认值"（`alibaba_bailian.py:214-215`）：
   yaml 里写错键会静默改变行为，配置注释需明确（§4.5 已删 `sample_rate`）。
5. 无 `finish-task` 的会话结束路径：orchestrator 关会话直接 `close()` WS
   （不调 `final()`），现役如此、保持不变；服务端自行回收。若新模型计费按
   `usage.duration` 且对未 finish 的任务计费更久，真机对账后再说（当前无对账渠道）。
6. 旧模型回退：`alibaba_bailian` 条目改 model 即切换，回退同样改配置即可；
   `compare_asr_realtime.py` 可做 A/B。

## 8. 改动清单

**实施顺序（硬约束，A2）**：`models.py` → `factory.py` → `provider` → `yaml`。
pydantic v2 默认 `extra="ignore"`（`models.py:255-260` 未设 extra），顺序颠倒时
yaml 新键被**静默丢弃**（不报错），provider 收到 `workspace_id=None` → 握手缺头
401/411，且只在首个会话以 1001 暴露。可选加固：给 `AsrProviderConfig` 加
`extra="forbid"`，让未来的配置拼写错误变响。

| 顺序 | 文件 | 改动 |
|---|---|---|
| 1 | `services/shared/config/models.py` | `AsrProviderConfig` + `workspace_id: str \| None = None`（镜像 `:116`） |
| 2 | `services/asr/factory.py` | `alibaba_bailian` 分支传 `workspace_id=cfg.get("workspace_id")` |
| 3 | `services/asr/providers/alibaba_bailian.py` | `__init__` + `workspace_id`；新增 `_headers()`（镜像 TTS `:402-407`）；`open()` 改用 `_headers()` 并补 model 日志（消息体 %-参数，勿用 `extra=`，§4.3-4）；`_receive_loop` 跳过 `heartbeat` 包 |
| 4 | `configs/dev/server.yaml` | `alibaba_bailian`：base_url/model/workspace_id/params（删 `sample_rate`）；删死配置 `asr.language`（yaml 行 + schema 字段 `AsrConfig.language` 一并删除——否则 `LIVACHAT__ASR__LANGUAGE` 仍可静默设置无人读的值；遗留 yaml 键静默忽略，不破坏存量配置） |
| 5 | 部署侧配置（线上确认，§4.5） | 同步 base_url/model/workspace_id/params 同一组键 |
| 6 | `services/tests/unit/asr/providers/test_bailian.py` | +2 用例（workspace 头、心跳包跳过） |
| 7 | `services/scripts/test_bailian_asr_real.py` | `--workspace-id` + 默认 model 更新 |

不做：orchestrator / gateway / 协议 / 客户端 / `alibaba_qwen_asr` provider 任何改动。

## 9. 参考

- 新模型实时识别 WebSocket API（接口地址/请求头/交互流程）：
  https://docs.bailian.console.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api
  （镜像：https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api）
- 客户端事件（run-task/continue-task/finish-task 全参数）：
  https://help.aliyun.com/zh/model-studio/fun-asr-client-events
- 服务端事件（task-started / result-generated / heartbeat / task-finished）：
  https://help.aliyun.com/zh/model-studio/fun-asr-server-events
- 语音识别选型（"实时推荐 qwen-audio-3.0-asr-flash-streaming"、AOQ 协议说明）：
  https://help.aliyun.com/zh/model-studio/asr-model
- 实时语音识别用户指南（SDK 示例、时间戳、断句参数差异）：
  https://help.aliyun.com/zh/model-studio/real-time-speech-recognition-user-guide
- 模型信息页（能力矩阵、价格）：
  https://help.aliyun.com/zh/model-studio/qwen-audio-3-0-asr-flash-streaming
- 热词与上下文增强（即时热词限制、context 结构）：
  https://help.aliyun.com/zh/model-studio/improve-asr-accuracy
- 代码内先例：`docs/tts_bailian_improvements.md`（TTS duplex 自愈重构，workspace 头实现与测试）；
  `services/tts/providers/alibaba_bailian.py:389-423`（`_headers`/`_connect_raw`）。
