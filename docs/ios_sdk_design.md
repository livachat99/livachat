# LivaChat iOS SDK 设计文档

**版本**: 1.0

**状态**: SDK 已实现；App M0 工程骨架已落地（见 `docs/ios_app_m0.md`），产品功能待实现；本文描述 SDK 目标契约

**日期**: 2026-10-19

**适用范围**: `apps/ios/LivaChatSDK`（SDK 本地 SPM package）+ `packages/sdk-swift`（协议 DTO）+
`apps/ios/LivaChatApp`（宿主 App，后续阶段）

**基准**:

- Android 现行实现：`docs/android_sdk_design_v3.md`、`apps/android/LivaChatSDK/`
- 协议 SSOT：`packages/api-schema/`；Swift DTO：`packages/sdk-swift/Sources/LivaChatProtocol/`
- 服务端事实：`services/gateway/`、`services/session/`
- iOS 规范：`docs/ios_dev_rules.md`（iOS 15 / Swift + SwiftUI / SPM）

> 本文所有“现状”结论均来自仓库源码或本机实测；推断性内容在文中明确标注。
> DTO 字段名、wire 名称、必填性以 `packages/sdk-swift` 生成物为准，禁止手改。

---

## 0. 结论摘要与关键决策

iOS SDK 的职责与 Android SDK 对齐：**协议传输 + 音频/视频数据处理**，媒体 I/O 仍由宿主 App 持有。
协议 DTO 已由 `scripts/generate_swift_api.py` 生成并通过测试（`docs/ios_protocol_dto.md` §9），
SDK 只负责在其上实现传输、会话、编解码管线。

| # | 决策点 | 结论 | 依据 |
|---|--------|------|------|
| D1 | WebSocket 传输 | `URLSessionWebSocketTask`（系统框架，无第三方依赖） | 本机实测：4001 自定义关闭码可读取、HTTP 401 可通过 `task.response` 识别、200 条连续 send 服务端收序一致（§2.4.1） |
| D2 | 并发模型 | GCD 串行队列 + 队列封闭（queue-confined）组件；公开 Facade `@unchecked Sendable` | Swift 6 严格并发；50Hz 音频链路不使用 actor 以避免任务调度开销 |
| D3 | 公开事件接口 | `AsyncStream<LivaChatEvent>`（单消费者）+ 独立 `LivaChatAudioSink` + 线程安全状态快照 | 事件流天然 `Sendable`；音频回调不能走异步流（实时性） |
| D4 | 本地 VAD | 已就绪：`3rdparty/dist-ios/WebRtcVadLite.xcframework`（arm64 真机 + arm64/x86_64 模拟器；两个切片均 `minos 15.0`） | 头文件与 Android `webrtc_vad_lite.h` **逐字节一致**；C shim 链接 iOS Simulator 成功；iOS 17.0 模拟器运行时冒烟通过（静音→0、正弦→1）；**仅入库 XCFramework，源码/revision/构建脚本不入库（D16）** |
| D5 | Opus | 复用 `3rdparty/dist-ios/Opus.xcframework`，通过 C shim target 链接 | 本机实测 C target 可 `#include <opus/opus.h>` 并链接成功（§2.2.2） |
| D6 | 视频上行 | JPEG 直通为主；`CGImage` 便捷路径（同步编码）为 P1 | 宿主 App 的相机/屏幕/图库均可在上游产出 JPEG；对齐 Android 主 App 的实际用法 |
| D7 | TLS | 默认 `.system`；提供显式 `.allowSelfSigned`（trust-all）。**已批准：允许该选项进仓库，生产自签名部署可通过显式配置使用** | Android 现行 trust-all（`android_sdk_design_v3.md` §10.4）不适合作为 iOS 默认；该模式接受任意服务端证书，不校验证书链/主机名，需在可信 endpoint/受控网络下使用；证书 pinning 暂不实现 |
| D8 | 重连后会话恢复 | SDK 不自动发 `session.start`；App 观察 `connectionEpoch` 后触发 | 与 Android 一致；同时 SDK 增加“首包必须是 session.start”的上行闸门（§5.3.2） |
| D9 | 音频参数 | 公开 16kHz / 20ms；24kHz、非 20ms 在构造期拒绝 | WebRTC VAD 只支持 8/16/32kHz；Opus/VAD native 固定 20ms（Android 同限制，见 v3 §10.1） |
| D10 | `bandwidthHint` | 不提供 | Android 该 StateFlow 初始化后从不更新，恒 `GOOD`（v3 §10.2）；App 无消费点 |
| D11 | 关闭/鉴权终态 | HTTP 401 与 close 4001 → 终态不重连；其余非 1000 → 退避重连 | 服务端 `services/gateway/auth.py`、`endpoint.py`；Android `failTerminal` 同语义 |
| D12 | 测试策略 | 主机侧 fakes（macOS `swift test`）+ 模拟器 native 测试 + 本地 WS 服务端集成测试 + Mock Server E2E | 复用现有 `make check-swift-api-generated` / Mock Server；native 二进制无 macOS 切片 |
| D13 | 包结构 | 单一 Swift package：`LivaChatProtocol` + `LivaChatSDK` 两个 product/target | 首期共用同一仓库依赖；协议层独立复用的需求出现时再拆分 |
| D14 | 身份持久化 | deviceID / token 放 Keychain（App 层）；卸载重装保留 | 设备标识稳定；若产品要求“卸载即新设备”，再改 UserDefaults |
| D15 | iOS 15 验收 | 以 `minos 15.0` 编译检查 + iOS 17 模拟器运行时验证；发布前用 iOS 15 真机补一次冒烟 | 本机 iOS 15 runtime 为 unavailable（宿主 macOS 过高） |
| D16 | VAD 交付方式 | **仅入库 `WebRtcVadLite.xcframework`**；WebRTC 源码、revision、构建脚本与许可证目录均不入库 | 产线只消费预编译 XCFramework；revision/来源信息由维护者在仓库外记录；升级/重建由维护者用外部构建链完成 |
| D17 | App 阶段决策边界 | 后台音频与双 engine 播放已由 App 文档决定；屏幕采集范围、`session.ended` 后断开、UIKit 例外仍属 App 阶段 | SDK 不保证后台唤醒/网络；SDK 公开面保持与 Android 一致的现状语义 |

---

## 1. SDK 描述

### 1.1 定位

LivaChat iOS SDK 是 LivaChat iOS 客户端的 **协议传输与数据处理 SDK**，负责：

- WebSocket 连接生命周期、应用层心跳、指数退避重连、鉴权终态判定
- C2S / S2C 协议消息的 Swift DTO 编解码（复用 `LivaChatProtocol`）
- 会话管理、C2S 序号分配与发送顺序、服务端状态机同步
- 上行音频的拼帧、WebRTC VAD、可选 AGC、Opus 编码
- 下行音频的 Base64 解码与 Opus 解码
- 上行视频的 JPEG 直通 / 可选 CGImage → JPEG 编码
- 向 App 暴露事件流、音频 PCM sink、线程安全状态快照

SDK **不** 负责：

- 麦克风采集（`AVAudioEngine` / `AVAudioConverter` / `AVAudioSession`）
- 音频播放（`AVAudioPlayerNode` / `AVAudioSourceNode`）与播放抖动缓冲
- 摄像头（`AVCaptureSession`）、屏幕采集（`ReplayKit`）、图库选择（`PHPicker`）
- 权限申请、后台模式、音频路由/中断/回声消除策略
- UI 状态展示与动画
- `session.start` 的重连自动重发（由 App 观察 epoch 触发）
- Token 派生与持久化

### 1.2 与 App 层的边界

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              apps/ios (SwiftUI)                         │
│                                                                          │
│  ┌──────────────┐   ┌──────────────────┐   ┌──────────────────────────┐  │
│  │ SwiftUI View │   │ ViewModel / Repo │   │ 权限 / 前后台 / AVAudioSession│  │
│  └──────┬───────┘   └────────┬─────────┘   └──────────────────────────┘  │
│         │                    │                                           │
│         │    ┌───────────────┴──────────────────┐                        │
│         │    │ App-owned Media Components       │                        │
│         │    │  AudioCaptureController          │  AVAudioEngine tap     │
│         │    │  AudioPlaybackController         │  AVAudioPlayerNode + JB│
│         │    │  CameraController                │  AVCaptureSession      │
│         │    │  ScreenCaptureController         │  ReplayKit             │
│         │    │  InterruptCoordinator            │  localVadState 事件    │
│         │    └───────────────┬──────────────────┘                        │
│         │                    │ PCM in / PCM out / JPEG in                │
│         ▼                    ▼                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                           LivaChatSDK                              │  │
│  │  Public Facade                                                     │  │
│  │    connect / disconnect / startSession / endSession                │  │
│  │    pushAudioPCM / pushVideoJPEG / pushVideoImage                   │  │
│  │    sendInterrupt / sendSessionChange                               │  │
│  │    events: AsyncStream<LivaChatEvent>                              │  │
│  │    audioSink: LivaChatAudioSink?                                   │  │
│  │    state: LivaChatState (connection/session/vad/epoch)             │  │
│  │  SessionManager · ConnectionManager · MessageCodec                 │  │
│  │  AudioSendPipeline · AudioRecvPipeline · VideoSendPipeline         │  │
│  │  OpusEncoder/Decoder · VadDetector · UplinkAGC · FrameAssembler    │  │
│  │  Native: libopus.a / libwebrtc_vad_lite.a（C interop）             │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| 职责单一 | SDK 只负责“协议 + 数据处理”；媒体 I/O 与平台资源由 App 持有 |
| 接口对称 | 上行接收 PCM/JPEG，下行回调 PCM；App 不感知 Opus/VAD 细节 |
| 编解码内聚 | Opus、VAD、JPEG 均为 SDK 内部组件，不作为公开处理器暴露 |
| Swift 原生 | 公开接口使用 `AsyncStream`、`Sendable` 值类型、线程安全快照；不暴露 Combine/Kotlin 式 Flow |
| 队列封闭 | 实时管线使用单串行队列；共享状态用锁保护；不使用 actor 承载 50Hz 数据面 |
| 平台无关核心 | Session/Scheduler/FrameAssembler/AGC 可在 macOS 主机侧单测（native 处理器用 fake） |
| Native 隔离 | App 不直接链接/调用 Opus、VAD；由 SDK 的 C shim target 封装 |
| 服务端权威 | `SessionState` 只由服务端 `status` 驱动，SDK 与 App 不自行推断 |
| 无播放缓冲 | SDK 解码即回调 PCM；播放缓冲、抖动缓冲与 flush 完全由 App 承担 |
| 上行闸门 | 每个连接的首个业务 C2S 必须是 `session.start`；音频/视频/控制信令在此之前丢弃 |

---

## 2. 代码库分析（基于源码）

### 2.1 Android SDK 现行实现要点

来源：`apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/`

| 维度 | Android 实际行为 | iOS 对应方案 |
|------|------------------|--------------|
| 传输 | OkHttp WebSocket；`connectTimeout=10s`、`readTimeout=0`、`pingInterval=0`；trust-all + cleartext | `URLSessionWebSocketTask`；默认系统 TLS；应用层心跳 |
| URL | `${serverUrl}?token=<wireToken>&device_id=<uuid>` | 同左；`URLComponents` 拼接并百分号编码 |
| 连接代际 | 每次 `didOpen` 自增 `connectionEpoch`，先置 `CONNECTED` 再自增 | 同左；事件顺序固定 |
| 鉴权终态 | HTTP 401 / close 4001 → `DISCONNECTED`，不退避 | 同左；iOS 通过 `task.response` 与 `didCloseWith` 判定 |
| 心跳 | 只判**入站**静默：≥15s 发一次 ping，≥35s 判死 close(4000) 后重连 | 同左；服务端自身为 15s ping / 45s close(4002)（§2.3.4） |
| 重连 | 1s×2^(n-1) 封顶 30s，±500ms 抖动，最多 5 次；手动 connect/成功建连清零 | 同左 |
| 发送闸口 | `sendLock` 内完成“取 seq → 构建 → 编码 → 交给传输”；video 例外（锁外取号+编码，锁内只送包） | `sessionQueue` 串行队列承担闸口；video 在 `videoSendQueue` 取号+编码后投递 |
| 首包约束 | Android 未显式保证 `session.start` 是首包（依赖 App 时序） | iOS 增加上行闸门（D8，§5.3.2） |
| 会话状态 | 仅 `status` 更新；`session.ready` 不变状态 | 同左 |
| 强制 flush | `status(thinking)` → `INTERRUPT_ACK`；`session.ended` → `SESSION_ENDED`；`error(fatal)` → `SERVER_ABORT` | 同左 |
| 音频上行 | `ShortArray` → FrameAssembler → WebRTC VAD → 可选 AGC → Opus(CBR 32kbps/20ms/VOIP) | `[Int16]` → 同左（Swift 移植） |
| 音频下行 | Base64 解码（WS 线程）→ Opus 解码（AudioRecv 线程）→ `onAiAudioPcm` | `Data` 解码（connectionQueue）→ Opus 解码（audioRecvQueue）→ audioSink |
| 视频 | `pushVideoBitmap`（缩放+压缩）或 `pushVideoJpeg`（直通） | `pushVideoImage(CGImage)`（同步编码）或 `pushVideoJPEG`（直通） |
| VAD | `libwebrtc_vad_lite.a`，模式 0/1/2，20ms | `WebRtcVadLite.xcframework`（已就绪；minos 15.0；头文件与 Android 一致） |
| Opus | JNI → `opus_bridge.cpp` | C shim → `Opus.xcframework` |
| JPEG | `Bitmap.compress` | ImageIO / CoreGraphics（P1） |
| 日志 | logcat + 可选文件；UTC 微秒 | `os.Logger` + 可选文件；UTC 微秒 |
| `bandwidthHint` | 恒 `GOOD`，未接线 | 不提供（D10） |

### 2.2 iOS 现有资产

#### 2.2.1 协议 DTO（已完成）

- 路径：`packages/sdk-swift/Sources/LivaChatProtocol/`
- 生成物：`Generated/ProtocolModels.swift`、`ClientMessage.swift`、`ServerMessage.swift`
- 手写：`ProtocolJSON.swift`（`JSONEncoder/Decoder` 工厂、`MessageHeader`、未知类型错误）
- 测试：`Tests/LivaChatProtocolTests/`（37 项，主机与模拟器均已通过，见 `docs/ios_protocol_dto.md` §9）
- 关键契约：
  - Base64 字段为 `Data`，由 `JSONEncoder/Decoder.dataEncodingStrategy = .base64` 处理
  - 可选字段为 nil 时省略；`session_id` 为 required-nullable，恒输出 key
  - 未知消息类型抛 `ProtocolDecodingError.unknownMessageType`
  - 严格枚举：未知枚举值解码失败，由传输层丢帧
  - 全部 DTO `Sendable`

#### 2.2.2 Opus（已存在）

- 产物：`3rdparty/dist-ios/Opus.xcframework`
  - `ios-arm64`（真机）
  - `ios-arm64_x86_64-simulator`（模拟器，fat）
- Headers：`Headers/opus/{opus.h,opus_defines.h,opus_types.h,...}`
- 构建脚本：`3rdparty/opus/build_opus_ios.sh`（min iOS 13.0，CMake + Xcode generator）
- 实测（2026-10-19，Xcode 16.4 / Swift 6.1.2）：
  - `lipo -info` 确认切片架构正确
  - SwiftPM `binaryTarget` 允许引用 package root 之外的相对路径（本机以 `../3rdparty/...` 探针包实测解析通过；
    在 `apps/ios/LivaChatSDK` 中对应 `../../../3rdparty/dist-ios/Opus.xcframework`）
  - 新增 C target 依赖该 binary target，`#include <opus/opus.h>` 并编译链接到 iOS Simulator **成功**

#### 2.2.3 WebRTC VAD（已就绪）

- 产物：`3rdparty/dist-ios/WebRtcVadLite.xcframework`
  - `ios-arm64`（真机）
  - `ios-arm64_x86_64-simulator`（模拟器，fat）
  - `BinaryPath`：`libwebrtc_vad_lite.a`
- Headers：`Headers/webrtc_vad_lite.h`、`Headers/webrtc_vad.h`
- 本机实测（2026-10-19，重建后）：
  - 两个头文件与 `3rdparty/dist-android/arm64-v8a/include/` 下的同名头文件 **逐字节一致**（`diff` 无输出）
  - `lipo -info` 确认真机 arm64、模拟器 arm64+x86_64
  - `nm -gU` 导出 `Wvad_Create/Free/SetMode/Process/ValidRateAndFrameLength/SourceRevision`
  - `otool -l` 确认两个切片的 `LC_BUILD_VERSION` 均为 **`minos 15.0`**（`sdk 27.0`；
    即用较新 SDK 构建、但部署目标正确）；对照 `Opus.xcframework` 为 `minos 13.0 / sdk 17.5`
  - SwiftPM C shim（`#include <webrtc_vad_lite.h>`）链接到 iOS Simulator **成功**，无 minos 告警
  - **运行时冒烟**（iOS 17.0 模拟器；本机 iOS 15 runtime 为 unavailable，无法启动 iOS 15 模拟器）：
    - `Wvad_SourceRevision()` 非空（具体值由维护者在仓库外记录）
    - `ValidRateAndFrameLength(16000, 320)` = 0（合法组合）
    - 320 样本静音帧 → 0（非语音）
    - 320 样本 220Hz 正弦 → 1（被判定为活动语音）
