# Video Image 处理：任意状态入 Buffer 修改与测试计划

> **状态**：已实施（内容已按 2026-10-06 代码校正；**§8.6.3 = 2026-10-09 修订，已实施**）
> **日期**：2026-09-05
> **范围**：`services/session/orchestrator.py`、`services/session/omni_mode.py`
> （原 `omni_orchestrator.py`）的视频帧接入路径
>
> **〔2026-09-11 更新〕** chat 链路的取图点已前移：由 `_start_thinking` 请求组装时
> 改为 **AF（ASR final）分发处理时刻**（`_snapshot_turn_images`，见
> [fd_pipeline_llm_v2.md](./fd_pipeline_llm_v2.md) §9.2/§6）。本文档中"取图只发生在
> `_start_thinking` 请求组装时"的表述已过时；"任意状态入 buffer"本身不变。
> omni 链路（§5.1/§6）不受影响。
>
> **〔2026-10-06 校正〕** 按当前代码逐条核对，以下引用已更新：
> - omni 链路于 bdfc622（2026-09-27）重构：`omni_orchestrator.py` 删除，引擎改为
>   `omni_mode.py` 的 `OmniModeEngine`；帧接入（解码/入 buffer/引擎投喂）收敛回
>   `orchestrator.py` `_handle_video`。旧 `tests/integration/omni/` 集成测试
>   （含 §5/§7 的回归锁）随重构删除——快照竞态三把回归锁已于 2026-10-06 在
>   **引擎单元层重建**（`TestResponseImageSnapshotLocks`，见 §5.1/§7.1/§7.2）；
> - v3 抓屏文本轮（`payload.text`，docs/android_screen_recording.md §5.2）引入
>   第三条取图路径：在 `_handle_video` 内调用 `_snapshot_turn_images` 起轮——§1.2/§6
>   中"取图只发生在 AF"的表述已按此限定（语音轮取图点不变）；
> - 测试改名（`test_listening_frame_still_processed` → `test_video_ack_in_listening`）、
>   SessionManager 路径行号、其余文档行号已就地修正。
>
> **〔2026-10-06 新增〕** §8 = `turn_id` 首帧直起轮 + `camera_facing=screen` 增强
> 设计——首轮已实施（方案 A，记录见 §8.6）；SDK 侧 turn_id 传递方式经 review 修订为
> 显式传参（Q8），**已实施（记录见 §8.6.1）**。
>
> **〔2026-10-09 修订〕** §8 首帧策略由"新会话首帧触发 / resume 首帧静默认领"改为
> **"按设备级 turn 变化触发"**（不区分 fresh/resume；无记录即触发；同轮不触发）——
> 修复重连瞬间开相机被 resume 静默认领吞掉（真机开相机 → 首句 16s）；设计见 §8.2.3
> 修订块，动机/证据/行为变化/边界见 §8.6.3。**已实施（2026-10-09）。**

## 1. 背景与目标

### 1.1 修改前行为

`_handle_video` 对视频帧做状态门控：**SPEAKING 状态到达的帧被丢弃**（不进
`latest_video_frames`，ACK 回 `processed=False`）；仅 IDLE / LISTENING / THINKING
状态的帧入 buffer。

问题：TTS 播报（SPEAKING）期间用户看到的画面被无条件丢弃，下一轮 LLM 请求只能使用
播报结束后到达的第一帧；若用户在播报期间打断（barge-in）并立即提问，请求携带的画面
偏旧。

### 1.2 目标

**任何状态（IDLE / LISTENING / THINKING / SPEAKING）到达的视频帧都进入 buffer。**

buffer 语义与取图/回图机制保持不变：

- buffer 为 `SessionContext.latest_video_frames`（deque），容量 `max_video_frames=1`
  （K=1），超出即逐出最旧帧 —— 任何时刻只保留**最新一张**；
- 语音轮取图只发生在 **AF（ASR final）分发处理时刻**（`_snapshot_turn_images`，
  〔2026-09-11 前移；原为 `_start_thinking` 请求组装时〕）：取出全部帧（K=1 即最新
  一张）、随后立即 `clear()`；〔2026-10-06〕另有 v3 抓屏文本轮（`payload.text`
  非空）在 `_handle_video` 内调用同函数快照并立即起轮；
- LLM reply 的 final ai_text 回带的是**AF 快照帧**（`image_frames[-1]`），不从 buffer
  重取 —— 快照后、流式期间新到帧不会污染本轮回复（归入下一轮）。

## 2. 修改内容

### 2.1 `services/session/orchestrator.py` — `_handle_video`

删除 SPEAKING 门控分支，存储路径不再区分状态：

```python
async def _handle_video(self, msg: Any) -> None:
    if not self._ctx:
        return
    # 任何状态都入 buffer（K=1 只保留最新一帧）：SPEAKING 期间到达的帧
    # 同样是下一轮请求的最新候选。取出与清空只发生在 AF 快照
    # （_snapshot_turn_images），保证送 LLM 的图 == 落 image/ 的图 ==
    # final ai_text 回带的图。
    processed = True
    if msg.payload.data:
        ...  # append + K=1 逐出 + 更新 _last_video_frame_time（逻辑不变）
    else:
        ...  # 空 payload 不入 buffer（逻辑不变）
    # ACK 逻辑不变
```

### 2.2 omni 链路的引擎投喂（原 `services/session/omni_orchestrator.py`）

同样删除 SPEAKING 门控。附带行为变化：**SPEAKING 期间到达的帧现在也会照常 feed
给 omni 引擎** —— 这符合 realtime 多模态 API 的用法（图像输入只更新模型视觉上下文，
不触发新响应），使模型看到的画面保持最新。

〔2026-10-06 校正〕omni 链路已于 bdfc622 重构：`omni_orchestrator.py` 删除，
帧接入（解码 → 入 buffer）与引擎投喂收敛回 `orchestrator.py` `_handle_video`；
投喂（`self._omni_engine.feed_image`）位于 v3 文本回合分支之后的独立段落
（不再是"同一分支"），投喂条件为 omni 激活 + 引擎存在 + `processed` + data
非空，无状态项——"任何状态 feed"不变。`omni_mode.py` 的 `OmniModeEngine` 只保留
feed 入口与事件泵。

### 2.3 注释修正（历史项）

- `services/tests/unit/session/test_image_history.py`：当轮将 docstring 中过时的
  例子 "SPEAKING-discarded frames" 改掉（SPEAKING 不再丢帧）。〔2026-10-06 校正〕
  该 docstring 已在 9517170 随"发言即入"语义整体重写，现不含 SPEAKING/空闲超时
  表述。

### 2.4 未改动的部分（明确边界）

| 机制 | 位置 | 说明 |
|---|---|---|
| K=1 buffer / 逐出 | `context.py`（`max_video_frames=1`），`orchestrator.py` `_handle_video` | 不变；〔2026-10-06〕omni 重构后解码/入队收敛为单点，不再有两个 `_handle_video` |
| 5s 空闲超时清 buffer | `orchestrator.py` run 主循环 | 不变，防陈旧画面（清"未被 AF 消费的残留帧"） |
| 取图 + clear | `orchestrator.py` `_snapshot_turn_images`（AF 分发时刻） | 〔2026-09-11 前移〕原 `_start_thinking` 组装时；〔2026-10-06〕v3 抓屏文本轮亦调用同函数（`_handle_video` 内） |
| final ai_text 回带快照帧 | `orchestrator.py` `_start_thinking`（`image_frames[-1]`） | 来源改为 AF 快照帧（语义不变：仅成功路径发送） |
| 音频侧 SPEAKING 门控 | `orchestrator.py` `_handle_audio` | **不涉及**：barge-in / THINKING 丢音频是独立设计 |

## 3. 影响面

### 3.1 协议 / 客户端

- `ai_video_ack.payload.processed` 语义变化：`processed=False` 仅剩畸形
  base64 一种情况（§5.2 / §7.3：解码失败不入 buffer）；空 payload 仍回
  `processed=True`，与修改前非 SPEAKING 分支一致。
