# Bailian Duplex TTS 自愈重构 v2 —— 评审意见 + 修订设计

> 状态：设计稿。本文 review `tts_bailian_improvements.md`（下称 v1）并给出修订设计；实现以本文为准。
> 范围同 v1：仅 `services/tts/providers/alibaba_bailian.py` + 单测 + 真机脚本；不动 orchestrator/gateway/config。

## 1. v1 评审

### 1.1 事实核对（通过）

对照 `~/temp/livachat.log`（2026-09-04）逐条验证，v1 的故障陈述全部成立：

- 06:00:32.181 eager open 发出空 run-task → 06:00:55.315 `task-failed: request timeout after 23 seconds`（间隔 23.1s，精确吻合）；
- 06:00:59.540 `connection dead, reconnecting before send` → 06:01:05.369 `reconnected`（5.83s），06:01:05.671 首音频；
- 06:03:27.6 末帧 + 23.0s = 06:03:50.6 第二次自爆（轮末 reset 的空任务），精确吻合。

根因判断正确：**空 run-task 空转 >23s 必被服务端杀死；死亡发现是惰性的，重连被放进首包关键路径**。

### 1.2 采纳的部分（v1 正确的设计决策）

- 任务随文本而生：`open()`/`reset()`/`_reconnect()` 只维护 WS，run-task 只在 `feed_text()` 发出 —— 两条自爆路径同时消失，这是唯一治本的改法；
- receiver 会话级常驻、不因单任务结束退出（对齐 qwen）；
- 连接参数补 `ping_interval=20, ping_timeout=20, close_timeout=5`；
- 失败后整段重试一次、再 `ProviderError`（编排层 text-only 兜底已存在，orchestrator.py:1041-1046）；
- `reset()` 幂等、先清标记再发 finish。

### 1.3 修正点（本文相对 v1 的实质差异，5 处）

**M1 — 旧 task-started 会误触发新任务的就绪等待（v1 的 Event 设计有洞）。**
v1 §4.2 写 "task-started → `asyncio.Event.set()`（每次 run-task 前 clear）"，但未要求 task_id 匹配。场景：feed 在 5s 等待中被 cancel（interrupt），run-task 已发出、任务成孤儿；下一轮 feed 发新 run-task 并 clear Event 后，**孤儿任务迟到的 task-started 会 set Event**，新任务被误判就绪——随后 continue-task 带着新 task_id 发出，而新任务其实未 started（行为依赖服务端容忍度），孤儿任务则在 23s 后自爆产生误导性 error 日志。必须：**只有 task_id == `_pending_task_id` 的 task-started 才置位 Event**。

**M2 — "按 task_id 丢弃残留二进制"在协议上不可能，须改为状态不变式。**
v1 §4.2 写 "不匹配的旧任务事件与残留二进制一律丢弃"。但 duplex 协议的二进制音频帧**不携带 task_id**，无法按 id 匹配。正确表述是 receiver 不变式：**仅在存在 active 任务时才入队二进制帧，否则丢弃**（无 active 时到达的帧必然是旧任务残留或错位帧）。配合 "active 仅在 task-started 确认后建立"（见 M5），旧任务残留帧的泄漏窗口收敛为零（reset 清 active 之后、新任务 started 之前的所有二进制都被丢）。

**M3 — task-failed 顶替 task-started 时应快速失败，而不是等满 5s。**
run-task 被拒（配额/参数/模型不可用）时服务端回 task-failed 而非 task-started。v1 的 5s 超时把这类确定性失败拖成 5s 首包延迟，且丢失了服务端的 error_message。设计：`_task_start_error: str | None` + Event；receiver 见 task-failed 且 task_id == pending → 写 error + set Event；feed 被唤醒后见 error 即按"建任务失败"走重试/ProviderError，不重等。

**M4 — flush 后 interrupt 的 reset 会对同一 task 双发 finish-task。**
v1 只防了 "reset 被双调"（pipeline finally 与 `_do_interrupt` 串行各调一次），漏了另一条：interrupt 发生在 `flush()` 之后、audio drain 之中——flush 已发 finish-task，reset 见 active 仍在又发一次。同 task_id 双 finish 的服务端行为未验证（大概率回 task-failed 噪音）。加 `_finish_sent: bool`：flush/reset 发 finish 前置位，新发 run-task 时清零；reset 只在 `active and not _finish_sent` 时补发。