- 公开 API（与 Android 完全一致）：
  - `Wvad_Create(mode)` / `Wvad_Free`
  - `Wvad_SetMode(mode)`
  - `Wvad_Process(handle, sample_rate_hz, pcm, frame_length)`
  - `Wvad_ValidRateAndFrameLength(rate, frame_length)`
  - `Wvad_SourceRevision()`
- 交付决策（2026-10-19）：**仅入库 XCFramework**；源码、revision、构建脚本与许可证目录不入库。
  revision/来源信息由维护者在仓库外记录；升级/重建由维护者用外部构建链完成。

#### 2.2.4 其他现状

- `apps/ios/LivaChatSDK` 已创建（本地 SPM package）；`apps/ios/LivaChatApp` M0 工程骨架已落地（产品功能待实现，见 `docs/ios_app_m0.md`）。
- `apps/ios/LivaChatSDK` 为本地 SPM package；`packages/sdk-swift` 只保留协议 DTO。
- `scripts/build-ios.sh` 支持 `sdk` / `app` / `all`：默认 `sdk`（DTO 校验 → 协议测试 → SDK 主机测试 → 模拟器测试，`RUN_IOS_TESTS=0` 可跳过）；`app` 为签名 App 构建。
- `Makefile` 已扩展 `swift-protocol-test` / `swift-sdk-test` / `swift-sdk-ios-test` / `build-ios`；
  SDK 包测试 scheme 为 `LivaChatSDK`，协议包测试 scheme 为 `LivaChatProtocol`。

### 2.3 协议与服务端事实（SDK 契约的权威来源）

#### 2.3.1 鉴权

- 客户端传 `token = sha1_hex(secret + "LivaChat")`，`device_id` 为 UUID v4（`docs/server_auth.md`；`services/gateway/auth.py:11-47`）。
- 服务端在 **accept 之前**校验；失败返回 HTTP 401（`services/gateway/endpoint.py:38-52`）。
- 因此 iOS 必须能区分“握手 401 终态”和“网络错误可重连”。

#### 2.3.2 序号

- C2S/S2C 各自独立、从 1 开始；服务端 `InboundSeqGuard` 丢弃 seq 回退的消息。
- `video` 在 `_TOLERATED_TYPES` 中：乱序仍处理且不移动水位（`services/gateway/seq.py:11-56`）。
- 服务端收到 `session.start` 会 `reset()` 水位；`ping` / `pong` 不参与 seq 校验（`services/gateway/connection.py:236-252`）。

#### 2.3.3 会话建立

- 同一连接允许重复发送 `session.start`；服务端复位状态与 seq 水位（`docs/ws-protocol-design.md` §2.2.1）。
- `session.start` 必须是建连后的首个业务消息；`session_id=null` 新建，携带旧 id 恢复上下文。
- `session.ready` 携带 `session_id`、`session_restored`（当前恒 false）、`server_time_ms`、`keepalive_interval_s`（恒 15）、`server_version`。
- 重连恢复由 App 在 `session.start` 中携带旧 `session_id`；SDK 不做自动重发。

#### 2.3.4 心跳与关闭码

| 方向 | 行为 | 来源 |
|------|------|------|
| 客户端 | 入站静默 ≥15s 发 `ping`；≥35s 判死 close(4000) 并重连 | Android `HeartbeatScheduler` + `ConnectionManager` |
| 服务端 | 无任何入站帧 ≥15s 发 `ping`；≥45s close(4002) | `services/gateway/heartbeat.py:27-28`、`configs/dev/server.yaml:29-30` |
| 鉴权 | HTTP 401（accept 前）；WS close 4001（解码保护） | `endpoint.py`、`docs/ws-protocol-design.md` §4 |
| 正常关闭 | 1000 | `docs/ws-protocol-design.md` |

> 客户端 35s 判死早于服务端 45s，避免单向流场景下互相等待。

#### 2.3.5 服务端如何使用 `vad_state`

`services/session/orchestrator.py:1336-1350`：

```python
is_speech = msg.payload.vad_state.value == "speech"
...
turn_ended = self._turn_detector.on_frame(is_speech)
```

`TurnDetector` 以连续静音帧数判定回合结束：low=45 帧（~900ms）、medium=30（~600ms）、high=20（~400ms）
（`services/session/turn_detector.py:9-47`）。

结论：**chat 模式下客户端必须逐帧提供准确的 `vad_state`**；VAD 不是“可选优化”，而是协议必需数据。
`omni` / `transcribe` / `lecture` 模式由 provider 自行处理回合边界，但 SDK 仍按统一管线标注 `vad_state`。

#### 2.3.6 视频回合语义

- `turn_id`：App 维护的采集轮标识，从 1 起；0 = 未启用；服务端观察到变化即视为新回合首帧，不等 ASR。
- `text`：非空时服务端立即以该文本 + 本帧起一轮；null/空仅入帧缓冲。
- `camera_facing`：`front` / `back` / `screen` / `gallery`。
- `frame_id`：SDK 级单调递增，用于 `ai_video_ack` 配对与丢帧检测。

### 2.4 iOS 平台差异与本机实测

#### 2.4.1 `URLSessionWebSocketTask` 实测（2026-10-19）

使用本机 Xcode 16.4 / Swift 6.1.2 / macOS 15，启动本地 Python WebSocket 服务端：

| 场景 | 实测结果 | 对设计的含义 |
|------|----------|--------------|
| 服务端 close 4001 | `didCloseWith(closeCode.rawValue == 4001, reason: "auth-failed")` | 可识别自定义关闭码，**不要**用 `CloseCode(rawValue: 4001)` 构造（该初始化器对 4001 返回 nil），直接读 delegate 的 `rawValue` |
| 关闭前 receive 出错 | 4001 与正常 1000 关闭前都先收到 `NSPOSIXErrorDomain Code=57 "Socket is not connected"`，随后 `didCloseWith` | receive 失败不能立即判定终态；需按连接代际去重，等待 close/didComplete |
| 握手 HTTP 401 | `didCompleteWithError(URLError -1011 badServerResponse)`，`task.response as? HTTPURLResponse` 的 `statusCode == 401` | 可在 `didCompleteWithError` 中读取 `task.response` 判定鉴权终态 |
| 200 条连续 `send(.string)` | 服务端收到顺序与发送顺序完全一致 | 单串行队列内顺序调用 `send` 可保持上线顺序；仍以串行发送队列作为契约 |
| 空闲连接与 timeout | `timeoutIntervalForRequest=2s` 的 WebSocket 在服务端静默 5s 期间未被超时终止 | 建连后的存活判定以应用层心跳为准；不要依赖请求超时 |
| `URLSessionWebSocketTask.CloseCode(rawValue:)` | 对未定义值返回 Optional（4001 → nil） | 运行时比较只用 `rawValue`；不要构造未定义枚举值 |

> 上述为 macOS 实测；iOS 使用同一 Foundation 实现，仍需在模拟器/真机回归（§10.3）。

#### 2.4.2 音频与媒体栈差异

| Android | iOS（App 层） |
|---------|----------------|
| `AudioRecord`（VOICE_COMMUNICATION） | `AVAudioEngine` 输入 tap + `AVAudioConverter` → 16k mono Int16 |
| `AudioTrack`（VOICE_COMMUNICATION/SPEECH） | 独立 playback `AVAudioEngine` + `AVAudioPlayerNode` + JitterBuffer |
| `AudioManager` 路由 / SCO | `AVAudioSession` category/mode/options、route change、interruption |
| 系统 AEC（VOICE_COMMUNICATION） | `inputNode.setVoiceProcessingEnabled(true)`（iOS 13+） |
| 前台服务保后台录音 | `UIBackgroundModes: audio`；capture engine 保持/停止，playback engine 独立运行 |
| MediaProjection 录屏 | ReplayKit（in-app capture；广播扩展后续） |
| CameraX | AVCaptureSession / AVCaptureVideoDataOutput |
| Bitmap | `CGImage`（CoreGraphics，非 UIKit） |

#### 2.4.3 iOS 规范约束

`docs/ios_dev_rules.md` 要求：

- 所有 target `IPHONEOS_DEPLOYMENT_TARGET = 15.0`；`Package.swift` 使用 `.iOS(.v15)`。
- 业务逻辑/网络/状态/UI 默认 Swift + SwiftUI；UIKit/Objective-C 仅在必要时使用且需事先批准。
- 依赖默认 SPM；预编译库优先 XCFramework；禁止 CocoaPods。
- 生成产物必须由脚本生成，禁止手改。

本 SDK 的对应落实：

- 不引入 UIKit、CocoaPods。
- Swift 之外的代码仅有两类：Opus/VAD 的 C shim（`COpus`/`CVad`），以及
  `Sources/CWebSocketClose/` 一个约 10 行的 Objective-C bridge。后者是**规则例外**：
  Foundation 的 `URLSessionWebSocketTask.CloseCode` 是 Swift enum，无法构造协议要求的自定义
  关闭码 4000；bridge 调用公开 ObjC API `cancelWithCloseCode:reason:` 原样发送 4000
  （§5.2）。按 `ios_dev_rules.md` §2/§4 需事先批准，当前实现待评审确认。
- 仅使用 Foundation、CoreGraphics/ImageIO（P1 CGImage 路径）、os（日志）。
- native 依赖通过 SPM `.binaryTarget` + C target 引入。
- DTO 继续由 `scripts/generate_swift_api.py` 生成。

---

## 3. 公开接口

### 3.1 核心入口

```swift
import Foundation
import CoreGraphics

/// LivaChat iOS SDK facade.
///
/// All public methods are thread-safe; most are non-blocking and dispatch
/// heavy work to SDK-internal serial queues. `pushVideoImage` is the exception:
/// it performs JPEG encoding synchronously on the caller's thread.
public final class LivaChatSDK: @unchecked Sendable {

    /// Creates the SDK. Throws if configuration is invalid or native
    /// processors cannot be initialized.
    public init(config: LivaChatConfig, audioSink: LivaChatAudioSink?) throws

    // ---- 连接 / 会话生命周期 ----
    public func connect()
    public func disconnect()
    public func startSession(resumeSessionID: String? = nil)
    public func endSession(reason: LivaChatSessionEndReason = .userHangup)

    // ---- 上行音频 ----
    /// 推送 16-bit mono PCM。SDK 内部完成拼帧、VAD、可选 AGC、Opus 编码与协议封装。
    /// 任意线程可调用；SDK 先将数组拷贝到内部缓冲，再投递到 audio send 队列。
    /// - Parameter muted: true = 麦克风静音补发帧，payload 标记 vad_state="muted"。
    public func pushAudioPCM(_ pcm: [Int16], muted: Bool = false)

    // ---- 上行视频 ----
    /// 预先编码的 JPEG 直通发送；不缩放、不重新压缩。
    /// - Parameters:
    ///   - width/height: 原始图像尺寸（调用方声明；须在 1...65535）
    ///   - turnID: App 维护的采集轮标识；0 = 未启用
    ///   - text: 与该帧同行的用户指令；nil/空 = 仅入服务端帧缓冲
    public func pushVideoJPEG(
        _ jpeg: Data,
        width: Int,
        height: Int,
        orientation: Orientation,
        cameraFacing: CameraFacing = .front,
        turnID: UInt32 = 0,
        text: String? = nil
    )

    /// CGImage → 等比缩放（长边 ≤ videoMaxLongSide）→ JPEG → 发送。
    /// 编码在调用线程同步完成（避免 CGImage 跨队列的生命周期问题）；相机等高频路径
    /// 应优先使用 pushVideoJPEG。
    public func pushVideoImage(
        _ image: CGImage,
        orientation: Orientation,
        quality: Int? = nil,
        cameraFacing: CameraFacing = .front,
        turnID: UInt32 = 0,
        text: String? = nil
    )

    // ---- 控制信令 ----
    public func sendInterrupt(reason: InterruptReason = .userVoice)
    public func sendSessionChange(
        mode: SessionMode,
        myLanguage: String? = nil,
        localLanguage: String? = nil,
        usecase: SessionUsecase? = nil
    )

    // ---- 状态观察 ----
    /// 所有非音频事件（连接/会话/转写/文本/任务/错误/flush/视频 ack）。
    /// 单消费者：App 应只创建一个消费 Task；多路消费请自行 fan-out。
    public let events: AsyncStream<LivaChatEvent>

    /// 解码后的 PCM sink。回调在 SDK 的 audio recv 串行队列，必须快速返回。
    /// 弱引用；App 负责强持有。仅 init 时注入，运行期不可更换
    /// （该属性在 audio recv 队列被读取，运行期任意线程写入会引入数据竞争）。
    public private(set) weak var audioSink: LivaChatAudioSink?

    /// 线程安全的原子状态快照。
    public var state: LivaChatState { get }

    // ---- 日志（os.Logger + 可选文件） ----
    public func logDebug(tag: String, _ message: String)
    public func logInfo(tag: String, _ message: String)
    public func logWarn(tag: String, _ message: String, error: Error? = nil)
    public func logError(tag: String, _ message: String, error: Error? = nil)
}
```

`connectionEpoch` 语义（与 Android 一致）：

- 初值 0；每次 WebSocket `didOpen` 成功后单调 +1（含首次连接与所有重连）。
- 单调不回退，事件流与快照都会反映新值。
- App 以 `.connectionEpochAdvanced` 事件为可靠触发，在每次建连后调用 `startSession(...)`。
- SDK 在发出该事件前已完成：置 `CONNECTED`、清零重连计数、启动心跳；并将
  `resetSessionGate(connectionEpoch:)` 入队到 `sessionQueue`（FIFO 保证先于 App 随后的 `startSession` 执行）。

### 3.2 配置

```swift
public struct LivaChatConfig: Sendable {
    /// 不含查询参数的 WS 地址，例如 wss://host:port/v1/ws/stream。
    public var serverURL: URL
    /// wire token：sha1_hex(secret + "LivaChat")；SDK 只透传。
    public var token: String
    /// 设备持久化 UUID v4（App 负责持久化）。
    public var deviceID: String
    /// 写入 session.start.config.client_version，例如 "ios-1.0.0.42"。
    public var clientVersion: String

    // 音频
    public var audioSampleRate: AudioSampleRate = .hz16000
    public var audioFrameMs: Int = 20
    public var vadSensitivity: VadSensitivity = .medium
    public var uplinkAGC: Bool = false

    // 视频
    public var videoQuality: Int = 70
    public var videoMaxLongSide: Int = 1080
    public var enableVideo: Bool = true

    // 网络
    /// 建连 watchdog 超时（黑洞握手兜底，§5.2）。
    public var connectTimeout: TimeInterval = 10
    public var reconnectMaxAttempts: Int = 5
    public var reconnectBaseDelay: TimeInterval = 1.0
    public var reconnectMaxDelay: TimeInterval = 30.0
    public var keepaliveInterval: TimeInterval = 15
    public var keepaliveTimeout: TimeInterval = 35

    // 诊断 / 平台
    public var logFileURL: URL? = nil
    public var timezone: String? = nil
    /// App UI 语言，写入 session.start.config.language；nil = 省略。
    public var language: String? = nil
    /// 每次 session.start 执行的动态 provider；优先于 language。
    public var languageProvider: (@Sendable () -> String?)? = nil
    public var tlsPolicy: TLSPolicy = .system

    public init(
        serverURL: URL,
        token: String,
        deviceID: String,
        clientVersion: String,
        audioSampleRate: AudioSampleRate = .hz16000,
        audioFrameMs: Int = 20,
        vadSensitivity: VadSensitivity = .medium,
        uplinkAGC: Bool = false,
        videoQuality: Int = 70,
        videoMaxLongSide: Int = 1080,
        enableVideo: Bool = true,
        connectTimeout: TimeInterval = 10,
        reconnectMaxAttempts: Int = 5,
        reconnectBaseDelay: TimeInterval = 1.0,
        reconnectMaxDelay: TimeInterval = 30.0,
        keepaliveInterval: TimeInterval = 15,
        keepaliveTimeout: TimeInterval = 35,
        logFileURL: URL? = nil,
        timezone: String? = nil,
        tlsPolicy: TLSPolicy = .system,
        language: String? = nil,
        languageProvider: (@Sendable () -> String?)? = nil
    )
}

public enum TLSPolicy: Sendable {
    /// 系统默认信任（推荐；生产唯一允许的取值）。
    case system
    /// 接受任意服务端证书（trust-all，非“仅自签名”校验）。开发、内网、自建及生产自签名部署
    /// 均可显式使用；它不提供 MITM 防护，需要更强保证时应使用 CA 证书或后续的证书 pinning。
    case allowSelfSigned
}
```

字段语义：