- Android SDK 对 `processed=false` 仅做 debug 日志
  （`apps/android/LivaChatSDK/.../sdk/internal/session/SessionManager.kt` L370-371；
  ack 连续性检查见同文件 L61 注释 "processed=false still acks"）—— **无兼容性
  风险**，无需客户端改动。

### 3.2 服务端行为

- SPEAKING 期间帧进 buffer 后，若用户在 SPEAKING 中 barge-in 并触发新一轮
  `_start_thinking`，请求携带的将是打断时刻的最新画面（修改前为更旧的一帧或空）。
  这正是本次修改的目的。
- 内存无变化：K=1 上限不变，单帧生命周期仍受 5s 空闲超时约束。

### 3.3 需要后续同步的文档（本次未改）

〔2026-10-06 核对：一处过时表述仍在〕

- `docs/server_recording_design.md` L284："SPEAKING 期间被对话逻辑丢弃的帧" 表述
  已过时（recording tap 仍记录全部上传帧，不受影响）。

## 4. 测试计划

### 4.1 自动化测试（已实施并通过）

**新增**：

| 测试 | 文件 | 断言 |
|---|---|---|
| `test_video_ack_in_speaking` | `tests/integration/orchestrator/test_video_frame_processing.py` | SPEAKING 状态发帧 → ACK `processed=True` 且帧入 buffer（frame_id=7 为最新） |
| `test_video_image_routed_to_engine` | `tests/integration/orchestrator/test_omni_mode_switch.py` | 〔2026-10-06 校正〕omni 模式下推帧 → 帧投喂引擎（`feed_image`）。原 `tests/integration/omni/test_omni_video_frame.py::test_speaking_frame_enters_buffer_and_feeds_omni` 已随 bdfc622 引擎重构删除；快照竞态三把回归锁已在同文件 `TestResponseImageSnapshotLocks` 重建（§5.1/§7） |
| `test_video_ack_in_listening` | `test_video_frame_processing.py` | 回归：LISTENING 行为不变（原 `test_listening_frame_still_processed`，已改名） |

**回归**：全量 `pytest`（services）—— **1079 passed, 0 failed, 1 skipped**；
其中 `test_image_history.py`（取图/挂图/快照语义）、`test_translate_mode.py`
（translate 不带图）、omni 集成测试全部通过，确认取图与回图机制未受影响。

运行方式：

```bash
cd services
pytest tests/integration/orchestrator/test_video_frame_processing.py \
       tests/integration/orchestrator/test_omni_mode_switch.py -q
pytest -q   # 全量回归
```

### 4.2 手动 / 端到端验证（建议在 dev 环境执行）

1. **SPEAKING 入 buffer**：开启视频，AI 播报（SPEAKING）期间持续推帧 →
   服务端 debug 日志不再出现 `video frame not stored (state=speaking)`；
   ACK 全部 `processed=true`。
2. **核心场景 — 打断带新图**：AI 播报中将摄像头对准新物体并打断提问
   "这是什么" → LLM 请求携带的是打断时刻的最新帧；final ai_text 回带的图
   与该请求提交的是同一张（客户端聊天气泡图与回答内容一致）。
3. **K=1 逐出**：连续推帧，任意时刻 buffer 只保留最新一帧
   （日志 `video frame evicted (buffer full)` 按预期出现）。
4. **空闲清空**：停止推帧 5s 后提问 → 请求不带图（`video buffer cleared after
   idle timeout` 日志出现）。
5. **omni 链路**（`session.change(mode=omni)` 进入 omni 模式）：SPEAKING 期间
   推帧 → omni 引擎收到 `feed_image`，模型后续回答基于最新画面。

## 5. Review 修复（2026-09-05，第二轮）

对"任意状态入 buffer"改动做回归 review，发现并修复 2 个严重问题。

### 5.1 🔴 omni 回图违反"请求时引用"不变量（本次改动引入的回归）

**问题**：omni 引擎（当时在 `omni_orchestrator.py`，现 `omni_mode.py`）原在
`RESPONSE_DONE`（回复时刻）从 `latest_video_frames[-1]` 取图并 clear。改动前
SPEAKING 丢帧，响应播放期间 buffer 冻结，取到的恰是响应开始前最后一帧（= 模型
实际看到的），与 chat 链路快照语义**偶然一致**；改动后 SPEAKING 期间新帧持续
入 buffer，`RESPONSE_DONE` 取到的是**响应播放中途**到达的帧 —— 模型生成该回答
时并未见过它，回带的图与回答内容不匹配。

**修复**（对齐 chat 链路"请求边界（AF 时刻）快照"语义）：

- 快照点前移到 **`RESPONSE_STARTED`**（响应边界：此刻 `buffer[-1]` == 本响应
  最后一次 `feed_image` 的帧，即"请求图"），存入 `self._response_image_payload`；
  总是赋值（空 buffer → None），防上一响应陈旧快照泄漏；
- `RESPONSE_DONE` 回带该快照，**不再读/清 buffer**——响应期间到达的帧已 feed
  给模型，是下一响应快照的合法候选（K=1 + 5s 空闲清空约束不变）；
- `interrupt()` / `_auto_interrupt` → `_cancel_response_and_reset`、`close()` →
  `_reset_turn` 复位快照（响应取消/引擎关闭，快照失效）。〔2026-10-06 校正：
  方法名按 `omni_mode.py` 更新，原名 `_do_interrupt` / `_reset_turn`〕
- 顺带清理：`__init__` 中从未使用的死字段 `_last_image` 由
  `_response_image_payload` 取代。

回归锁：`test_reply_carries_request_boundary_snapshot` —— STARTED 与 DONE
之间注入新帧 F2，断言 final ai_text 回带 F1（快照）而非 F2，且 DONE 后 buffer
仍持有 F2。〔2026-10-06：原锁随 `tests/integration/omni/` 于 bdfc622 删除后，
已在引擎单元层**重建**——`tests/integration/orchestrator/test_omni_mode_switch.py`
`TestResponseImageSnapshotLocks`（队列驱动 fake stream + 可控时序；含本节与
§7.1/§7.2 三把同名锁）。已做变异验证：三处旧 bug 各自复现均被对应锁捕获。〕

### 5.2 🔴 畸形 base64 帧崩掉整个会话（健壮性，暴露面被本次改动扩大）

**问题**：当时两个 `_handle_video`（chat / omni 各一）的 `base64.b64decode()`
无防护，`run()` 主循环的 `_dispatch` 无 try/except —— 一帧非法 padding 的 data
（`binascii.Error`）直接杀死会话 dispatch 循环。改动前 SPEAKING 的畸形帧被门控
吞掉，改动后全状态可达，暴露面扩大。

**修复**：decode 包 try/except，失败时 log warning、不入 buffer（omni 侧也不
feed）、ACK `processed=False`（客户端对 False 仅 debug 日志，无兼容风险）。

〔2026-10-06 校正〕omni 重构后解码收敛为 `orchestrator.py` `_handle_video`
单点，omni 投喂复用同一 `jpeg_bytes`；畸形帧 `processed=False` 同时使 omni
投喂条件不成立，单点天然覆盖两侧。

回归锁：

- `test_invalid_base64_frame_does_not_kill_loop`（orchestrator 集成）：畸形帧 →
  `processed=False` + buffer 空 + 后续正常帧仍被处理（主循环存活）；现覆盖坏
  padding（`"A"`）与可解码垃圾（`"A*B=C"`，validate=True 拒绝）两种变体；
- `test_invalid_base64_not_stored_and_survives`（omni 集成）：〔2026-10-06
  校正〕已随 `tests/integration/omni/` 于 bdfc622 删除；omni 侧行为由单点解码
  覆盖（见上）。

### 5.3 修复后全量回归

`pytest`（services）：**1082 passed, 0 failed, 1 skipped**。

## 6. 两条链路的不变量终态

