# Dev / Test Plan: Server 全模态大模型 (Omni Model) 支持

> **Status**: draft
> **Date**: 2026-07-02
> **Design ref**: [`docs/fd_server_omni_model.md`](fd_server_omni_model.md)
> **Scope**: 后端服务 (`services/`) 的 Omni 能力层落地开发与测试计划。
>
> **更新（2026-09-27）**：`alibaba_omni` 已迁移至 `qwen3.8-omni-flash-realtime`——业务空间专属域名、
> nested `audio.*`（16 kHz，生效字段）、`semantic_vad`、转写增量事件 `.delta`（`text`+`stash`），
> 且 omni 会话采样率改为与 Opus 编解码器同源。本文下方 T2.x 的旧写法（公共域名、flat `sample_rate`、
> `server_vad`、`.text` 事件名）为当时实现记录；现状以
> [`docs/qwen_omni_realtime.md`](qwen_omni_realtime.md) 为准。
>
> **本计划的两条硬边界（来自任务约束）**
> 1. **第一阶段只完整支持 Alibaba Qwen-Omni Realtime**（`alibaba_omni`）。
> 2. **OpenAI Realtime (`openai_realtime`) 本阶段只落地"框架/骨架"**：类结构、工厂注册、配置位齐全，可被单元测试实例化，但不做真机联调、不保证跑通（`NotImplementedError` / stub + 明确 TODO）。
> 3. 遵循设计文档 §2.1 的注记——**第一阶段不抽取 `session/pipeline_common.py`**。共享逻辑先用"从 `DialogOrchestrator` 复制/内联 + 后续再重构"策略，避免在本期同时改动稳定的三段式代码，降低回归风险。抽象化列为第二阶段任务。

---

## 1. 现状基线 (Baseline — 已核对代码)

计划中引用的现有实现（已逐一读代码确认），Omni 实现将直接对齐/复用：

| 关注点 | 现有实现 | 复用方式 |
|---|---|---|
| Provider 契约 | `asr/base.py` `AsrStream/AsrProvider`、`tts/base.py` `TtsStream/TtsProvider`、`llm/base.py` `LlmProvider` | 新增 `omni/base.py`，`open()` 对齐 `TtsProvider.open`，`feed_audio` 对齐 `AsrStream.feed`，`events()` 对齐 `AsrStream.results` |
| Realtime WS 模式 | `tts/providers/alibaba_qwen_tts.py`（`session.created`→`session.update`→`session.updated` 握手、后台 `_receive_loop` + `asyncio.Queue`、`response_id` turn 边界、`_reconnect`、采样日志 `_log_msg`） | Omni provider 内部基类 `_RealtimeOmniStream` 直接照搬此结构 |
| 失败切换 | `llm/service.py:LlmService`（`[active]+fallbacks`，首 token 前切换） | `omni/service.py:OmniService`：仅在 `open()` 握手阶段切换 |
| 工厂 | `llm/factory.py:create_llm` / `_create_provider` | `omni/factory.py:create_omni`，`_PROVIDER_CLASSES` 表 |
| 配置 | `shared/config/models.py`（`LlmProviderConfig/LlmConfig`，`AppConfig` 嵌套 env `LIVACHAT__`） | 新增 `OmniProviderConfig/OmniConfig`，`AppConfig.omni` 字段 |
| 错误码 | `shared/utils/errors.py:ErrorCode`（IntEnum，provider 级用 10xx） | 新增 `OMNI_UNAVAILABLE`（复用 `LLM_UNAVAILABLE=1003` 网段的 fatal=false 语义） |
| 装配 | `app/application.py:lifespan`（asr/llm/tts 创建块 + `app.state.*`） | 增加 `omni` 分支，`app.state.omni_service/omni_enabled` |
| 端点选择 | `gateway/endpoint.py:ws_stream`（构建 `DialogOrchestrator` 注入 `ConnectionHandler`） | 增加 `omni_enabled` 分支构建 `OmniOrchestrator`，`ConnectionHandler` 不变 |
| 编排 send/persist | `session/orchestrator.py`（`_send_transcription/_send_ai_text/_send_ai_audio/_send_status`、`_persist_*`、`AudioFrameBuffer`、`InterruptController`、`SessionContext.epoch`） | 第一阶段：`OmniOrchestrator` 内联/复制这些 helper（不抽 `pipeline_common.py`） |
| 断线快照 | `gateway/connection.py:_snapshot_session`（读 `orchestrator._session_mgr/_ctx/_history`） | `OmniOrchestrator` 暴露同名属性即零改动复用 |
| 采样率 | `media/resample.py:Resampler`（soxr 流式）、`media/buffer.py:AudioFrameBuffer`、`media/opus.py` | provider 内封装采样率协商；输出分帧复用 `AudioFrameBuffer` |
| 测试 fake | `tests/unit/asr/fakes/fake_asr_provider.py`、`tests/integration/orchestrator/conftest.py`（`_FakeHandler`/`FakeLlmService`） | 新增 `FakeOmniProvider`/`_FakeHandler` 复用 |

