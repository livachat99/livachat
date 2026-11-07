# session.change 失败专用错误码 1009 —— 分析与修改方案

**状态**: 已实施（2026-10-03：服务端 + Android App + 协议文档已按 §3–§7 落地；服务端 1175 unit 全绿、App 相关单测全绿）
**日期**: 2026-10-03
**范围**: 服务端（services/）+ Android App（apps/android/LivaChatApp）+ 协议文档（docs/ws-protocol-design.md）；**不动** SDK、JSON Schema、mock server（见 §8 理由）

---

## 1. 现状（以 codebase 为准）

### 1.1 App 侧：回声窗口内"任何错误"都触发模式回落

`ConversationListenerAdapter.onError`（apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/conversation/ConversationListenerAdapter.kt:318-345）：

- 用户在 UI 触发模式切换时，ViewModel **乐观置位**开关并 `markModeChangePending(mode)`（调用点：ConversationViewModel.kt:389/429/481/512），随后发 `session.change`；
- 在收到 `session.changed` 回声前（`pendingModeChange != null` 的"乐观窗口"），**收到任何 error 帧**（不看 code）即视为"本次切换失败"：
  - 清 `pendingModeChange`；
  - 对应模式 flag 回落为 false（translate 连带 lecture）；
  - code ∈ `permanentModeRejectCodes = {4001, 4003, 1006}`（:96）时清 `lastModeRequest`（不再断线自动恢复）；
- `onSessionChanged`（:310-316）到达时清 `pendingModeChange` 并按回声设置四个 flag。

该行为的原始依据是注释 :88-90/:321："服务端拒绝帧 fatal=false 会映射为 NetworkError，故不能只认 ServerError"——即因为**没有专属错误码**，只能用"窗口内任意错误"近似"切换被拒"。

### 1.2 服务端侧：session.change 被拒时下发什么码（实测枚举全部发码路径）

`orchestrator._handle_session_change`（services/session/orchestrator.py:626）+ `_send_mode_unavailable`：

| 触发条件 | 位置 | 当前发码 |
|---|---|---|
| 会话未建立（session.change 先于 session.start，重连竞态） | orchestrator.py:628-633 | `make_error(code=4003, message="session not started")` — **回绝，无 session.changed** |
| 非法 mode 字符串 | orchestrator.py:636-642 | `make_error(code=4001, message=f"invalid mode: {mode}")` — **回绝，无 session.changed** |
| omni 不可用（两处失败路径均在 `_enter_omni_mode` 内：`omni_service is None` 或 `engine.open()` 抛错，都走 `_send_mode_unavailable`） | 发码 orchestrator.py:754-758；返回 False 后调用方 :670-674 直接 return | `make_error(code=4001, message="omni mode unavailable")` — **回绝，无 session.changed** |
| lecture 开流失败 | orchestrator.py:940-945 | `make_error(code=1008, ...)`，**但随后仍发 `session.changed(mode="translate")`**（降级为逐轮翻译，切换算成功） |

补充事实（均已核实，影响方案边界）：

1. **"invalid mode" 在当前线上不可达**：`session.change.payload.mode` 是 `ClientSessionChangeMode` 枚举（packages/sdk-python/src/livachat_protocol/generated/protocol_models.py:241-247），网关用 `ClientMessageAdapter.validate_json` 整体校验（services/gateway/codec.py:51-59），非法 mode 在协议层就被 **1006**（`"invalid JSON"`）拒绝，到不了 orchestrator。orchestrator 的 4001 分支是防御性代码——单测直接调 `_handle_session_change` 绕过校验（services/tests/unit/session/test_transcribe_mode.py:417-427）。**结论：线上真实会发生的"切换被拒"只有 omni 不可用（4001）与会话未建立（4003）两类。**
2. App 的 SDK 层在发送前就过滤非法 mode（apps/android/LivaChatSDK/.../internal/session/SessionManager.kt:209-212，只打印警告不发帧），所以 App 不可能因非法 mode 收到 1006 作为"切换被拒"。
3. App 对错误码的其它消费：没有任何地方按 4001/4003/1006 展示特殊 UI；错误统一走 banner（ConversationScreen.kt:579-598）。code 语义只影响 §1.1 的回落逻辑与 `lastModeRequest` 清理。
4. SDK 对 code 完全不透明透传（SessionManager.handleError :303-318，仅额外做 `fatal → emitForceFlush(SERVER_ABORT)`）；Kotlin/Python DTO 中 error.code 是纯整数（UInt16 / Int），**无码枚举，无需 codegen**。

