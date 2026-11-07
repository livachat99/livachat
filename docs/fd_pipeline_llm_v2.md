# Pipeline LLM v2：生成链路、失败处理与 LLM 历史（现行基准）

> 本文档是 LLM 管线（请求组装 → 流式生成 → failover → 成功/失败收尾 → 历史/落库/chatlog）
> 的**现行实现基准**，取代并合并早期 LLM/history/chatlog 设计稿。
> 文中行为以代码为准、逐条核实，锚点给到文件与符号。
>
> 不在范围：ASR / TTS 自身失败处理（另行文档）；omni（全模态 realtime）与 lecture（同传）
> 引擎的 LLM 链路（独立链路，不组装 messages、不走本文 failover / 历史 / 回滚语义）；
> 客户端侧渲染细节（仅在边界处注明）。

## 1. 链路总览

```
client audio / video(text) 
  → DialogOrchestrator（每连接一个，session/orchestrator.py）
       触发回合（入口见 §3.2：4 个 `_start_thinking` 调用点，ASR 回合含双路径）
         → _start_thinking
              ├─ 组装 messages = system prompt + WorkingMemory history（图片仅最新 2 张，§9.4）
              ├─ LlmService.stream（failover 语义见 §5）
              │      └─ LlmProvider（OpenAICompatProvider 流式，见 §4）
              ├─ token 循环：epoch 守卫（interrupt）→ 逐 token ai_text 上屏
              └─ 收尾：final ai_text → assistant 三写 → TTS → LISTENING
       失败路径（§7）：error 文本经 final ai_text 透出（无 TTS、无三写）
         └─ 4xx 不可恢复（§8）：同步回滚本轮 user 记录（history + messages 行）
```

一次 user/assistant 发言的三个落点（三写）：

| 数据面 | 存储 | 用途 | 写方式 |
|---|---|---|---|
| WorkingMemory | 进程内 `deque`（`session/history.py`） | 下一轮 LLM 请求组装 | 同步 `add_message` |
| chat_store `messages` 表 | SQLite（`shared/persistence/chat_store.py`） | 会话恢复唯一路径 + 检索 | best-effort 队列，满即丢（有计数） |
| recording chatlog | jsonl 文件（`recording/chatlog.py`） | 回放 / lvdata 分析 | best-effort 队列，整项丢弃 |

## 2. 常量速查

| 常量 | 值 | 位置 |
|---|---|---|
| 首 token 看门狗 | 5.0s | `llm/providers/openai_compat.py:_DEFAULT_TIMEOUT_FIRST_TOKEN` |
| read timeout（相邻数据块间隔上限） | 60.0s，connect=5s | `openai_compat.py:_READ_TIMEOUT_S`（`httpx.Timeout(60, connect=5.0)`） |
| 失败透出 error 文本上限 | 1000 字符 | `session/orchestrator.py:_ERROR_TEXT_MAX_CHARS` |
| 4xx 回滚排除集（瞬态） | {408, 429} | `orchestrator.py:_UNRECOVERABLE_4XX_EXCLUDED` |
| 回滚令牌有界集合 | deque(maxlen=8) + set | `orchestrator.py.__init__` |
| TTS 播报截断（chat 模式） | 100 units（CJK 字 / 英文 word） | `orchestrator.py:_TTS_MAX_TEXT_UNITS` |
| 请求组装历史图上限 | 最新 2 张（其余条目不组装） | `orchestrator.py:_HISTORY_IMAGE_LIMIT` |
| 有效发言最小字数 | 2（`_count_meaningful_chars`：去尾部 `,.，。` 后长度 ≥2） | `orchestrator.py:_min_meaningful_chars` |
| 轮末判定静音帧 | low 45 / medium 30 / high 20 帧（20ms/帧） | `session/turn_detector.py` |
| WorkingMemory 窗口 | max_turns=10 → 20 条目 | `session/manager.py` |
| 会话恢复上限 | 最近一天 ≤20 条 | `manager.py:_RESTORE_HISTORY_LIMIT` |
| 视频缓冲 | K=1 帧（`max_video_frames=1`），5s 空闲清空 | `session/context.py` / `orchestrator.py:run` |
| TTS drain 单块超时 | 30s | `orchestrator.py`（drain 块局部量 `_AUDIO_TIMEOUT_S`） |
| agent job 入史截断 | 默认 4096 字符，`AGENT_JOB_HISTORY_MAX_CHARS=0` 关闭 | `session/agent_job_ingest.py` |
| agent job 结果截断链 | mark_done 16KB / publish 16KB（均为默认值） | `agent_relay/job_store.py:max_result_bytes` / `agent_relay/config.py:max_reply_bytes` |
| push enqueue 背压上限 | 2s | `agent_relay/publisher.py:_ENQUEUE_TIMEOUT_S` |