---

## 2. 阶段划分 (Milestones)

```
M0  脚手架 + 配置 + 错误码          （可导入、不改行为）
M1  omni/base + service + factory   （抽象层 + 失败切换，纯单测）
M2  alibaba_omni provider           （真 realtime，含 mock 单测 + 真机脚本）
M3  OmniOrchestrator                （事件驱动编排，FakeOmniProvider 集成测试）
M4  装配 + 端点选择 + 配置样例      （omni.enabled 端到端打通）
M5  openai_realtime 骨架            （stub，仅结构/工厂/单测可实例化）
M6  回归 + 真机联调 + 文档          （omni.enabled=false 零回归；DashScope 真机通）
```

依赖关系：M0 → M1 → {M2, M3 并行} → M4 → {M5, M6}。M5 可与 M6 并行。

---

## 3. 详细开发任务 (按文件)

### M0 — 脚手架 / 配置 / 错误码

**T0.1 错误码** — `services/shared/utils/errors.py`
- `ErrorCode` 增加 `OMNI_UNAVAILABLE = 1007`（接 `OVERLOAD=1005` 之后；1006 已被协议层 `MALFORMED_JSON`/`UNSUPPORTED_MESSAGE_TYPE`/`FRAME_TOO_LARGE` 占用，不可复用）。
- 验收：`import` 成功；`test_ids`/现有 error 测试不受影响。

**T0.2 配置模型** — `services/shared/config/models.py`
- 新增 `OmniProviderConfig`（`enabled/base_url/api_key/model/voice/supports_vision/modalities/extra_body`）与 `OmniConfig`（`enabled=False/active/fallback/system_prompt_path/generation: LlmGenerationConfig/providers`）。
- `AppConfig` 增加 `omni: OmniConfig = OmniConfig()`。
- 验收：`omni.enabled` 默认 `False`（向后兼容）；`LIVACHAT__OMNI__ENABLED=true`、`LIVACHAT__OMNI__ACTIVE=alibaba_omni` 可覆盖（env nested delimiter `__`）。

**T0.3 包骨架** — 新建目录/空实现
```
services/omni/__init__.py
services/omni/base.py            # 仅 Protocol/dataclass（M1 填充）
services/omni/service.py         # 占位（M1）
services/omni/factory.py         # 占位（M1）
services/omni/providers/__init__.py
services/omni/providers/alibaba_omni.py     # 占位（M2）
services/omni/providers/openai_realtime.py  # 占位（M5）
```
- 验收：`python -c "import omni, omni.base"` 通过；`make server-test` 不新增失败。

---

### M1 — Omni 抽象层 + 失败切换

**T1.1 `omni/base.py`** — 按设计 §3.1 实现
- `OmniEventType(StrEnum)`、`OmniEvent(dataclass)`、`OmniStream(Protocol)`、`OmniProvider(Protocol)`。
- 契约点：`feed_audio(pcm)`、`feed_image(jpeg,*,width,height)`、`commit_user_turn()`、`events()->AsyncIterator[OmniEvent]`、`cancel_response()`、`reset()`、`close()`；`OmniProvider.open(*, sample_rate, system_prompt, voice, modalities, generation_opts)`。
- 验收：mypy/类型通过；纯定义无运行时逻辑。