### 1.3 问题：非切换原因的错误会误伤模式切换

当前"窗口内任意错误即回落"造成三类错误行为（其中第 3 类已被现有测试固化为"预期"）：

1. **无关瞬态错误取消有效切换**：切换请求在途时恰好收到 1001（ASR 不可用，服务端每 30s 节流重发）或 1007（omni 流错误）等与本次切换无关的错误 → 开关被错误回落为关；若回声稍后到达（S2C 乱序由客户端 200ms reorder buffer 缓冲，协议 §4.1.1），UI 出现"闪断"；若回声与错误乱序跨 buffer 窗口，还可能停留错误状态。
2. **lecture 入口失败的回落闪烁**：服务端降级路径（1008 先发、`session.changed(mode="translate")` 后发，orchestrator.py:940-945 → :742-748）在 App 上表现为"先回落（1008 触发）→ 再被回声置回"，净效果正确但有中间闪烁。
3. **语义含糊被测试固化**：`transient_error_keeps_request_retries_on_next_ready`（ConversationListenerAdapterModeRestoreTest.kt:193-215）断言"1001 也回落但保留请求"——用 1001 代表"瞬态错误"，却与"切换是否失败"毫无关系。

目标（用户定稿语义）：**只有专属错误码才表示 `session.change(mode=xxx)` 失败**；其他错误不得触碰模式状态，也不得影响后续 `session.changed` 回声的结算。

---

## 2. 方案：新增错误码 1009 = `SESSION_CHANGE_FAILED`

### 2.1 语义定义

> `1009 SESSION_CHANGE_FAILED`：服务端拒绝了本次 `session.change` 请求，**该请求不会再有 `session.changed` 回声跟随**。

- 码含义与连接存活无关（fatal 仍恒 false；会话继续，模式保持切换前的旧模式）；
- 客户端以"最近一次未确认的切换请求"（`pendingModeChange`）为对象回滚，**不解析 message 文本**；
- 该码与 `session.changed` 互斥：收到 1009 ⇒ 不会收到对应回声；收到回声 ⇒ 不会收到对应 1009（服务端发码路径已核实，见 §1.2）。

### 2.2 与既有码的边界（明确不做变更的部分）

| 码 | 保持现状的理由（已核实） |
|---|---|
| **1008 lecture 降级** | 切换实际成功（发 `session.changed(mode="translate")`），1008 只是降级通告。改后 App 不再回落 → 回声负责置位，行为净结果不变且消除闪烁 |
| **1007 omni 流错误** | 运行期引擎错误（懒重开），与 session.change 无关 |
| **1001 ASR 不可用** | 运行期故障提示，与切换无关 |
| **1006 报文错误** | 协议层拒绝；App 经 SDK 不可能触发该路径（§1.2-2） |
| **4001 作误差帧** | 语义改为"仅剩 WS close code（连续 10 帧解码失败，gateway/connection.py:223 硬编码）+ AuthError 默认码（HTTP 401 路径）"，error 帧下发路径全部迁至 1009 |
| **4003 作误差帧** | 唯一使用点即为"切换被拒"（orchestrator.py:630），迁至 1009 后 4003 变为纯枚举保留 |

---

## 3. 服务端修改清单（已定位到行）

### 3.1 services/shared/utils/errors.py

在 :36（`TRANSLATE_UNAVAILABLE = 1008`）后追加：

```python
    # session.change 被拒（该请求不会跟随 session.changed 回声）；客户端据此回滚模式开关
    SESSION_CHANGE_FAILED = 1009
```

（与该文件既有命名风格一致：AUTH_FAILED / TRANSLATE_UNAVAILABLE。不放 20xx——20xx 注释语义是"鉴权与会话"，而 10xx 注释是"provider-level (10xx, fatal=false)"，1009 同为 fatal=false 的应用级下发码，归 10xx 与线上现状一致。）

### 3.2 services/session/orchestrator.py（3 处，`ErrorCode` 已在 :24 导入）

1. **:629-631**（会话未建立）：