| 字段 | 语义 |
|------|------|
| `serverURL` | 不含查询参数；SDK 拼接 `?token=...&device_id=...` |
| `token` | wire token；SDK 不派生、不持久化 |
| `clientVersion` | 写入 `session.start.config.client_version`；App 用 `ios-<CFBundleShortVersionString>.<CFBundleVersion>`（build 为单段数字） |
| `audioSampleRate` | 16k / 24k；**24k 在 init 阶段抛错**（WebRTC VAD 不支持 24k） |
| `audioFrameMs` | 仅支持 20；其他值在 init 阶段抛错（native Opus/VAD 固定 20ms） |
| `vadSensitivity` | 0/1/2 对应 low/medium/high，并上报服务端 |
| `uplinkAGC` | makeup-only 上行电平补偿；默认关闭 |
| `videoQuality` | JPEG 质量 1...100；写入 `video.quality` |
| `videoMaxLongSide` | 仅约束 `pushVideoImage`；`pushVideoJPEG` 直通不缩放 |
| `enableVideo` | 仅作为能力声明写入 `session.start.config`，不阻断上行 |
| `connectTimeout` | 建连 watchdog 超时（黑洞握手兜底）；默认 10s |
| `reconnectMaxAttempts` / `reconnectBaseDelay` / `reconnectMaxDelay` | 重连次数与指数退避参数 |
| `keepaliveInterval` / `keepaliveTimeout` | 入站静默 ping / 判死阈值 |
| `timezone` | nil 时每次 `session.start` 现取 `TimeZone.current.identifier` |
| `language` | 静态 App UI 语言，写入 `session.start.config.language`；nil 时省略 |
| `languageProvider` | 可选动态 provider，每次 `session.start` 执行并优先于 `language`；App 用 `AppLocale.resourceCode` |
| `logFileURL` | 非 nil 时追加写入；父目录自动创建 |
| `tlsPolicy` | `.system` 默认；`.allowSelfSigned` 为显式 opt-in（trust-all），开发/内网/自建及生产自签名部署均可开启（§5.2、§11） |

构造期校验（不满足抛 `LivaChatError`）：

- `serverURL.scheme` 必须为 `wss`（`ws` 仅限 App 已配置 ATS 例外；SDK 记警告）
- `token` / `deviceID` 非空；`deviceID` 建议由 App 生成 UUID v4（服务端会拒绝非法值）
- `audioSampleRate == .hz16000`；`audioFrameMs == 20`
- `videoQuality` 1...100；`videoMaxLongSide` > 0
- `keepaliveInterval` > 0 且 `keepaliveTimeout > keepaliveInterval`
- `reconnectMaxAttempts >= 0`；`reconnectMaxDelay >= reconnectBaseDelay`
- `connectTimeout` > 0

### 3.3 事件与音频 sink

```swift
public enum LivaChatEvent: Sendable {
    case connectionStateChanged(ConnectionState)
    case connectionEpochAdvanced(UInt64)
    /// 连接进入终态（不再自动重连）：鉴权失败 / 重连耗尽 / 服务端正常关闭。
    /// 先于 `.connectionStateChanged(.disconnected)` 发出；用户主动 `disconnect()` 不触发。
    /// state 快照不携带终态原因，App 需要区分终态时必须消费该事件。
    case connectionTerminated(ConnectionTerminatedReason)
    case sessionStateChanged(SessionState)
    case localVadStateChanged(VadState)

    case sessionReady(sessionID: String, restored: Bool)
    case sessionEnded(reason: SessionEndedReason, sessionID: String)

    case transcription(text: String, isFinal: Bool, transcriptionID: String)
    /// 全量快照（replace 语义；空数组表示清空）。
    case jobStatus([AgentJob])

    case aiText(
        text: String,
        messageID: String,
        index: UInt32,
        isFinal: Bool,
        image: AiImage?,
        source: AITextSource?
    )

    // 注意：音频 PCM 不进入事件流；由 LivaChatAudioSink 在 audio recv 队列同步回调。
    /// epoch 为 flush 后的新播放代际（§5.3.4）；App 据此丢弃在途过期 PCM 帧。
    case playbackShouldFlush(reason: PlaybackFlushReason, epoch: UInt64)
    case videoAck(frameID: UInt32, processed: Bool)
    case sessionChanged(
        mode: SessionMode,
        myLanguage: String?,
        localLanguage: String?,
        usecase: SessionUsecase?
    )
    case error(code: UInt16, message: String, fatal: Bool, recoverable: Bool)
}
```

```swift
/// 音频 PCM sink。实现必须在 audio recv 队列上快速返回（拷贝/入队后立即返回）。
/// 协议：16-bit mono；sampleRate 目前恒为 config.audioSampleRate。
public protocol LivaChatAudioSink: AnyObject, Sendable {
    func livaChat(_ sdk: LivaChatSDK, didReceiveAudio frame: AudioPCMFrame)
}

public struct AudioPCMFrame: Sendable, Equatable {
    public let samples: [Int16]
    public let sampleRate: Int
    public let sampleBits: Int      // 当前恒 16
    public let durationMs: Int
    /// SDK 生命周期内单调递增，从 1 开始；不随重连重置。
    public let sequenceNumber: UInt64
    /// 播放代际（§5.3.4）：flush 触发与连接断开时递增。
    /// App 应丢弃 playbackEpoch 小于"最近一次 flush 记录值"的在途过期帧。
    public let playbackEpoch: UInt64
    /// 当前恒为空字符串（保留字段，与 Android 一致）。
    public let turnID: String
}
```

设计说明：

- **事件流不承载音频 PCM**：`AsyncStream` 的消费延迟会直接造成播放卡顿；音频必须走同步 sink。
- 事件流单消费者；App 若需要多处消费，应自行 fan-out（对应 Android 的 `CompositeLivaChatListener`）。
- 事件流使用 `.unbounded` 缓冲；App 必须持续消费。若长期不消费，内存会增长（与 Android Listener 无背压一致）。
- 连接/会话状态同时可通过 `sdk.state` 原子读取，适合 SwiftUI 在事件回调里做门控。

### 3.4 模型

```swift
public enum AudioSampleRate: Int, Sendable {
    case hz16000 = 16_000
    case hz24000 = 24_000
}

public enum VadSensitivity: Int, Sendable {
    case low = 0, medium = 1, high = 2
}

public enum VadState: Sendable {
    case speech, silence, unknown
}

public enum InterruptReason: Sendable {
    case userVoice, userTap, appCancel
}

/// 仅 C2S session.end 可用的原因。
public enum LivaChatSessionEndReason: Sendable {
    case userHangup, appBackground, error
}

/// S2C session.ended 的原因（与服务端枚举一致）。
public enum SessionEndedReason: String, Sendable {
    case userQuit = "user_quit"
    case appBackground = "app_background"
    case appTerminate = "app_terminate"
    case serverShutdown = "server_shutdown"
    case error = "error"
    case idleTimeout = "idle_timeout"
}

public enum SessionState: Sendable {
    case idle, listening, thinking, speaking
}

public enum ConnectionState: Sendable {
    case disconnected, connecting, connected, reconnecting
}

/// 连接终态原因（随 `.connectionTerminated` 事件出现；不进入 state 快照）。
public enum ConnectionTerminatedReason: Sendable {
    /// HTTP 401 或 close 4001；停止重试，App 应引导重新配置凭证。
    case authFailed
    /// 重连次数达到 reconnectMaxAttempts 上限。
    case reconnectExhausted
    /// 服务端以 1000 正常关闭。
    case serverClosed
}

public enum PlaybackFlushReason: Sendable {
    case interruptAck, sessionEnded, serverAbort
}

public enum SessionMode: String, Sendable {
    case chat, translate, transcribe, omni
}

public enum SessionUsecase: String, Sendable {
    case lecture, dialogue
}

public enum AITextSource: String, Sendable {
    case llm, agent
}

public enum Orientation: Int, Sendable {
    case portrait0 = 0
    case landscape90 = 90
    case portrait180 = 180
    case landscape270 = 270
}

public enum CameraFacing: String, Sendable {
    case front, back, screen, gallery
}

public struct AiImage: Sendable, Equatable {
    /// JPEG bytes (decoded from the protocol Base64 string).
    public let data: Data
    public let mime: String
    public let width: Int
    public let height: Int
    public let orientation: Orientation
    public let cameraFacing: CameraFacing
}

public struct AgentJob: Sendable, Equatable {
    public let jobID: String
    public let agentName: String
    public let summary: String
    /// 已用 session.ready 的时钟偏移换算为设备本地 epoch ms。
    public let startedAtMs: Int64
    /// 服务端发送时计算的已运行时长（ms）。
    public let runningTimeMs: Int64
}

public struct LivaChatState: Sendable, Equatable {
    public let connectionState: ConnectionState
    public let connectionEpoch: UInt64
    public let sessionState: SessionState
    public let localVadState: VadState
    /// 播放代际（§5.3.4）。App 在连接状态转换触发自行 flush 时读取该值。
    public let playbackEpoch: UInt64
}

public enum LivaChatError: Error, Sendable, Equatable {
    case invalidConfiguration(String)
    case unsupportedSampleRate(Int)
    case unsupportedFrameDuration(Int)
    case audioCodecUnavailable
    case vadUnavailable
}
```

`AiImage` 仅在 `ai_text.is_final == true` 且服务端启用视频时出现。
`AgentJob.startedAtMs` 的换算方式与 Android 一致：`local = Int64(serverStartedAtMs) - clockOffsetMs`，
其中 `clockOffsetMs = serverTimeMs - localReceiptMs`（`session.ready` 时更新）。

### 3.5 线程 / 队列契约

| 接口 / 回调 | 执行上下文 |
|-------------|------------|
| `connect` / `disconnect` / `startSession` / `endSession` / `sendInterrupt` / `sendSessionChange` | 任意线程；非阻塞，内部投递到对应串行队列 |
| `pushAudioPCM` | 任意线程；先做防御性拷贝，再投递到 `audioSendQueue` |
| `pushVideoJPEG` | 任意线程；`Data` 值语义，投递到 `videoSendQueue` |
| `pushVideoImage` | 任意线程；**同步**编码 JPEG 后转 `pushVideoJPEG` |
| `LivaChatAudioSink.didReceiveAudio` | `livachat.audio.recv` 串行队列（非 Main），QoS `userInteractive` |
| `events` | 由各来源队列 yield；App 通常在 Main 消费 |
| `state` | 任意线程读取（`NSLock`） |
| `log*` | 任意线程；文件写入串行化 |

注意事项：

- **事件顺序**：同一来源（连接队列 / 会话队列 / 音频发送队列）内有序；跨来源不保证先后。
  例如 `onSessionReady` 与 `sessionStateChanged` 可能因来源不同而看似乱序（Android 同样如此）。
- **事件合并**：与 Android StateFlow 不同，iOS 事件流不做等值合并；每个状态变化都会产生事件。
  若同一状态被重复设置，SDK 内部会去重后再 yield（`localVadState` 每帧计算，仅变化时上报）。
- `audioSink` 为弱引用、仅初始化时注入，运行期不可更换（如需更换，重建 SDK 实例）；App 必须在整个会话期间强持有 sink 对象。
- 音频与视频发送队列无背压；调用频率由 App 控制。非 `CONNECTED` 状态或尚未 `startSession` 的帧会在闸口丢弃。

---

## 4. SDK 架构

### 4.1 分层

```
┌───────────────────────────────────────────────────────────────────────┐
│ Public API Layer                                                      │
│   LivaChatSDK (Facade)                                                │
│   events / audioSink / state / push / control                         │
├───────────────────────────────────────────────────────────────────────┤
│ Core Logic Layer                                                      │
│   SessionManager       ConnectionManager                              │
│   AudioSendPipeline    AudioRecvPipeline    VideoSendPipeline         │
│   HeartbeatScheduler   ReconnectScheduler   ClockOffset               │
├───────────────────────────────────────────────────────────────────────┤
│ Protocol Layer                                                        │
│   LivaChatProtocol（生成的 DTO + ProtocolJSON）                       │
├───────────────────────────────────────────────────────────────────────┤
│ Transport Layer                                                       │
│   URLSessionWebSocketTask（WSS，token/device_id 查询参数）             │
├───────────────────────────────────────────────────────────────────────┤
│ Native Layer                                                          │
│   COpus (C shim)  → libopus.a (XCFramework)                           │
│   CVad  (C shim)  → libwebrtc_vad_lite.a (WebRtcVadLite.xcframework)  │
│   JPEG：ImageIO / CoreGraphics（无 native 依赖）                       │
└───────────────────────────────────────────────────────────────────────┘
```

### 4.2 内部模块依赖

```
LivaChatSDK
 ├─→ ConnectionManager ─→ URLSession / URLSessionWebSocketTask
 │       ├─→ HeartbeatScheduler（纯 Swift）
 │       ├─→ ReconnectScheduler（纯 Swift）
 │       └─→ WebSocketDelegate（NSObject，内部）
 ├─→ SessionManager ─→ MessageCodec ─→ LivaChatProtocol
 │       ├─→ ClockOffset
 │       ├─→ SeqGenerator
 │       └─→ EventSink / AudioFrameDispatcher
 ├─→ AudioSendPipeline ─→ FrameAssembler / VadDetector / UplinkAGC / OpusEncoder
 ├─→ AudioRecvPipeline ─→ OpusDecoder
 ├─→ VideoSendPipeline ─→ JPEGEncoder（P1）
 └─→ SdkLogger（os.Logger + 文件）
```

依赖规则：上层可依赖下层，下层不感知上层；核心逻辑不依赖 AVFoundation/UIKit。
`CoreGraphics` 仅用于 P1 的 `pushVideoImage`。

### 4.3 并发模型（队列）

| 队列 | QoS | 用途 |
|------|-----|------|
| `livachat.connection` | `userInitiated` | 入站 JSON 解码、心跳定时器、重连调度、建连 watchdog；URLSession delegate 回调经转发后在此处理（§5.2） |
| `livachat.session` | `userInitiated` | C2S 序号/编码/发送闸门、会话状态、flush、时钟偏移、视频 ack 统计 |
| `livachat.audio.send` | `userInitiated` | 拼帧、VAD、AGC、Opus 编码 |
| `livachat.audio.recv` | `userInteractive` | Opus 解码、`LivaChatAudioSink` 回调 |
| `livachat.video.send` | `userInitiated` | JPEG 编码（CGImage 路径）、视频 payload JSON 编码 |
| `livachat.log` | `utility` | 文件日志串行写入 |

- 除 `audioSink` 外，所有公开回调通过 `events` 异步交给 App；SDK 不直接调用 App 的 UI。
- 队列封闭组件标记 `@unchecked Sendable`，并在类注释中声明其所属队列；Swift 6 严格并发下由代码审查与测试保证。
- 不引入 actor：50Hz 音频链路需要确定性优先级与低调度开销；actor 仅用于未来独立扩展（如指标采集）。

### 4.4 包与 target

决策（2026-10-19，已按此实施）：与 Android 结构对齐：

- `packages/sdk-swift`：**只放协议 DTO**（`LivaChatProtocol`），对应 `packages/sdk-kotlin`
- `apps/ios/LivaChatSDK`：本地 Swift package，放 SDK 源码、C shim 与测试，对应
  `apps/android/LivaChatSDK`
- `apps/ios/LivaChatApp`：后续宿主 App，依赖 `apps/ios/LivaChatSDK`

```
apps/ios/
├── LivaChatSDK/                          # SDK（本地 SPM package）
│   ├── Package.swift
│   ├── Sources/
│   │   ├── COpus/                        # Opus C shim
│   │   ├── CVad/                         # WebRTC VAD C shim
│   │   ├── CWebSocketClose/              # ObjC bridge：原样发送关闭码 4000
│   │   └── LivaChatSDK/                  # SDK 主体
│   │       ├── LivaChatSDK.swift         # Facade
│   │       ├── LivaChatConfig.swift
│   │       ├── LivaChatEvent.swift
│   │       ├── LivaChatAudioSink.swift
│   │       ├── Model/
│   │       └── Internal/
│   │           ├── Network/              # ConnectionManager, Heartbeat, Reconnect, TLS
│   │           ├── Session/              # SessionManager, SeqGenerator, ClockOffset, Codec
│   │           ├── Pipeline/             # AudioSend/AudioRecv/VideoSend, FrameAssembler, AGC
│   │           ├── Processors/           # Opus, VAD, JPEG
│   │           └── Util/                 # Logger, Queues, StateStore
│   └── Tests/LivaChatSDKTests/
└── LivaChatApp/                          # 后续：SwiftUI App

packages/sdk-swift/                        # 协议 DTO
├── Package.swift                         # name: LivaChatProtocol
├── Sources/LivaChatProtocol/
└── Tests/LivaChatProtocolTests/
```

`apps/ios/LivaChatSDK/Package.swift` 关键片段：