## 3. 回合触发与请求组装

### 3.1 `_start_thinking`

入口（`orchestrator.py:_start_thinking`）：`transition_or_reject(THINKING)` + `status(thinking, asr_final)`，
随后组装 messages、开 LLM 流、token 循环、收尾（详见 §6）。签名带 `turn_token`（本轮唯一令牌，
4xx 回滚守卫用，§8）与 `event_note`（并入 system 的系统注记）。

### 3.2 回合入口（user 写入路径）与 turn_token

`_start_thinking` 实际调用点共 4 处：路径 A（ASR 回合，含路径 B 经暂存帧并入）、v3 文本轮、
cam 首帧轮、avdetector 事件轮。各入口写 user 记录的路径：

| 入口 | 位置（`orchestrator.py`） | turn_token | user 条目内容 |
|---|---|---|---|
| ASR 本地静音判停（turn-end 主路径 A） | `_handle_audio` turn_ended 分支 | `_current_transcription_id`（tid） | ASR final 文本 |
| ASR 主动 final（路径 B） | `_poll_asr_results` 写三件 + 暂存帧；thinking 由路径 A 下一音频帧触发（`force_turn_end` + `_asr_final_sent`） | tid | ASR final 文本 |
| v3 视频/录屏文本轮 | `_handle_video` payload.text 分支 | `f"video-{frame_id}"` | 帧携带的 text |
| cam 首帧直起轮 | `_handle_video` turn_id 变化分支 | `f"cam-{frame_id}"` | `[相机画面]`（+ `_VIDEO_TURN_NOTE` 系统注记） |
| avdetector 环境事件轮 | `_on_av_event` | `f"avd-{generate_message_id()}"` | `【环境事件】{desc}` |

各入口都遵守同一时序：**user 三写（history / chat_store / chatlog）先于 `_start_thinking` 启动**——
组装时本轮 user 条目天然已在 history 末尾（图片组装裁剪规则见 §9.4）。

### 3.3 chat 模式 messages 组装

- system = 装配期加载的 dialog system prompt（已并入 §11.4 两条声明）+ 可选 `event_note`。
- 普通用户回合（`event_note` 为空）在 system 末尾追加输出语言规则
  （`build_output_language_rule`，`docs/server_multilingual.md` §4.12）：
  用户当前消息的显式语言要求 > 当前消息语言 > `session.start.language`（空/未知 → `en`）。
  translate、主动 av/cam 回合（`event_note` 非空）与 omni 不追加。
- history 逐条转 `ChatMessage(role, content, images)`，组装时经 `_select_recent_images`
  裁剪——**仅保留最新 2 张图**（§9.4），其余条目的 images 不进入请求（history 条目原样保留）；
  `images` 为 data_url 字符串列表（mime 在 `data:image/jpeg;base64,` 前缀内）；**非 list 脏值
  不当作图片**（恢复数据防线，`session/history.py:add_message` 与组装处双保险）。
- 生成参数：`**cfg.llm.generation`（temperature / max_tokens / top_p）经 `_gen_opts` 传入
  stream；provider 级 `extra_body`（如 bailian `enable_thinking: false`）另在 provider 内附带。

### 3.4 translate 模式（含 loc-auto）

- 不读 history（因此不组装图片）；user 文本也不写 history / chat_store（chatlog 落
  `translate.jsonl`，§10.3）。
- 普通 translate：系统 prompt = translate prompt + 语言方向指令（`_build_translate_instruction`）；
  LLM 输出直接上屏 + TTS。
- loc-auto（`local_language="auto"`）：指令要求 LLM 输出严格 JSON
  `{source_language, translation}`（`_build_auto_translate_instruction`）；**token 不上屏**，
  整句到齐后解析 → `source != my` 时更新 effective 对方语言（latest-wins）→ 以纯译文重放
  （`_send_ai_text` index=0 非 final，final index=1）。
- loc-auto 未守约处置：先经 `_extract_json_object` 做 `'{'..'}'` 轻量截取后**二次解析**；
  仍失败且文本含 `'{'`（JSON 残片嫌疑）→ 仅上屏原样供排查，清空全文（不喂 TTS、
  不进 recording）；纯 prose（无 `'{'`）视为可读内容照常走 TTS。
- `llm_error` 时整个解析/重放块跳过（§7），final 仅 error 文本。

## 4. Provider 层（`llm/providers/openai_compat.py`）

- 每次 `stream()` 新建 `AsyncOpenAI`（连接失败快速 failover），`finally` 关闭；
  timeout `httpx.Timeout(60, connect=5.0)`——read/write/pool 60s = **相邻数据块间隔上限**
  （不是整流传时长上限），connect 5s 保持 SDK 默认；SDK 连接级重试保持默认（未覆写 `max_retries`）。