```python
            frame = self._codec.make_error(
                code=ErrorCode.SESSION_CHANGE_FAILED, message="session not started", fatal=False
            )
```

2. **:638-640**（非法 mode，防御性分支）：

```python
            frame = self._codec.make_error(
                code=ErrorCode.SESSION_CHANGE_FAILED, message=f"invalid mode: {new_mode}", fatal=False
            )
```

3. **:755-757**（`_send_mode_unavailable`，omni 不可用）：

```python
        frame = self._codec.make_error(
            code=ErrorCode.SESSION_CHANGE_FAILED, message="omni mode unavailable", fatal=False
        )
```

message 文本保持原样（协议约定 message 仅供调试，客户端不得据此分支）。

---

## 4. 客户端修改清单（Android）

### 4.1 ConversationListenerAdapter.kt

**删除** :94-96 的 `permanentModeRejectCodes`（整段注释 + 定义），替换为常量：

```kotlin
    companion object {
        // 协议 §4.3：session.change 被拒专用错误码。服务端拒绝时不再发 4001/4003，
        // 且请求不会再有 session.changed 回声 —— 本码是唯一的"切换失败"信号。
        private const val SESSION_CHANGE_FAILED = 1009
    }
```

**重写 onError**（:318-345），回落与 pending 清理只挂在 1009 上；错误类型映射（fatal → ServerError / 非 fatal → NetworkError）与 banner 行为不变：

```kotlin
    override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {
        if (code == SESSION_CHANGE_FAILED) {
            // 服务端拒绝了本次切换，不会再有 session.changed 回声 → 回滚乐观置位。
            // 其余错误（1001 ASR / 1007 omni 流 / 1008 lecture 降级 / 网络类）与本次
            // 切换无关：不得回落，也不得清 pending —— 后续回声仍按原路径正常结算。
            val rejectedMode = pendingModeChange
            if (rejectedMode != null) {
                pendingModeChange = null
                when (rejectedMode) {
                    "translate" -> {
                        _translateMode.value = false
                        _lectureMode.value = false
                    }
                    "transcribe" -> _transcribeMode.value = false
                    "omni" -> _omniMode.value = false
                }
                // 服务端拒绝为持久性（会话未建立 / omni 不可用）→ 清自动恢复请求，
                // 避免每次重连"乐观置位→被拒→弹错"的无尽循环（原 4001/4003 同款语义）
                lastModeRequest = null
            }
        }
        val error = if (fatal) {
            ConversationError.ServerError(code, message)
        } else {
            ConversationError.NetworkError(message, retryable = recoverable)
        }
        scope.launch { _errors.emit(error) }
    }
```

**更新注释**：

- :88-90 `pendingModeChange` 字段注释 → 改为"回声前收到 **1009** 视为本次切换失败 → 按模式回落"；
- :321 段内联注释随 onError 重写（上面代码块已含）。

**不变的部分**（明确）：`markModeChangePending`/`rememberModeRequest`/`onSessionReady`/`onSessionChanged`/`clearSession` 均不改——pending 生命周期变为：置位（markModeChangePending）↔ 清除（回声 / 1009 / session.ready / clearSession）。

### 4.2 ConversationViewModel.kt（仅注释）

- :195 注释"4001 失败回落"→"1009 失败回落"；
- :479-480 注释（"回声前任何错误 → adapter 回落开关…不依赖错误类型"）→ 改为"回声前收到 1009（专属失败码）→ adapter 回落开关"。

### 4.3 SDK / mock / Schema

不改（§1.2-4 已核实：code 全链路为不透明整数；mock server 未实现 session.change，tools/local-dev/mock-services/.../app.py 无该分支）。

---

## 5. 行为变化对照

| 场景 | 现状 | 改后 |
|---|---|---|
| 切换被拒（omni 不可用 / 会话未建立） | 4001/4003 → 回落 + 清恢复请求 | 1009 → 回落 + 清恢复请求（语义不变，码变更） |
| 乐观窗口内无关错误（1001/1007/网络类） | **误回落**（回声若及时到达再置回，产生闪烁；跨 reorder 窗口则可能停留错误态） | 不回落、不清 pending；回声正常结算 |
| lecture 入口降级（1008 + 回声） | 先回落、回声再置位（闪烁） | 不回落；回声直接置位（无闪烁，净状态同现状） |
| 回声已确认后收到任意错误（含 1009） | 不回落（pending 已清） | 不变（1009 分支因 pending==null 仅出 banner） |
| 非法 mode 报文 | 网关 1006（App 不可能发出） | 不变 |