**M5 — pending 态（run-task 已发、task-started 未至）语义未定义。**
v1 只有 `_active_task_id` 一个标记，"发了 run-task 还没确认"这段时间属于灰色地带（cancel 落在这里时，reset 该不该发 finish？下个 feed 该不该复用？）。本文显式拆成两态：`_pending_task_id`（已发未确认）与 `_active_task_id`（已确认）。语义见 §2/§3。

另补两点明确化（非错误，v1 未写）：

- **不加锁的依据**：feed/flush/reset/close 全部入口在编排层已串行——`_cancel_pipeline` `await` 了 pipeline task（orchestrator.py:1463），pipeline finally 的 reset 先于 `_do_interrupt` 的 reset 完成；与 qwen 一致，不引入 asyncio.Lock。
- **flush/close 不做 `_ensure_ready`**：连接已死时任务随之消失，为发 finish-task 而重连无意义；best-effort 吞掉即可。

## 2. 状态机与不变式

### 2.1 流状态字段

| 字段 | 含义 |
|---|---|
| `_ws: ClientConnection \| None` | None = 连接已死（receiver 异常置空），下个发送入口必须重连 |
| `_recv_task` | 会话级常驻 receiver；只在 `_reconnect`/`close` 时被取消重建 |
| `_pending_task_id: str \| None` | run-task 已发送、task-started 未确认 |
| `_active_task_id: str \| None` | task-started 已确认；二进制帧只在此期间入队 |
| `_finish_sent: bool` | 本任务已发过 finish-task（防双发，M4） |
| `_task_started: asyncio.Event` | task-started/task-failed(=pending) 到达时置位 |
| `_task_start_error: str \| None` | task-failed 顶替 task-started 时的服务端错误（M3） |

任务生命周期：`None → pending（feed 发 run-task）→ active（task-started）→ None（task-finished/failed 或 reset）`。任意时刻 pending/active 至多一个非 None。

### 2.2 receiver 不变式

- **I1**：二进制帧仅当 `_active_task_id is not None` 时入队，否则丢弃（M2）；
- **I2**：`task-started` 仅当 task_id == pending 时生效（→ active、清 pending、set Event，M1）；不匹配的记 debug 丢弃；
- **I3**：`task-finished`/`task-failed` 仅当 task_id == active 时放哨兵并清 active；== pending 时写 `_task_start_error` + set Event（M3）；其余记 debug 丢弃（不再出现误导性 error 日志）；
- **I4**：`async for` 抛错（连接异常）→ `_ws = None`，有 active 时放哨兵（audio() 立即结束而非挂 30s），任务标记全清；**有 pending 等待者时**（feed 正等 task-started）写 `_task_start_error`（"connection lost before task-started"）+ 置 Event 立即唤醒——连接死亡意味着任务不可能再被确认，feed 不白等满 5s 超时（与 I3 的 pending 通知同通道；唤醒者已被 cancel 则无人消费，无副作用）；**不 return 的语义仅限任务结束**——连接死亡时 loop 本就无法继续，允许退出，由 `_ensure_ready` 重建；
- **I5**：哨兵一个任务至多一个（finished/failed 匹配 active 才放），消费端每轮恰好收一个。

## 3. 各方法精确语义

### 3.1 `open()`（provider）

`_connect_raw()`（带 `ping_interval=20, ping_timeout=20, close_timeout=5`）→ 建流、起 receiver，返回。**不发 run-task、不做任务级握手**。
权衡：协议可用性确认推迟到首个 feed（v1 §5.1 注，维持）；连接级失败（DNS/TCP/握手 401）仍在 open 抛出，`TtsService` 的 open-time failover 不受影响。已考虑 "open 时 run-task + 立即 finish 做 eager 验证" 的替代方案，否决——增加一次未验证行为（即时 finish）的协议往返，收益只是把失败暴露点提前，而 feed 路径已有完整兜底（§6）。

### 3.2 `feed_text(text)` —— 唯一建任务点