| 环节 | chat 链路（orchestrator） | omni 链路（omni_mode.py `OmniModeEngine`） |
|---|---|---|
| 入 buffer | 任何状态（含 SPEAKING），K=1 | 任何状态（含 SPEAKING），K=1 + 帧同时投喂引擎（`_handle_video` 尾段，投喂条件无状态项） |
| 取图/快照点 | **AF 分发处理时刻**（`_snapshot_turn_images`，快照 + 立即 clear）〔2026-09-11 前移〕；〔2026-10-06〕v3 抓屏文本轮在 `_handle_video` 内同函数快照 | `RESPONSE_STARTED`（响应边界快照，不清 buffer） |
| 提交给模型 | 快照同一份 data_url 随 history user 条目入请求（fd_pipeline_llm_v2 §9.2/§9.4） | 持续 `feed_image`（buffer[-1] == 最后 feed 的帧） |
| reply 回带 | AF 快照帧（`image_frames[-1]` 经暂存传入） | `RESPONSE_STARTED` 快照引用（`_response_image_payload`） |
| 失效路径 | interrupt 取消 pipeline（回图帧随任务局部变量丢弃）；AF 暂存由轮次重置块"取走 + 重绑"、session.start 重置管理〔2026-10-06 校正：原表述"_do_interrupt 清 AF 暂存"与代码不符〕 | `interrupt()` / `_auto_interrupt` → `_cancel_response_and_reset`、`close()` → `_reset_turn` 显式复位 |

## 7. Review 修复（2026-09-05，第三轮：竞态与输入校验）

### 7.1 🔴 `_cancel_response_and_reset` 快照清理晚于 `cancel_response` 的 await，误杀新响应快照

〔2026-10-06：该方法现位于 `omni_mode.py`，原名 `_do_interrupt`（旧
`omni_orchestrator.py`），omni 重构后迁移并改名；修复本身仍在。〕

**问题**：快照复位 `self._response_image_payload = None` 原位于 interrupt
序列尾部、多处 await（`interrupt()`、`cancel_response()` WS 往返）之后。事件泵
是独立 task：barge-in 后 R2 的 `RESPONSE_STARTED` 可能在 `cancel_response` 等待
期间已赋好新快照（`_response_epoch` 同步取新 epoch），interrupt 恢复后的尾部
清理把 R2 快照抹成 None → R2 的 delta 全部通过 epoch 门控正常播放，但
`RESPONSE_DONE` 回带 `image=None` —— 打断后那一轮丢失图像气泡。

**修复**：快照清理上移到 `_cancel_response_and_reset` 最前、任何 await 之前
（同步完成；现代码该行带注释）。此时语义为"清的是被打断的旧响应快照"；新响应
的 STARTED 快照必然在其后赋值，永不被误杀。

回归锁：`test_interrupt_cleanup_does_not_kill_new_response_snapshot` —— 用
gate 卡住 `cancel_response`，在其间让事件泵处理 R2 STARTED，再放行 interrupt，
断言 R2 的 DONE 仍回带 F1 快照。〔2026-10-06：已随 §5.1 同批在
`TestResponseImageSnapshotLocks` 重建（同名）；gate 卡 `cancel_response` 的
WS 往返窗口由 fake stream 的 `cancel_gate` 精确复现。〕

### 7.2 🟠 `RESPONSE_STARTED` 分支内快照在 await 之后才取

**问题**：分支内先 `transition` → `await send_status(...)` → … → 最后才
`_snapshot_latest_frame()`。`send_status` 让出控制权期间，dispatch 循环可能把
响应开始后才到达的帧 append+feed 进 buffer，被事后快照错捕 —— 与 §5.1 同类
泄漏，窗口从"整个响应期"缩小到"分支内几个 await"。inbound dispatch 与事件泵
是两个队列，无全局序。

**修复**：`_snapshot_latest_frame()` 提为分支第一条语句（`_response_epoch`
赋值后、首个 await 前；现代码该行带注释）。快照只含"事件泵开始处理 STARTED
之前已 feed"的帧——这是服务端能做到的最精确的"请求图"边界。

回归锁：`test_snapshot_precedes_branch_awaits` —— 在 thinking status 出站时
（分支内首个 await 点）注入 F2，断言回图仍为 F1。〔2026-10-06：已随 §5.1 同批
重建（同名）；出站闸（`_GatedThinkingHandler`）精确复现该注帧窗口。〕

### 7.3 🟠 base64 只拦"会抛异常"的畸形帧，拦不住"可解码的垃圾"

**问题**：`b64decode(data)` 默认 `validate=False`，非字母表字符被静默丢弃——
`"A*B=C"` 这类输入解码成垃圾字节照样入 buffer / feed omni / 快照回带，ACK 仍
`processed=True`，与"`processed=True` = 帧被采用"的契约不符。

**修复**：当时两处 `_handle_video` 统一 `b64decode(data, validate=True)`，非法
字母表与坏 padding 都走既有 except 分支（`processed=False`）。〔2026-10-06
校正：omni 重构后收敛为 `orchestrator.py` 单点。〕

**分层事实**（修复时查证）：生产路径上字母表非法**首先**被协议层 pydantic
`Base64String` 拦截（`ClientVideoPayload.data` 构造即拒绝，实测 `"A*B=C"` 在
协议层 ValidationError）；坏 padding（`"A"`）能通过协议层，由 `_handle_video`
的 except 兜住。`validate=True` 因此是点用处的统一兜底（覆盖 padding + 任何
非 pydantic 构造路径），客户端 `Base64.NO_WRAP` 标准字母表不受影响。
测试以赋值绕过协议层校验，直达兜底层断言。

### 7.4 修复后全量回归

`pytest`（services）：**1084 passed, 0 failed, 1 skipped**。

## 8. 功能增强设计（2026-10-06 定稿）：turn_id 首帧直起轮 + camera_facing=screen

> **〔2026-10-17 增补（v7）〕** `camera_facing` 再新增第四值 **`"gallery"`**（图库照片；非实时摄像头），
> 设计与发布顺序见 `docs/camera_enhance.md` §11；服务端先行约束与本节 `screen` 相同（旧服务端会按
> 非法枚举丢帧 1006）。本节其余历史设计不变。
>
> **状态**：设计定稿（Q1-Q8 见 §8.3）→ 首轮已实施（方案 A，记录见 §8.6）→
> **SDK 侧 turn_id 传递方式经 review 修订为显式传参（Q8），已切换实施（记录见 §8.6.1）**。
>
> 需求（定稿口径）：
> 1. video 消息 `camera_facing` 增加第三个值 `screen`，屏幕抓图使用；
> 2. video 消息新增字段 `turn_id`（integer）：每次**打开摄像头**或**切换前后摄像头**，
>    turn_id 增长；
> 3. 服务端收到 video 帧，若 `turn_id` 变化 ⇒ 新 turn 首帧 ⇒ **立即提交 LLM/TTS
>    pipeline**，不等 ASR；带 `payload.text` 的 video 帧维持 v3 文本回合现状不变；
>    `turn_id` 未变化则完全走现有逻辑；
> 4. 分析与设计以当前 codebase 为准（下述均有行号取证）。
>
> 补充定稿（2026-10-06 review 反馈）：
> 5. 服务端**仅凭 turn_id** 判定是否执行首帧处理；**没有 turn_id（或为 0）的帧与
>    现有处理逻辑完全一致**（新分支不可达）；
> 6. 直起轮 user 行 content 用**统一标记**（开启/切换不区分）。

### 8.1 现状取证

#### 8.1.1 协议层

| 事实 | 位置 |
|---|---|
| `CameraFacing` 枚举仅 `["front","back"]` | `packages/api-schema/common.json` `$defs.CameraFacing` |
| `video` required 7 字段 + 可选 `text`（≤512）；`additionalProperties:false` | `packages/api-schema/c2s/video.json` |
| 生成物为 Pydantic `extra="forbid"` / Kotlin 数据类；Python 枚举为 `StrEnum` | `packages/sdk-python/.../generated/protocol_models.py:93`（CameraFacing）等 |
| 重生成：`python scripts/generate_python_api.py`（默认 in/out）；`make generate-android-api`（CI 校验 `check-android-api-generated`） | `scripts/generate_python_api.py:1181-1210`、`Makefile:1-8` |
| 协议文档副本：`docs/ws-protocol-design.md` §2.2.5（video）、§2.3.7（ai_video_ack） | 同文档 L245、L524 |

