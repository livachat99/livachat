# Lecture 同传改进 —— 模式切换阻塞治理（15s → ~1s）调研与设计

> 状态：**已实施（2026-09-14）**。A/B/C 全部落地；单测 1229 用例绿（含新增 4 条契约/
> 并发用例）、集成 1 例；真机验收：**进入 lecture 1.11s（改前 15.1s）、退出 1.01s（改前 10s）**，
> 服务端日志无停读/积压特征（§5.4）。
> 追加（同日 review）：修复 **lecture→lecture 重入泄漏**——切目标语言时客户端重发
> `session.change(translate, lecture)`，"cut out"关闭分支被跳过 → 旧引擎被直接覆盖（旧 WS/
> 事件泵永久泄漏）。修复：`_enter_lecture_mode` 开场先 `_close_lecture_engine`（§7 末行）；
> 真机验证 2 开 2 关、重入 ack 1.10s。
> 本文仅覆盖 2026-09-14 真机日志分析中的「问题 1：进入 lecture 模式阻塞 15s」；
> 同日日志中的「问题 2：客户端停送音频后服务端无看护」属客户端侧 + 看护策略，另行处理。
> 范围：`services/asr/providers/*`、`services/tts/providers/*`、`services/omni/providers/*`
> （connect 限时）+ `services/session/orchestrator.py`（关闭并发化）+ 测试 + 验收脚本。
> 结论文先：**根因是"百炼 WS 不回 close 帧 + `await ws.close()` 等满 `close_timeout`
> + orchestrator 派发循环内串行关闭"三者叠加；修复 = 源头限时（`close_timeout=1`）
> + 关闭并发化。15.1s → ~1s，无协议/行为语义变更。**

## 1. 背景与目标

- 2026-09-14 真机日志 `~/temp/livachat.log`（03:59:58–04:06:49 UTC）分析：全程 0 个
  ERROR/WARNING，启动/连接/agent relay/recording/心跳均正常；**唯一严重问题**是客户端
  `session.change` 切 lecture 时服务端阻塞 **15.1s**。
- 阻塞的可观测危害（日志实证）：
  1. 客户端 15.1s 收不到 `session.changed` 回执；
  2. 网关读循环被背压停读 socket 10.6s（TCP 背压），恢复瞬间 ~9s 积压音频一次性灌入；
  3. 积压音频被压缩转推给同传模型（≈15s 音频在 1.5s 内推完），破坏"实时同传"前提。
- 目标：模式切换阻塞 **15s → ~1s**，消除上述三个危害；覆盖进入/退出 lecture 与
  session 收尾全部关闭路径。
- 非目标：不改协议、不改客户端、不引入后台关闭（见 §4.6）。

## 2. 证据（2026-09-14 真机日志 + 同端点复测）

### 2.1 日志时间线（UTC）

| 时刻 | 事件 |
|---|---|
| 04:03:03.855 | 收到 `session.change`（切 lecture）；`_enter_lecture_mode` 开始 |
| 04:03:03.856 | `cancelling ASR poll task` —— 其后**开始关 ASR/TTS 流，无任何日志** |
| 04:03:08.3 | 网关读循环停止出日志（`_inbound` 队列填满 → 背压） |
| 04:03:08.3 → 18.9 | **10.6s 全日志空档**（不读 socket） |
| 04:03:18.937 | `alibaba livetranslate open …`（15.1s 后才开始建同传连接） |
| 04:03:19.001 | `session.changed` 才回给客户端（**延迟 15.1s**） |
| 04:03:19.005–19.05 | `recv audio` seq **3265→3716**（≈9s 音频）在 ~70ms 内涌入 |
| 04:03:19.28–20.28 | `input_audio_buffer.append` **evt_1→evt_772（≈15s 音频）1.5s 内推完** |

### 2.2 同端点、同代码路径复测（2026-09-14，实测值）

| 操作 | 实测 | 说明 |
|---|---|---|
| `ASR close`（bailian duplex） | **10.00s** | `websockets` 默认 `close_timeout=10`，ASR provider 未设 |
| `TTS close`（bailian duplex） | **5.00s** | TTS provider 显式 `close_timeout=5`（代码注释已知端点不回 close 帧） |
| `LiveTranslate open`（含握手） | 0.10s | 排除"连接慢"假设；workspace 域名+头路径无退化 |
| `LiveTranslate close` | **10.00s** | 该 provider 未设超时（退出 lecture / session 收尾同样受影响） |