**T1.2 `omni/service.py:OmniService`** — 对齐 `LlmService`
- `__init__(active, fallbacks)`；`async open(**kwargs) -> tuple[OmniStream, str]`：遍历 `[active]+fallbacks`，`open()` 握手失败切下一个；全失败抛 `ProviderError(code=OMNI_UNAVAILABLE, provider="omni")`。
- **不做连接建立后中途切换**（realtime 有状态）。
- 验收：单测覆盖 全成功 / 首个失败切次个 / 全失败抛错（见 §4.1）。

**T1.3 `omni/factory.py:create_omni`** — 对齐 `create_llm`
- `_PROVIDER_CLASSES = {"alibaba_omni": AlibabaOmniProvider, "openai_realtime": OpenAIRealtimeProvider}`。
- `create_omni(config: OmniConfig) -> OmniService`：校验 `active in providers`，构建 active + fallbacks；未知 provider 抛 `ConfigError`。
- 验收：单测覆盖 active 解析、fallback 列表、未知 provider 抛错。

---

### M2 — Alibaba Qwen-Omni Provider（第一阶段核心）

**T2.1 内部基类 `_RealtimeOmniStream`** — `omni/providers/alibaba_omni.py`
> 直接移植 `alibaba_qwen_tts.py` 的成熟结构，差异仅在 payload/事件名映射。
- 连接：`websockets.connect(base_url, additional_headers={"Authorization": f"Bearer {api_key}"}, ping_interval=20, ping_timeout=20)`。
- 握手：等 `session.created` → 发 `session.update`（`modalities`、`voice`、`input_audio_format: pcm`、`sample_rate`、`turn_detection:{type:"server_vad"}`、`instructions:<system_prompt>`）→ 等 `session.updated`。
- 后台 `_receive_loop` + `asyncio.Queue[OmniEvent]`；日志照搬 tts 模式（见下方 **T2.1a 协议消息日志规范**）。
- `response_id` turn 边界：新 `response.created` 清空陈旧队列（照搬 `_get_response_id` 双位置解析）。
- `_reconnect()`：轻量重连（照搬 tts 实现）。

**T2.1a 协议消息日志规范** — omni provider 与 realtime API 之间的每条首发性消息**必须**打 log，格式严格对齐 `alibaba_qwen_tts.py`：

```python
import logging
from shared.observability.logging import get_logger

logger = logging.getLogger("livachat.omni.alibaba")
msg_logger = get_logger("livachat.omni.alibaba.messages")

def _log_msg(direction: str, msg_type: str, **extra: object) -> None:
    msg_logger.debug(f"{direction} {msg_type}", direction=direction, type=msg_type, **extra)
```

双 logger 分工：
- `logger`（INFO/WARNING/ERROR）：生命周期事件（连接建立、重连、receiver 崩溃/退出、turn 边界）
- `msg_logger`（DEBUG）：逐消息 send/recv，靠 `_log_msg` 统一打点

**必须覆盖的日志点**（按 realtime 会话生命周期排列）：