含义：① 增加枚举值 / 可选字段都是**服务端侧向后兼容**扩展（旧客户端不发送即不感知）；
② 新客户端 + 旧服务端不兼容（`extra=forbid` + 枚举严格校验会拒帧）⇒ 发布顺序
**服务端先行**。

#### 8.1.2 客户端（Android App + Kotlin SDK）

上行链路（现状，单一出口）：

- 摄像头：`CameraController` analyzer（暖机 1000ms、节流 900ms，`CameraController.kt:284-287`）
  → `processFrame` → `sdk.pushVideoJpeg(jpeg, w, h, PORTRAIT_0, facing)`，facing 由
  `_isFrontCamera` 决定（`CameraController.kt:200-210`）→ SDK
  `videoFrameIdGen.incrementAndGet()` 生成 frame_id（`LivaChatSDK.kt:282`）→
  `VideoSendPipeline` → `SessionManager.sendVideo`。
- 屏幕抓图：`ScreenCaptureController.grabAndPush`（浮窗点按，`ScreenCaptureController.kt:605-638`）
  → `sdk.pushVideoJpeg(..., "front" 占位, text = context.getString(R.string.conv_image_action_text))`（2026-10-27 已实施）；
  **`camera_facing` 恒传 "front" 是"协议枚举无 screen"的临时占位**（代码注释 D4，
  `ScreenCaptureController.kt:481-483`）。2s 定时上行（tick 路径）已停用：
  `AUTO_TICK_ENABLED = false`（`:917`，代码与单测保留）。
- SDK 映射：`when (cameraFacing) { "back" -> BACK; else -> FRONT }`
  （`LivaChatSDK.kt:182`）——新增 `screen` 必须显式加分支。
- 可选字段先例：`ClientVideoPayload.text: String? = null` + encodeDefaults=false
  不落线（`LivaChatSDK.kt:184-185`）。
- `videoFrameIdGen` 为 SDK 实例级 AtomicLong（`LivaChatSDK.kt:145`），
  **跨会话/重连不重置**（`SessionManager.reset()` 只复位 reorderBuffer/seqGenerator）。

摄像头生命周期（turn 递增点的判据事实）：

- 开/关：`ConversationViewModel.toggleCamera()`（`:708-730`）→ UI 驱动
  `bindCamera()` → `CameraController.bind()`；`bind()` 内部先 `unbind()`，但入口
  捕获 `wasOn` 保留状态（`CameraController.kt:79-94`）。
- 前后切换：`switchCamera()` 只翻转 `desiredFrontCamera`（`:732-736`），
  经 cameraResetCount → 重绑生效。
- **关键事实（决定递增点选型）**：离开会话页 onDispose 会调 `unbindCamera()`
  （`ConversationScreen.kt:299-301`，仅转发 `CameraController.unbind()`）复位
  `_isCameraOn`；再次进入时若发生自动重绑，`bind()` 看到的仍是"off→on"——
  **"bind 内 wasOn / 镜头比较"无法区分『用户开相机』与『页面重进/回前台的自动
  重绑』** ⇒ 递增判据不可放在 `bind()` 内部（§8.2.2 因此改为**用户动作点驱动**）。
- `autoCameraOnJoin`：进会话页自动开相机（`ConversationViewModel.kt:288-296`），
  属用户已开启的设置项（Q3 语义：自动开启同样触发首帧回合）；重进页面时是否重发
  该分支取决于 VM 作用域——重发 = 再次自动开相机 = 再次触发，语义自洽。
- 互斥：开录屏自动关相机、开相机自动停录屏（`:671-682`、`:709-713`），
  两路不会并发推帧。

#### 8.1.3 服务端

`_handle_video`（`services/session/orchestrator.py:1955-2137`）现状四段：

| 段 | 行号 | 内容 |
|---|---|---|
| ① 解码 + 入 buffer | :1962-2000 | 任何状态入 K=1 buffer（validate=True 解码兜底）；ACK `processed` 语义所在 |
| ② v3 文本回合 | :2002-2080 | `payload.text` 过门控（非空 + ≥ min_chars + 模式适用）→ 当场快照 → 三件一体 → 条件中断 → `_start_thinking(text, image_frames=frames)` |
| ③ omni 投喂 | :2082-2097 | `_omni_active` 时 `_omni_engine.feed_image`（视觉上下文） |
| ④ avdetector | :2099-2124 | full 模式且无 text：帧入缓存 + 触发检查（要求 pipeline 空闲 + LISTENING + 未在说话 + 冷却） |

可直接复用的既有机制：

- **起轮原语** `_spawn_pipeline`（:2585）：写入前同步取消插队者（[P6.8] 防孤儿）。
- **状态机约束**（`state_machine.py:15-20`）：`SPEAKING→THINKING`、`THINKING→THINKING`
  非法 ⇒ 起轮前必须先 `_do_interrupt`（v3 同款条件，:2059-2063）。
- **形态先例：avdetector 主动回合**（:961-1099）——"无用户文本、仅图 + 系统注记"
  的完整形态已存在（仅 payload 组织方式参考；检测实现位置见 §8.2.3"实现位置"）：
  - user 行落三件一体（content=`【环境事件】desc`、ref_id=`avd-…`、kind="asr"、
    `_turn_user_record` 回滚句柄）；
  - `_start_thinking("", image_frames=…, turn_token=ref_id, event_note=…)`
    （event_note 并入 system prompt）；
  - 门控纪律：[P5] 请求必须以 **user 消息结尾**（2026-10-03 真机"咳嗽复读"事故的
    根治）；[P6] 危险在飞保护（危险且非危险回合 → 抢占；危险在飞 → 放弃）。
- **快照/回图**：`_snapshot_turn_images`（:2139，快照 + 立即 clear）；final ai_text
  回带 `image_frames[-1]`（:1511-1525）——视觉回合天然复用。
- **状态帧理由**：`_start_thinking` 统一发 thinking/`asr_final`（:1455-1456）——
  v3 起轮亦如此（非 ASR 起轮复用无协议风险）。
- **会话边界**：`_handle_session_start` 重置块（`:496-507`，2026-10-09 行号复核）；
  `resume_id = msg.payload.session_id`（`:531`）；`manager.py` 注（`manager.py:74-82`）：
  去 Redis 后 `restored` 恒 False，恢复只以 resume_id 延续 session。
- **AF 对在飞回合的接管**：AF 到达按 `was_barge_in`/meaningful 走
  `_cancel_pipeline` →（SPEAKING 时）`_do_interrupt` → 新 `_spawn_pipeline`
  （:1371-1415）——换装机制现成，视觉回合无需新增接管代码。

### 8.2 设计

#### 8.2.1 协议

1. `common.json` `$defs.CameraFacing.enum`：`["front","back"]` →
   `["front","back","screen"]`（描述补"screen = 屏幕采集"）。
2. `c2s/video.json` `payload` 新增**可选**字段（不加进 required）：

   ```json
   "turn_id": {
     "$ref": "../common.json#/$defs/UInt32",
     "description": "采集轮标识（有效值从 1 起）。客户端每次打开摄像头、切换前后摄像头或开始录屏时递增；服务端检测到变化时把该帧视为新轮首帧，立即起 LLM/TTS 回合。缺省或 0 = 未启用（等同旧客户端，行为不变）。"
   }
   ```

3. 重生成：`python scripts/generate_python_api.py && make generate-android-api`；
   同步 `docs/ws-protocol-design.md` §2.2.5 字段表。
4. 生成后：Python `turn_id: UInt32 | None = None`；Kotlin
   `@SerialName("turn_id") val turnId: Long? = null`。

#### 8.2.2 客户端设计

〔2026-10-06 修订（review 定稿）：turn_id 改为**显式传参**——App 层维护状态、逐帧交给
SDK，SDK 直通落线不做任何逻辑；替代原 `beginVideoTurn()` 内部计数方案（Q8）。依据：
`frame_id` 是**无状态**唯一号（SDK 自取即可）；`turn_id` 是**有状态**的采集轮标识，
状态属于 App 层（边界语义全是 App 动作），不应在 SDK 内驻留。参数默认
`turnId = 0`（= 未启用，旧行为）：遗忘传参只导致该路径功能不激活（fail-inert），
不会误触发；同仓 mock 等无采集轮语义的调用方零改动。〕