**已知限制（与现状一致，不在本次范围）**：error payload 无 mode 字段（packages/api-schema/s2c/error.json，`additionalProperties:false`），App 以单槽 `pendingModeChange`（最近一次请求）回滚；快速连续切换时理论上存在"旧请求的 1009 到达时 pending 已被新请求覆盖"的竞态。UI 层切换均经确认对话框串行化（ConversationViewModel.kt:375/415/456/500），实际不可达；如需彻底消除需给 error payload 加可选 `mode` 字段（schema + codegen 变更，本次不做）。

**回滚不恢复切换前模式（既有行为，本次不变）**：`when (rejectedMode)` 只关闭被拒模式的开关，不重放切换前的模式——若非 chat 模式下切换到被拒模式（如 transcribe → omni 被拒），UI 会落到 chat 而服务端仍运行原模式，直到下次重连（`session.start` 双侧复位）才重新一致。修复需要"恢复旧模式"的新语义（客户端重发 `session.change` 或服务端提供回滚回声），超出本码变更范围。

**发布顺序（版本错位代价分析）**：

| 组合 | 结果 |
|---|---|
| 旧 App + 新服务端 | 旧版"窗口内任意错误即回落"：1009 不在其永久集 {4001,4003,1006} → 回落但保留恢复请求 → 每次重连对持久被拒模式（如 omni 关闭）多一轮"乐观置位→被拒→banner"（无功能损坏，仅多一次提示） |
| 新 App + 旧服务端 | 拒绝仍发 4001/4003，新版只认 1009 → 不回落，开关停留乐观态直至下次重连被 `session.ready` 复位（omni 场景会持续显示开启而服务端在 chat） |

结论：推荐服务端与 App **同批发布**；若必须错序，**服务端先上**（旧 App 的退化仅是多余 banner）。

---

## 6. 测试修改清单（已逐一核实当前断言）

### 6.1 服务端（6 处，码替换）

单元测试（4 处，mock `make_error` 断言）：

| 文件:行 | 现断言 | 改为 |
|---|---|---|
| services/tests/unit/session/test_translate_mode.py:502 | `code=4003, message="session not started"` | `code=1009`（message 不变） |
| services/tests/unit/session/test_translate_mode.py:520 | `code=4001, message="invalid mode: invalid_mode"` | `code=1009` |
| services/tests/unit/session/test_transcribe_mode.py:425 | `code=4001, message="invalid mode: invalid_mode"` | `code=1009` |
| services/tests/unit/session/test_transcribe_mode.py:438 | `code=4003, message="session not started"` | `code=1009` |

集成测试（2 处，解析真实 outbound 帧断言）：

| 文件:行 | 现断言 | 处置 |
|---|---|---|
| services/tests/integration/orchestrator/test_omni_mode_switch.py:319-329 | `test_service_missing_rejects_with_4001_and_no_echo`：`errs[0]["payload"]["code"] == 4001`（omni_service=None → 拒绝且无回声） | `== 1009`；建议测试名同步改 4001→1009 |
| services/tests/integration/orchestrator/test_omni_mode_switch.py:335-345 | `test_open_failure_rejects_4001_and_keeps_dispatch_alive`：`== 4001`（open 抛错被吞、dispatch 主循环存活） | 同上 |

（全仓 grep `4001|4003` 核实：测试中除 `test_metrics_registry.py:29-31` 的无关 metric label 外，仅上述 6 处 + 一个注释（test_transcribe_mode.py:382），无遗漏。）

### 6.2 App 单测（7 处改 + 2 处新增）