- `create()` 阶段异常 → `ProviderError("LLM stream creation failed: ...")`
  （`http_status` 若 `APIStatusError`）；`ProviderError.http_status` 字段定义于
  `shared/utils/errors.py`。
- 首 token 看门狗：`asyncio.wait_for(..., 5.0)` 取首个 chunk，跳过无 `choices` 的
  thinking-mode 空 chunk；超时 → `"first token timeout"`；流立即结束 → `StopAsyncIteration`（空流，
  视为正常结束）。
- 流循环只转发 `delta.content`（**契约：绝不 yield 空串**，`llm/base.py`）；中途异常两类：
  - 超时（`APITimeoutError` / `TimeoutError` / vendored httpx2 超时按 MRO 类名集识别，
    因为 openai 3.x 流式 body 迭代中途的超时不经 SDK 包装且 `str()` 为空）→
    `"LLM stream timeout (no chunk within 60s): ..."`（与其他中途错误可区分，是回滚预案判断信号）；
  - 其余（含中途 4xx/5xx）→ `"LLM stream error: {detail}"`（`http_status` 若 `APIStatusError`）。
- vision 渲染（`_build_oai_messages`）：**逐条**注入——仅 `role=user` 且 `images` 非空的条目
  转 parts（text + image_url×N，`detail="auto"`），无图消息保持纯字符串 content；
  provider `supports_vision=False` 时忽略全部图片并**单次** warning（`_vision_warned`）；
  model 选择条件 `supports_vision and vision_model and any(images)`（三者缺一不切换）。
- 已实现的 provider（`llm/factory.py`）：`alibaba_bailian`（supports_vision=True，可选
  X-DashScope-WorkSpace 头）/ `openrouter`（True）/ `deepseek`（False），均继承
  `OpenAICompatProvider`、不覆写 `stream`。

## 5. LlmService failover（`llm/service.py`）

`providers = [active] + fallbacks`（`factory.create_llm` 从 config 构建）。

- **failover 判据只有一条：首个 token 之前**。`stream()` 先 peek 首个 token——peek 失败且还有
  下一个 provider → warning 后切换（此时"before first token"日志名副其实）；peek 成功即绑定该
  provider 续流。
- **首个 token 产出后中途失败：绝不切换**，包装为上抛 `LLM stream failed mid-stream (provider): ...`
  ——中途切换会把 A 的残句与 B 的全文拼接下发给客户端。
- 全部 provider 失败：`all LLM providers failed: ...`；**`http_status` 取最后实际失败 provider 的
  值**（`getattr(exc, "http_status", None)` 逐层透传；4xx 回滚以此为判据）。
- 失败分支防御性 `_aclose_gen`（对已终止 generator 为 no-op；对"活的 iterator"型 provider 是
  正确保险）；顶层留一条空 provider 列表的防御 raise——不允许退化为"静默正常结束（空 final）"。

## 6. 成功路径收尾（`_start_thinking` 尾段）

token 循环（`orchestrator.py`）：

1. 每个 token 前查 `_interrupt.should_yield(epoch_snap)`——失败（epoch 被 interrupt 递增）即
   `break`，之后 `return`：**中断轮不发 final ai_text、不写 assistant 三写**（CancelledError 是
   BaseException，不被失败兜底收编，穿透取消）。
2. 累积 `full_text`；非 loc_auto 时逐 token 发非 final `ai_text`（index 递增）。
3. 流结束（正常完成）后按序：

| 步骤 | 条件 | 行为 |
|---|---|---|
| final ai_text | epoch 有效即发（含空文本） | `_send_ai_text(final_text, index=final_text_index, is_final=True, image=latest_image_payload)`；image 来自 AF 快照帧最后一帧（translate 无快照 → None） |
| assistant 写入 history + chat_store | `llm_error is None and full_text and not is_translate` | `history.add_message("assistant", full_text)`；`chat_store.on_message(kind="llm", role="assistant", ref_id=message_id)` |
| recording tap（assistant） | `llm_error is None and full_text`（translate 也 tap） | `on_chat_text(role="assistant", ref_id=message_id, source="translate" if is_translate else None)` → 路由见 §10.3 |
| SPEAKING 提前迁移 | `will_speak = llm_error is None and tts_stream is not None and bool(full_text)` 且 mode ≠ `asr_llm` 且 epoch 有效 | `transition(SPEAKING)` + `status(speaking, tts_start)`——客户端看到 final 文本即可语音打断，不等首 TTS 帧；置 `first_audio_sent` 防首音频帧重复迁移 |
| TTS | `llm_error is None and tts_stream and full_text` | `feed_text`（translate 全文；chat 模式 `_clip_for_tts` 截断至 100 units）→ `flush()` → `audio()` 逐块 drain（单块 30s 超时）→ `_send_audio_chunk` 编码 Opus 发 `ai_audio` |
| 收尾状态 | epoch 有效 | `transition(LISTENING)` + `status(listening, tts_finish)`；`_audio_out_buf.reset()` |