〔2026-10-06 修订·P2-1：前后切换的递增点由动作点 `switchCamera()` **下沉到
`CameraController.processFrame()` 的 facing 变化检测**——与切换后首帧严格同帧递增。
原实现的问题：动作点在 tap 时刻递增，而重绑由重组异步驱动（tap→LaunchedEffect→
`bind()`，窗口 50–300ms），窗口内旧镜头 analyzer 照常推帧、在 push 点读到**新** id
上行 → 服务端在切换前画面上触发"新视野评论"，且新镜头首帧同 id 不再触发——该次切换
评论整体错位不自愈（900ms 节流下命中率约 5–30%）。下沉后：旧镜头帧持旧 id（正确），
新镜头首帧同帧取新 id（零竞态）；`shouldBumpTurn(lastPushedFacing, facing)` 纯函数判据
——首帧（null）不比较、同镜头重绑天然不递增（正是 §8.1.2 要求的性质）。〕
〔残留（既有，非本次引入）：tap→重绑窗口内旧镜头帧仍会进 K=1 buffer，若恰有 AF 在
该窗口快照会取到旧镜头帧——窗口 ~百 ms 且帧已正确携带旧 turn id，不在 P2-1 范围。〕

**App（`dev.livachat.app.media`）**

- 新增单例 `CaptureTurnCounter`：`next(): Long`（递增并缓存）/ `val current: Long`。
  **进程级**（跨会话/重连不重置；服务端只判"变化"，绝对值无语义）；计数从 1 起
  （**0 保留为"未启用"哨兵**，见 §8.2.3 触发规则 2）。
- 递增时机（用户动作点驱动，唯一四处；语义与 Q2/Q3 定稿不变）：

  | 事件 | 调用点（`turnCounter.next()`） |
  |---|---|
  | 用户开相机 | `ConversationViewModel.toggleCamera()` 打开分支（`isCameraOn=true` 置位处） |
  | 前后切换 | **`CameraController.processFrame()` 的 facing 变化检测**（与切换后首帧严格同帧递增；动作点 `switchCamera()` 只翻转期望镜头，不递增——P2-1） |
  | 自动开相机 | `autoCameraOnJoin` 分支（Q3 语义含自动开启） |
  | 录屏开始 | `ScreenCaptureController.start()`（Q2） |

- **不递增**：任何**自动重绑**（回前台、页面重进/生命周期驱动、同镜头重绑、权限恢复）
  ——同镜头 facing 不变，首帧检测天然排除；`bind()` 内仍无任何判定（§8.1.2 关键事实：
  bind 内无法区分用户动作与自动重绑——检测放在**实际帧的 facing 层**，与 bind 不同层）。
  开相机后首帧若与上次推流镜头不同（如默认镜头变更后重开），会叠加一次递增——无副作用
  （服务端只判"变化"，绝对值无语义）。
- 关相机、bind 失败、点按抓屏不递增（点按帧带 text 走 v3 流程，行为不变——
  turn_id 仅作采集上下文标识随帧携带）。
- 打开时先 bump、bind 若失败无帧流出——多一次无效递增无副作用（服务端只判
  "变化"）。
- push 调用点（`CameraController` 相机帧 / `ScreenCaptureController` 两处屏幕帧）
  逐帧传 `turnCounter.current`。

**SDK（`LivaChatSDK`）**

- `pushVideoJpeg` / `pushVideoBitmap` 新增参数 **`turnId: Long = 0`**（置于
  `cameraFacing` 之后、`text` 之前；`text` 保持可选）——SDK 仅把该值放进 payload
  （`buildVideoPayload` 直通），**无任何内部状态/逻辑**；`VideoSendPipeline` 与
  `onEncoded` 同步透传。默认 0 会以显式 `"turn_id": 0` 落线（SDK
  `encodeDefaults=false` 只省略等于默认 `null` 的场合）——**由服务端把 0 视同
  缺省**（§8.2.3 规则 2），语义即"回退旧行为"。
- mock app（`LivaChatAppMock`）无采集轮语义：不传（默认 0），零改动。
- 屏幕帧 `camera_facing` 改传 `"screen"`：tick 路径与点按路径两处；SDK 映射
  `mapCameraFacing` 加 `"screen" -> CameraFacing.SCREEN`。

#### 8.2.3 服务端设计

**状态字段**：`DialogOrchestrator.__init__` 增 `self._last_video_turn_id: int | None
= None`；`_handle_session_start` 重置块（`:496-507`）以**设备级记忆**初始化（见下）。
〔2026-10-09 修订：原 `_seed_video_turn_on_first_frame` 开关随新策略删除；
`SessionManager` 增设备级记忆 `record_video_turn()` / `last_video_turn()`。〕

**会话首帧 / turn 变化策略（2026-10-09 修订，取代原"新会话首帧触发 / resume 静默
认领"定稿）**：

- 判定基准是**设备上次视频 turn**（`SessionManager` 进程内设备级记忆，跨连接/跨
  会话延续——orchestrator 每连接一个，自身字段不跨连接）：`_handle_session_start`
  以 `last_video_turn(device_id)` 初始化 `_last_video_turn_id`（无记录 → None）；
- 变化即触发，**不区分 fresh / resume**：
  - `turn_id != _last_video_turn_id`（含无记录）：触发首帧直起轮（重连瞬间开/切
    相机不再被吞；无记录 = 旧"新会话首帧"语义，覆盖 autoCameraOnJoin）；
  - 相同：不触发（flap 重连后画面延续不是用户动作——原 resume 静默认领的目标
    保持）；
- 每次变化把新值写回设备记忆。

〔2026-10-09 修订动机：原策略在"WS 断线重连与用户开相机同一时刻发生"时，resume
静默认领会把真实用户动作一并吞掉——实测开相机到首句响应 ~16s（对照非 resume
会话 2.3s）；证据与行为变化见 §8.6.3。〕

**实现位置（2026-10-06 定稿：orchestrator 直做，不走 avdetector）**：首帧直起轮由
`orchestrator.py` `_handle_video` 新增第 ②.5 段直接完成（同 v3 文本回合的形态）——
不放 avdetector 的理由：① avdetector 可被禁用（`app/application.py:104`
`avdetector.enabled` 才创建服务；`_ensure_av_monitor` 服务缺失即 no-op，
`orchestrator.py:955`）且仅 full/chat 模式挂载——放进去功能会随配置/模式静默消失；
② avdetector 视频触发是"环境推断"语义（独立节拍 `video_check_interval_s` + 冷却/
复检，`av_detector.py:250-263`），会把"用户开/切相机"的**显式信号**吞掉或延迟。
§8.1.3 的 avdetector 仅为"无文本仅图 + 系统注记"回合的**形态先例**（payload 组织
方式），不是检测实现载体。

**触发规则**（`_handle_video` 新增第 ②.5 段；位置：v3 分支之后、omni 投喂之前）
——全部满足才触发：

1. `processed and msg.payload.data`（帧实入 buffer；空 data/畸形帧不参与）；
2. `msg.payload.turn_id` **非空且非 0**（0 = 未启用哨兵，等同缺省；SDK 默认值会以
   显式 `"turn_id": 0` 落线——`encodeDefaults=false` 只省略等于默认 `null` 的场合，
   见 §8.2.2）且 `turn_id != self._last_video_turn_id`（基准=设备级记忆，含无记录
   首帧；见上）；
   **没有 turn_id / 为 0 的帧 = 与现有处理逻辑逐位一致**——新分支不可达，定稿口径；
3. `not text`（`text` = `payload.text` **strip 后**；非空——无论是否达 min_chars
   ——**一律不改**）：严格保持"有 text 的帧与现状一致"；text 帧同样记录 turn id
   （避免"text 帧吃掉 turn、下一帧又触发"）；
4. `_pipeline_mode not in ("translate","transcribe","asr_only","omni")`
   （与 v3 门控同元组；omni 下摄像头帧继续只作视觉上下文投喂；asr_llm 下随模式
   语义只有 LLM 无 TTS）；