| 阶段 | direction | msg_type | 级别 | extra 字段 | 备注 |
|---|---|---|---|---|---|
| 握手 | `recv` | `session.created` | DEBUG | `session_id` | 连接后首条 recv |
| 握手 | `send` | `session.update` | DEBUG | `event_id`, `modalities`, `voice`, `sample_rate` | 配置载荷 |
| 握手 | `recv` | `session.updated` | DEBUG | `session_id` | 握手完成确认 |
| 音频输入 | `send` | `input_audio_buffer.append` | DEBUG | `event_id`, `audio_n` (1st + every 50th) | 高频，采样打点 |
| 音频输入 | `send` | `input_audio_buffer.commit` | DEBUG | `event_id` | 手动 turn 模式；server_vad 下为 no-op |
| 图像输入 | `send` | `conversation.item.create` | DEBUG | `event_id`, `width`, `height` | 视频帧注入 |
| VAD 事件 | `recv` | `input_audio_buffer.speech_started` | DEBUG | `response_id` | 用户开始说话 / barge-in 触发 |
| VAD 事件 | `recv` | `input_audio_buffer.speech_stopped` | DEBUG | `response_id` | turn 结束 |
| 转写 | `recv` | `conversation.item.input_audio_transcription.delta` | DEBUG | `response_id` | 增量转写（`text`+`stash`） |
| 转写 | `recv` | `conversation.item.input_audio_transcription.completed` | DEBUG | `response_id` | 转写 final |
| 回复 | `recv` | `response.created` | **INFO** | `response_id`, `prev_response_id` | **turn 边界**；新 response 到则清空陈旧队列 |
| 回复 | `recv` | `response.text.delta` | DEBUG | `response_id` | 文本增量 |
| 回复 | `recv` | `response.audio_transcript.delta` | DEBUG | `response_id` | OpenAI 文本通路 |
| 回复 | `recv` | `response.audio.delta` | DEBUG | `response_id`, `audio_n` (1st + every 50th) | 音频增量（base64→PCM16 后入队） |
| 回复 | `recv` | `response.done` | DEBUG | `response_id` | 本轮回复结束 |
| 中断 | `send` | `response.cancel` | DEBUG | `event_id`, `reason` | 客户端 interrupt 或 barge-in |
| 中断 | `send` | `input_audio_buffer.clear` | DEBUG | `event_id` | 清空输入缓冲 |
| 关闭 | `send` | `session.finish` | DEBUG | `event_id` | 会话结束 |
| 异常 | `recv` | `error` | **WARNING** | `error_message`, `error_code` | provider 返回错误 |
| 生命周期 | — | — | INFO | `session_id`, `reason` | receiver loop 启动/退出/崩溃/重连 |

**采样策略**（对齐 tts）：
- JSON 事件：每条 recv 都打 `_log_msg("recv", event_type, ...)`
- 每条 send 都打 `_log_msg("send", event_type, ...)`
- 音频二进制帧：每 response 第 1 帧 + 每 50 帧打一次 `_log_msg("recv", "audio", response_id=..., audio_n=...)`
- 音频 append：每 response 第 1 次 + 每 50 次打一次 `_log_msg("send", "input_audio_buffer.append", event_id=..., audio_n=...)`
- `response.created`（turn 边界）升到 **INFO** 级别，打印 `prev_response_id` → `response_id` 切换
- `error` 事件升到 **WARNING** 级别

**真机联调前置要求**：M2 编码前**先行**跑一次裸 ws 脚本连接 DashScope Qwen-Omni Realtime，dump 完整的 send/recv 消息序列到日志，人工核对事件名/字段是否与上述表格一致。如有偏差先修正本文档 T2.2 映射表，再开始 M2 编码。M6 正式联调时再做一次对照确认。

**T2.2 事件映射**（设计 §3.3 表）→ `OmniEvent`
| Qwen Realtime event | OmniEventType |
|---|---|
| `input_audio_buffer.speech_started` | `SPEECH_STARTED` |
| `input_audio_buffer.speech_stopped` | `SPEECH_STOPPED` |
| `conversation.item.input_audio_transcription.delta` | `INPUT_TRANSCRIPTION_DELTA` |
| `...input_audio_transcription.completed` | `INPUT_TRANSCRIPTION_DONE` |
| `response.created` | `RESPONSE_STARTED` |
| `response.text.delta` / `response.audio_transcript.delta` | `TEXT_DELTA` |
| `response.audio.delta` (base64→PCM16) | `AUDIO_DELTA` |
| `response.done` | `RESPONSE_DONE` |
| `error` | `ERROR`（填 `error_message`） |

**T2.3 输入 / 中断方法**
- `feed_audio(pcm)` → `input_audio_buffer.append`（base64）。
- `feed_image(jpeg,*,width,height)` → `conversation.item.create`（image_url data URL）；`supports_vision=True`。
- `commit_user_turn()` → server_vad 模式下 no-op（保留手动兜底钩子）。
- `cancel_response()` → `response.cancel` + `input_audio_buffer.clear`。
- `reset()` → 清 `response_id` + 清队列（照搬 tts）。
- 采样率：输入若 provider 要求 ≠ 会话率，用 `media/resample.py` 升采样；输出 PCM16 → 必要时重采样到会话 `audio_sample_rate`（Qwen 可配 16kHz，优先直配免重采样）。