1. `_ensure_ready()`（receiver 死 + WS 死 → `_reconnect()`；receiver 死而 WS 活 → 原地重启 receiver）；
2. 若 `_pending_task_id` 残留（上轮 feed 被 cancel 在等待中）→ 清掉（孤儿任务事件按 I2/I3 丢弃，23s 自爆无害）；
3. 若 `_active_task_id is None`：
   a. drain 队列（清旧哨兵与 abandon drain 残留帧）；
   b. `_pending_task_id = uuid4()`，清 Event / `_task_start_error` / `_finish_sent`；
   c. 发 run-task（空 input）；
   d. `await wait_for(_task_started.wait(), 5s)`：
      - 醒来看见 `_task_start_error` → 建任务失败（M3）；
      - 超时 → 建任务失败；
      - 发送抛错 → 建任务失败；
   e. 建任务失败 → `_reconnect()` + **整段重试一次**（新 pending、新 run-task、重等）→ 仍败 `ProviderError`；
4. 发 `continue-task(text)`；发送抛错 → `_reconnect()` + 整段重试一次 → 仍败 `ProviderError`。
   （active 已存在时直接发 continue-task——保留 duplex 增量喂文本能力；编排层当前每轮只 feed 一次，该分支为协议能力兜底。）

注意重试的幂等性：若首次 run-task 实际已到达服务端只是 ack 丢失，重试发生在新 WS 上，旧连接已关、旧任务随之消亡，无重复音频风险。

### 3.3 `flush()`

`active and not _finish_sent` 时 best-effort 发 finish-task 并置 `_finish_sent`；否则 no-op。发送失败仅 warning（任务将由服务端 23s 超时清理，audio() 经 I3/I4 哨兵结束）。不重连。

### 3.4 `reset()` —— 幂等清场，不发 run-task

1. 摘出 `tid = _active_task_id`、记下 `was_active`；**先清** `_active/_pending/_finish_sent`，drain 队列（先清标记是 reset 双调幂等的关键，同 v1）；
2. `was_active and not _finish_sent` 且连接可用 → best-effort finish-task（interrupt 中止在途合成，停服务端计费/出音）。**pending 态任务不发 finish**（服务端可能尚不认识它；让其自然超时，事件按 I3 丢弃）；
3. `_ensure_ready()`（reset 时发现死连接就地把重连成本移出下一轮首包路径，对齐 qwen reset 语义）。

### 3.5 `close()` / `_reconnect()` / `_closed()`

- `close()`：cancel receiver → 兼容 None/CLOSED/CLOSING 地关 WS，幂等，不发任何任务消息；
- `_reconnect()`：cancel receiver → 容错关旧 WS → `_connect_raw()` → drain 队列、清 pending/active/`_finish_sent`/`_task_start_error`/Event → 起 receiver。**不发 run-task**；失败置 `_ws = None` 并抛出（close 安全性已有回归测试锁）；
- `_closed()`：先判 `None` 再判 `protocol.state`（现有行为，保留）。

## 4. 故障路径推演

| 场景 | 行为 |
|---|---|
| v1 故障 A（eager open 后 27s 无文本） | open 不建任务，无 23s 计时；首 feed 时 WS 活着（ping 保活），run-task→started≈1 RTT→continue-task，首音频预期 0.3-0.6s |
| v1 故障 B（轮末 reset + 轮间 >23s） | reset 不建任务，无计时器存在 |
| run-task 被拒 | task-failed(=pending) → 立即醒（M3）→ 重连重试一次 → ProviderError → 编排层 text-only |
| ack 丢失/5s 超时 | 同上；旧任务随旧连接消亡 |
| feed 等 started 期间连接死 | receiver 按 I4 立即唤醒 pending（不等 5s）→ 建任务失败 → 重连重试一次 |
| interrupt 在 feed 等待中 | cancel 落在 3d → finally reset：pending 被清、不发 finish；孤儿事件全丢弃（I2/I3） |
| interrupt 在 drain 中 | flush 已置 `_finish_sent` → reset 只清场不双发（M4）；迟到 task-finished 按 I3 丢弃 |
| interrupt 在 flush 前 | reset 见 active 且未 finish → 补发 finish-task 中止合成 |
| 服务端无任务 idle 断连 | receiver 死 + `_ws=None`/CLOSED → 下个 feed/reset `_ensure_ready` 重连（~1s 级，无任务级 5.8s） |
| receiver 死 WS 活（理论路径） | `_ensure_ready` 原地重启 receiver（对齐 qwen） |

## 5. 改动清单

### 5.1 `services/tts/providers/alibaba_bailian.py`（唯一生产代码）

