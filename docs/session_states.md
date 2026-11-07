# THINKING→SPEAKING 提前迁移 —— 目的、影响与修改方案

> **变更目的（已确认）：客户端看到文本即可打断。** 用户在 client 上看到 LLM final 文本后即可语音插话，跳过/中止尚未开始播放的 TTS 播报，不必等首个音频帧。
> 结论：**命题方案（final text 发送后立即 THINKING→SPEAKING）成立**，按 §3 的三个条件实施即可。此前评估的 P1/P2"风险"在本目的下重新定级：P1 是功能本身，P2 收敛为罕见失败路径的可接受闪烁。

## 1. 现状差距（为什么必须动状态迁移点）

"看到文本即可打断"需要两条打断路径在文本可见时都已武装，当前都不满足：

| 路径 | 现状 | 缺口 |
|---|---|---|
| 客户端 VAD 打断 | `InterruptCoordinator.kt:37`：`sessionState == SPEAKING && localVad == SPEECH` 才发 interrupt（500ms 防抖，`vadInterruptEnabled` 可关） | 文本可见时（final ai_text 已发）状态仍是 THINKING，**VAD 打断未武装**，首帧前约 0.3-0.6s（重试时更长）用户说话无效 |
| 服务端 barge-in | orchestrator.py:637：`state not in (LISTENING, SPEAKING)` → **用户音频帧直接丢弃** | THINKING 全程语音不进 ASR/TurnDetector，无法形成打断回合 |
| 客户端按钮打断 | `_handle_interrupt` 无状态门控，THINKING 下已可用 | 无缺口（但不是语音打断） |

first-audio 迁移点（orchestrator.py:1127-1131）是当前唯一 SPEAKING 入口，它把两条语音路径的武装推迟到了"文本可见"之后整整一个 TTS 启动延迟。

## 2. 影响重估（相对上一版评估的定级修正）

1. **静默窗口内 VAD 武装（原 P1，高风险 → 功能本身）**。文本可见到首帧出声之间扬声器静默、无 TTS 回声，此时打断正是目的。噪声误触的固有代价由既有机制承接：客户端 500ms 防抖 + 用户开关；服务端 `_handle_audio` 的 silence-only 过滤（`meaningful or had_asr_final`，772/779 行）——**客户端 VAD 路径无 ASR 确认是其既有行为，与真实播放期一致，不因本变更变差**。
2. **text-only 兜底伪 SPEAKING（原 P2，高风险 → 罕见闪烁，接受）**。仅当 `feed_text` 双重重试失败（ProviderError）时，客户端看到 speaking→listening 无音频 + 一次假震动。百炼自愈落地后该路径概率极低；且 1096 行 SPEAKING→LISTENING 合法、状态无泄漏。用条件 3.1-c 进一步压缩：TTS 不可用（stream None/asr_llm 模式/空文本）时根本不迁移。
3. **epoch 竞态（原 P3，保留为实施条件）**：interrupt 落在 final text 与迁移点之间时，须 `should_yield(epoch_snap)` guard，否则 SM 迁移被静默拒绝但 status 会脱缰发出。
4. **`tts_start` 语义漂移（原 P4，接受）**：reason 语义从"首帧已发"变为"应答就绪、播报开始"。不改协议 schema。
5. **视频帧窗口（原 P5，低）**：SPEAKING 不存帧（orchestrator.py:1239），每轮多丢"final text→首帧"约 0.3-0.6s 视频帧，可接受。
6. **观测口径（原 P6，知情即可）**：status 时间戳口径中 TTS 启动延迟从 thinking 改记 speaking。
7. **范围外（原 P7）**：LLM streaming 期间（THINKING，数秒级）语音仍被丢弃——本变更只覆盖"文本定稿后"。streaming 期间可插话是更大设计，另立项。

无影响项：状态机 `_LEGAL` 无需改（THINKING→SPEAKING→LISTENING 均合法）；interrupt.py 两态→LISTENING 均合法；SDK `handleStatus` 的 THINKING→force-flush 不触发（序列是 thinking→speaking→listening）；`_barge_in_turn_started` 门控（628-635）在无声窗口内天然安全（无回声，VAD speech 才开闸）。

## 3. 修改方案（唯一生产代码：services/session/orchestrator.py）

在 `_run_pipeline` 的 final ai_text 发送之后（1007-1008 行后）、`feed_text`（1039 行）之前插入提前迁移，三个条件缺一不可：