**T2.4 `AlibabaOmniProvider`**
- `name="alibaba_omni"`, `supports_vision=True`；`open(...)` 建连+握手，返回 `_RealtimeOmniStream` 并启动 receiver。
- 验收：mock 单测（§4.2）；真机脚本（§4.4）连通 DashScope。

---

### M3 — OmniOrchestrator（事件驱动编排）

**T3.1 `session/omni_orchestrator.py:OmniOrchestrator`** — 设计 §3.8
- `__init__` 签名对齐 `endpoint.py` 注入点：`handler/codec/session_mgr/omni_service/opus_encoder/opus_decoder/system_prompt/generation_opts/voice/pg_session_factory`。
- **暴露 `_session_mgr/_ctx/_history` 属性**（`connection.py:_snapshot_session` 零改动复用）。
- 两协程：
  1. `run()` dispatch loop：`wait_for(inbound.get(), 0.5)` → `_dispatch`；`finally` cancel event pump + `omni_stream.close()`。
  2. `_event_pump()` 后台 task：`async for ev in omni_stream.events()` → 映射 s2c + 状态机。
- `_dispatch`：`session.start`→`_handle_session_start`（eager open omni + 启动 pump）；`audio`→Opus 解码→`feed_audio`；`video`→base64 JPEG→`feed_image`；`interrupt`→`_do_interrupt("client")`；`session.end`→cancel pump + close + `session_mgr.end` + `handler.stop`。

**T3.2 event pump 状态映射**（设计 §3.8/§5）
- `INPUT_TRANSCRIPTION_DELTA/DONE` → `_send_transcription`（final 时 `history.add_message("user")` + `_persist_transcription`）。
- `SPEECH_STARTED` 且 `state==SPEAKING` → `_do_interrupt("user_voice")`（barge-in）。
- `RESPONSE_STARTED` → `transition_or_reject(THINKING)` + `status("thinking","asr_final")`。
- `TEXT_DELTA` → `_send_ai_text(is_final=False)`，累积 `_full_text`。
- `AUDIO_DELTA` → 首帧 `transition_or_reject(SPEAKING)` + `status("speaking","tts_start")`；`AudioFrameBuffer.feed` 分帧 → `OpusEncoder.encode` → `_send_ai_audio`。
- `RESPONSE_DONE` → `_send_ai_text("",is_final=True, image=last_image)` + `history.add_message("assistant")` + `_persist_messages` + `transition_or_reject(LISTENING)` + `status("listening","tts_finish")` + `_reset_turn()`。
- `ERROR` → `codec.make_error(code=OMNI_UNAVAILABLE/1007, message, fatal=false)`。

**T3.3 中断 `_do_interrupt`**（设计 §4.3/§4.4）
- `ctx.epoch += 1`（event pump 用 epoch 快照丢弃陈旧 `TEXT_DELTA/AUDIO_DELTA`）→ `omni_stream.cancel_response()` → `audio_out_buf.reset()` → `status("listening","interrupted")`。
- 复用 `InterruptController` 语义（epoch 机制已在 `session/interrupt.py`）。

**T3.4 第一阶段 helper 策略（不抽 pipeline_common）**
- `_send_transcription/_send_ai_text/_send_ai_audio/_send_status` 与 `_persist_*`：从 `DialogOrchestrator` **复制**进 `OmniOrchestrator`（或 `OmniOrchestrator` 直接 `import` 现有独立函数——凡是纯函数/无状态的优先 import；带 `self._` 的复制）。
- 在文件顶部加 `# TODO(phase2): 抽取 pipeline_common.py，与 DialogOrchestrator 共享` 注释，标注可维护性债务。
- 验收：`FakeOmniProvider` 集成测试断言与三段式相同的 s2c 序列（§4.3）。

---