1. `__init__`：新增 §2.1 五个任务态字段；
2. `_connect_raw`：补 `ping_interval=20, ping_timeout=20, close_timeout=5`；
3. `open()`：去掉 run-task 发送，连 WS + 起 receiver 即返回（`start_payload` 仍存入流，供 feed 建任务）；
4. `_receive_loop`：按 I1-I5 重写事件分支；会话级常驻（task 结束不 return）；异常置 `_ws=None` + 有 active 放哨兵 + 清任务标记；保留匹配 active 的 task-failed error 日志，不匹配的降 debug；
5. 新增 `_ensure_ready()`；
6. `feed_text()`：按 §3.2 重写；
7. `flush()`：按 §3.3；
8. `reset()`：按 §3.4；
9. `_reconnect()`：去掉 run-task，清全部任务标记；
10. `_closed()`/`close()`/`audio()`/`poll_audio()`：维持现语义。

### 5.2 `services/tests/unit/tts/providers/test_bailian.py`

- 现有 mock 时序改：run-task 移至 feed 内（open/reset 不再触发）；
- 回归锁（v1 已有，保留）：open/reset 后零 run-task；同 WS 两轮（连接数==1、新 task_id）；reset 中断合成只发一次 finish；started 超时→重连一次成功、连续失败→ProviderError；receiver 崩溃后 feed 自愈；workspace header / CLOSING / close 幂等；
- **新增（对应 M1-M4）**：
  - M1：pending 期间收到**旧 task_id** 的 task-started → Event 不被误置，feed 仍等到新任务 started；
  - M2：无 active 时收到二进制帧 → 丢弃，不进队列（紧接的新轮音频纯净）；
  - M3：run-task 后回 task-failed → feed 立即 ProviderError（远小于 5s），且错误信息透传；
  - M4：flush 后 reset → 同一 task_id 只发过一次 finish-task。

### 5.3 `services/scripts/test_bailian_tts_real.py`

同 v1：`--multi-round` + `--idle-s 30`，验证同 WS 顺序多任务与无任务 idle 不断连两个协议假设。

## 6. 已考虑并否决的替代方案

- **open 时 eager run-task + 立即 finish 做协议验证**：多一次依赖未验证行为（即时 finish）的往返；失败暴露点提前的收益由 feed 路径兜底覆盖。否决。
- **单任务常驻 + 空 continue-task 心跳保活**：依赖空文本不产出音频/不报错的服务端行为，hack 且引入常驻计时器。否决。
- **每轮新建连接**：每轮 +0.5-1s 握手进首包路径，放弃会话复用。否决。
- **编排层 watchdog/预测性重连**：同 v1，另立项；provider 层修复后首包路径已无重连，watchdog 属优化而非修复。

## 7. 验证

> 注：`uv run pytest` 会因私有 workspace 包（livachat-protocol）无法从 registry 解析而失败；用 venv 内解释器直接跑（README：先 `source .venv/bin/activate` 再裸 pytest 亦可）。

1. `cd services && .venv/bin/python -m pytest tests/unit/tts/providers/test_bailian.py -q` 全绿（M1-M4 及 I4 pending-wake 等 20 用例）；
2. `.venv/bin/python -m pytest tests/unit/tts/providers/test_qwen_tts.py -q` 参照实现无牵连；
3. 真机：`--multi-round --idle-s 30` → 单轮首音频 ~0.3-0.6s、第二轮同连接、idle>23s 后可合成、全程无 `request timeout after 23 seconds`；
4. 完整 app 回归：首轮 + 停顿 30s 再说话，日志无 `bailian tts task failed: request timeout` 及首包路径上的 `connection dead, reconnecting before send`。

## 8. 残留风险（真机确认）

- 同 WS task-finished 后顺序 run-task：协议事件全部 task_id 作用域，多任务路由是协议一等公民，信心高；若被拒表现为无 task-started → 走重连降级（新 WS 必然可用），真机确认无反复；
- 无任务 idle 时服务端若在 WS 层断连：发现仍是惰性（下入口重连 ~1s），可接受；真机复现再立项编排层看护；
- 旧任务残留音频泄漏窗口已由 I1 + "active 仅在 started 后建立"收敛（reset 清 active 与新任务 started 之间的二进制全丢弃）；理论残余：旧任务最后一帧与新任务 started 完全同时到达——概率可忽略，不处理。