TTS 流生命周期：session.start 时 eager open（失败降级，首轮 thinking 时 lazy open 兜底）；
**session 级复用**——每轮结束 `finally` 里 `reset()`（不 close）；TTS 不可用降级纯文本
（仍发 final text 与三写）。`asr_llm` 模式只跑 LLM 不跑 TTS（跳过 SPEAKING 迁移与音频 drain）。

零文本正常完成（LLM 合法地没说话）：空 final ai_text 照发，无三写、无 TTS、不迁 SPEAKING，
回 LISTENING——与失败路径经 §7 的 error 透出区分（客户端不再面对"不可区分的空 final"）。

`finally`：`tts_stream.reset()`；`llm_iter.aclose()`（释放上游连接；`llm_iter` 预初始化判空守卫，
防开流同步抛错时 NameError 掩盖原异常）。

## 7. 失败处理（统一失败路径）

### 7.1 失败分类与动作

| 场景 | 动作 |
|---|---|
| 首 token 前失败（连接错 / 4xx / 5xx / 429，含 SDK 重试耗尽） | 有 fallback → 切下一 provider（§5）；全败 → 失败路径，http_status 决定是否回滚 |
| 首 token 后停流（60s 无数据块） | 不切换，包装上抛 → 失败路径（连接类，http_status=None → 不回滚） |
| 首 token 后 4xx / 5xx | 同上一行（4xx 可回滚） |
| 循环体内非 ProviderError 意外异常 | 兜底收编：转 `ProviderError("LLM pipeline error: ...", http_status=None)` → 失败路径（透出不回滚）；日志 `exc_info=True` |
| 开流调用本身同步抛错 | 同失败路径（`llm_iter` 判空守卫保证 finally 安全） |
| interrupt / barge-in | epoch 失效 → 放弃（非失败路径）：不发 final、不回滚；`_do_interrupt` 置 LISTENING + `status(listening, interrupted)` |
| CancelledError | 穿透（`_cancel_pipeline` 取消），不在此收编 |

### 7.2 失败路径行为（`llm_error is not None` 统一门控）

- **final ai_text（epoch 有效时）必发**：文本 = `str(llm_error)[:1000]`（原样截断；本轮已有
  partial 上屏时加 `"\n"` 前缀分隔）；**带图**（与成功路径同源 `latest_image_payload`，视觉轮
  用户可据此定位是哪帧图导致的错误）。错误文本**不进** history / chat_store / recording
  （不是 assistant 发言）。
- **不喂 TTS**：不 feed、不 drain、不迁 SPEAKING；本轮新建的 TTS 流由既有 `finally.reset()` 回收。
- loc-auto 失败：跳过 JSON 解析/重放块（§3.4），final 仅 error 文本。
- 状态收尾：`THINKING → LISTENING` + `status(listening, tts_finish)`（沿用现存 reason 值，
  不引入 error 变体）。
- **不发 error 帧**（协议 1003 不发）、不新增 `StatusReasonCode` / `ai_text.source` 枚举。
- 4xx 不可恢复（400–499 排除 408/429）→ **先同步回滚**（§8）：回滚紧接失败分支同步完成
  （无 await 窗口）、先于 interrupt/epoch 检查——失败后再被打断也已完成回滚。

### 7.3 失败矩阵（现行）

| 场景 | 结果 |
|---|---|
| 全 provider 首 token 前失败 | error 文本 ai_text（截 1000）+ 带图；无 TTS/三写；4xx → 回滚 |
| 停流（60s 无块；有 fallback） | 不再切换；error 透出；partial 已上屏则保留 + `"\n"` + error 追加 |
| 停流（无 fallback） | 同上（partial 保留上屏 + error 追加、无 TTS） |
| 中途 4xx / 首 token 前 4xx 全败 | 同上 + 回滚本轮 user 记录 |
| 429 / 408 / 5xx / 连接类 / 超时 | 透出但不回滚（不加回滚令牌） |
| 生成正常但零文本 | 空 final（合法终点），无回滚 |
| 中断 / barge-in | 无 final、无回滚、`listening/interrupted` |

## 8. 4xx 不可恢复回滚

### 8.1 判定

`llm_error.http_status ∈ [400,499] 且 ∉ {408, 429}`（`_rollback_unrecoverable_turn`）。
`http_status=None`（连接/超时/意外异常）不回滚、**不加令牌**。

### 8.2 回滚对象：本轮 user 记录（五元组）