### M4 — 装配 / 端点选择 / 配置样例

**T4.1 `app/application.py:lifespan`** — 设计 §3.6
- 在 asr/llm/tts 创建块处改为 **互斥分支**：`if cfg.omni.enabled:` 创建 `omni_service = create_omni(cfg.omni)` + `system_prompt = load_system_prompt(cfg.omni.system_prompt_path)`，跳过三段式创建；`else:` 保留现有逻辑。
- `app.state.omni_service = omni_service`；`app.state.omni_enabled = cfg.omni.enabled`。
- 失败（provider 不可用）→ `logger.warning` + `omni_service=None`（不 crash 启动）。

**T4.2 `gateway/endpoint.py:ws_stream`** — 设计 §3.7
- 在构建 orchestrator 处增加：`if getattr(ws.app.state,"omni_enabled",False) and ws.app.state.omni_service:` 构建 `OmniOrchestrator`（注入 opus enc/dec、`voice=cfg.omni.providers[cfg.omni.active].voice or ""`、`generation_opts=cfg.omni.generation.model_dump()`）；`else:` 现有 `DialogOrchestrator` 不变。
- `handler` 创建与 `orchestrator._handler = handler` 回填逻辑不变。

**T4.3 配置样例** — `configs/dev/server.yaml`
- 增加 `omni:` 段（设计 §6），默认 `enabled: false` 提交到仓库；本地联调用 env 覆盖或单独 dev 配置。
- 验收：`omni.enabled: true` + `DASHSCOPE_API_KEY` 可启动并走 omni 路径；`false` 恢复三段式。

---

### M5 — OpenAI Realtime 骨架（stub only）

**T5.1 `omni/providers/openai_realtime.py:OpenAIRealtimeProvider`**
- 类结构齐全：`name="openai_realtime"`, `supports_vision=False`；`__init__` 接受与 alibaba 相同的 `base_url/api_key/model/voice`。
- `open(...)`：**stub** —— 抛 `NotImplementedError("openai_realtime provider not implemented in phase 1")` 或返回未实现的 `_stub` stream，带 `# TODO(phase2)` 标注 URL/鉴权头（`OpenAI-Beta: realtime=v1`）、24kHz 输出重采样、`feed_image` no-op 等差异点（设计 §3.3）。
- 在 `factory._PROVIDER_CLASSES` 中已注册，可被 `create_omni` 实例化用于结构测试。
- 验收：单测 `create_omni` 能构建含 `openai_realtime` 的 fallback 列表；调用 `open()` 明确抛 `NotImplementedError`（不静默失败）。**不做真机、不列入 M6 联调。**

---

### M6 — 回归 / 真机 / 文档

**T6.1 零回归**：`omni.enabled=false`（默认）时 `make server-test` 全绿——现有 asr/llm/tts/orchestrator 测试不受任何影响（M0–M4 均为 additive 或分支）。
**T6.2 真机**：`services/scripts/test_omni_real.py`（§4.4）打通 DashScope Qwen-Omni。
**T6.3 观测**：新增 `livachat.omni.messages` logger，对齐 `livachat.gateway.messages` 采样策略。
**T6.4 文档**：更新 `docs/fd_server_omni_model.md` §8 测试策略状态；REPO.md「分阶段联调」补充 omni 开关说明。

---

## 4. 测试计划 (Test Plan)

测试目录沿用现有布局；新增文件如下。

```
services/tests/unit/omni/
  __init__.py
  fakes/
    __init__.py
    fake_omni_provider.py          # FakeOmniProvider / FakeOmniStream（脚本化事件）
  test_service_failover.py         # OmniService 失败切换
  test_factory.py                  # create_omni 解析 / 未知 provider / openai stub 实例化
  providers/
    __init__.py
    test_alibaba_omni.py           # mock realtime 事件流 → 断言 OmniEvent 映射
    test_openai_realtime_stub.py   # 骨架：open() 抛 NotImplementedError

services/tests/integration/omni/
  __init__.py
  conftest.py                      # _FakeHandler 复用 + OmniOrchestrator fixture
  test_omni_turn_flow.py           # 一轮对话 s2c 序列
  test_omni_interrupt.py           # client interrupt + server VAD barge-in
  test_omni_transcription.py       # 转写 delta/final + 持久化调用

services/scripts/
  test_omni_real.py                # 真机：DashScope Qwen-Omni（仅 alibaba）
```