`10 + 5 + 0.08(握手) ≈ 15.1s`，与日志窗口精确吻合。**百炼三个端点均不回 close 帧**
（实测 3/3），故该阻塞是必然路径，不是偶发。

### 2.3 放大机制与库行为

- 背压链：派发循环阻塞 → `_inbound`（`gateway/connection.py:67`，`maxsize=256` ≈ 5.1s
  音频）填满 → 网关读循环阻塞在 `await put` → TCP 背压 → 客户端积压 → 恢复后突发回放。
- `websockets` 源码确认（`.venv/.../websockets/asyncio/connection.py:50,909-945`）：
  `close_timeout` 是**单个总预算**（`close_deadline`），到点由库自行关闭 socket，
  **无连接泄漏**；`0` 亦合法（发完 close 帧立即终止）。

## 3. 现状核对（codebase，逐条可溯源）

### 3.1 阻塞点

| 位置 | 行为 |
|---|---|
| `session/orchestrator.py:664-676`（`_enter_lecture_mode`） | **串行** `await` 关闭 ASR、TTS 两条 WS（问题 1 热路径） |
| `session/orchestrator.py:478-489`（`_handle_session_end`） | 串行关闭 ASR/TTS/Lecture 引擎 |
| `session/orchestrator.py:303-313`（`run()` finally） | 同上 |
| `session/lecture_translate.py`（`close()`） | `await stream.close()` → `ws.close()`（10s） |

### 3.2 connect 点与既有 `close_timeout` 约定

| 现状 | 点位 |
|---|---|
| 已设 5s | `tts/providers/alibaba_bailian.py:413`、`tts/providers/alibaba_qwen_tts.py:79,324`、`agent_relay/adapters/hermes.py:470` |
| **未设（默认 10s）** | `asr/providers/alibaba_bailian.py:205`、`omni/providers/alibaba_livetranslate.py:102,497`、以及 asr/tts/omni 其余 provider（deepgram/elevenlabs/qwen_asr/cartesia/alibaba_omni/openai_realtime） |

活动链路：ASR=`alibaba_bailian`、TTS=`alibaba_bailian`、lecture=`alibaba_livetranslate`。

## 4. 设计

### 4.1 方案总览

| # | 改动 | 效果 |
|---|---|---|
| A | 三个活动 provider 的 `websockets.connect` 补 `close_timeout=1` | 单条 close 上限 10/5s → 1s |
| B | `_enter_lecture_mode` 的 ASR/TTS 关闭改并发（`asyncio.gather` + null 安全 helper） | 入口阻塞 2s → ~1s |
| C（可选，建议） | 同类清扫：其余 provider 补 `close_timeout=1`；`_handle_session_end`/`run() finally` 同样并发化 | 覆盖配置切换与收尾路径 |

### 4.2 A：provider 源头限时

| 文件 | 行 | 现值 | 改为 |
|---|---|---|---|
| `services/asr/providers/alibaba_bailian.py` | 205（open） | 默认 10s | `close_timeout=1` |
| `services/tts/providers/alibaba_bailian.py` | 413-420 | 5s | `close_timeout=1` + 注释同步 |
| `services/omni/providers/alibaba_livetranslate.py` | 102（`_reconnect`）、497（open） | 默认 10s | `close_timeout=1` |

选 1s：正常对端 close 握手是 RTT 级（毫秒），1s 富余两个数量级；对不回应的端点，
到点由库关 socket（§2.3）。

### 4.3 B：入口关闭并发化

`session/orchestrator.py` 新增模块级 helper：

```python
async def _close_quietly(stream: Any) -> None:
    """null 安全；异常语义与现状一致（吞掉）。"""
    if stream is None:
        return
    try:
        await stream.close()
    except Exception:
        pass
```

`_enter_lecture_mode` 664-676 改为先取引用、置 `self._asr_stream/_tts_stream = None`，
再 `await asyncio.gather(_close_quietly(asr), _close_quietly(tts))`。两条连接彼此独立、
无共享状态，时序无约束。

### 4.4 C：同类清扫（建议一并做，镜像 (a)/(b) 先例）

- 其余 provider（`alibaba_qwen_asr`/`deepgram`(asr)/`elevenlabs`(asr)/`alibaba_qwen_tts`/
  `deepgram`(tts)/`elevenlabs`(tts)/`cartesia`/`alibaba_omni`/`openai_realtime`）
  同补 `close_timeout=1` —— 防"配置切 provider 后复发"。