| 文件:行 | 现内容 | 处置 |
|---|---|---|
| ConversationListenerAdapterTranscribeTest.kt:108-117 | `error_before_echo_rolls_back_transcribe` 用 4001 | 4001 → 1009 |
| ConversationListenerAdapterModeRestoreTest.kt:89 | 4001 回落断言 | 4001 → 1009 |
| 同上 :110 | 4001（omni）回落断言 | 4001 → 1009 |
| 同上 :168-191 | `permanent_reject_clears_request_stops_future_restore` 用 4001 | 4001 → 1009 |
| 同上 :193-215 | `transient_error_keeps_request_retries_on_next_ready` 断言 1001 也回落 | **语义反转**：1001 不回落（`assertTrue(transcribeMode)`），请求保留、下次 ready 重新乐观置位 |
| 同上 :217-241 | `translate_reject_rolls_back_translate_and_lecture_flags` 用 4003 | 4003 → 1009 |
| ConversationViewModelTranscribeModeTest.kt:170 | `onError(code = 4001, ..., fatal = true)` | 4001 → 1009（fatal 建议同步改为 false，与服务端一致） |

**新增用例（ConversationListenerAdapterTranscribeTest 或 ModeRestoreTest）**：

1. `unrelated_error_in_pending_window_keeps_mode_and_echo_resolves`：置位+markPending → `onError(1001)` → flag 仍 true；**随后再注入 `onError(1009)` → 正常回落**（以"pending 仍存活"可证伪的方式断言，而非直接读私有字段）；另覆盖 `onSessionChanged` 正常结算路径；
2. `session_change_failed_without_pending_only_emits_banner`：无 pending 时 `onError(1009)` → 无 flag 变化、无崩溃（banner 路径）。

### 6.3 验证步骤

- 服务端（仓库根；`.venv` 已按 README 装好 test 依赖，或先 `source .venv/bin/activate`）：
  `python -m pytest services/tests/unit/session/test_translate_mode.py services/tests/unit/session/test_transcribe_mode.py services/tests/integration/orchestrator/test_omni_mode_switch.py`；
- App（工作目录 `apps/android`，模块名见 settings.gradle.kts）：
  `./gradlew :LivaChatApp:testDebugUnitTest --tests "*ConversationListenerAdapter*" --tests "*ConversationViewModel*"`；
- 真机（app 联调）：
  1. 服务端 omni 关闭（`omni.enabled=false`）→ App 切全模态 → logcat 见 `code=1009`，开关回落、错误 banner、"曾拒绝"后重连不再自动尝试 omni；
  2. lecture 入口失败注入（临时扩写 translate.base_url 指向不可达）→ 只见 1008 banner，开关随回声保持 translate+lecture，无回退闪烁；
  3. 切换途中注入 1001（停 ASR 上游触发）→ 开关不回落、回声正常置位。

---

## 7. 协议文档更新（docs/ws-protocol-design.md，实施时同步）

1. §2.2.3 `session.change` "服务端行为"第 3/5 条：`omni mode unavailable` 与 `session not started`/`invalid mode` 的错误码由 4001/4003 改为 **1009**（omni 分支保留"不发 session.changed"说明）；
2. §4.3 "错误帧——当前实际下发"表：删除 4001/4003 两行，新增 1009 行（含义："session.change 被拒，该请求不会再有 session.changed 回声；客户端据此回滚模式开关并停止对该模式的自动恢复"）；"实际会出现在 error 帧里的码"从 6 个变为 **5 个**：1001 / 1006 / 1007 / 1008 / 1009；
3. §4.3 预留表：4001 行注释改为"仅存于 WS close code（解码保护）与 AuthError 默认码（HTTP 401 路径）"；4003 行改为"枚举保留，无下发路径"；
4. §5.1 `session.change` 行：重试说明补充"被 1009 拒绝后不得自动重试，应以 `session.changed`+`status(mode_changed)` 为成功标志"。

---

## 8. 明确不做

- **不给 error payload 加 `mode` 字段**：error schema `additionalProperties:false`（packages/api-schema/s2c/error.json），加字段需 schema + Python/Kotlin codegen 联动；单槽 pending 已覆盖实际场景（§5 限制说明）；
- **不改 SDK**：SDK 对 code 全程不透明透传，无枚举可加；
- **不改 mock server**：其未实现 session.change（已核实），App e2e 的 error 注入走 `MockServerHarness.error(code=...)`，调用方自行传 1009 即可；
- **不迁移 1008 降级路径到 1009**：§2.2 已述，切换实际成功；
- **不引入瞬态/永久二分码**：服务端拒绝路径当前全部为持久性（配置缺失/会话未建立），统一 1009 + 清恢复请求；若未来 omni 开流失败需可重试语义，再单独设计。