### 4.1 单元 — `OmniService` 失败切换 (`test_service_failover.py`)
- **全成功**：active `open()` 成功 → 返回 `(stream, "alibaba_omni")`，不触碰 fallback。
- **首个握手失败**：active `open()` 抛异常 → 切 fallback → 返回 fallback stream + name；断言 warning 日志。
- **全部失败**：抛 `ProviderError`，`code==OMNI_UNAVAILABLE`, `provider=="omni"`。
- **无中途切换**：连接建立后 provider 运行期抛错，不触发 service 层切换（由 orchestrator/`_reconnect` 处理）。
- 用 `FakeOmniProvider(raise_on_open=...)` 驱动（对齐 `FakeAsrProvider`）。

### 4.2 单元 — Provider 事件映射 (`providers/test_alibaba_omni.py`)
- 用 **mock WebSocket**（内存 async iterable，喂 JSON 帧序列）替换 `websockets.connect`，或直接测试 `_RealtimeOmniStream` 的帧→`OmniEvent` 纯映射函数。
- 断言每类 Qwen event → 正确 `OmniEventType`；`response.audio.delta` base64 正确解码为 PCM16；`response_id` turn 边界丢弃陈旧队列；`error` → `OmniEvent.ERROR` 带 message。
- 握手：`session.created`→发 `session.update`（断言 payload 含 `turn_detection.server_vad`、`sample_rate`、`instructions`）→`session.updated`；缺失 `session.created` 抛 `ProviderError`。
- **OpenAI stub** (`test_openai_realtime_stub.py`)：`open()` 抛 `NotImplementedError`；`supports_vision is False`。

### 4.3 集成 — `OmniOrchestrator` (需 `FakeOmniProvider`)
`fake_omni_provider.py` 设计：`FakeOmniStream.events()` 为脚本化 async generator，按注入的事件列表逐个 yield（可插 `await asyncio.sleep(0)`），`feed_audio/feed_image/cancel_response/reset/close` 记录调用。`FakeOmniProvider.open()` 返回预置 stream（对齐 `FakeAsrProvider` + `conftest._FakeHandler`）。

- **`test_omni_turn_flow.py`（一轮对话）**：脚本 `[INPUT_TRANSCRIPTION_DONE, RESPONSE_STARTED, TEXT_DELTA×2, AUDIO_DELTA×2, RESPONSE_DONE]` → 断言 `_FakeHandler.outbound` 依次含：`transcription(final)` → `status:thinking` → `ai_text delta×2` → `status:speaking` → `ai_audio×N` → `ai_text final` → `status:listening`。**与三段式 `test_full_turn.py` 的 s2c 序列一致**（核心兼容性验收）。
- **`test_omni_interrupt.py`**：
  - client interrupt：注入 `interrupt` inbound → 断言 `cancel_response()` 被调、`ctx.epoch` +1、`status:listening(interrupted)`。
  - server VAD barge-in：state=SPEAKING 时注入 `SPEECH_STARTED` → 断言触发 `_do_interrupt`，后续陈旧 `AUDIO_DELTA`（旧 epoch）被丢弃。
- **`test_omni_transcription.py`**：`INPUT_TRANSCRIPTION_DELTA` → `transcription(is_final=False)`；`DONE` → final + `history.add_message("user")` + `_persist_transcription` 被调（用 mock pg factory / spy）。
- **eager open**：`session.start` → 断言 `omni_service.open()` 被调 + event pump task 启动 + `session.ready` + `status:idle→listening`。