```swift
let package = Package(
    name: "LivaChatSDK",
    platforms: [.iOS(.v15), .macOS(.v13)],
    products: [
        .library(name: "LivaChatSDK", targets: ["LivaChatSDK"]),
    ],
    dependencies: [
        .package(path: "../../../packages/sdk-swift"),
    ],
    targets: [
        .binaryTarget(
            name: "Opus",
            path: "../../../3rdparty/dist-ios/Opus.xcframework"
        ),
        .binaryTarget(
            name: "WebRtcVadLite",
            path: "../../../3rdparty/dist-ios/WebRtcVadLite.xcframework"
        ),
        // COpus / CVad 与 iOS 条件依赖
        // 唯一 ObjC target：Swift CloseCode 枚举无法构造 4000
        .target(name: "CWebSocketClose"),
        .target(
            name: "LivaChatSDK",
            dependencies: [
                .product(name: "LivaChatProtocol", package: "sdk-swift"),
                "CWebSocketClose",
                .target(name: "COpus", condition: .when(platforms: [.iOS])),
                .target(name: "CVad", condition: .when(platforms: [.iOS])),
            ]
        ),
        .testTarget(
            name: "LivaChatSDKTests",
            dependencies: [
                "LivaChatSDK",
                .product(name: "LivaChatProtocol", package: "sdk-swift"),
            ]
        ),
    ]
)
```

关键点：

- **本地包 identity**：`packages/sdk-swift` 的包名是 `LivaChatProtocol`，但 SwiftPM 本地依赖的
  identity 取自目录名 `sdk-swift`；`.product(...)` 的 `package:` 必须写 `sdk-swift`。
- **binaryTarget 路径**：从 `apps/ios/LivaChatSDK` 到仓库根为 `../../..`，因此是
  `../../../3rdparty/dist-ios/...`。CI 必须检出完整 monorepo（含 `3rdparty/dist-ios`）。
- **macOS 构建**：`COpus` / `CVad` 仅 iOS 条件依赖；SDK 代码用 `#if canImport(COpus)` 保护 native 实现，
  macOS 上使用 fake 处理器（仅测试注入），保证 `swift test` 的纯逻辑测试可跑。
- **Objective-C 例外**：`CWebSocketClose` 不依赖任何平台库，macOS/iOS 均可编译；仅暴露
  `LivaChatWebSocketCancelWithCode(task, code, reason)`，用于发送 Swift 枚举无法表达的 4000 关闭码。
- **模块名冲突**：`LivaChatSDK` 内部 `import LivaChatProtocol`，公开枚举与协议枚举同名时，
  在本模块内使用本地定义、在构造 DTO 时显式 `LivaChatProtocol.Xxx` 限定，避免歧义。
- **测试 scheme**：`apps/ios/LivaChatSDK` 是单 product 包，测试 scheme 为 `LivaChatSDK`；
  `packages/sdk-swift` 的测试 scheme 为 `LivaChatProtocol`。

---

## 5. 组件详述

### 5.1 LivaChatSDK (Facade)

初始化顺序：

```
1. 校验 config（16k/20ms 之外抛 LivaChatError.invalidConfiguration / unsupportedSampleRate）
2. SdkLogger.init(logFileURL)
3. 创建 OpusEncoder / OpusDecoder / VadDetector（失败抛 LivaChatError）
4. 创建 StateStore（`playbackEpoch` 与其余状态同一把锁、单一账本）、AudioRecvPipeline、
   SessionManager、ConnectionManager
5. 创建 AudioSendPipeline / VideoSendPipeline
6. wire:
     connectionManager.onMessage        → connectionQueue 解码为 ServerMessage
                                        → sessionQueue: sessionManager.handleInbound
     connectionManager.onPing            → sessionQueue: sessionManager.sendPing
     connectionManager.onOpen(epoch)     → sessionQueue: sessionManager.resetSessionGate(connectionEpoch: epoch)
                                        （仅入队；epoch 事件统一由 onEpoch 发出，避免重复）
     connectionManager.onStateChange(state, epoch)
                                        → stateStore.transitionConnectionState(state, connectionEpoch: epoch)
                                          （单锁内完成：旧 CONNECTED 且新状态 != CONNECTED 时 playbackEpoch +1，
                                          并写回完整快照；随后才 yield .connectionStateChanged，§5.3.4）
                                        → state != .connected 时 sessionQueue: resetSessionGate(connectionEpoch: epoch)
     connectionManager.onEpoch            → 更新 state 快照 + yield 事件
     connectionManager.onTerminated(reason)
                                        → yield `.connectionTerminated(reason)`（先于后续 disconnected 事件）
     sessionManager.eventSink            → sessionState 变化更新 state 快照；其余事件直接 yield
     audioSendPipeline.onEncoded         → sessionQueue: sessionManager.sendAudio
     audioSendPipeline.onVad             → 更新 VAD 快照（去重）+ yield 事件
     videoSendPipeline.onEncoded         → videoSendQueue: sessionManager.sendVideo（取号+编码）
                                        → sessionQueue: sendRaw（只送包）
```

> `onOpen(epoch)` / `onStateChange(state, epoch)` 运行在 `connectionQueue`，**不得直接调用 SessionManager**。
> `resetSessionGate(connectionEpoch:)` 必须先入队到 `sessionQueue`，再 yield epoch 事件；这样 App 收到 epoch 后
> 调用的 `startSession` 会排在 reset 之后（sessionQueue FIFO），不会被 reset 误清。旧连接的延迟 reset 由
> `connectionEpoch` 比较丢弃（§5.3.2）。

行为约定：

- `pushAudioPCM` / `pushVideoJPEG` 先做防御性拷贝（Swift `Array` / `Data` 值语义；`Data` 为写时复制），
  再投递到对应队列；链路内异常只记录日志，不向 App 抛出。
- `pushVideoImage` 在调用线程同步编码（避免 `CGImage` 跨队列生命周期问题）；相机高频路径应使用 JPEG 直通。
- 音频失败日志按“失败段”节流：同一连续失败段只记首条，成功后复位。
- `disconnect()` 仅断开连接；不发送 `session.end`，不重置会话状态，不释放 native 处理器。
- `deinit` 中停止定时器、取消 URLSession、`finish()` 事件流；native 资源随对象释放。
- SDK 通常作为 App 进程级单例长期存活；配置在实例生命周期内不可变。

### 5.2 ConnectionManager

```swift
internal final class ConnectionManager: @unchecked Sendable {
    init(serverURL: URL, token: String, deviceID: String,
         connectTimeout: TimeInterval,
         reconnect: ReconnectConfig, keepalive: KeepaliveConfig,
         tlsPolicy: TLSPolicy, logger: SdkLogger)

    func connect()
    func disconnect()
    func send(_ text: String) -> Bool

    var onMessage: ((String) -> Void)?
    var onPing: (() -> Void)?
    /// didOpen 后回调；参数为本次建连的新 connectionEpoch。
    var onOpen: ((UInt64) -> Void)?
    /// 状态变化；参数为当前连接的 connectionEpoch（供 session gate 丢弃旧 epoch 的延迟 reset）。
    var onStateChange: ((ConnectionState, UInt64) -> Void)?
    var onEpoch: ((UInt64) -> Void)?
    /// 连接进入终态（不再自动重连）时回调；必须先于 onStateChange(.disconnected)。
    var onTerminated: ((ConnectionTerminatedReason) -> Void)?
}
```

**建连**

- 每次 `connectInternal()` 新建 `URLSession`（`delegateQueue: nil`，即 URLSession 自建串行 OperationQueue），
  并通过 `URLComponents` 拼接：

```
${serverURL}?token=<percent-encoded token>&device_id=<percent-encoded UUID>
```

- `URLSessionConfiguration`：
  - `timeoutIntervalForRequest`：建议 10s（仅作为握手/请求层兜底；本机实测它**不会**终止已建立但空闲的
    WebSocket，空闲判活由应用层心跳负责）
  - `waitsForConnectivity = false`
  - 不启用系统 WebSocket ping（应用层已有 JSON ping/pong）
- **Delegate 回调转发**：`delegateQueue: nil` 时 delegate/task 回调运行在 Foundation 自建串行队列，
  **而非** `livachat.connection`。所有回调（didOpen / didCloseWith / didCompleteWithError / receive 完成）
  必须先做 generation 比对、再 `connectionQueue.async` 转发；状态机、JSON 解码、心跳与重连处理只在
  `livachat.connection` 上执行，禁止在 Foundation 队列直接读写内部状态。
- **建连 watchdog**：每次 `connectInternal()` 在 `livachat.connection` 启动 `connectTimeout`
  （默认 10s）定时器；`didOpen`、`didCloseWith`、`didCompleteWithError` 或 `disconnect()` 时取消。
  超时则按心跳超时同一路径发送 close(4000)（经 ObjC bridge），0.2s 后 invalidate 当前 session，
  并进入退避重连。原因：`timeoutIntervalForRequest` 对 upgrade 阶段是否生效未经验证，黑洞握手
  （TCP 已 accept、永不完成握手）不能依赖它，否则可能永久挂在 `CONNECTING`。
- 每个连接一个 `URLSession`；连接关闭/失败/进入重连前调用 `finishTasksAndInvalidate()` 释放旧
  session（`disconnect()` 用 `invalidateAndCancel()`）。不要跨连接复用 session 或 task。
- **循环引用**：`WebSocketDelegate` 对 manager 持 **weak** 引用，避免 manager → session → delegate → manager
  的持有环。实例被遗弃（未调用 `disconnect()`）时，manager 的 `deinit` 执行
  `cleanupSession(cancel: true)`（取消 task、`invalidateAndCancel()`），确定性释放 session 与 delegate。
- 每个 socket 维护 `generation: UInt64`；所有 delegate/receive 回调先比对 generation，旧代回调直接丢弃。
- 收到 `didOpen` 后启动 receive 循环；同一时刻只有一个未完成的 `receive`。
- 二进制帧或未知消息类型：只记日志，经 `continueReceiving(generation:)` 立即重新发起 `receive`
  并刷新心跳入站时间。**不得**因非文本消息让接收循环中断，否则其后所有消息会静默丢失。

**状态转换**

| 事件 | 结果 |
|------|------|
| `connect()` | `CONNECTING`，重置退避计数 |
| `didOpen` | `CONNECTED`，`connectionEpoch + 1`，启动心跳，重置退避 |
| close 1000 | `.connectionTerminated(.serverClosed)` → `DISCONNECTED`，不重连 |
| close 4001（应用层关闭码） | 鉴权终态：`.connectionTerminated(.authFailed)` → `DISCONNECTED`，不重连 |
| HTTP 401（握手，`URLError.badServerResponse` + `task.response.statusCode == 401`） | 鉴权终态：`.connectionTerminated(.authFailed)` → `DISCONNECTED`，不重连 |
| 其他 close / 网络错误 / 心跳超时 / 建连 watchdog 超时 | `RECONNECTING` → 退避后重连 |
| `disconnect()` | `DISCONNECTED`，停止心跳与重连；`cancel(with: .normalClosure, reason: nil)` + `invalidateAndCancel()`；不发 `.connectionTerminated`（对随后到达的 1000 回声按 generation 抑制） |

`didOpen` 内部顺序固定为：取消建连 watchdog → 置 `CONNECTED` → 自增 connectionEpoch → 重置退避计数 →
启动心跳 → 回调 `onOpen(epoch)`（内部将 `resetSessionGate(connectionEpoch:)` 入队到 `sessionQueue`）→
`onStateChange(.connected, epoch)` 发 `.connectionStateChanged(.connected)` →
`onEpoch(epoch)` 发 `.connectionEpochAdvanced(n)`。

> `onOpen` 运行在 `connectionQueue`，只做入队；SessionManager 是 `sessionQueue` 封闭的，
> 不得被跨队列直接调用。

> 关于关闭回调：本机实测 close 4001 时 `didCloseWith` 的 `closeCode.rawValue == 4001`；4001 与正常
> 1000 关闭前 `receive` 都可能先报 `NSPOSIXErrorDomain Code=57`。实现必须按 generation 去重，**不能**把
> receive 失败当作独立终态；以 `didCloseWith` / `didCompleteWithError` 的合并结果为准。

**心跳（入站静默判据）**

`HeartbeatScheduler` 只记录最后一次入站消息时间：

- 入站静默 ≥ `keepaliveInterval`（默认 15s）：触发一次 `sessionManager.sendPing()`，每个静默窗口最多一次。
- 入站静默 ≥ `keepaliveTimeout`（默认 35s）：判定连接死亡，close(4000) 并进入重连。
- 任意入站消息（含 `pong`、服务端 `ping`）都会重置窗口。
- 出站流量不影响判据（与 Android 一致，避免单向流场景下永不 ping）。
- 服务端 `session.ready.keepalive_interval_s` 当前不采用（与 Android 一致；保留字段）。

> 关闭码 4000 的发送：`URLSessionWebSocketTask.CloseCode` 是 Swift enum，`init(rawValue: 4000)`
> 返回 nil，`cancel(with:reason:)` 无法表达该值。实现经 `Sources/CWebSocketClose` 的 ObjC bridge
> （`cancelWithCloseCode:reason:`）原样发送 4000；发送后延迟 0.2s 再 invalidate session，让关闭帧
> 先落网，然后进入退避重连。进程内测试服务端已断言实际收到 `code=4000`；心跳超时与建连 watchdog
> 共用 `closeWithRawCodeAndReconnect(code:reason:)`。

**重连**

- 延迟：`base × 2^(attempt-1)`，封顶 `max`，叠加 ±0.5s 抖动。
- 默认 1s、2s、4s、8s、16s，最多 5 次；超过上限：发 `.connectionTerminated(.reconnectExhausted)` 后进入
  `DISCONNECTED`。耗尽判断先于 `RECONNECTING` 状态更新，因此 App 不会看到
  `reconnecting → terminated → disconnected` 的瞬态抖动。
- 手动 `connect()` 与任意一次成功建连都会重置退避计数。
- `disconnect()` 取消待执行的重连；再次 `connect()` 重新开始。
- 重连成功后 SDK **不会**自动发送 `session.start`；由 App 观察 epoch 触发。

**发送**

- `send()` 仅在 `CONNECTED` 时调用 `task.send(.string(text))`；否则静默丢弃并返回 false。
- 同一失败段只记录一条 `send dropped` 日志；恢复后记录一条 `send resumed`。
- 发送由 `sessionQueue` 串行调用；本机实测 200 条连续 send 收序一致，但实现仍以串行队列为契约。
- 发送失败不直接触发重连（由心跳/接收路径判定）。

**TLS**

- 决策（2026-10-19）：`.allowSelfSigned` **允许进入仓库**，为显式 opt-in；开发/内网/自建以及
  **生产自签名部署**均可开启。SDK 不限制构建类型，生产需要自签名证书时直接配置该模式。
  它等价于 trust-all（接受任意服务端证书，不校验链与主机名），有 MITM 风险，需在可信
  endpoint/受控网络下使用；若需更强保证，使用 CA 证书或后续的证书 pinning（暂不实现）。
  启用时 SDK 在初始化日志输出一条 WARN（不打印 URL/token）。
- `.system`：使用系统证书校验。
- `.allowSelfSigned`：`URLSessionDelegate` 的 `didReceive challenge` 中对 `serverTrust` 调用
  `completionHandler(.useCredential, URLCredential(trust: trust))`。仅对
  `NSURLAuthenticationMethodServerTrust` 使用该处理，其他挑战走默认。该模式是 trust-all：接受任意
  服务端证书（不校验证书链与主机名），不是“只信任自签名证书”。生产自签名部署同样可用，
  但需有意识地承担 MITM 风险，并确保 endpoint 可信。
- ATS：SDK 默认使用 `wss://`；`ws://` 需要 App 的 Info.plist 例外（不建议）。

### 5.3 SessionManager

```swift
internal final class SessionManager: @unchecked Sendable {
    init(codec: MessageCodec,
         sendRaw: @escaping (String) -> Bool,
         eventSink: EventSink,
         stateStore: StateStore,
         now: @escaping () -> Int64,
         configProvider: @escaping () -> ClientSessionStartConfig)

    var sessionState: SessionState { get }
    var sessionID: String? { get }
    var currentConnectionEpoch: UInt64 { get }

    func startSession(resumeSessionID: String?)
    func endSession(reason: LivaChatSessionEndReason)
    func sendInterrupt(reason: InterruptReason)
    func sendSessionChange(mode: SessionMode, myLanguage: String?, localLanguage: String?, usecase: SessionUsecase?)
    func sendAudio(_ payload: ClientAudioPayload)
    func sendVideo(_ payload: ClientVideoPayload)
    func sendPing()
    func resetSessionGate(connectionEpoch: UInt64) // 新连接建立/断开时调用；旧 epoch 的延迟调用被丢弃（§5.3.2）
    func handleInbound(_ message: ServerMessage)
}
```

除 `sendVideo` 外，所有方法均在 `sessionQueue` 上执行（`handleInbound` 由 connectionQueue 解码后投递）。
`sendVideo` 在 `videoSendQueue` 取号并编码，再投递 `sessionQueue` 发送（对齐 Android 的 video 宽容路径）。

#### 5.3.1 发送闸口

- `SeqGenerator` 从 1 开始递增；计数器必须线程安全（`NSLock` / 原子），因为 `sessionQueue`
  与 `videoSendQueue` 都会取号。
