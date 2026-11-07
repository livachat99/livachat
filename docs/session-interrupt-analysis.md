# Session Interrupt 机制与流程全面分析

## 一、整体架构

Interrupt 机制跨越 **Client → Wire Protocol → Gateway → Orchestrator → InterruptController → State Machine → LLM/TTS Streams** 六层，核心思路是 **epoch-based 协作式取消**，目标延迟 < 50ms。

---

## 二、触发路径（两种来源）

### 路径 A：Client 主动发送 `interrupt` 消息

```
Android App
  └─ InterruptCoordinator (app 层)
       │  监听 sdk.sessionState + sdk.localVadState + vadInterruptEnabled
       │  条件: sessionState == SPEAKING && vadState == SPEECH && enabled
       │  防抖: 500ms 间隔
       │
       ├─ playback.flush()           // 清空本地音频播放队列
       ├─ sdk.sendInterrupt(USER_VOICE)
       │    └─ LivaChatSDK.sendInterrupt()
       │         └─ SessionManager.sendInterrupt()
       │              ├─ 构造 ClientInterruptPayload(protoReason)
       │              ├─ 构造 ClientMessage.Interrupt(seq, timestamp, payload)
       │              └─ encodeAndSend(msg) → WebSocket send
       │
       └─ haptic.lightTap()          // 触觉反馈
```

关键代码位置：
- `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/media/InterruptCoordinator.kt:17` — 客户端 interrupt 协调器
- `apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/internal/session/SessionManager.kt:143` — 协议消息构造与发送

协议消息格式 (`packages/sdk-python/src/livachat_protocol/generated/client_message.py:112`):
```python
class ClientInterrupt(_MessageBase):
    type: Literal['interrupt'] = 'interrupt'
    seq: SeqNumber
    timestamp: Timestamp
    payload: ClientInterruptPayload  # 包含 reason 字段
```

**协议约定**: Client 必须在发送后续 audio frame **之前**发送 interrupt 消息，并且应立即 flush WebSocket write buffer。

### 路径 B：Server-side VAD Guard（服务端自主中断）

```
Server: _handle_audio() 
  └─ 条件: ctx.state == SPEAKING && vad_state == "speech"
       └─ _vad_speech_count 递增
            └─ ≥ _vad_debounce_threshold (3 frames) 时
                 └─ _do_interrupt("user_voice")
```

关键代码：`services/session/orchestrator.py:292-301`

这是 **N5 需求**：当服务端正处于 SPEAKING 状态时，如果检测到用户持续说话（VAD speech），服务端主动触发 interrupt，无需等待客户端发送 interrupt 消息。使用 3 帧 debounce 防止 VAD blip 误触发。

---

## 三、服务端 Interrupt 执行流程

### Step 1: `_handle_interrupt` (orchestrator.py:694)

```python
async def _handle_interrupt(self, msg):
    await self._do_interrupt(str(msg.payload.reason))
```

简单的 dispatch，从协议消息中提取 reason 字段。

### Step 2: `_do_interrupt` (orchestrator.py:699) — 核心执行序列

```
1. 记录 from_status (用于 event 持久化)                    [N8]
2. await _interrupt.interrupt(reason)                      ← InterruptController
   ├─ ctx.epoch += 1            // 递增 epoch，失效所有 in-flight 任务
   └─ sm.transition_or_reject(LISTENING)  // 仅当当前为 THINKING/SPEAKING
3. await _cancel_pipeline()     // 取消后台 pipeline asyncio.Task
4. await _cancel_asr_poll()     // 取消 ASR 轮询 task
5. await _asr_stream.reset()    // 清空 ASR 缓冲区
6. await _tts_stream.reset()    // 清空 TTS 缓冲区
7. 重置 per-turn 状态:
   - _current_transcription_id = ""
   - _asr_final_sent = False
   - _vad_speech_count = 0
   - _audio_out_buf.reset()
   - _turn_detector.reset()
8. await _send_status("listening", "interrupted")          ← 通知客户端
9. _persist_event("interrupt", ...)                        ← 持久化到 PG
```

### Step 3: `InterruptController.interrupt()` (interrupt.py:20)

```python
async def interrupt(self, reason="user_voice"):
    # 1. 递增 epoch — 这是取消的核心机制
    self._ctx.epoch += 1
    
    # 2-3. TTS/LLM 流的取消由 orchestrator 通过 epoch check 处理
    
    # 4. 状态转换: THINKING/SPEAKING → LISTENING
    if self._ctx.state in (THINKING, SPEAKING):
        self._ctx.sm.transition_or_reject(LISTENING)
    
    # 5. ASR 重置由 orchestrator 处理
```

---

## 四、Epoch 机制 — 协作式取消的核心

**原理**：每个 LLM/TTS 任务在启动时拍摄 epoch 快照 (`epoch_snap = ctx.epoch`)。在运行过程中，通过 `should_yield()` 检查当前 ctx.epoch 是否与快照一致。如果不一致，说明发生了 interrupt，任务应立即退出。

**检查点**（在 `_start_thinking` pipeline 中）：