5. 危险在飞豁免：`busy`（≜ pipeline task 在飞且未 done，与 av 门控同款）
   `and _pipeline_danger` ⇒ 放弃本次触发（不消费帧；turn id 照记，"首帧机会
   一次不重来"）；
6. `frames = self._snapshot_turn_images()` 非空（enable_video=false 等 → 空 →
   log 跳过）。

**turn id 记录规则**：每次"帧实入 buffer"（processed + data + turn_id 非空**且非 0**）
即更新 `_last_video_turn_id` 并于变化时写回设备记忆（`record_video_turn`；与是否
触发解耦，门控不触发时同样记录）；触发判定读更新前的旧值。

**动作顺序**（触发帧；与该段既有注释纪律对齐：append→snapshot 同一同步段、无
await；分支整体 try/except 防穿出 `_dispatch`）：

```python
# turn_id: None（缺省）/ 0（未启用哨兵） 均为假值 → 新分支不可达
if processed and msg.payload.data and turn_id:
    changed = (turn_id != _last_video_turn_id)  # 基准=设备记忆；变化时写回记忆
    if (changed and not text and mode-gate ok
            and not (pipeline_busy and self._pipeline_danger)):
        frames = self._snapshot_turn_images()          # 同步段，紧随入 buffer
        if frames:
            urls = _to_data_urls(frames)
            ref_id = f"cam-{msg.payload.frame_id}"
            # 三件一体（先落盘后中断，与 AF/v3/avd 同顺序）
            self._history.add_message("user", "[相机画面]", images=urls)
            self._chat_store.on_message(..., kind="asr", role="user",
                                        content="[相机画面]", images=urls,
                                        ref_id=ref_id, ts_ms=ts_ms)
            self._turn_user_record = {...}             # D5c 回滚句柄，token=ref_id
            self._recording_sink.on_chat_text(role="user", text="[相机画面]",
                                              frames=frames, ref_id=ref_id, ...)
            if self._ctx.state in (THINKING, SPEAKING) or pipeline in flight:
                await self._do_interrupt("video_turn")
            self._spawn_pipeline(
                self._start_thinking("", image_frames=frames, turn_token=ref_id,
                                     event_note=NOTE),
                danger=False)
```

**user 行 content 与系统注记（2026-10-06 定稿：开启/切换不区分，统一标记）**：

- content = 统一标记 `[相机画面]`。理由：服务端从 turn_id / camera_facing 无法判定
  是"开启"还是"切换"（开启用默认镜头、切换必然换 facing，两者都可能出现任意
  facing），定稿不做区分；chatlog 可见、LLM 请求以 user 行结尾（avdetector [P5]
  纪律）。统一串最终可替换（如 `[相机开启]`），不影响机制。
- `event_note`（定稿）：「【实时画面·系统注入】用户刚开启了摄像头或切换了镜头，
  最新画面见上一条用户消息。请立即用一句自然、简短的话回应画面内容，或主动关心/
  提示；不要复述、不要延续之前的对话内容。」（措辞同时覆盖两种情形，不依赖区分）
- 状态帧沿用 thinking/`asr_final`（v3 先例，零协议改动）。
- final ai_text 回带 `frames[-1]`（现有机制；客户端气泡图与回答内容一致）。

**与既有机制的交互**：

| 机制 | 交互结果 |
|---|---|
| avdetector 触发 | 直起轮同步占住 `_pipeline_task` ⇒ 同帧 av 触发条件（pipeline 空闲）不成立 ⇒ 不重复起轮；帧照常入 av 帧槽（K=1，无 text；docs/fd_pipeline_avdetector.md §10 条目 27）。视觉回合结束后，相机长开的后续帧仍按 av 自有节拍（`video_check_interval_s`）触发环境检查——既有行为，不做联动抑制（两路语义不同：环境主动 vs 用户显式） |
| v3 文本回合 | text 帧优先走 v3；turn id 照记（触发规则 3） |
| AF 回合 | 视觉回合在飞时 AF 到达 → 现有换装（cancel/interrupt + 新起轮）接管；接受"视觉回合被文本回合取代" |
| omni | 分支互斥；omni 下 turn id 只记录不触发 |
| K=1 / 5s 清理 | 不变；首帧被快照消费，后续同 turn 帧照常缓冲供 AF 用 |
| ack | 不变（`processed` 仍表示"帧入 buffer"） |
| 录屏点按抓屏 | 带 text → v3 流程不变；facing 改 `"screen"` 仅字段语义修正 |

**已知边界行为（随定稿接受；实施与验收时按此对齐预期）**：

- **打开后立即口述**：视觉回合即刻起轮（"不等 ASR"口径）；随后 AF 到达由换装机制
  接管——其间若视觉回合已开始 TTS，会听到被截断的短促播报（与 barge-in 同机制的
  既有噪音）。不做"说话预判延迟"：起轮时点通常尚无任何说话证据，预判无收益且违背
  需求口径。
- **AF 快照窗口无新帧**：视觉回合已消费 K=1 首帧；若 AF 回合处理时刻前无新帧到达
  （<1s 窗口），AF 回合自身请求/回图无图——画面仍在视觉回合的 user 行上（模型
  上下文可见，请求以该行结尾）；视频 ~1Hz，多数情况 AF 快照已获得新帧。
- **autoCameraOnJoin / 带相机进会话**：由此产生的"开相机"递增 turn ⇒ 照常触发一次
  画面评论；**相机长开、跨会话/重连延续同一轮时不再触发**（2026-10-09 修订后：
  只有 turn 变化才触发）。

#### 8.2.4 兼容与发布顺序

1. **服务端先行（硬要求）**：旧服务端对带 `turn_id` / `"screen"` 的帧按**非法
   帧**处理——网关回 error 1006（非致命）并丢弃该帧，仅连续 10 个非法帧才 4001
   断连（`gateway/connection.py:218-227` + `gateway/codec.py:36,144-159`）；音频
   帧 ~50Hz 持续插帧会复位该计数 ⇒ 实际后果 = 相机/屏幕帧全部被丢 + 1006 噪声
   （相关功能不可用），但不会断连。客户端后发。
2. 反向兼容（新服务端 + 旧客户端）：turn_id 缺省 ⇒ 触发规则 2 不成立；final
   ai_text 的 `camera_facing` 是**上行值回显**（`:1517-1525` / omni 快照取帧内
   字段），旧客户端只发 front/back ⇒ 永远不会收到 `"screen"`。
3. mock server（`tools/local-dev/livachat_mock_server`，依赖 sdk-python 生成物）
   随重生成自动跟进。
4. 回滚：客户端回退 ⇒ 新字段消失 ⇒ 服务端行为自动回到现状（无脏状态）。

### 8.3 决策点定稿一览（2026-10-06）

| # | 决策点 | 定稿 |
|---|---|---|
| Q1 | 直起轮 user 行 content | 统一标记 `[相机画面]`（开启/切换不区分）；无 turn_id = 与现状完全一致 |
| Q2 | 录屏开始是否也算新 turn（递增 turn_id） | 是——`ScreenCaptureController.start()` 无条件递增（"采集上下文"语义统一）；屏幕帧带 text，实际不受触发影响 |
| Q3 | 会话首帧（None→值）策略（**2026-10-09 修订**，见 §8.6.3） | 判定基准=设备级 turn 记忆：`turn_id` 与上次不同（含无记录）即触发，不区分 fresh/resume；同轮（flap 延续）不触发。原定稿（新会话首帧触发 / resume 静默认领）废止 |
| Q4 | 危险在飞时首帧放弃后是否"迟到补触" | 不补——turn id 照记，首帧机会一次 |
| Q5 | event_note 措辞 | §8.2.3 定稿文案 |
| Q6 | 状态帧 reason 复用 `asr_final` vs 新增枚举 | 复用 `asr_final`（v3 先例，零协议改动） |
| Q7 | 未来若恢复屏幕 2s 定时帧（现停用）是否直起轮 | 不在本次范围；恢复时随 Q2 语义（录屏开始递增）一并定 |
| Q8 | turn_id 传递方式（2026-10-06 review 修订） | **显式传参**：App 层 `CaptureTurnCounter` 维护状态、push 逐帧传入；SDK 直通无逻辑、参数默认 `turnId = 0`（0 = 未启用＝旧行为，服务端视同缺省）。默认值使遗忘传参 = 该路径功能不激活（fail-inert，不误触发）。替代 `beginVideoTurn()` 内部计数，见 §8.2.2 修订块 |