- 非 video 业务消息（`audio` / `interrupt` / `session.change` / `session.end`）：先检查闸门，通过后才
  `next()`，payload 构建、JSON 编码与 `sendRaw()` 在 `sessionQueue` 的同一次同步执行中完成
  （该队列本身即临界区）。`ping` / `pong` 与 `session.start` 不检查闸门（`session.start` 本身是开门动作）。
- `ping` / `pong` 也分配 seq（服务端对二者跳过 seq 校验）。
- `video` 例外：由 `videoSendQueue` 取号并编码，`sessionQueue` 只负责发送；发送前再检查一次闸门，
  false 则丢弃（已消耗的 video seq 形成空洞；服务端只校验回退，不校验空洞）。
  服务端对 video 乱序容忍（`services/gateway/seq.py`），因此视频 seq 可能小于已发送的音频 seq。
- 序号不在重连时归零；服务端每连接新建水位，并在收到 `session.start` 时重置。

#### 5.3.2 上行闸门（iOS 新增）

为保证“建连后的首个业务 C2S 必须是 `session.start`”：

- `sessionStartedOnConnection` 初值 false；`resetSessionGate(connectionEpoch:)` 在每次 `didOpen`、连接关闭/失败/进入
  `RECONNECTING`/`DISCONNECTED` 时由 connectionQueue 投递到 sessionQueue，并携带触发它的那条连接的
  connectionEpoch。仅当 `connectionEpoch >= currentConnectionEpoch` 时生效（更新 `currentConnectionEpoch`
  后置闸门 false）；`connectionEpoch < currentConnectionEpoch` 的调用直接丢弃——旧连接的延迟 reset 不得
  误清新连接的闸门。否则新连接上 `startSession` 之后到达的旧 reset 会把闸门清零，后续音频/视频静默
  丢弃且无任何机制恢复。
- `startSession(...)`：发送 `session.start`；成功时置闸门 true，**失败时保持原值**并记日志。
  首次发送失败时闸门保持 false（避免“session.start 没上线但后续音频已放行”）；已开启会话上重复
  `startSession` 发送失败时闸门保持 true（服务端旧会话可能仍存活，不能静默停发音频）。
- `sendAudio` / `sendVideo` / `sendInterrupt` / `sendSessionChange` / `endSession` 在 false 时丢弃并记日志；
  其中 `sendAudio` 走 50Hz 高频路径，按“闸门关闭段”节流（每段仅一条 warn，闸门重新打开时复位），
  避免线上“音频没上去”零观测。非 video 的闸门检查在取 seq 之前；video 的最终检查在 sessionQueue 发送前。
- `sendPing` / `sendPong` 不受闸门限制（服务端在 seq 校验前处理它们）。
- `session.ended` 或 `error(fatal)` 后置 false；后续音视频/控制信令丢弃，直到 App 重新 `startSession`。

> `resetSessionGate(connectionEpoch:)` 必须先于 epoch 事件被 App 消费；实现上先入队 sessionQueue，再 yield 事件。

> 与 Android 的差异：Android 依赖 App 在 epoch 回调中调用 `startSession` 的时序，未在 SDK 内强制闸门。
> iOS 在 SDK 内强制，避免重连窗口内音频先于 `session.start` 上线。

#### 5.3.3 服务端状态机

- 运行期只有 `status` 消息更新 `sessionState`；`session.ready` 不改变状态。
- 映射：`idle/listening/thinking/speaking` → 同名 `SessionState`。
- `status(thinking)` 同时触发 `playbackShouldFlush(.interruptAck, epoch: 新播放代际)`。
- `SPEAKING → LISTENING` 当前不触发 flush（与 Android 一致）。

#### 5.3.4 强制 flush

| 触发 | reason |
|------|--------|
| `status(thinking)` | `.interruptAck` |
| `session.ended` | `.sessionEnded` |
| `error(fatal=true)` | `.serverAbort` |

播放代际（`playbackEpoch`）：

- `playbackEpoch` 初值 1，与其余状态同由 `StateStore` 在**同一把锁**内持有，SDK 生命周期内不重置。
  `StateStore` 提供 `incrementPlaybackEpoch() -> UInt64` 与
  `transitionConnectionState(_:connectionEpoch:) -> UInt64`；`sdk.state` 是一次 `snapshot()`
  原子读取，不存在“state 与 epoch 分两次读再拼装”的撕裂窗口，也不存在第二份被忽略的 epoch 账本。
  两处使其 +1：
  (a) 上表 flush 触发时（SessionManager 在 sessionQueue 调 `incrementPlaybackEpoch()`，先 +1，
      再携带新值发 `playbackShouldFlush`）；
  (b) 连接状态从 `CONNECTED` 转入任意其他状态时（`transitionConnectionState` 在单锁内完成
      “判断离开 CONNECTED → epoch +1 → 写回完整快照”，Facade 随后 yield
      `.connectionStateChanged`——保证 App 收到事件后读 `sdk.state.playbackEpoch` 已是新值；
      `DISCONNECTED → CONNECTING` 的手动重连不重复递增）。
- SessionManager 在入站 `ai_audio` 处理路径为每帧打上 `stateStore.snapshot().playbackEpoch`
  （与 `sequenceNumber` 同时分配）。
- `sdk.state.playbackEpoch` 始终来自同一次 `snapshot()`，flush 后（含身份/连接状态并发变化时）
  不会出现状态与代际来自不同时刻的问题。
- `playbackShouldFlush` 事件携带 **flush 后的新 epoch**。App 记录该值，之后丢弃 `playbackEpoch`
  小于记录值的在途帧。事件流（Main）与 audio recv 队列之间无先后保证（§3.5），该机制是打断/断连
  场景下丢弃过期 PCM 的唯一可靠手段；`playbackEpoch` 大于记录值的帧（flush 后新到的音频）允许
  先到达，不丢弃。

#### 5.3.5 消息分发

- 解码失败（非法 JSON / 未知 `type`）→ 静默忽略并记日志；不断开连接。
- `ping` 自动回 `pong`，`ping_seq` 回显服务端 ping 的 `seq`。
- 服务端 `pong` 忽略（心跳只关心入站流量）。
- 入站消息按到达顺序处理；不实现 `ReorderBuffer`（Android 也保留但未接入分发路径）。
- `ai_audio`：Base64 解码由 `JSONDecoder` 在 connectionQueue 完成；解码后的消息投递到 sessionQueue，
  由 SessionManager 在入站处理路径分配 `sequenceNumber`（从 1 单调递增，不重置），再投递到
  `audioRecvQueue` 解码；单串行入站路径保证序号与解码顺序一致。
- `ai_video_ack`：记录 `lastAckedVideoFrameID`；若 `frameID > lastAcked + 1`，记录丢帧区间警告；
  仅向 App 暴露 `frameID` 与 `processed`。
- `job_status`：全量替换；`startedAtMs` 用 `ClockOffset` 换算为本地 epoch ms。
- `ai_text`：仅暴露 `text/messageID/index/isFinal/image/source`（与 Android 一致；`replyTo/agentName/proactive` 不暴露）。

#### 5.3.6 会话生命周期

- `startSession(resumeSessionID)`：发送 `session.start`；仅在发送成功后置闸门 true（失败保持原值，
  重发失败不会关闭已开启的闸门）；不改变 `sessionState`。
- `endSession(reason)`：发送 `session.end`；原因映射 `userHangup→user_quit`、`appBackground→app_background`、`error→error`；
  发送后置闸门 false。
- `sendInterrupt`：`userVoice→user_voice`、`userTap/appCancel→manual`。
- `sendSessionChange`：`SessionMode` 编译期限定为 chat/translate/transcribe/omni；`SessionUsecase` 为 lecture/dialogue；
  不再需要 Android 的字符串校验与警告。
- 重连后状态保持断开前的值，直到服务端下发新的 `status`。

### 5.4 AudioSendPipeline

```swift
internal final class AudioSendPipeline: @unchecked Sendable {
    init(sampleRate: Int,
         frameMs: Int = 20,
         vad: VadDetecting,
         encoder: OpusEncoding,
         onEncoded: (Data, Int, VadState, Bool) -> Void,
         onVad: (VadState) -> Void,
         uplinkAGC: Bool,
         isAISpeaking: () -> Bool)

    func pushPCM(_ pcm: [Int16], muted: Bool)
    func reset()
}
```

处理顺序（逐帧）：

```
PCM → FrameAssembler → VAD → （可选）UplinkAGC → Opus encode → SessionManager.sendAudio
```

- `FrameAssembler.frameSamples = sampleRate × frameMs / 1000`（16k/20ms = 320）。
  不足一帧的数据保留在内部缓冲，跨多次 `pushPCM` 拼帧。
- 每完成一帧：先 `vad.detect` 并回调 `onVad`（驱动 `localVadState`），再执行 AGC，最后 Opus 编码。
- `localVadState` 初值 `.unknown`；线上映射为 `silence`；`muted=true` 的帧线上标记 `vad_state="muted"`。
- `muted` 标记取“完成该帧的那次 push”的值（若一帧跨越 mute 沿，取静音侧值）。
- 输入 PCM 采样率必须与 `config.audioSampleRate` 一致；SDK 不做重采样。
- `FrameAssembler` 在回调前先复位 `pendingLen`，避免回调抛错后反复重发同一帧（移植 Android 的防御逻辑）。

**UplinkAGC（makeup-only，移植 Android `UplinkAgc.kt`）**

| 参数 | 值 |
|------|-----|
| 目标电平 | -20 dBFS |
| 最大增益 | +18 dB |
| 噪声门 | -55 dBFS（低于此不提升） |
| 峰值上限 | -3 dBFS |
| attack / release | 20ms / 400ms |
| 作用位置 | VAD 之后、Opus 编码之前 |
| AI 说话期间 | 保持 unity（不提升），避免放大扬声器回声造成假打断 |

AGC 只提升、不衰减；数字静音帧直接跳过逐样本处理。

### 5.5 AudioRecvPipeline

```swift
internal final class AudioRecvPipeline: @unchecked Sendable {
    init(sampleRate: Int, decoder: OpusDecoding,
         onPCM: ([Int16], Int, Int, UInt64) -> Void)

    func handleAIAudio(_ opus: Data, durationMs: Int, sequenceNumber: UInt64)
    func reset()
}
```

- 入站 `ai_audio.data` 已由 `JSONDecoder` 从 Base64 解为 `Data`（connectionQueue）；Opus 解码在 `audioRecvQueue`。
- `sequenceNumber` 与 `playbackEpoch` 由 SessionManager 在入站时同时分配/打戳；sequenceNumber 单调递增不重置，playbackEpoch 语义见 §5.3.4。
- 解码器以 `config.audioSampleRate`（16k）输出；服务端普通 TTS 输出 16k，lecture 模式使用 24k Opus 编码时
  由 Opus 解码器内部重采样到 16k，`durationMs` 不变。
- SDK 不缓冲 PCM，解码后立即调用 `LivaChatAudioSink`；播放顺序与缓冲由 App 负责。
- 解码失败：记录日志并丢弃该帧，不影响后续帧。
- `ai_audio` 的 `message_id/index/is_final` 会被解析，但当前不暴露给 App（与 Android 一致）。
  `turnID` 当前恒为空字符串。

### 5.6 VideoSendPipeline

```swift
internal final class VideoSendPipeline: @unchecked Sendable {
    init(logger: SdkLogger,
         onEncoded: (Data, UInt32, Int, Int, Orientation, CameraFacing, UInt32, String?) -> Void)

    func pushJPEG(_ jpeg: Data, width: Int, height: Int, orientation: Orientation,
                  cameraFacing: CameraFacing, turnID: UInt32, text: String?)
}
```

- `frameID` 由 Facade 的锁保护计数器分配，JPEG/Image 两条路径共用，从 1 递增。
- JPEG 路径：字节直通，不缩放、不重新压缩；`width/height` 为调用方声明值。
- Image 路径**不在 pipeline 内**：Facade 的 `pushVideoImage` 在调用线程完成质量钳制（1...100）、
  `videoMaxLongSide` 等比缩放与 ImageIO 编码，然后转 `pushJPEG`。质量钳制只此一处，pipeline
  不持有 encoder。payload 的 `width/height` 使用 **原始 CGImage 尺寸**（对齐 Android Bitmap 路径）。
- 视频 payload 的 `quality` 字段固定写 `config.videoQuality`（两条路径均如此，对齐 Android）。
- 不做帧率限制与发送节流，由 App 决定调用频率。
- `cameraFacing` 为强类型枚举；不存在 Android 的“未知字符串兜底 front”。
- `turnID` 直通落线；`turnID=0` 时仍编码为 `"turn_id": 0`（对齐 Android 实际行为）。
- `text=nil` 时字段整体省略（Swift 可选字段默认省略）。

### 5.7 Native 处理器

```swift
internal protocol OpusEncoding: AnyObject {
    func encode(_ pcm: UnsafeBufferPointer<Int16>) throws -> Data
}
internal protocol OpusDecoding: AnyObject {
    func decode(_ opus: Data) throws -> [Int16]
}
internal protocol VadDetecting: AnyObject {
    func detect(_ pcm: UnsafeBufferPointer<Int16>) -> VadState
}
internal protocol JPEGEncoding {
    func encode(_ image: CGImage, quality: Int, maxLongSide: Int?) throws -> Data
}
```

#### 5.7.1 Opus

| 参数 | 值 |
|------|-----|
| Application | `OPUS_APPLICATION_VOIP` |
| 采样率 | 16000（24k 构造期拒绝） |
| 通道 | mono |
| 帧长 | 固定 20ms（320 samples @16k） |
| 码率 | CBR 32 kbps |
| 复杂度 | 5 |
| 信号类型 | `OPUS_SIGNAL_VOICE` |
| 编码输出缓冲 | 256 bytes |
| 解码最大样本 | 5760（兼容任意合法 Opus 包时长） |

- 通过 `COpus` 的 C shim 调用，Swift 不直接处理 `OpusEncoder*` 生命周期。
- 每个处理器队列封闭；`deinit` 释放 native handle。

#### 5.7.2 WebRTC VAD

- API 与 Android `webrtc_vad_lite` 一致：`Wvad_Create/Free/SetMode/Process/ValidRateAndFrameLength`。
- 支持采样率 8000 / 16000 / 32000；SDK 公开配置仅开放 16000。
- 固定 20ms 帧；初始化时调用 `Wvad_ValidRateAndFrameLength(rate, 320)` 校验。
- 返回 1 → `.speech`，0 → `.silence`，其余 → `.unknown`（记日志）。
- iOS 产物已就绪：`3rdparty/dist-ios/WebRtcVadLite.xcframework`（minos 15.0；
  `Wvad_SourceRevision()` 非空）；SDK 通过 `CVad` target 调用。
  主机侧单测仍可用 fake VAD。

#### 5.7.3 JPEG（P1）

- `CGImage` → 等比缩放（长边 ≤ `videoMaxLongSide`）→ ImageIO `CGImageDestination` 编码 JPEG。
- 使用 CoreGraphics/ImageIO；**不使用 UIKit**。
- 缩放使用 `CGContext` 绘制，保持宽高比；无需 `UIImage`。

### 5.8 SdkLogger

- 同时写 `os.Logger`（subsystem `dev.livachat.sdk`，category 为 tag）与可选文件。
- 文件追加写入；父目录自动创建；使用 `O_APPEND` + 单次 `write(2)`，无需应用层 flush。
- 每行格式：`<ISO8601 UTC 微秒> <D|I|W|E>/SDK/<tag> <message>`，例如
  `2026-10-20T07:28:56.598000Z I/SDK/WS reconnect scheduled in 1.00s`。
- App 与 SDK 可写入同一文件（App 行为 `<timestamp> <level>/App/<component> <message>`），
  两写者都用 `O_APPEND`；消息内 `\n`/`\r` 转义，单条截断到 8192 字符。
- 文件写入在 `logQueue` 串行执行；失败静默忽略，不向业务链路抛出。
- `deinit` **不得**对 `logQueue` 做 `sync`：日志异步块内的强引用可能成为最后一个引用，
  使 deinit 恰好在 `logQueue` 上执行（libdispatch 同队列 sync 会直接 abort）。关闭文件句柄
  改为 `queue.async { close }`，FIFO 保证在全部已排队写入之后执行。
- App 可通过公开 `log*` 方法写入同一日志文件；也可使用 App 侧统一 `AppLog`（推荐）。
- 日志中禁止输出完整 WS URL（token/device_id），只输出 host/port 与状态。

---

## 6. 协议

### 6.1 消息类型

C2S（`ClientMessage`）：

| type | 用途 |
|------|------|
| `session.start` | 新建或恢复会话；建连后的首个业务消息 |
| `session.end` | 结束会话 |
| `session.change` | 请求切换管线模式 |
| `audio` | 20ms Opus 帧（Base64，`codec="opus"`，`duration_ms`，`vad_state`） |
| `video` | JPEG + `frame_id` / 尺寸 / 朝向 / 来源 / `turn_id` / 可选 `text` |
| `interrupt` | 打断当前 AI 回合 |
| `ping` | 应用层心跳 |
| `pong` | 回应服务端 ping |