- `_handle_session_end`（478-489）、`run()` finally（303-313）换 `_close_quietly` + gather
  —— session 收尾少卡 2~3s，`session.ended` 更快发布给 agent relay。

### 4.5 边界矩阵

| 场景 | 现状 | A+B | A+B+C |
|---|---|---|---|
| 进入 lecture（ASR+TTS bailian） | **15.1s 阻塞** | ~1s | ~1s |
| 退出 lecture（LiveTranslate close） | 10s | ~1s | ~1s |
| session.end / 连接断开收尾 | ~15s 串行 | ~15s（未改） | ~1s |
| 对端**正常**回 close 帧 | 立即返回 | 立即返回 | 立即返回 |
| 配置切到其他 provider | 10s 默认 | 未覆盖 | ~1s |
| 网关 inbound 队列 | 填满→停读→积压回放 | 不填满，无背压 | 同 |
| 客户端 `session.changed` 延迟 | 15.1s | ~1s | ~1s |
| 音频积压回放 | 9s 一次性灌入 | 无（≤1s） | 无 |

### 4.6 已考虑并否决

1. **后台 fire-and-forget 关闭**（不 `await`，`create_task(stream.close())`）：可把 ack
   降到 ~0ms，但引入任务生命周期管理（跟踪/取消/异常/与 session 收尾的交叉）；且 1s
   残余已不再触发队列填满与积压回放，无可观测收益。**否决**。
2. **lecture 期间不关 ASR/TTS、退出时复用**：ASR 有停帧/静音判死（23s/300s 经验），
   lecture 时长不可控，连接大概率已死；且白占资源。**否决**。
3. **缩小 inbound 队列/加大队列**：治标不治本（阻塞仍在），且队列大小有背压保护语义。
   **否决**。
4. **给 `ws.close()` 套 `asyncio.wait_for`**：超时取消会把连接留在 CLOSING 半开态、
   不触发库内 abort（可能泄漏）。正确做法是 connect 时交 `close_timeout`（§2.3）。
   **否决**。

## 5. 测试与验证

### 5.1 单测

1. `close_timeout` 契约锁（镜像 `test_bailian.py` 的 workspace 头契约测试写法）：
   ASR bailian open、TTS bailian、LiveTranslate open + `_reconnect` —— 断言
   `websockets.connect` kwargs 含 `close_timeout=1`（4 用例）。
2. `_enter_lecture_mode` 并发语义：两个 fake stream 的 `close()` 用 rendezvous
   （双方都"开始"才算完成，带超时）——串行实现必超时失败，天然判并发，无墙钟抖动。
3. 异常吞掉 + slot 置 `None` 的回归（沿用现有用例）。

### 5.2 集成（`tests/integration/orchestrator/`）

fake ASR/TTS 的 `close()` 各 sleep 0.3s，走 `run()` + `session.change(lecture)`，
断言 `session.changed` 在 ≤0.5s 内入队（串行=0.6s，可区分）。

### 5.3 真机验收（复现日志场景）

按 `services/scripts/measure_e2e_asr_final_latency.py` 的 fake-device 先例：本地起真
server（真百炼）→ 合成客户端 `session.start` → 喂音频 → `session.change(lecture)`，量：

- `session.change` 发出 → `session.changed` 收到 延迟（**期望 ≤1.5s**，改前 15.1s）；
- 期间的音频突发（期望无 9s 级突发）；
- 退出 lecture 同样量一次（期望 ≤1.5s）。

结果记入 commit message（与 ASR/TTS 先例同款）。

### 5.4 实测结果（2026-09-14 实施后）

真机验收（本地真 server + 真百炼；`services/scripts/measure_lecture_switch_latency.py
--repeats 2`）：

| 指标 | 改前 | 改后（run 1 / run 2） | 达标线 |
|---|---|---|---|
| 进入 lecture 回执（session.change → session.changed） | **15.1s** | **1111ms / 1092ms** | ≤1.5s ✅ |
| 退出 lecture 回执 | **10s** | **1005ms / 1004ms** | ≤1.5s ✅ |

服务端日志核对（`/tmp/lecture_accept_server.log`）：

- 切换窗口全程无 >3s 日志空档（唯一空档是启动后首客户端前的空闲）——**停读/背压消失**；
- 入口内部：`session.change` → `livetranslate open` 1.06s ≈ 并发界（ASR 1s ∥ TTS 1s；
  串行应为 2s）——并发关闭生效的直接证据；