| 位置 | 代码行 | 说明 |
|------|--------|------|
| LLM token 迭代循环中 | `orchestrator.py:516` | 每个 token 后检查，interrupt 后停止输出 ai_text |
| LLM 流结束后 | `orchestrator.py:535` | 如果被中断，直接 return，不发送 final text 也不进入 TTS |
| TTS audio drain 循环中 | `orchestrator.py:589` | 每个 audio chunk 后检查，停止发送 ai_audio |
| Pipeline 结尾状态转换前 | `orchestrator.py:633` | 只有未被中断的 turn 才转换到 LISTENING |

**`should_yield` 实现** (`interrupt.py:35`):
```python
def should_yield(self, epoch_snapshot: int) -> bool:
    return self._ctx.epoch == epoch_snapshot
```

---

## 五、Pipeline 取消机制

`_cancel_pipeline()` (`orchestrator.py:894`):
```python
task = self._pipeline_task
self._pipeline_task = None
if task is not None and not task.done():
    task.cancel()        # 发送 asyncio.CancelledError
    await task           # 等待 task 完成清理
```

由于 `_start_thinking` 是一个 `asyncio.Task`，`task.cancel()` 会在下一个 `await` 点抛出 `CancelledError`。但这需要配合 epoch check 才能快速响应——因为 LLM streaming 的 `async for` 可能长时间阻塞在 provider 的网络上。

---

## 六、状态机转换

```
              interrupt
                 │
    ┌────────────┼────────────┐
    ▼            ▼            ▼
  IDLE       THINKING      SPEAKING
    │            │            │
    └────────────┼────────────┘
                 ▼
             LISTENING
```

合法的状态转换 (`services/session/state_machine.py:15-19`):
- `THINKING → LISTENING` ✓
- `SPEAKING → LISTENING` ✓
- `IDLE → LISTENING` ✗ (interrupt 只在非 IDLE 状态有效)
- `LISTENING → LISTENING` ✗ (已经在听，无需 interrupt)

`transition_or_reject` 静默吞掉非法转换，仅记录 metric。

---

## 七、TTS Provider 层的处理

TTS Provider 本身**不感知 interrupt**。中断通过两层机制实现：

1. **Epoch check** — orchestrator 在 drain audio 时检查 epoch，不匹配则停止消费
2. **Stream reset** — `_do_interrupt` 调用 `tts_stream.reset()` 清空 provider 的内部队列并取消 receiver task

`TtsStream.reset()` 在各 provider 的实现：
- **Bailian** (`services/tts/providers/alibaba_bailian.py:69`): 取消 receiver task，drain queue，创建新 task_id，发送新的 `run-task`
- **Cartesia** (`services/tts/providers/cartesia.py:47`): 取消 receiver task，drain queue，重启 receiver

---

## 八、完整时序图

```
Client                          Server (Gateway)                Server (Orchestrator)           LLM/TTS Provider
  │                                    │                              │                              │
  │  SPEAKING state, user starts      │                              │                              │
  │  speaking (VAD=speech)            │                              │                              │
  │                                    │                              │                              │
  │──interrupt msg──────────────────►│                              │                              │
  │                                    │──enqueue inbound──────────►│                              │
  │                                    │                              │                              │
  │                                    │                   _handle_interrupt()                     │
  │                                    │                     _do_interrupt()                       │
  │                                    │                       │                                  │
  │                                    │                       ├─ ctx.epoch += 1                  │
  │                                    │                       ├─ sm → LISTENING                  │
  │                                    │                       ├─ _cancel_pipeline()              │
  │                                    │                       │   ├─ task.cancel()               │
  │                                    │                       │   └─ await task                  │
  │                                    │                       │       └─ CancelledError ──────────┤
  │                                    │                       │           (or epoch check fails)  │
  │                                    │                       ├─ asr_stream.reset()              │
  │                                    │                       ├─ tts_stream.reset() ─────────────┤
  │                                    │                       │                                  │
  │◄──status {listening, interrupted}─│                       │                                  │
  │                                    │                       │                                  │
  │  Client now sends audio frames     │                       │                                  │
  │  for the new turn                  │                       │                                  │
```

---

## 九、关键设计决策与权衡

1. **协作式取消而非抢占式** — 使用 epoch 检查而非强制 `CancelledError`。LLM/TTS 的 provider stream 是外部网络连接，无法被 Python 的 task.cancel() 立即中断网络 I/O。epoch check 在每个 token/chunk 边界快速退出，实际延迟通常在 1-2 个 token 时间内。

2. **Stream 级别复用** — ASR/TTS stream 在整个 session 期间保持长连接，interrupt 时只 `reset()` 而不 `close()`。这节省了每次 turn 100-300ms 的重连延迟。

3. **双路径 interrupt** — 同时支持 client 主动发送和 server VAD guard 自主触发。VAD guard 使用 3 帧 debounce 防抖。

4. **Pipeline 作为独立 Task** — `_start_thinking` 在独立的 `asyncio.Task` 中运行，确保 orchestrator 的主 dispatch loop 始终能响应新的 interrupt/audio 消息。

5. **Per-turn 状态清理** — `_do_interrupt` 重置所有 per-turn 变量（transcription_id, asr_final_sent, vad_speech_count, audio_out_buf, turn_detector），确保新 turn 从一个干净的状态开始。

6. **状态先于通知** — 先执行 epoch 递增和状态转换（<50ms），再发送 `session.status` 给客户端。客户端收到 status 后即可开始发送新 turn 的 audio。