S2C（`ServerMessage`）：

| type | SDK 处理 |
|------|----------|
| `session.ready` | 更新 sessionID 与时钟偏移，发 `.sessionReady` |
| `session.ended` | 发 `.sessionEnded`，触发 `.sessionEnded` flush，闸门置 false |
| `session.changed` | 发 `.sessionChanged`（强类型枚举） |
| `transcription` | 发 `.transcription` |
| `ai_text` | 发 `.aiText`，携带可选 `image` 与 `source` |
| `ai_audio` | Base64 → `Data` → Opus 解码 → `audioSink` |
| `ai_video_ack` | 发 `.videoAck`，SDK 内检测丢帧区间 |
| `job_status` | 全量替换 → `.jobStatus` |
| `status` | 更新 `sessionState`，按需 flush |
| `error` | 发 `.error`；fatal 时触发 `.serverAbort` flush |
| `ping` | 自动回 `pong` |
| `pong` | 忽略（仅作为入站存活证据） |

DTO 唯一来源是 `packages/api-schema/`；修改协议时先改 schema，再执行 `make generate-swift-api`。

### 6.2 MessageCodec

复用 `LivaChatProtocol.ProtocolJSON`：

```swift
let encoder = ProtocolJSON.makeEncoder() // dataEncodingStrategy = .base64
let decoder = ProtocolJSON.makeDecoder()
```

- DTO 已显式声明 `CodingKeys`，不使用 `.convertFromSnakeCase`。
- `JSONEncoder` / `JSONDecoder` 不是线程安全的：`sessionQueue` 持有非 video 的 encoder，
  `videoSendQueue` 持有 video 的 encoder，`connectionQueue` 持有 decoder；不要跨队列共享实例。
- 可选字段 nil → key 省略；`session_id` required-nullable → 恒输出 `null` 或字符串。
- 编码失败：记录日志并丢弃该消息（Swift 抛错由调用方捕获）。
- 解码失败 / 未知 `type`：记录日志并丢帧，不断开连接。
- 严格枚举：未知枚举值导致整帧解码失败并丢帧（与 DTO 设计 D6 一致）。

### 6.3 会话启动配置

`session.start.payload.config`：

```json
{
  "enable_video": true,
  "audio_sample_rate": 16000,
  "vad_sensitivity": "medium",
  "client_version": "ios-1.0.0.42",
  "timezone": "Asia/Shanghai",
  "language": "zh-Hans"
}
```

- `timezone`：`config.timezone` 优先；nil 时每次发送前读取 `TimeZone.current.identifier`。
- `language`：`config.languageProvider` 优先（每次发送前执行），否则用 `config.language`；两者都 nil 时省略。
- `audio_sample_rate`：`AudioSampleRate.rawValue`。
- `vad_sensitivity`：low / medium / high。
- `session_id`：`resumeSessionID`；nil 时显式编码为 `null`（DTO 已处理）。

`session.ready` 字段处理：

| 字段 | SDK 处理 |
|------|----------|
| `session_id` | 保存并在 `.sessionReady` 回调；App 重连时作为 resume id |
| `session_restored` | 透传为 `.sessionReady.restored`（当前服务端恒 false） |
| `server_time_ms` | 与本地时间计算 `ClockOffset`，用于 job status 换算 |
| `keepalive_interval_s` | 当前未使用（与 Android 一致） |
| `server_version` | 仅信息字段，SDK 不消费 |

### 6.4 序号与发送顺序

- C2S `seq` 从 1 开始；非 video 在 `sessionQueue` 取号，video 在 `videoSendQueue` 取号，二者共享
  一个线程安全计数器。
- 除 `video` 外的所有消息在 `sessionQueue` 内完成“取号 → 构建 → 编码 → 交给传输”。
- `video` 在 `videoSendQueue` 取号与编码，`sessionQueue` 只发送；服务端容忍乱序（可能产生 seq 空洞）。
- S2C 按到达顺序处理，不重排。

### 6.5 视频回合语义

- `frame_id`：SDK 级单调递增，用于 ack 配对与丢帧检测。
- `turn_id`：App 维护；从 1 起；0 = 未启用。服务端观察到变化即视为新回合首帧。
- `text`：非空时服务端立即以该文本 + 本帧起一轮。
- `camera_facing`：front / back / screen / gallery。
- `ai_video_ack`：SDK 仅暴露 `frameID` 与 `processed`；`ack_frame_id` 用于内部丢帧统计。

### 6.6 状态与 flush

```
status(idle)      → SessionState.idle
status(listening) → SessionState.listening
status(thinking)  → SessionState.thinking + playbackShouldFlush(.interruptAck, epoch: 新播放代际)
status(speaking)  → SessionState.speaking
```

正常对话顺序示例：`listening → thinking → speaking → listening`。
服务端进入 `thinking` 表示上一轮 TTS 已被截断或已结束，App 应清空播放队列。

### 6.7 公开值映射

| 公开值 | DTO / wire |
|--------|-----------|
| `AudioSampleRate.hz16000` | `LivaChatProtocol.AudioSampleRate.hz16000`（wire 16000） |
| `VadSensitivity.medium` | `LivaChatProtocol.VadSensitivity.medium`（wire "medium"） |
| `VadState.unknown`（上行） | `VadState.silence`（wire "silence"） |
| `VadState.*` + `muted` | wire `vad_state` = speech / silence / muted |
| `InterruptReason.userVoice` | `user_voice` |
| `InterruptReason.userTap` / `.appCancel` | `manual` |
| `LivaChatSessionEndReason.userHangup` | `user_quit` |
| `LivaChatSessionEndReason.appBackground` | `app_background` |
| `LivaChatSessionEndReason.error` | `error` |
| `SessionEndedReason.*` | 服务端 `session.ended.reason` 同名 raw value |
| `SessionMode.chat` | `chat` |
| `SessionUsecase.lecture` | `lecture` |
| `AITextSource.llm` | `llm`（缺省） |
| `Orientation.landscape90` | wire 90 |
| `CameraFacing.screen` | wire `screen` |
| `AgentJob.startedAtMs` | `started_at_ms − clockOffset` |
| `AiImage.data` | `ai_text.image.data`（Base64 → `Data`） |

---

## 7. 宿主 App 集成（apps/ios 宿主约束；M0 骨架已落地）

> 本节是 SDK 的宿主约束，不是 SDK 实现。SDK 已在 `apps/ios/LivaChatSDK`；宿主 App
> `apps/ios/LivaChatApp` 的 M0 工程骨架已落地（产品功能待实现，见 `docs/ios_app_m0.md`）。

### 7.1 生命周期与装配

- SDK 作为进程级单例持有；配置在实例生命周期内不可变（与 Android 一致）。
- 服务器地址、token、deviceID 的装配：
  - 地址：`wss://<host:port>/v1/ws/stream`（`docs/setting_server_address.md`）
  - token：`CryptoKit.Insecure.SHA1.hash(secret + "LivaChat")` → 小写 hex（协议固定向量见 `docs/server_auth.md`）
  - deviceID：UUID v4，持久化到 Keychain（决策 2026-10-19；卸载重装保留）
  - 明文 secret 只存设置层（Keychain）；wire token 由 secret 派生，可缓存于 Keychain；两者都不写日志
  - clientVersion：`ios-<CFBundleShortVersionString>.<CFBundleVersion>`（build 单段数字）
- App 启动顺序（对齐 Android `ConversationCoordinator`）：

```
coordinator.start()
  ├─ 取/建本地会话
  ├─ 启动 sdk.events 消费 Task
  ├─ sdk.connect()
  ├─ audioCapture.start()
  ├─ audioPlayback.start()
  └─ interruptCoordinator.start()
```

> 采集在 connect 后立即启动（对齐 Android）；`session.start` 之前的音频帧会被 SDK 闸门丢弃。
> 若产品要求“开口不丢字”，可在 `sessionReady` 后再启动采集（代价是首轮延迟）。

`connectionEpochAdvanced` 事件处理：

```swift
if sessionActive, sdk.state.connectionState == .connected {
    sdk.startSession(resumeSessionID: serverSessionID)
}
```

- 首次连接：`serverSessionID == nil`，服务端新建会话。
- 重连：回传服务端下发的 `sess_<...>` id；服务端从聊天记录恢复上下文。
- 本地会话 id 是 UUID，不能作为协议 `session_id` 回传。
- `sessionReady`：保存服务端 sessionID；`restored == false` 时清空本地消息缓存。
- 服务端每次 `session.start` 都会把模式重置为 chat：App 本地模式状态也应复位；若重连前用户处于
  translate/transcribe/omni，按用户意图重新发送 `session.change`，以服务端 `session.changed` 为准。
- `.sessionEnded`：**必须清空 `serverSessionID`**，避免下次重连 resume 一个已结束的会话；同时停止
  本地转写/任务展示。
- `.connectionTerminated`：先于 `.connectionStateChanged(.disconnected)` 到达；state 快照不携带原因，
  App 必须消费该事件：`authFailed` → 停止自动重连并提示重新配置凭证；`reconnectExhausted` → 提示
  网络失败，等待用户/前台自愈触发 `connect()`；`serverClosed` → 结束当前会话并清空
  `serverSessionID`。用户主动 `disconnect()` 不触发该事件。

### 7.2 音频采集（App）

- `AVAudioEngine.inputNode.installTap` + `AVAudioConverter` → 16k mono Int16。
- `AVAudioSession`：`.playAndRecord` + `.voiceChat`，options 至少 `.defaultToSpeaker`、`.allowBluetooth`、`.allowBluetoothA2DP`。
- 开启 `setVoiceProcessingEnabled(true)`（iOS 13+）获取系统 AEC，避免 TTS 外放被本地 VAD 判成用户说话。
- 静音补发（移植 Android `SilenceFlushGate`）：
  - mute 沿后补发 50 帧（1s）全零 PCM，`pushAudioPCM(zeroFrame, muted: true)`；
  - 窗口耗尽后丢弃帧；
  - unmute 后立即恢复真实帧；
  - App 进入后台且静音时，先补满剩余静音窗口，再释放采集与音频会话。
- 权限缺失时跳过启动并记录警告；采集/转换失败进入有限重试，耗尽后停止并上报 UI。
- 麦克风电平表由 App 旁路计算；静音/停止时恒 0。

### 7.3 音频播放（App）

- App 使用独立的 playback `AVAudioEngine` + `AVAudioPlayerNode` + JitterBuffer
  （与 capture engine 分离，详见 `docs/ios_app_audio.md`）。
- `LivaChatAudioSink` 回调在 audio recv 队列：
  - 按 `sequenceNumber` 放入 App 的 `JitterBuffer`；目标 3 帧（60ms），最大积压 15000 帧；
  - 迟到帧与溢出帧丢弃并计数；
  - 回调必须快速返回，不得在此队列做磁盘/网络/UI 操作。
- **JitterBuffer 线程安全**：audioSink（audio recv 队列）写入、event stream（Main）的
  `playbackShouldFlush` 按 epoch 过滤；必须内部加锁或串行化，禁止无同步访问。
- `playbackShouldFlush(reason, epoch)` → **丢弃 jitter buffer 中 `playbackEpoch < epoch` 的帧，
  保留 `playbackEpoch >= epoch` 的帧**（新 epoch 的音频可能先于事件到达，不能整队清空）；
  记录 `acceptedEpoch = max(acceptedEpoch, epoch)`；播放器在无可用帧时保持静默，不阻塞。
- 连接进入 `RECONNECTING`/`DISCONNECTED` 时同样按新代际过滤（丢弃 `< 新 epoch` 的帧，保留
  `>= 新 epoch` 的帧），并记录 `acceptedEpoch = max(acceptedEpoch, sdk.state.playbackEpoch)`
  （SDK 已在状态转换时递增代际，§5.3.4）。两个来源的 epoch 单调递增，用 max 更新避免旧事件回退。
- audioSink 每帧检查：`frame.playbackEpoch < acceptedEpoch` → 丢弃。这是丢弃"flush 前已解码、
  flush 后才送达"的在途过期帧的唯一可靠手段（sequenceNumber 跨连接不重置，且事件流与
  audio recv 队列无先后保证）；`playbackEpoch > acceptedEpoch` 的新帧不丢弃。
- 支持音频路由/中断（`AVAudioSession.interruptionNotification`、`routeChangeNotification`、媒体服务重置）与静音。
- 16k mono 16-bit buffer；由 `AVAudioEngine` 负责到硬件格式的转换。

### 7.4 本地 VAD 打断（App）

- 订阅 `.sessionStateChanged` 与 `.localVadStateChanged`（或读 `sdk.state`）。
- 条件：`sessionState == .speaking && localVadState == .speech`。
- 动作：`sdk.sendInterrupt(.userVoice)` + 轻触觉反馈；500ms 节流。
- 与 Android 一致：不在本地 VAD 触发时直接 flush 播放队列；依赖服务端进入 `thinking` 后的
  `.playbackShouldFlush(.interruptAck, epoch: 新播放代际)`。

### 7.5 摄像头 / 屏幕 / 图库（App）

| 来源 | 实现 | cameraFacing | turnID | text |
|------|------|--------------|--------|------|
| 摄像头 | `AVCaptureVideoDataOutput`；长边 ≤1280、q70、按 `videoOrientation` 旋正；约 1 fps；开流前 1s AE 暖机 | front / back | 开相机/切换镜头时递增 | nil |
| 屏幕 | ReplayKit in-app capture；JPEG；门控放行时每 2s 采样一帧 | screen | 开始录屏时递增 | 持续录屏 nil；点按抓取为客户端指令（设计占位；omni 下 nil） |
| 图库 | `PHPickerViewController` + ImageIO；长边 ≤1920、q70、EXIF 旋正 | gallery | 当前采集轮 | `conv_gallery_action_text`（16 locale）或 nil（omni） |

- `turnID` 由 App 进程级 `CaptureTurnCounter` 维护，从 0 开始；开相机、切前后镜头、开始录屏时递增，跨会话/重连不重置。
- 摄像头路径每轮开流前 1s 丢弃帧（AE 暖机）；帧间隔约 900ms。
- 屏幕采集范围（ReplayKit in-app vs Broadcast Upload Extension）延后到 App 阶段决策（§11.3）。
- `PHPickerViewController` 是 UIKit（iOS 15 无 SwiftUI 等价物），必须在 App 内用
  `UIViewControllerRepresentable` 封装，并按 `ios_dev_rules.md` §4 走例外审批（App 阶段决策）。
- 预览层 `AVCaptureVideoPreviewLayer` 同样需要 `UIViewRepresentable` 适配层；不得扩散到业务层。

### 7.6 重连自动恢复

```
网络断开
  ├─ ConnectionManager → RECONNECTING
  ├─ 退避重连（1s, 2s, 4s, ...）
  ├─ didOpen → CONNECTED，epoch++
  ├─ App（消费 events）
  │     └─ sdk.startSession(resumeSessionID: serverSessionID)
  ├─ Server → session.ready
  └─ 后续音频/视频继续推送
```

重连期间：

- App 可继续采集；SDK 在非 CONNECTED 或未 startSession 时丢弃帧（同段只记一条日志）。
- SDK 不自动重置会话状态、不清空未满音频帧、不重启媒体组件。
- 恢复失败（`restored=false`）：App 清理本地消息缓存与模式状态。
- 连接非 CONNECTED 时，App 清空临时 UI 状态（streaming `ai_text`、`transcription`、`job_status`），
  不依赖服务端补发（与 Android `ConversationListenerAdapter` 的 D6 行为一致）。
- App 回前台自检（与 Android `ConversationCoordinator` 一致）：`sessionActive && connectionState == .disconnected`
  → `sdk.connect()`；采集死态 → `audioCapture.start()`（两者幂等；`stop()` 后不复活）。

### 7.7 权限、后台与 ATS

- Info.plist：
  - `NSMicrophoneUsageDescription`
  - `NSCameraUsageDescription`
  - 若使用 `PHAsset`（非 PHPicker）：`NSPhotoLibraryUsageDescription`
  - 后台会话：`UIBackgroundModes` 包含 `audio`
- 权限申请、`AVAudioSession` 配置与恢复、前后台切换、音频焦点/路由均由 App 负责。
- ATS：
  - `wss://` 默认可用；
  - 自签名服务器（含生产）：配置 `tlsPolicy: .allowSelfSigned`，或在设备上安装并信任 CA；
  - `ws://` 需要 App 的 ATS 例外，不推荐；
  - `.allowSelfSigned` 只关闭证书信任校验，不降低 ATS 的 TLS 版本/密码套件要求；如服务器不满足，
    需要 App 的 ATS 例外（仅受控环境）。
- 局域网：连接 LAN IP 时，Info.plist 需要 `NSLocalNetworkUsageDescription`；iOS 14+ 首次连接会弹
  本地网络权限，未授权时连接失败。