五个写入点都在写 history / chat_store 时同步覆写 `self._turn_user_record`：

```python
{"content": <user 文本>, "ts_ms": <写入时 now_ms()>, "token": <turn_token>,
 "device_uuid": <写入时 ctx.device_id>, "session_id": <写入时 ctx.session_id>}
```

`token` 即 §3.2 的 turn_token（tid / `video-{frame_id}` / `cam-{frame_id}` / `avd-{id}`）；
device/session 取写入点实际使用的 ctx（路径 B 为捕获态），保证 delete 五元组与已写入行同源。
所有写入点 chat_store `kind="asr"`。

### 8.3 回滚执行（`_rollback_unrecoverable_turn`，同步、无 await）

非 §8.1 触发条件直接 return；命中时按下列步骤：

1. **登记令牌**：`turn_token` 非空时 `_mark_turn_rolled_back`（有界 deque(maxlen=8)+set 对，
   淘汰最旧）。此步不依赖 record 匹配、**先于**第 3 步执行——用于拦截路径 B 迟到写。
2. **读并清 record**（read-and-clear，天然防同轮重复回滚）。
3. **仅当 `record.token == turn_token`**（本轮确实写入过）才删除：
   - `history.remove_recent_user(record.content)`——从尾部找最后一条同 content 的 user 条目删除
     （幂等，找不到返回 False）；
   - `chat_store.delete_message(device_uuid, session_id, kind="asr", role="user", ts_ms)`——
     5 元组精确删除，随 best-effort 队列 FIFO 执行（insert 未落盘时 delete 在其后，"如果进入，
     则删除"；insert 被丢弃时删 0 行，幂等）。
   - 不匹配（translate 轮不写 / 路径 B 尚未送达 / stale holder）→ **仅拦不删**，防误删历史同文条目。

### 8.4 守卫与生命周期

- **路径 B 迟到写拦截**：`_deliver_final` 闭包写 history/chat_store 前以捕获的 tid 查
  `_is_turn_rolled_back`，命中则跳过（`orchestrator.py:_poll_asr_results` 内）。
- **record 生命周期**：覆写于五个写入点；清于 `session.start`（跨会话安全）；
  失败分支消费后置 None；**不在轮次复位块清**（与路径 A 写入同 handler 且在其后执行，清则
  路径 A 回滚永不触发）。
- **令牌集合**：仅在 4xx 不可恢复时添加；`session.start` **不清**（有界自然淘汰）——闭合
  跨会话迟到写复活窗口；单事件循环各临界区无 await 交叠。
- 回滚先于 interrupt 检查（§7.2），失败后再被打断也已完成。

### 8.5 不可回撤边界

- 客户端本地 Room 已持久化的转录行（final + 非空即落库）与已上屏 partial——服务端删除不影响；
- recording chatlog（jsonl）不在回滚范围（保持现有行为）；
- messages 五元组同毫秒碰撞（同 session 同 kind/role 两条 user 行 ts_ms 相同 → 一并删）：
  `now_ms` 毫秒分辨率下概率可忽略，接受。

## 9. WorkingMemory 历史与图片

### 9.1 结构与条目

`session/history.py:WorkingMemory`：`deque(maxlen=max_turns*2)`，max_turns=10（manager 内建）。
条目 `{role, content, images?, source?, ref_id?}`——可选键仅在存在时写入（旧数据兼容）。

### 9.2 图片语义：发言即入，与请求成败解耦

图在 **AF（ASR final / v3 文本帧 / cam 首帧 / avd 事件）分发处理时刻**随 user 条目写入
history / chat_store / chatlog 三处；请求失败（ProviderError / barge-in / 开流失败）图**仍在
历史**（4xx 回滚除外，§8）。与 provider `supports_vision` 无关（无 vision 的 provider 在渲染层
丢弃，图仍入历史）。

- 快照唯一取图点：`_snapshot_turn_images`（取走并清空 `ctx.latest_video_frames`；丢弃
  frame_id/ts；`enable_video=false` 或缓冲空 → []）。
- 路径 B → 路径 A 的跨任务暂存：`_turn_image_frames`（AF 覆盖式写入 latest-wins；重置块取走
  并重新绑定空 list，任务私有）；`session.start` 清空。
- 5s 空闲自动清空未被消费的残留帧（`run()` 循环兜底）。

门控矩阵（快照/落图/挂图同时适用）：

| 模式 / 条件 | 是否挂图 | 说明 |
|---|---|---|
| full / asr_llm，meaningful，有帧 | ✅ | 三写齐全 |
| translate / transcribe / asr_only | ❌ | translate 请求本不带图；transcribe/asr_only 不触发 thinking |
| filler（< 2 有效字符） | ❌ | 不触发 thinking；文本行照写、无 images |
| enable_video=false / 缓冲空 / 5s 空闲已清 | ❌ | 快照返回 [] |
| 路径 A 且 `_asr_final_sent`（路径 B 已处理同一 final） | ❌ | 双路径去重：同一 final 只落一次图 |
| omni / lecture | ❌ | 独立引擎（omni 直送 realtime；lecture 自管） |