- 无积压音频突发（原日志的 `evt_1→evt_772 / 1.5s` 特征不再出现）。

单测/集成：全量 **1228 passed, 1 skipped**（新增：ASR/TTS/LiveTranslate close_timeout 契约
4 条、入口并发 rendezvous 1 条、慢 close 集成 1 条）；ruff clean。

## 6. 风险与未决项

- **R1**：1s 内未完成合法握手的对端会被提前 abort（TCP 直接关）。后果=对端可能残留
  会话记录，本地无泄漏；百炼实测从不回 close，无差别。
- **R2**：TTS 5→1 收紧了既有决策（当时注释为"防 shutdown/reload 卡数十秒"）。如保守，
  可只改 ASR/LiveTranslate，代价是入口仍有 TTS 5s（会触发队列填满，危害残留）。
- **R3**：gather 改变两条 close 的相对时序——两条连接独立，风险≈0。
- **R4**：connect 握手阶段仍留 websockets 默认 `open_timeout=10`（实测 0.1s，本次不动）。
- **R5**：不做 C 时，配置切换 provider 后同类阻塞复发（`alibaba_qwen_asr` 当前 enabled
  但非 active/fallback）。
- **未决**：问题 2（客户端停送音频 + 服务端无看护）另案处理。

## 7. 改动清单（实施记录，2026-09-14）

| 项 | 文件 | 说明 |
|---|---|---|
| A | `asr/providers/alibaba_bailian.py`（open）、`tts/providers/alibaba_bailian.py`（`_connect_raw`，5→1）、`omni/providers/alibaba_livetranslate.py`（open + `_reconnect`） | `close_timeout=1`（各附注释指向本文） |
| B | `session/orchestrator.py` | 新增模块级 `_close_quietly`；`_enter_lecture_mode` 两路 close 改 `asyncio.gather` |
| C1 | `asr/providers/{alibaba_qwen_asr,deepgram,elevenlabs}.py`、`tts/providers/{alibaba_qwen_tts(×2,5→1),deepgram,elevenlabs,cartesia}.py`、`omni/providers/{alibaba_omni(×2),openai_realtime(×2)}.py` | 同补 `close_timeout=1`（全库 connect 点已核对覆盖） |
| C2 | `session/orchestrator.py` | `run()` finally 与 `_handle_session_end` 三路 close 改 gather |
| 测试 | `tests/unit/asr/providers/test_bailian.py`、`tests/unit/tts/providers/test_bailian.py`、`tests/unit/omni/providers/test_alibaba_livetranslate.py`、`tests/unit/session/test_lecture_translate.py`、`tests/integration/orchestrator/test_lecture_switch.py`（新） | 契约 4 + 并发 1 + 集成 1 |
| 验收 | `services/scripts/measure_lecture_switch_latency.py`（新，`measure_*` 同族） | 进入/退出切换延迟测量；`--repeats/--feed-ms/--lecture-hold-ms` |
| 配置 | `services/pyproject.toml` | 新脚本的 ruff per-file-ignores（E402/I001，镜像 measure_e2e_*） |
| 追加 | `session/orchestrator.py::_enter_lecture_mode` | **重入 lecture 先关旧引擎**（切语言重发 session.change 时旧 WS/事件泵被覆盖泄漏）；回归用例 `test_reenter_lecture_closes_old_engine` |

## 8. 决策点（已定，2026-09-14 review 通过）

1. **C（同类清扫）→ 做**：全库 connect 点统一 `close_timeout=1`，配置切 provider 不复发（消 R5）。
2. **TTS 5→1 → 接受**：该端点实测同样不回 close 帧，5 与 1 行为一致（消 R2 顾虑）。
3. **验收脚本 → 落库** `services/scripts/measure_lecture_switch_latency.py`（与 `measure_*` 同族）。

## 9. 参考

- 日志：`~/temp/livachat.log`（2026-09-14 03:59–04:06 UTC，791 行；0 ERROR/WARNING）
- 库行为：`websockets/asyncio/connection.py:50,909-945`（`close_timeout` 语义与 abort）
- 队列：`gateway/connection.py:67`（`_inbound` maxsize=256）
- 先例：`docs/asr_bailian_improvements.md`（同类"真机实测驱动"文档风格）、
  `docs/fd_pipeline_translate_lecture.md`（lecture 特性设计）