- 后台（App 已启用 `UIBackgroundModes = [audio]`）：
  - iOS 没有 Android 的麦克风前台服务等价物；`audio` 后台模式 + 活跃的 `AVAudioSession` 是持续采集/播放的前提。
  - 未静音时保持 full 采集；静音且 TTS 活动时只保留独立 playback engine；空闲后 teardown，App 可挂起。
  - SDK 不保证进程被挂起后还能收到新 TTS 或保持网络；长期后台仍受系统资源管理影响，App 需处理中断与媒体服务重置。

### 7.8 断开流程

```
interruptCoordinator.stop()
audioCapture.stop()
audioPlayback.stop()
camera/screen unbind()
sdk.disconnect()
本地会话结束
```

- 与 Android 当前实现一致：不调用 `sdk.endSession(...)`；服务端通过断连/超时清理。
- `sdk.disconnect()` 停心跳、停重连、以 1000 关闭 WebSocket、置 `DISCONNECTED`；
  不发送 `session.end`、不重置 `SessionManager`、不释放 native 处理器。

### 7.9 SwiftUI 集成建议

- App 的 `ConversationViewModel` 作为 `@MainActor` 的 `ObservableObject`，用一个 `Task` 消费 `sdk.events`：

```swift
@MainActor
final class ConversationViewModel: ObservableObject {
    @Published private(set) var connection: ConnectionState = .disconnected
    @Published private(set) var session: SessionState = .idle

    private var eventTask: Task<Void, Never>?

    func start() {
        eventTask = Task { [weak self] in
            guard let self else { return }
            for await event in sdk.events {
                self.handle(event)
            }
        }
    }
}
```

- `LivaChatAudioSink` 由独立的、`@unchecked Sendable` 的播放控制器实现（不是 ViewModel），
  避免把音频实时回调串到 Main。

---

## 8. 典型流程

### 8.1 建连与启动会话

```
App                                   SDK                              Server
 │ sdk.connect()                        │                                 │
 │                                     ├ CONNECTING ────────────────────► │
 │                                     │ ◄────────── didOpen ──────────── │
 │                                     ├ CONNECTED, epoch++              │
 │ event: connectionEpochAdvanced      │                                 │
 ├ sdk.startSession(resumeID) ────────►│                                 │
 │                                     ├ session.start ─────────────────►│
 │                                     │ ◄──────────── session.ready ─────│
 │ ◄── event: sessionReady(id, restored)                                 │
 │                                     │ ◄──────────── status(listening) ─│
 │ ◄── event: sessionStateChanged(.listening)                            │
```

### 8.2 一轮对话

```
App                                   SDK                              Server
 │ capture 每 20ms 读 PCM               │                                 │
 ├ sdk.pushAudioPCM(frame) ───────────►│                                 │
 │                                     ├ 拼帧 → VAD → AGC → Opus         │
 │                                     ├ audio (vad_state) ─────────────►│
 │                                     │ ◄────────── transcription ──────│
 │ ◄── event: transcription             │                                 │
 │                                     │ ◄────────── status(thinking) ───│
 │ ◄── event: playbackShouldFlush(.interruptAck, epoch:)                 │
 │ ◄── event: sessionStateChanged(.thinking)                             │
 │                                     │ ◄────────── ai_text ────────────│
 │ ◄── event: aiText                    │                                 │
 │                                     │ ◄────────── status(speaking) ───│
 │ ◄── event: sessionStateChanged(.speaking)                             │
 │                                     │ ◄────────── ai_audio ───────────│
 │                                     ├ Opus 解码                       │
 │ ◄── audioSink.didReceiveAudio ──────│  [audio recv queue]             │
 │ App: JitterBuffer → AVAudioPlayerNode                                 │
 │                                     │ ◄────────── status(listening) ──│
```

### 8.3 本地 VAD 打断

```
用户说话
  │
  ├─ App 持续 pushAudioPCM
  │
  ├─ SDK 每帧 VAD → localVadState = .speech（事件）
  │
  ├─ App InterruptCoordinator:
  │     sessionState == .speaking && vad == .speech
  │     └─ 500ms 节流后 sdk.sendInterrupt(.userVoice)
  │
  ├─ SDK: interrupt ─────────────────────────────► Server
  │
  ├─ Server: 停止 TTS / 取消 LLM
  │
  ├─ Server ── status(thinking)（新回合开始）──────► SDK
  │                                               │
  ├─ SDK ── event: playbackShouldFlush(.interruptAck, epoch:)
  │                                               │
  └─ App: JitterBuffer 丢弃 playbackEpoch < 新 epoch 的帧（保留新 epoch 帧）
```

### 8.4 重连

```
网络断开
  │
  ├─ ConnectionManager → RECONNECTING
  ├─ 退避重连（1s, 2s, 4s, ...）
  │
  ├─ didOpen → CONNECTED，epoch++
  │
  ├─ SDK 重置 session gate（本连接尚未 startSession）
  │
  ├─ App（消费 epoch 事件）
  │     └─ sdk.startSession(serverSessionID)
  │
  ├─ Server → session.ready
  │
  └─ 后续音频/视频继续推送
```

### 8.5 静音补发

```
mute=true
  │
  ├─ 后续 50 帧：pushAudioPCM(zeroFrame, muted: true)
  │     └─ 线上 vad_state="muted"（服务端替换为全零 PCM）
  │
  ├─ 50 帧后：App 继续采集但不 push（DROP）
  │
  └─ unmute：立即恢复 push 真实 PCM
```

App 进入后台且静音时，在释放采集前补齐剩余静音帧，保证服务端能按连续静音结束当前 ASR 句。

### 8.6 断开

```
sdk.disconnect()
  ├─ 停止心跳定时器
  ├─ 取消重连任务
  ├─ WebSocket cancel(with: .normalClosure)
  ├─ connectionState = DISCONNECTED
  └─ 不发送 session.end，不重置 SessionManager，不释放 native 处理器
```

App 侧按 §7.8 释放 `AVAudioEngine`、音频会话、摄像头/录屏与后台任务。

---

## 9. 项目结构、构建与 CI

### 9.1 目录（目标状态）

```
apps/ios/
├── LivaChatSDK/                         # SDK 本地 SPM package
│   ├── Package.swift
│   ├── Sources/COpus/
│   ├── Sources/CVad/
│   ├── Sources/CWebSocketClose/         # ObjC bridge（唯一 ObjC target）
│   ├── Sources/LivaChatSDK/
│   └── Tests/LivaChatSDKTests/
└── LivaChatApp/                         # 后续：SwiftUI App

packages/sdk-swift/                      # 协议 DTO（对应 packages/sdk-kotlin）
├── Package.swift                        # name: LivaChatProtocol
├── Sources/LivaChatProtocol/
└── Tests/LivaChatProtocolTests/

3rdparty/
├── dist-ios/
│   ├── Opus.xcframework                 # 已存在
│   └── WebRtcVadLite.xcframework        # 已就绪（minos 15.0；仅二进制入库）
└── opus/build_opus_ios.sh               # 已存在
```

### 9.2 构建配置

| 项 | 值 |
|----|----|
| Swift tools | 6.0 |
| Deployment target | iOS 15.0（`Package.swift` `.iOS(.v15)`；App target 15.0） |
| macOS host | `.macOS(.v13)`（仅主机侧测试） |
| Native 依赖 | Opus.xcframework、WebRtcVadLite.xcframework（均已就绪；VAD minos 15.0） |
| 第三方依赖 | 无（仅系统框架） |
| ObjC/UIKit | 无 UIKit/CocoaPods；唯一 ObjC target 为 `CWebSocketClose`（发送 4000 关闭码），属规则例外；App 的 PHPicker/预览层适配需单独审批 |

### 9.3 Native 产物

**Opus（已就绪）**

```bash
3rdparty/opus/build_opus_ios.sh
# 输出：3rdparty/build-ios/opus-install/Opus.xcframework
# 仓库中的副本：3rdparty/dist-ios/Opus.xcframework
```

**WebRTC VAD（已就绪）**

产物：`3rdparty/dist-ios/WebRtcVadLite.xcframework`

> 注意：该 XCFramework 当前在 git 中尚未跟踪；提交 SDK 时需一并入库（CI 与 App 构建依赖）。

- 真机 `arm64`；模拟器 `arm64;x86_64`
- 头文件与 Android 同名头文件逐字节一致
- `otool -l` 两个切片均为 `minos 15.0`（`sdk 27.0`；部署目标符合 iOS 15 要求）
- SwiftPM C shim 链接到 iOS Simulator 成功；无 minos 告警
- 运行时冒烟（iOS 17.0 模拟器；`Wvad_SourceRevision()` 非空）：静音帧 → 0，220Hz 正弦 → 1

交付方式（决策 2026-10-19）：

- **仅入库预编译 XCFramework**；WebRTC 源码、revision、构建脚本与许可证目录**不入库**。
- `Wvad_SourceRevision()` 非空；具体 revision/来源信息由维护者在仓库外记录。
- 升级/重建由维护者使用外部构建链完成；替换产物后必须重跑 §10.2 的 native 测试与 §2.2.3 的
  `otool`/运行时冒烟。
- Swift 侧仍通过 `CVad` target 调用；`VadDetector` 的单测在模拟器上跑。

> 备选方案（不推荐）：纯 Swift 能量 VAD。它与服务端 `TurnDetector` 的语义不一致，
> 会让回合结束阈值漂移；不采用。

### 9.4 Makefile / 脚本

当前 `Makefile` 已有：

```make
generate-swift-api:
	python3 scripts/generate_swift_api.py --schema packages/api-schema --out $(SWIFT_API_OUT)

check-swift-api-generated:
	python3 scripts/generate_swift_api.py --schema packages/api-schema --out $(SWIFT_API_OUT) --check

swift-protocol-test:
	cd packages/sdk-swift && swift test
```

已实现：

```make
.PHONY: swift-protocol-test swift-sdk-test swift-sdk-ios-test build-ios

# 协议 DTO 主机测试
swift-protocol-test:
	cd packages/sdk-swift && swift test

# SDK 主机侧（纯逻辑；native 用 fake）
swift-sdk-test:
	cd apps/ios/LivaChatSDK && swift test

# SDK 模拟器测试（native Opus/VAD/JPEG + E2E）
# apps/ios/LivaChatSDK 是单 product 包，测试 scheme 为 LivaChatSDK；
# packages/sdk-swift 的测试 scheme 为 LivaChatProtocol。
swift-sdk-ios-test:
	cd apps/ios/LivaChatSDK && xcodebuild test \
	  -scheme LivaChatSDK \
	  -destination 'platform=iOS Simulator,name=iPhone 16 Pro'
```

`scripts/build-ios.sh` 支持 `sdk` / `app` / `all`：

1. `sdk`（默认）：校验 `Opus.xcframework` / `WebRtcVadLite.xcframework` 存在
2. `make check-swift-api-generated`
3. `make swift-protocol-test`
4. `make swift-sdk-test`
5. `make swift-sdk-ios-test`（`RUN_IOS_TESTS=0` 可跳过）
6. `app`：签名构建 `apps/ios/LivaChatApp`（需 GUI Terminal/Xcode；详见 `docs/ios_app_m0.md`）

### 9.5 CI

`.github/workflows/ci-mobile.yml` 在 `docs/mono-repo.md` 中已规划，建议加入：

- `make check-swift-api-generated`（防手改 DTO）
- `make swift-protocol-test`（现有 37 项）
- macOS runner：`make swift-sdk-test`
- macOS + iOS Simulator：`make swift-sdk-ios-test`
- 检查 `Package.swift` 的 `.iOS(.v15)` 与 iOS 工程 `IPHONEOS_DEPLOYMENT_TARGET = 15.0`
- 禁止 CocoaPods（检查 `Podfile` 不存在）
- SDK target 不 import UIKit；Objective-C 仅允许 `Sources/CWebSocketClose/`（可用 `grep` 检查其余目录）

### 9.6 apps/ios（M0 骨架已创建；产品功能后续）

- M0 已用 Xcode 创建 SwiftUI App（iOS 15），通过本地 SPM 依赖 `apps/ios/LivaChatSDK`（见 `docs/ios_app_m0.md`）。
- App 不直接链接 `Opus.xcframework` / `WebRtcVadLite.xcframework`；只依赖 `LivaChatSDK` product。
- App 的媒体层（采集/播放/相机/录屏）不放进 SDK。

---

## 10. 测试

### 10.1 主机侧（macOS，`swift test`）

| 范围 | 用例 |
|------|------|
| 调度器 | `ReconnectSchedulerTests`（次数、指数、封顶、抖动、STOP）；`HeartbeatSchedulerTests`（探测、超时、入站重置） |
| 拼帧 / AGC | `FrameAssemblerTests`（任意 chunk、跨 push、回调前复位）；`UplinkAGCTests`（gate/ceiling/attack/release/静音） |
| 会话 | `SessionManagerTests`（seq、闸门置位/复位顺序、首次 `startSession` 发送失败保持 false、已开启会话重发失败保持 true、状态映射、flush、end/interrupt 映射、job status 时钟换算、video ack 丢帧） |
| 闸门 / 多线程 | `SendGateTests`（audio/interrupt/session.change 在闸门 false 时丢弃且不消耗 seq；video 取号/编码在 videoSendQueue、发送在 sessionQueue；旧连接 reset 携带 epoch 被丢弃、不误杀新 startSession） |
| 协议 | `MessageCodecTests`（C2S golden、S2C fixture、required-nullable、未知 type/enum、Base64） |
| Facade / 配置 | `LivaChatSDKConfigTests`（16k/20ms、24k 抛错、quality 1...100、maxLongSide、keepalive 顺序、reconnect 边界、scheme、默认值） |
| 事件 | `EventStreamTests`（顺序、快照一致性、单消费者语义、connected 先于 epoch、`connectionTerminated` 先于 disconnected） |
| 播放代际 / 状态快照 | `StateStoreTests`（flush/断连递增、仅离开 CONNECTED 时递增、单锁原子快照）；`FacadeE2ETests` 断言事件携带新值、audio frame 打戳、SDK 生命周期不重置 |

主机侧 native 依赖不可用：通过协议注入 fake `OpusEncoding/OpusDecoding/VadDetecting/JPEGEncoding`。
`#if canImport(COpus)` / `#if canImport(CVad)` 保护真实实现。

### 10.2 模拟器（native）

| 范围 | 用例 | 基线 |
|------|------|------|
| Opus 往返 | 16k 正弦/语音 fixture，encode → decode，样本数与能量近似 | p99 编码 ≤5ms、解码 ≤5ms |
| VAD | 16k 静音/语音 fixture；模式 low/medium/high；非法采样率 | p99 ≤1ms |
| JPEG | 1080p CGImage，q70，缩放路径 | p99 ≤30ms |
| 内存 | 连续 600 帧编码/解码 | native heap 增量 ≤30MB |

### 10.3 传输集成（本地 WS 服务端）

用本地 Python WebSocket 服务端（或现有 Mock Server）驱动真实 `URLSessionWebSocketTask`：

1. 握手 HTTP 401 → `didCompleteWithError` + `task.response.statusCode == 401` → 终态
   （`.connectionTerminated(.authFailed)`），不重连。
2. close 4001 → `didCloseWith` 读到 4001 → 终态（`.connectionTerminated(.authFailed)`），不重连。
3. close 1000 → `DISCONNECTED`（`.connectionTerminated(.serverClosed)`），不重连；
   用户 `disconnect()` 路径不得发出该事件。
4. close 4002 / 异常断开 → `RECONNECTING` → 退避重连。
5. 心跳：入站静默 15s 发 ping；35s 超时 close(4000) 并重连（可用注入 clock 缩短）；
   服务端侧断言实际收到关闭码 4000（非 1001/1000）。
6. 发送顺序：200 条连续 send，服务端收序一致。
7. 旧 generation 回调（disconnect → connect 背靠背）不得改变新连接状态；旧 URLSession 必须
   `finishTasksAndInvalidate()`，不得继续回调。
8. 4001/1000 关闭时 receive 可能先报 socket 错误：不得重复处理/重复重连。
9. 重连成功后：`resetSessionGate(connectionEpoch:)` 先于 epoch 事件被 App 消费；App 调用 `startSession` 后
   `session.start` 是首个业务 C2S（ping/pong 除外）。
10. `startSession` 在 transport 未连接时发送失败：闸门保持 false，后续 audio/interrupt 不上线。
11. 黑洞握手（accept TCP、永不完成 upgrade）：建连 watchdog 超时 → close(4000) → 退避重连
    （可注入短 `connectTimeout` 缩短用例时长）。
12. 二进制帧或未知消息不中断 receive：服务端在 `session.ready` 前插入二进制帧，其后的
    `session.ready`/`status` 仍被处理（接收循环继续）。
13. 重连耗尽（4002 + `maxAttempts=0`）：状态序列不含 `RECONNECTING`，直接终止并进入 `DISCONNECTED`。
14. 未调用 `disconnect()` 直接释放 manager：weak delegate 不形成持有环，manager 与 session 可确定性释放。
> 本机 macOS host 测试已用进程内 `TestWebSocketServer`（Network.framework 最小 RFC 6455 服务端）
> 覆盖 1–5、11–14；iOS 模拟器/真机需回归同一组用例（当前测试文件以 `#if os(macOS)` 编译）。
> 第 9/10 条还覆盖 SDK 侧闸门与 `startSession` 发送失败；App 侧 JitterBuffer 的 epoch 丢弃规则见 §10.4。