多 final 语义：每条 final 独立快照（图互不重叠）；暂存 latest wins（与"最新 final 即
final_text"一致）；同一 final 双路径经 `_asr_final_sent` 去重。

### 9.3 chatlog 图片副本

AF 快照帧同时异步落 `image/<uuid7>.jpg`（recording writer，帧先落盘、行后写，行内路径必然
存在；`video/` 全量 tap 独立保留，双份落盘有意为之）。行内 `images` 为相对 `chat/` 的路径
数组（`../image/<uuid7>.jpg`）；单帧写失败跳过该帧、全失败省略字段。

### 9.4 请求组装的图片裁剪与成本模型

- **组装裁剪**：`_start_thinking` 组装 messages 时仅保留**最新 2 张图**
  （`_select_recent_images`，从 history 末尾向前按图计数、条目内取尾部帧）——其余条目即使有
  images 也不组装（渲染为纯文本条目，文本/角色不变）。目标：控制 prompt 体积、降 TTFT。
- **历史不裁剪**：裁剪只发生在请求组装层——WorkingMemory 条目、chat_store 行、chatlog
  行与 `image/` 副本、恢复路径仍全量保留图片（"发言即入"语义不变）。
- 成本模型：每请求图 ≤2 张；实测每张真实帧（960×1280，JPEG 67–156KB）≈1202 prompt
  tokens（服务端内部降采样，与 JPEG 字节数无关）；`detail="auto"` 由上游降采样。
- **实测证据**（2026-10-06；`services/scripts/probe_llm_image_latency.py`；DashScope
  qwen3.8-max-0902 + 生产 gen opts + 10 轮 history 形态）：TTFT 中位 0 张 704ms →
  2 张 1357ms → 10 张 1889ms（真实帧取自 chat_server.db `messages.images_json`，n=20）；
  配对 ΔTTFT(10−2) 中位 **+491ms**（15/20 为正；合成帧对照 +797ms，n=15，14/15）——
  裁到 2 张预期每次调用省 **~0.5s TTFT**（观察区间 0.3–0.9s，端点抖动大以中位计）
  ＋ prompt **−78%**（12270 → 2654 tokens）。
- 语义代价：更早的图（第 3 新及以前）对模型不可见——跨多轮指代更早图片的能力下降（接受）。
- 非 vision provider：图在渲染层被丢弃（单次 warning），不影响历史语义。

### 9.5 会话恢复（chat_store 唯一路径）

`session/manager.py:start`：`chat_store.load_day(device_uuid, timezone, limit=20)`（**设备维度**
最近一天，按对话序升序）→ 逐行映射重建 WorkingMemory：

| kind | 恢复为 |
|---|---|
| `asr` | user 条目（含 images） |
| `llm` | assistant 条目 |
| `agent_job` | assistant 条目（source="agent_job" + ref_id） |

其余 kind 静默丢弃；失败只 log 不阻断（best-effort）。Redis 快照恢复不再接入本链路
（`redis_store.py` 模块保留但无生产调用），chat_store 为现行唯一恢复路径。

## 10. chat_store 与 chatlog

### 10.1 chat_store `messages` 表

字段：`id / device_uuid / session_id / role / kind / content / images_json / ref_id / ts_ms / utc`。
kind 契约仅三值：`asr`（user）/ `llm`（assistant）/ `agent_job`（assistant）。

- `on_message`：fire-and-forget best-effort 队列（put_nowait，满即丢弃 + `chat_store_total{result="queue_dropped"}` 计数）；单 writer 循环 FIFO 消费。
- `delete_message`（4xx 回滚用，§8.3）：`("message_delete", {...})` op，`_MESSAGE_DELETE_SQL`
  5 元组精确删除。
- 每小时 prune（按 ts_ms）；写失败只计数不重试。恢复语义见 §9.5。

### 10.2 协议事实（现状保持）

- 失败只经 ai_text 承载：**不发 error 帧**、无 `StatusReasonCode.error`、`ai_text.source` 枚举
  维持 ["llm","agent"]（错误文本走缺省）。
- `ai_text.text` 无 maxLength 约束（1k 截断可行）。

### 10.3 recording chatlog 行与路由

`ChatLogWriter` 直写 flush，路径 `<base>/<device>/<recording_session_id>/chat/*.jsonl`。
行通用字段：`{ts, utc, role, text, ref_id, chat_session_id, interrupted?}`，可选扩展：