### 8.4 测试计划（实施时）

服务端（`services/tests/integration/orchestrator/test_video_frame_processing.py`
扩展；构造法参照 `test_video_text_turn.py`）：

- `test_turn_change_starts_immediate_turn`：turn_id 变化 + 无 text → LLM 收到含图
  user 行（content=`[相机画面]`）、thinking status、final ai_text 回图 = 首帧；
- `test_turn_unchanged_no_turn`：同 turn_id 连帧 → 不触发（回归现状）；
- `test_turn_change_with_text_keeps_v3`：text 帧 + turn 变化 → 仅 v3 回合；
- `test_turn_change_in_speaking_interrupts`：SPEAKING 中首帧 →
  `_do_interrupt("video_turn")` 后起轮；
- `test_turn_change_dropped_when_danger_inflight`：危险在飞 → 放弃且帧未被消费；
- `test_turn_change_gated_in_translate_transcribe_omni`：三模式不触发；
- `test_turn_id_absent_inert`：旧客户端（无字段）**或 `turn_id=0`** → 不触发
  （两条同为"与现状逐位一致"的 fail-inert 路径）；
- `test_first_frame_turn_change`（2026-10-09 修订，替代 `test_first_frame_fresh_vs_resume`）：
  无记录首帧触发 / 同轮不触发 / 换轮触发（判定基准=设备级记忆）；
- `test_turn_change_after_reconnect_triggers`（2026-10-09 增）：跨连接回归（两个
  orchestrator 共享一个 `SessionManager`）——连接 1 开相机 turn=5 触发；连接 2
  首帧 turn=5 不触发 / turn=6 触发。

客户端：

- `CaptureTurnCounter`：`next()` 单调递增；VM 动作点递增矩阵单测（用户开 / 关 /
  切换 / autoCameraOnJoin 递增；自动重绑不递增，含 onDispose unbind → 重进页面
  自动 rebind 不递增）；SC `start()` 递增；
- SDK：`buildVideoPayload` 透传 turnId（参数直通落线）、`mapCameraFacing` 三值；
- ScreenCaptureController：两处 push 的 facing="screen"。

协议：`packages/sdk-python/tests/test_protocol_models.py`（可选字段往返）、
`make check-android-api-generated`。

### 8.5 实施影响清单

| 层 | 文件（预计改动） |
|---|---|
| 协议 schema | `packages/api-schema/common.json`、`packages/api-schema/c2s/video.json`（turn_id 描述含"缺省或 0 = 未启用"） |
| 生成物 | `packages/sdk-python/…/generated/*`、`packages/sdk-kotlin/…/generated/ProtocolModels.kt`（脚本重生成，不手改） |
| 服务端 | `services/session/orchestrator.py`（`__init__` / `_handle_session_start` / `_handle_video`；`turn_id` 0/缺省视同未启用）+ 测试扩展 |
| SDK Kotlin | `LivaChatSDK.kt`（`pushVideoJpeg`/`pushVideoBitmap` + `turnId: Long = 0` 参数、`buildVideoPayload` 直通、facing 映射）、`VideoSendPipeline.kt`（turnId 透传） |
| App | `ConversationViewModel.kt`（三处动作点递增：`toggleCamera` 开分支 / `switchCamera` / `autoCameraOnJoin`）、`ScreenCaptureController.kt`（facing、start 递增、push 传参）、**新增 `CaptureTurnCounter.kt`（单例计数）**、`CameraController.kt`（push 调用点补传 `turnCounter.current`；**仍无任何递增判定**——判定严禁放进 `bind()`，§8.1.2 关键事实） |
| 文档 | `docs/ws-protocol-design.md`（§2.2.5 字段表）；本文档（实施后回填） |

### 8.6 实施记录（2026-10-06）

〔2026-10-06 修订（review）：SDK 侧 turn_id 传递方式由"`beginVideoTurn()` 内部计数"
改为"**显式传参**"（Q8/§8.2.2 修订块）。下表为方案 A 的落地记录；**代码切换清单见
§8.6.1（已实施，2026-10-06）**。〕

按 §8.2/§8.3 定稿实施，全量回归通过（0 failed）。

| 层 | 实际改动 |
|---|---|
| 协议 | `common.json` CameraFacing +`"screen"`；`c2s/video.json` +可选 `turn_id`（UInt32）；重生成 Python/Kotlin DTO（`scripts/generate_python_api.py` + `make generate-android-api`，生成物 diff 仅预期字段） |
| 服务端 | `orchestrator.py`：`_VIDEO_TURN_NOTE` 常量；`__init__` 增 `_last_video_turn_id` / `_seed_video_turn_on_first_frame`；`_handle_session_start` 重置（resume 首帧静默认领）；`_handle_video` ②.5 段（触发规则 1-6 + 动作顺序；实现位置见 §8.2.3"实现位置"） |
| 服务端测试 | `test_video_frame_processing.py::TestVideoTurnFirstFrame` 8 用例（模式门控含 asr_only 共 4 参）；另修 2 处 MagicMock 视频帧 fixture（显式 `turn_id = None`——MagicMock 凭空生成的属性值会误入新分支） |
| SDK Kotlin | `LivaChatSDK.kt`：`beginVideoTurn()` + `videoTurnIdGen` / `@Volatile currentVideoTurnId`；抽出顶层 internal `buildVideoPayload()` / `mapCameraFacing()`（可测），私有 `mapOrientation` 并入组装后删除 |
| App | `ConversationViewModel`：toggleCamera 开分支 / switchCamera / autoCameraOnJoin 三动作点递增（自动重绑路径零改动故不递增）；`ScreenCaptureController`：`start()` 递增 + tick/点按两处 push facing 改 `"screen"` |
| 测试结果 | services 全量 **1463 passed / 0 failed**（2 skipped、97 deselected）；LivaChatSDK **180 passed / 0 failed**（新增 `VideoTurnPayloadTest` 4 例）；LivaChatApp **681 passed / 0 failed**（新增 `ConversationViewModelVideoTurnTest` 4 例） |
| 未实施项 | §8.3 Q7（屏幕 2s 定时帧若恢复时的语义）不在本次范围 |

#### 8.6.1 修订清单（Q8：A→B，已切换 2026-10-06）

| 层 | 改动 |
|---|---|
| App | 新增 `CaptureTurnCounter`（@Singleton：`next(): Long` / `val current: Long`，**从 1 起**）；`ConversationViewModel` 注入并三动作点改调 `next()`；`ScreenCaptureController` 注入、`start()` 改调 `next()`、两处 push 传 `current`；`CameraController` 注入并在相机 push 调用点传 `current`（bind 逻辑不变）。三处构造点均新增 `turnCounter` 注入（构造函数变更） |
| SDK Kotlin | `LivaChatSDK.kt`：删 `beginVideoTurn` / `videoTurnIdGen` / `currentVideoTurnId`；`pushVideoJpeg` / `pushVideoBitmap` 加 `turnId: Long = 0`（`cameraFacing` 之后、`text` 之前；默认 0 = 未启用）；`buildVideoPayload` 沿用既有 `turnId` 参数直通（来源改为 `onEncoded` 透传）；`VideoSendPipeline`（`pushJpeg`/`pushBitmap`/`onEncoded` 签名）同步透传 |
| 服务端 | `orchestrator.py`：触发/记录门控把 `turn_id` 缺省**或 0** 归一为未启用（真值判断 `and turn_id`）；`test_turn_id_absent_inert` 扩 `turn_id=0` 变体 |
| mock | `LivaChatAppMock`：**零改动**（不传 `turnId` = 默认 0 = 未启用） |
| 测试 | SDK `VideoTurnPayloadTest` 保留（参数直通断言仍成立）+ `VideoSendPipelineTest` 新增 2 例（默认 `turnId=0` 透传 / 报文 `turn_id` 显式 0 与 5 落线锁）；App `ConversationViewModelVideoTurnTest` 改用**真实 `CaptureTurnCounter` 实例**断言 `counter.current`（免 mockk、更行为化）+ 新增 `CaptureTurnCounterTest`；**构造点波及**：既有 VM 测试 7 个 `createViewModel()` + `ScreenCaptureController` 直构测试 2 个（Waiting/Race）补 `turnCounter` 注入（真实实例，非 mockk） |
| 守卫/文档 | `scripts/check_android_api.sh` 删 `beginVideoTurn` 行（已随本次文档修订移除）；`docs/android_sdk_design_v3.md` push 签名含 `turnId = 0`（已同步）；发布说明条目已改按 B 描述；`video.json` 描述改"缺省或 0 = 未启用" + 重生成（docstring 传播） |
| 回归 | 服务端**仅门控归一（0＝缺省），其余行为不变**；services 全量 + Android 全量单测重跑 |