### 10.4 E2E（Mock Server）

使用 `tools/local-dev/mock-services/livachat_mock_server`（`make mock-server`）：

- `ConversationE2E`：session.ready → status listening → 推送音频 → transcription → thinking/speaking/listening；flush 事件。
- `InterruptE2E`：interrupt 到达服务端；localVadState 随 PCM 变化。
- `ErrorHandlingE2E`：可恢复错误；fatal 错误触发 `.serverAbort` flush。
- `ReconnectE2E`：断连 → 退避重连 → epoch → App 重新 startSession(resume id) → ready。
- `JobStatusE2E`：全量替换、空数组清空、startedAtMs 时钟换算。
- `VideoE2E`：JPEG 帧 → ai_video_ack；frameID 配对；丢帧日志。
- App 侧 `playbackEpoch` 丢弃规则：flush 后旧 epoch 的 PCM 丢弃、新 epoch 帧允许；在 App 测试中覆盖
  （SDK 侧断言 `playbackShouldFlush` 事件与 `AudioPCMFrame.playbackEpoch` 的打戳值）。

E2E 不需要真实麦克风/摄像头/播放器：音频用合成 PCM，视频用 fixture JPEG。

### 10.5 性能基线

与 Android 对齐（v3 §9.4）：

| 指标 | 断言值 |
|------|--------|
| Opus 编码 p99（16k） | ≤5ms |
| Opus 解码 p99（16k） | ≤5ms |
| VAD 检测 p99（16k） | ≤1ms |
| JPEG 1080p q70 p99 | ≤30ms |
| Native heap 增量 | ≤30MB |

性能测试只在真机或有代表性的模拟器上作为参考；CI 以功能测试为主。

### 10.6 验收标准

- `make check-swift-api-generated` 通过（DTO 无漂移）。
- `make swift-protocol-test` 通过（37+）。
- `make swift-sdk-test`（macOS）通过。
- `xcodebuild test -scheme LivaChatSDK`（iPhone 16 Pro 模拟器）通过，包括 native 往返与 E2E。
- Mock Server E2E 通过。
- SDK 代码无 UIKit/CocoaPods；唯一 ObjC target 为 `CWebSocketClose`（规则例外，仅发送 4000 关闭码）；
  所有 target deployment target = 15.0。
- iOS 15 兼容性：以 `minos 15.0` 编译检查 + iOS 17 模拟器运行时验证；发布前补 iOS 15 真机冒烟
  （本机 iOS 15 runtime 为 unavailable）。
- 逐字段对照 Kotlin 生成物：消息 type、wire name、必填性、required-nullable、枚举值一致。

---

## 11. 当前边界、风险与决策

### 11.1 阻塞项

| 风险 | 影响 | 处理 |
|------|------|------|
| `apps/ios` 为空 | 无法端到端验证真机音频/相机 | SDK 已以进程内 WS 服务端 + 合成 PCM/JPEG 验证；App 阶段补齐真机媒体 |

### 11.2 平台/实现边界

| 边界 | 说明 | 处理 |
|------|------|------|
| `URLSessionWebSocketTask` 自定义关闭码 | 入站 4001 本机 macOS 实测可读（iOS 需回归）；出站 4000 无法用 Swift `CloseCode` 枚举构造 | 4001 按 generation 去重；4000 经 `CWebSocketClose` ObjC bridge 原样发送，进程内测试服务端已观测到 `code=4000` |
| ObjC bridge 例外 | `ios_dev_rules.md` §2/§4 要求 Objective-C 使用事先批准 | 限制为 `Sources/CWebSocketClose/` 单个函数；待评审确认。备选（自研 WS / Starscream）不优先 |
| 握手 401 可见性 | 依赖 `task.response` 在 `didCompleteWithError` 中可用（本机实测） | 回归测试；不可见时按可重连处理，受 5 次上限约束 |
| TLS / ATS | 自签名服务器需要 `.allowSelfSigned` 或安装 CA；`ws://` 需要 ATS 例外 | `.allowSelfSigned` 已批准入库；默认 `.system`；**生产自签名可显式开启**（trust-all）；需 MITM 防护时使用 CA 或后续 pinning |
| 后台执行 | iOS 无 Android mic FGS；依赖 `audio` 后台模式与活跃音频会话 | App 层处理；SDK 不承诺后台永久存活 |
| 24kHz | WebRTC VAD 不支持；public enum 保留 24k 但 init 抛错 | 与 Android 一致（v3 §10.1）；App 使用 16k |
| 服务端输出采样率 | 普通 TTS 输出 16k；lecture 模式 24k（客户端 16k 解码器内部重采样）；若服务端改默认值，客户端解码/时长需同步验证 | 保持 16k 默认；E2E 覆盖 lecture 24k |
| 非 20ms 帧 | native Opus/VAD 固定 20ms；服务端 `TurnDetector` 按 20ms 计阈值 | init 拒绝非 20ms（与 Android 的静默截断不同，属有意修正） |
| Swift 6 严格并发 | 队列封闭组件需 `@unchecked Sendable` | 类注释声明队列；核心模块启用 `-strict-concurrency=complete` 审查 |
| 事件流单消费者 | 多处消费会竞争 | 文档明确；App 自行 fan-out |
| `AsyncStream` 无界缓冲 | App 不消费会累积 | 文档明确；E2E 检查消费 |
| SPM binary path | 依赖 monorepo 布局 | CI 检出完整仓库；路径为 `../../3rdparty/...` |
| `CGImage` 便捷路径 | 同步编码，可能阻塞调用线程 | 标注 P1；相机/屏幕优先 JPEG 直通 |
| `PHPicker`/预览层 | UIKit 组件，需审批 | App 阶段按 `ios_dev_rules.md` §4 走例外 |
| `bandwidthHint` 缺失 | 与 Android 公开面不同 | 有意省略（Android 未接线）；未来需要时再加 |
| VAD 仅二进制交付 | 仓库内无源码/构建脚本，升级无法在 CI 重建 | 维护者用外部构建链重建；替换后重跑 native 测试与 `otool` 校验（D16） |

### 11.3 决策记录（2026-10-19）

**已决策（SDK 首批实现按此执行）**

| # | 决策 | 结论 |
|---|------|------|
| 1 | TLS `allowSelfSigned` | **允许进入仓库**：保留 `.allowSelfSigned`（trust-all）；开发/内网/自建及生产自签名部署均可显式开启；证书 pinning 暂不实现 |
| 2 | 包结构 | 单一 Swift package（`LivaChatProtocol` + `LivaChatSDK` 两个 product/target） |
| 3 | VAD 交付方式 | **仅入库 `WebRtcVadLite.xcframework`**；WebRTC 源码、revision、构建脚本与许可证目录均不入库 |
| 4 | iOS 15 验收 | 接受 `minos 15.0` 编译检查 + iOS 17 模拟器运行时验证；发布前补 iOS 15 真机冒烟 |
| 5 | deviceID / token 持久化 | Keychain（App 层）；卸载重装保留。若产品要求“卸载即新设备”再调整 |
| 6 | 音频质量 | 首期 16kHz only；24kHz/非 20ms 在 init 拒绝 |
| 7 | 视频便捷路径 | 首期只做 JPEG 直通；`pushVideoImage(CGImage)` 延后（P1） |

**App 阶段后续决策**

| # | 事项 | 说明 |
|---|------|------|
| 1 | 后台持续会话 | **已决策（2026-10-24）**：启用 `UIBackgroundModes: audio`；full / playback-only / stopped 由 Coordinator 统一策略控制 |
| 2 | 屏幕采集范围 | ReplayKit in-app capture vs Broadcast Upload Extension |
| 3 | `session.ended` 后行为 | App 是否立即 `disconnect()`；SDK 侧保持现状（不自动断开） |
| 4 | UIKit 例外 | PHPicker / 预览层适配按 `ios_dev_rules.md` §4 审批 |
| 5 | App 播放实现 | **已决策**：独立 playback `AVAudioEngine` + `AVAudioPlayerNode` + JitterBuffer |

---

## 12. 设计权衡

| 决策 | 选择 | 备选 | 理由 |
|------|------|------|------|
| WebSocket 实现 | `URLSessionWebSocketTask` | Starscream / Network.framework 手写 | 无第三方依赖、iOS 15 原生、错误码/401/顺序已实测满足契约 |
| 关闭码 4000 | 约 10 行 ObjC bridge（`cancelWithCloseCode:reason:`） | 仅用 Swift `CloseCode`（无法构造 4000）/ 自研 WS 栈 | 协议要求 4000；Foundation 公开 ObjC API 可原样发送；影响面限制在单一 target |
| 并发 | GCD 串行队列 | Swift actors | 50Hz 音频需要低开销、确定性优先级；队列封闭更贴近 Android 移植 |
| 公开事件 | `AsyncStream` + 音频 sink | Delegate 协议 / Combine | Swift 6 `Sendable` 友好；音频实时性不被异步流阻塞；SwiftUI 消费简单 |
| 状态 | 单一 `LivaChatState` 快照 | 多个 `@Published` / Combine Subject | 避免跨属性不一致；App 可自行映射到 ObservableObject |
| VAD | 预编译 WebRTC VAD XCFramework（仅二进制入库） | 纯 Swift 能量 VAD / Apple Speech | 与服务端 `TurnDetector` 语义一致；Speech 不提供逐帧 VAD；源码/构建脚本不入库（D16） |
| Opus | 复用现有 XCFramework + C shim | Swift 重写 / 其他编解码 | 与 Android 参数完全一致；已验证可链接 |
| 视频 | JPEG 直通为主 | SDK 内统一编码 | App 的相机/屏幕/图库上游已有 JPEG；避免二次压缩 |
| 会话恢复 | App 触发 | SDK 自动重发 | 让 App 掌握本地会话/历史/模式恢复策略 |
| 上行闸门 | SDK 强制首包 session.start | 依赖 App 时序 | 避免重连窗口内音频抢跑；服务端要求首包 |
| TLS | 系统信任默认；`.allowSelfSigned` 已批准入库（显式 opt-in） | 复刻 Android trust-all | 安全默认；生产自签名可显式开启；pinning 暂不实现 |
| 24k / 40ms | init 拒绝 | 静默截断（Android 现状） | 避免服务端回合阈值漂移；在边界尽早失败 |
| JPEG native | 否 | libjpeg-turbo | ImageIO 足够；减少 native 依赖 |

---

## 13. 实施步骤

### 阶段 0：Native 准备

1. `WebRtcVadLite.xcframework` 已就绪：`minos 15.0`（两个切片）、头文件与 Android 一致、
   C shim 链接成功、iOS 17.0 模拟器运行时冒烟通过。**仅入库 XCFramework；源码/构建脚本不入库。**
2. 确认 `Opus.xcframework` 在 Xcode 16.4 下可被 SPM 引用（已在本机验证 C shim 链接；`minos 13.0`）。

### 阶段 1：SDK 骨架

3. 创建 `apps/ios/LivaChatSDK` 本地 package（`LivaChatSDK` / `COpus` / `CVad` target）与
   `Package.swift`；`packages/sdk-swift` 只保留协议 DTO。
4. 实现 `SdkLogger`、`Queues`、`StateStore`、公开模型/事件/配置。
5. 主机侧 `swift test` 能编译（native 用 fake）。

### 阶段 2：纯逻辑组件

6. `ReconnectScheduler`、`HeartbeatScheduler`、`FrameAssembler`、`UplinkAGC`、`ClockOffset`、`SeqGenerator`。
7. 单测覆盖边界（抖动、超时、跨帧、静音、偏移）。

### 阶段 3：传输层

8. `ConnectionManager`：URLSessionWebSocketTask、generation、状态机、心跳、重连、TLS、401/4001 终态。
9. 本地 WS 服务端集成测试（§10.3 全部用例）。

### 阶段 4：会话与协议

10. `MessageCodec` 封装（复用 `ProtocolJSON`）。
11. `SessionManager`：发送闸门、seq、会话闸门、状态机、flush、job status、video ack。
12. fixture 驱动的主机侧测试（编码/解码/映射）。

### 阶段 5：音频/视频管线

13. `OpusEncoder/Decoder`（C shim）、`VadDetector`（CVad）、`JPEGEncoder`（P1）。
14. `AudioSendPipeline` / `AudioRecvPipeline` / `VideoSendPipeline`。
15. 模拟器 native 往返与性能测试。

### 阶段 6：Facade 与端到端

16. `LivaChatSDK` Facade：事件流、audio sink、状态快照、公开方法。
17. Mock Server E2E（§10.4）。
18. 性能基线与真机回归。

### 阶段 7：工程化

19. `scripts/build-ios.sh` 已支持 `sdk` / `app` / `all`；CI（`.github/workflows/ci-mobile.yml`）待接入。
20. 逐字段对照 Kotlin 生成物；确认 `generate_kotlin_api.py --check` 与 `check-swift-api-generated` 均通过。

### 阶段 8：App（M0 骨架已完成；产品功能后续，单独设计）

21. M0 已创建 `apps/ios/LivaChatApp` SwiftUI 工程并接入 `apps/ios/LivaChatSDK` 本地 SPM；后续实现产品功能。
22. 实现采集/播放/打断/相机/录屏/图库；按 `ios_dev_rules.md` 审批 UIKit 适配层。
23. 真机端到端：语音对话、打断、重连恢复、视频帧、后台音频。

---

## 附录 A：与 Android v3 的公开面差异

| 能力 | Android v3 | iOS 本设计 |
|------|------------|------------|
| 状态观察 | `StateFlow`（connection/session/vad/bandwidth/epoch） | `AsyncStream` + `LivaChatState` 快照 |
| 回调 | `LivaChatListener`（普通回调在 WS reader；状态在 Main） | 事件流（App 在 Main 消费）+ 音频 sink（audio recv 队列） |
| `connectionEpoch` | `StateFlow<Long>` | 事件 `.connectionEpochAdvanced(UInt64)` + 快照 |
| `bandwidthHint` | 公开但恒 GOOD | 不提供 |
| AI 图片 | `AiImage.data: String`（Base64） | `AiImage.data: Data`（JPEG） |
| 打断原因 | `USER_VOICE/USER_TAP/APP_CANCEL` | `userVoice/userTap/appCancel`（映射到 wire） |
| 会话变更 | `String` + 运行期校验 | 强类型 `SessionMode` / `SessionUsecase` |
| 视频来源 | `String` + 未知兜底 front | 强类型 `CameraFacing` |
| 首包约束 | App 时序保证 | SDK 闸门强制 |
| 音频参数 | 24k 在构造期抛错；40ms 静默截断 | 24k、非 20ms 均在 init 抛错 |
| TLS | trust-all + cleartext | 系统信任默认；生产自签名可显式开启（trust-all） |
| Native | JNI（`liblivachat_native.so`） | C shim + XCFramework |
| 日志 | logcat + 文件 | os.Logger + 文件 |
| 关闭 | 无公开 close | 同左；`deinit` 清理 |

## 附录 B：关键源码索引

| 主题 | 文件 |
|------|------|
| Android Facade | `apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatSDK.kt` |
| Android 传输/心跳/重连 | `.../internal/network/{ConnectionManager,HeartbeatScheduler,ReconnectScheduler}.kt` |
| Android 会话 | `.../internal/session/SessionManager.kt` |
| Android 音频管线 | `.../internal/pipeline/{AudioSendPipeline,AudioRecvPipeline,FrameAssembler,UplinkAgc}.kt` |
| Android native | `apps/android/LivaChatSDK/src/main/cpp/{opus_bridge,vad_bridge}.cpp` |
| Swift DTO | `packages/sdk-swift/Sources/LivaChatProtocol/Generated/` |
| Swift codec | `packages/sdk-swift/Sources/LivaChatProtocol/ProtocolJSON.swift` |
| Swift 生成器 | `scripts/generate_swift_api.py` |
| iOS Opus 构建 | `3rdparty/opus/build_opus_ios.sh` |
| Opus 产物 | `3rdparty/dist-ios/Opus.xcframework` |
| VAD 产物 | `3rdparty/dist-ios/WebRtcVadLite.xcframework`（minos 15.0） |
| VAD 头文件 | `3rdparty/dist-android/*/include/webrtc_vad_lite.h`（与 iOS Headers 逐字节一致） |
| 服务端鉴权 | `services/gateway/auth.py`、`services/gateway/endpoint.py` |
| 服务端 seq | `services/gateway/seq.py` |
| 服务端心跳 | `services/gateway/heartbeat.py` |
| 服务端回合检测 | `services/session/turn_detector.py`、`services/session/orchestrator.py` |
| 协议 | `docs/ws-protocol-design.md`、`packages/api-schema/` |
| iOS DTO 设计 | `docs/ios_protocol_dto.md` |
| iOS 规范 | `docs/ios_dev_rules.md` |