| 字段 | 何时带 |
|---|---|
| `images` | user 行（AF 挂图，路径数组） |
| `source` | `translate` / `lecture`（路由标记）/ `agent_job`（来源标记） |
| `agent_name` / `agent_id` / `job_key` | agent job 结果行（§11） |

路由（`recording/session.py:_handle_text`）：`source in ("translate","lecture")` →
`translate.jsonl`；transcribe 文本轨 → `transcribe.jsonl`；其余（chat user/assistant 含
agent_job 行）→ `history.jsonl`。recording 关闭时 NoOp sink 全降级；队列满整项丢弃。

## 11. agent job（Hermes 后台任务）结果入史

### 11.1 投递链

```
hermes job message.complete (agent_relay/adapters/hermes.py)
  ├─ "NOOP" / "error:" 前缀 → mark_done + mark_delivered（就地消费，不投递）
  └─ mark_done(job_key, text)   # result_text 截断 16KB
       └─ JobResultDeliverer.deliver  (agent_relay/delivery.py)
            ├─ per-device lock + 锁内重读 status==done（防 sweep/live 双发）
            ├─ online_check：进程内无 handler → 不投递不 mark_delivered，保持 done 等补投
            ├─ PushMessage(message_id=f"msg_agent_job_{job_key}" [确定性幂等键],
            │              text, is_final=True, proactive=True,
            │              agent_name/agent_id, job_key)
            ├─ PushPublisher.publish  (agent_relay/publisher.py)
            │     ├─ 无 handler → False（离线）；enqueue 超时 2s → False（背压）
            │     ├─ max_reply_bytes 16KB 截断（仅 ai_text text）
            │     ├─ 帧构造白名单（source/agent_name/reply_to/proactive；job_key 不透传客户端）
            │     └─ 帧入队成功后（先帧后写）→ handler.record_agent_job_result(...)
            └─ publish True → mark_delivered（CAS 幂等）→ on_delivered 回收连接
```

补投：`AgentSessionManager.on_session_started` → `sweep_device`（按 completed_at 升序，
首个失败即停），与 live 路径同走 `deliver`。

### 11.2 消费侧注入（`session/agent_job_ingest.py:ingest_agent_job_result`）

身份链：publisher → `ConnectionHandler.record_agent_job_result`（同步委托，失败只 warning；
orch 为 None 或 text 为空直接返回）→ `DialogOrchestrator.record_agent_job_result`
→ ingest helper。omni 激活时 `history=None`。
全部 best-effort、无 await 阻塞点（sink 契约：同步非阻塞）。

| 目标 | 内容 | 条件 |
|---|---|---|
| ① WorkingMemory | `assistant` 条目：content = `[Agent job result]\n` + 截断文本；`source="agent_job"`、`ref_id=message_id`；**去重**：`has_agent_job(message_id)` 命中即跳过 | history 非 None（omni 跳过） |
| ③ chat_store | `kind="agent_job", role="assistant"`，content 同上（截断+前缀），ref_id=message_id | 与 ① 同点（history 写入成功且 device_uuid 非空） |
| ② chatlog | **无前缀、写投递文本**（上限 16KB，`mark_done` 时已截断；ingest 收到的即 publisher 截断后文本）；`source="agent_job"` + `agent_name/agent_id/job_key` 随行落盘（lvdata 据此铸造事件） | recording_sink 非 None（NoOp 天然降级） |

- 截断：`AGENT_JOB_HISTORY_MAX_CHARS`（env，默认 4096，0=关闭）按**头 3/4 + 尾 1/4** 保留、
  中间插 `\n…[truncated]…\n`（结论常在尾部；标记表示原文中部有省略）。
- 指标：`livachat_agent_job_ingest_total{path=history|chatlog, result=ok|dedup_skip|no_history|failed}`
  ——`dedup_skip` 是崩溃重投窗口的在线证据，`no_history` 观测无 live 会话频率。
- 幂等分层：投递幂等（done→delivered CAS + per-device lock）/ 客户端去重（确定性 message_id）/
  WorkingMemory 去重（has_agent_job，随条目序列化并在恢复时透传）。chatlog 为 append-only 不做
  强去重（publish→mark_delivered 窗口极小；下游按确定性 event_ref 内容比对 upsert 收敛）。

### 11.3 LLM 上下文语义

- job 结果以普通 `role=assistant` 条目进入下一轮请求（主 LLM"看得到"它对用户说过的话）；
  `source`/`ref_id` 不参与组装（组装只读 role/content/images）。
- content 前缀（模型侧来源信号）+ system prompt 声明（§11.4）互补：抑制复述/误归属。
- 时间序：插入 history 末尾（结果此刻才完成，"chronology is truth"）。

### 11.4 装配期 prompt 声明（`app/application.py`）