```python
            await self._send_ai_text("", message_id=message_id, index=text_index, is_final=True,
                                     image=latest_image_payload)

            # THINKING→SPEAKING 提前迁移：客户端看到 final 文本即可语音打断，
            # 不等首个 TTS audio 帧（目的：看到文本即可打断）。
            # a) 仅当本轮真的会播报（TTS 可用且文本非空）——text-only/无 TTS
            #    路径不迁移，避免无音频的伪 SPEAKING；
            # b) epoch guard：interrupt 落在 final text 之后时不得再迁移/发 status；
            # c) 复用 first_audio_sent 标记 "speaking status 已发"，
            #    _send_audio_chunk 首帧分支（1127-1131）因此跳过重复迁移与 status。
            will_speak = tts_stream is not None and bool(full_text)
            if will_speak and self._pipeline_mode != "asr_llm" \
                    and self._interrupt.should_yield(epoch_snap):
                self._ctx.sm.transition_or_reject(SessionState.SPEAKING)
                await self._send_status("speaking", "tts_start")
                first_audio_sent = True
```

配套说明：

- `_send_audio_chunk` **不改**：`first_already_sent=True` 时其自然跳过迁移分支（零帧 edge 时 speaking 已由上面发出，drain 收哨兵走 1096 正常收尾）；
- feed 失败路径：`will_speak` 已为 True 已迁移 → 1041 捕获后跳过 flush/drain → 1096 `should_yield` → SPEAKING→LISTENING + `tts_finish`，合法，仅失败时一次无音频闪烁（§2-2）；
- 窗口内打断（本变更的目的路径）：客户端 VAD → `sendInterrupt` 或服务端 barge-in（meaningful 确认）→ `_do_interrupt` → `_cancel_pipeline`（feed_text 内 cancel 安全：百炼 pending 由 reset 清理，孤儿任务 23s 自清）→ TTS reset 幂等 → SM→LISTENING + `status("listening","interrupted")`；
- 无人打断：状态序列 `thinking → speaking(final text 后) → listening(tts_finish)`，首帧不再重复发 speaking；
- **不改**：状态机、协议 schema、`_handle_audio`/`_handle_video`/interrupt.py、lecture_translate.py / omni_orchestrator.py（lecture 模式保留首帧迁移，如需同样体验另议）、客户端（按钮路径已可用；VAD 路径随 status 自动武装，无需发版）。

## 4. 边界情况清单

| 场景 | 行为 |
|---|---|
| 文本可见后、首帧前用户说话（目的路径） | 打断成立：pipeline 取消、TTS reset、回 LISTENING 收新回合 |
| 同窗口内纯噪声 | 客户端 VAD 路径：与真实播放期行为一致（既有代价）；服务端路径：silence-only 被 `meaningful` 过滤，pipeline 不动 |
| TTS feed 双失败 | speaking→listening 无音频闪烁一次（罕见，接受） |
| interrupt 早于 final text | epoch guard 阻止迁移；序列 thinking→listening(interrupted) |
| TTS 不可用 / asr_llm / 空文本 | 不迁移，现状不变 |
| 零帧 TTS（立即 task-finished） | speaking 已发 → drain 即收哨兵 → 1096 收尾，无挂起 |

## 5. 测试

单测（orchestrator 级，沿用现有集成测试基建）：
1. final ai_text 发出后、首帧到达**前**已发 `status("speaking","tts_start")`；首帧到达后**不重复**发（回归锁）；
2. 窗口内 barge-in：final text 后注 VAD speech + ASR meaningful → pipeline 取消、`status("listening","interrupted")`、TTS reset 恰好一次 finish-task（与百炼幂等断言复用）；
3. feed_text 抛 ProviderError → 状态序列 speaking→listening(tts_finish)，无挂起、无二次 speaking；
4. final text 前已 interrupt（epoch++）→ 不迁移、不发 speaking（guard 回归锁）；
5. `tts_stream=None` / 空 `full_text` → 无 speaking status（条件 a 回归锁）。

真机回归：说一句触发长回答 → final 文本出现时立即插话 → AI 不出声、直接进入新回合；不插话 → 音频正常播放、无重复 speaking status。

## 6. 明确不做

- LLM streaming（THINKING）期间的语音收编（§2-7，另立项）；
- 协议 `StatusReasonCode` 新增枚举；
- lecture_translate / omni_orchestrator 迁移点统一；
- 客户端任何改动（VAD 打断随 status 提前自动生效）。