### 4.4 真机脚本 — `scripts/test_omni_real.py`（仅 alibaba）
- 对齐 `scripts/test_qwen_tts_real.py`：从 env 读 `DASHSCOPE_API_KEY`，`AlibabaOmniProvider.open()` → 喂一段测试 PCM（或 wav 文件）→ 打印收到的 `OmniEvent` 序列（转写/文本/音频帧数）→ 落地音频到 wav 供人工听感验证。
- 覆盖：server VAD 是否自动 `speech_stopped`、`response.done` 是否收到、音频采样率是否符合。
- **openai_realtime 不在本脚本范围**（骨架阶段跳过）。

### 4.5 回归 — 零改动验证
- CI/本地：`omni.enabled=false` 默认下 `make server-test` 全绿。
- 显式断言：`AppConfig()` 默认 `omni.enabled is False`；`endpoint.py` 在无 `omni_enabled` 时仍构建 `DialogOrchestrator`（现有 `tests/integration/gateway/*`、`tests/integration/orchestrator/*` 不受影响）。

### 4.6 覆盖率目标
- `omni/base.py`、`omni/service.py`、`omni/factory.py`：≥90%（纯逻辑）。
- `omni/providers/alibaba_omni.py` 映射逻辑：≥80%（mock 覆盖 recv loop 分支）。
- `session/omni_orchestrator.py`：≥85%（event pump 各分支 + 中断）。

---

## 5. 验收清单 (Definition of Done)

**第一阶段整体 DoD：**
- [ ] M0–M4 全部完成；`omni.enabled=true` + `alibaba_omni` 在真机（DashScope）跑通一轮完整对话（音频进 → 转写 + 文字 + 语音回 → listening）。
- [ ] client interrupt 与 server VAD barge-in 均正确回 `status:listening`。
- [ ] 图像帧 best-effort 送入（`supports_vision=True`），`RESPONSE_DONE` 的 `ai_text.image` 快照正确。
- [ ] Provider 握手失败切 fallback（可用第二个 alibaba 实例或 mock 验证；openai 因是 stub，作为 fallback 会抛 `NotImplementedError`，需在配置样例注释说明本阶段勿将 openai 设为唯一 fallback 用于真机）。
- [ ] `openai_realtime` 骨架可被 `create_omni` 实例化，`open()` 明确抛 `NotImplementedError`。
- [ ] `omni.enabled=false` 时 `make server-test` 零回归。
- [ ] 断线快照复用 `connection.py:_snapshot_session` 无需改动，重连以 `system_prompt + 历史` 重建 omni 会话。
- [ ] WS 协议 / `api-schema` / 客户端 SDK **零改动**（硬约束）。

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Qwen-Omni realtime 事件名/字段与设计表不符 | provider 映射错 | M2 先跑真机脚本 dump 真实事件，再定映射；映射集中在一处便于修正 |
| server VAD 质量不足（turn 判定过早/过晚） | 对话体验 | 预留 `commit_user_turn()` 手动兜底钩子（设计 §9.3），本期不实现但接口在位 |
| Qwen realtime 视频帧支持形态未定 | 视觉 best-effort 失效 | 本期仅"最近 K 帧图像 item"，不支持则 no-op，不阻塞主流程 |
| 不抽 `pipeline_common` 造成复制代码漂移 | 可维护性 | 复制处加 `TODO(phase2)`；纯函数优先 import 而非复制；第二阶段专项重构 |
| 输出 24kHz→16kHz 重采样质量损失（openai） | 音质 | 本期 openai 是 stub，不触发；alibaba 优先直配 16kHz 免重采样 |
| provider 名持久化列缺失 | 观测/分析 | 第一阶段先复用 `ctx.llm_provider` 记录 omni 名；新增 `omni_provider` 列 + migration 列为待决（设计 §9.1） |

---

## 7. 第二阶段预告 (Out of Phase 1)

- 抽取 `session/pipeline_common.py`，`DialogOrchestrator` 与 `OmniOrchestrator` 共享 send/persist/audio helper（消除 M3.4 的复制债务）。
- 完整实现 `openai_realtime` provider + 真机联调 + 24kHz 重采样。
- `ctx.omni_provider` 持久化列 + PG migration。
- 手动 turn 模式（`omni.turn_detection: manual`）复用 `TurnDetector` 兜底。
- 实时视频流（超越"最近 K 帧"）。