| 声明 | 追加到 | 内容要点 |
|---|---|---|
| `AGENT_JOB_PROMPT_DECLARATION` | dialog system prompt | `[Agent job result]` 前缀含义：不重复播报 |
| `AGENT_AWARENESS_DECLARATION_TEMPLATE`（`$nickname` 代入） | dialog + omni system prompt | 后台助手身份/能力（查历史、联网、多步任务）与分工口径：点名时简短确认、结果以文字显示不朗读 |

translate prompt 不追加（translate 不读 history）。

### 11.5 边界（接受并文档锁定）

- 无 live 会话（连接建立→session.start 窗口）：① 跳过、② chatlog 写 `chat_session_id=""`；
  `no_history` 计数可观测（窗口极短；补投由 session.started 触发时必有 live 会话）。
- 窗口外超迟重投递：去重窗 = WorkingMemory 窗口（≤20 条）；需"relay 崩溃 + 其间塞满历史 +
  sweep 重投"同时成立，实际不可达；chatlog/lvdata 侧仍由 event_ref 收敛
  （同一 job_key → 同一事件）。
- WorkingMemory 窗口挤占：每条 job 结果占 1 个条目名额（截断管体积不管条数），密集 job 场景
  正常对话容量收缩；当前 job 频率低，接受。
- assistant-role 间接注入：job 结果可能含 agent 处理过的不可信内容，以最高信任的 assistant role
  入场；content 前缀提供弱来源信号，残余风险接受。
- 下游 lvdata（`tools/lvdata-mcp/src/lvdata_mcp/analysis/chatlog.py`）：chatlog 三 jsonl 按
  增量字节读入 → events 表；行 `source=="agent_job"` → 铸造 `type="agent_job"` 事件
  （event_ref = `{rsession}:agent_job:{job_key}`，与 agent_jobs 表对齐）。映射幂等 =
  确定性 event_ref + 内容比对 upsert（重复物理行收敛为同一事件；`chat_messages` 表已删除，
  原文即 jsonl 本身）。

## 12. 测试锚点

| 层 | 文件 |
|---|---|
| provider 流式（timeout/停流文案/http_status） | `tests/unit/llm/test_openai_compat_stream.py` |
| failover（首 token 语义/中途不切换/http_status 传播/aclose） | `tests/unit/llm/test_factory_failover.py` |
| vision 渲染（逐条/单次 warning/vision_model 门控） | `tests/unit/llm/test_vision_payload.py` |
| orchestrator 失败路径与回滚（error 透出/1k 截断/带图/无 TTS/令牌守卫/路径 B 迟到写/中断后仍回滚/v3 回滚） | `tests/unit/session/test_llm_failure_handling.py` |
| 图片历史语义 + 组装裁剪（仅最新 2 张） | `tests/unit/session/test_image_history.py`、`test_image_snapshot.py` |
| WorkingMemory（remove_recent_user 等） | `tests/unit/session/test_history.py` |
| agent job 注入（前缀/截断/去重/omni/NoOp sink/指标） | `tests/unit/session/test_agent_job_ingest.py` |
| 恢复映射 | `tests/unit/session/test_session_manager_restore.py` |
| 投递与消费（job_key/白名单/背压） | `tests/unit/agent_relay/test_delivery.py`、`test_publisher.py` |
| 集成（全败 → error 文本 + 无 error 帧 + 回滚；图片链路；v3 文本轮回滚） | `tests/integration/orchestrator/test_provider_failures.py`、`test_image_pipeline.py`、`test_video_text_turn.py` |
| 重投递全链（WorkingMemory + chatlog 各一条） | `tests/integration/agent_relay/test_redelivery_integration.py` |

## 13. 已知边界（客户端侧，服务端不处理）

- 客户端本地 Room 行不可撤（final + 非空即持久化；`ConversationListenerAdapter.kt`）；
- 已上屏 partial 不可撤：失败 final 的 error 文本在其后拼接展示（本轮有 partial 时服务端加
  `"\n"` 前缀分隔）；
- translate 模式客户端对 final 文本追加 `" (译文)"` 后缀——错误气泡会显示为 `… (译文)`（化妆品级）。

## 14. 非目标

- 不改首 token 5s 看门狗与 SDK 连接级重试；
- read timeout 不配置化（回滚预案 = `_READ_TIMEOUT_S` 改回 600.0，前提是"停流判死"日志信号可区分——现行文案已保证）；
- 不发 error 帧、不新增 status reason / ai_text.source 枚举；
- 历史存储不裁剪图片（组装侧裁剪至最新 2 张，§9.4）；
- chatlog 不做强去重（下游按 event_ref 收敛）；
- omni / lecture 引擎、ASR / TTS 内部重试与降级不随本文调整。