**实施结果（2026-10-06）**：按清单完成——services 全量 **1464 passed / 0 failed**
（2 skipped、97 deselected；`test_turn_id_absent_inert` 参数化 None/0 两变体）；
LivaChatSDK **182 passed / 0 failed**（+2 例：pushJpeg 默认 `turnId=0` 透传 / 报文
`turn_id` 显式 0 与 5 的落线锁）；LivaChatApp **682 passed / 0 failed**
（+1 `CaptureTurnCounterTest`；VM 专项测试改用真实计数断言 `counter.current`）；
`LivaChatAppMock` 编译通过（零改动路径验证）。

#### 8.6.2 修订记录（P2-1：镜头切换递增下沉到首帧 facing 检测，2026-10-06）

| 层 | 改动 |
|---|---|
| App | `CameraController.kt`：增 `lastPushedFacing`（analyzer 线程私有，初值 null）+ `processFrame` 内检测（`shouldBumpTurn` 为真才 `turnCounter.next()`，与切换后首帧严格同帧）+ companion 纯函数 `shouldBumpTurn`；`ConversationViewModel.switchCamera()` 移除动作点递增（只翻转期望镜头） |
| 测试 | `CameraControllerTest` 增 `shouldBumpTurn` 矩阵 3 例（首帧 null 不 bump / 同镜头不 bump / 换镜头 bump）；`ConversationViewModelVideoTurnTest` switch 用例改"动作点不递增"断言 |
| 回归 | LivaChatApp **685 passed / 0 failed**（+3）+ `LivaChatAppMock` 编译通过；服务端零改动（行为变化仅客户端递增时机，未重跑） |

#### 8.6.3 修订记录（resume 首帧静默认领 → 按设备级 turn 变化触发，2026-10-09）

**动机（真机实测）**：WS 断线期间（keepalive 判死 + 公网隧道 TCP 连接超时）App 回
前台，用户在 WS 重连完成前按开相机；重连后 `session.start` 带 resume_id ⇒ 首帧被
静默认领吞掉 ⇒ 直起轮未触发，只能等 avdetector 兜底（`video_check_interval_s=10s`
独立节拍，且其首次检查返回 NONE）——开相机到首句响应实测 **16s**（时间线：开相机
17:12:39.7 → 首帧 17:12:42.3 → avdetector 第二次检查 17:12:52.6 → 主 LLM 首 token
~2s → 首句 17:12:55.5；证据：设备 `files/logs/livachat.log` + 服务端
`~/temp/livachat.log`，注意 Mac 时钟慢 ~22s。对照同日非 resume 会话：首帧后 **2.3s**
已出 ai_text）。

**策略**：判定收敛为一条——「`turn_id != 设备上次视频 turn` 即触发」，不再区分
fresh/resume（§8.2.3 修订块）。设备级记忆落在 `SessionManager`（进程单例、跨连接/
跨会话延续；键=device_id——App `CaptureTurnCounter` 为进程级单例、跨会话不重置，
device 键在同设备多会话下正确；进程内存无锁，单事件循环 dict 读写原子）。

| 层 | 改动 |
|---|---|
| 服务端 | `session/manager.py`：增 `_last_video_turn_by_device` + `record_video_turn()` / `last_video_turn()`；`session/orchestrator.py`：删 `_seed_video_turn_on_first_frame` 及全部 resume 分支；`_handle_session_start` 以 `last_video_turn(device_id)` 初始化 `_last_video_turn_id`；`_handle_video` 判定收敛为 `turn_id != _last_video_turn_id` + 变化写回记忆（其余触发条件逐字不动） |
| 测试 | `test_first_frame_fresh_vs_resume` 重写为 `test_first_frame_turn_change`；增跨连接回归 `test_turn_change_after_reconnect_triggers`（17:12 场景）；既有 `test_turn_unchanged_no_turn` / `test_turn_id_absent_inert` 等零改动 |
| 行为变化 | ① 修复：重连后开/切相机立即触发；② 保持：flap 同轮不触发；③ 收紧：相机长开、跨会话/重连同轮不再触发（autoCameraOnJoin 因 `next()` 递增仍触发）；④ 边界：服务端重启后首个重连无记忆 → 首帧按变化触发一次（一次性；旧行为静默） |
| 回归 | 见下"实施（2026-10-09）"——services 全量 **1526 passed**；**真机复现待做**（17:12 序列 / flap 同轮） |

**实施（2026-10-09）**：`session/manager.py`：`__init__` 增 `_last_video_turn_by_device`
+ `record_video_turn()` / `last_video_turn()`（进程内存、键=device_id）；`session/
orchestrator.py`：删 `_seed_video_turn_on_first_frame`（字段/重置/判定三处）及 resume
分支；`_handle_session_start` 以 `last_video_turn(handler.principal.device_id)` 播种
`_last_video_turn_id`；`_handle_video` 判定收敛为 `turn_id != _last_video_turn_id`，
变化时 `record_video_turn(ctx.device_id, turn_id)` 写回（与触发解耦）。测试——
`test_first_frame_fresh_vs_resume` 重写为 `test_first_frame_turn_change`（无记录触发 +
记忆写回断言 / 重发 session.start 以记忆播种 / 同轮静默 / 换轮触发）；新增跨连接回归
`test_turn_change_after_reconnect_triggers`（两个 orchestrator 共享一个 `SessionManager`：
连接 1 turn=5 触发 → 连接 2 首帧 turn=5 静默 / turn=6 触发），**先失败后通过验证**
（改码前 2 例红：`last_video_turn` 缺失 AttributeError）。**回归**：services 全量
（`tests/`）**1526 passed / 0 failed / 1 skipped**（97 deselected；较实施前 1525 +1
——替换 1 例为 2 例）；`ruff check` 三个改动文件通过。**真机复现待做**（重连瞬间
开相机 → 立即触发；flap 同轮 → 不触发）。

**已知边界（接受）**：

- 服务端重启：记忆丢失 ⇒ 重启后首个重连若相机延续，首帧按变化触发一次画面评论
  （一次性误报，此后恢复精确）。进程内存不持久化：真实复现场景（重连）均在进程
  存活期内，持久化收益低（备选方案 A2 未采用）。
- App 进程重启：`CaptureTurnCounter` 从 1 重新计数——记忆是"服务端最后**见到**的
  turn"，故撞值条件 = 旧进程最后送达的 turn 为 1 且新进程首个相机动作同为 1
  （少见；断线期产生的换轮不落记忆会加大此概率），撞上则同轮静默一次；其余均判为
  变化触发一轮（与"覆盖 autoCameraOnJoin"语义一致）。
- 部署假设：记忆为**进程内**，依赖单进程单副本部署（`chatserver_no_redis.md` §4.4：
  多实例能力已放弃、k8s replicas=1；`infra/kubernetes/` 的 deployment/hpa 两清单已按
  replicas=1 校正）——多副本时跨副本重连记忆缺失，flap 同轮首帧会误触发。
  （2026-10-09 review 补：原清单 replicas=2 / HPA min 2 max 10 与本文档同期发现并修正。）
