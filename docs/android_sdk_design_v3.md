# LivaChat Android SDK 设计文档 (v3)

**版本**: 3.0

**状态**: 现行实现（与 `apps/android/LivaChatSDK` 代码同步）

**日期**: 2026-10-19

**适用范围**: `LivaChatSDK`（SDK library）+ `LivaChatApp`（宿主 App 集成）

**协议 DTO**: `packages/sdk-kotlin`（`dev.livachat.protocol.generated`）

> 本文档描述 SDK 当前的实际行为与契约。公开接口、字段名、默认值均以源码为准。

---

## 1. SDK 描述

### 1.1 定位

LivaChatSDK 是 LivaChat Android 客户端的 **协议传输与数据处理 SDK**，负责：

- WebSocket 连接生命周期、心跳、指数退避重连
- C2S / S2C 协议消息编解码（复用 `sdk-kotlin` 自动生成的 DTO）
- 会话管理、上行消息序号、服务端状态机同步
- 上行音频的 VAD、可选 AGC、Opus 编码
- 下行音频的 Base64 解码与 Opus 解码
- 上行视频的 JPEG 压缩（Bitmap 路径）或 JPEG 直通
- 向 App 暴露状态 Flow 与 Listener 回调

SDK **不** 负责：

- 麦克风采集（`AudioRecord` / Oboe / AAudio）
- 音频播放（`AudioTrack`）、播放队列与抖动缓冲
- 摄像头采集（CameraX / Camera2）、Surface / 预览管理
- 权限申请、前台服务、音频焦点与路由
- UI 状态展示与动画
- `session.start` 的自动重发（重连后由 App 触发）
- Token 派生与持久化

### 1.2 与 App 层的边界

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              LivaChatApp                                 │
│                                                                          │
│  ┌──────────────┐   ┌──────────────────┐   ┌──────────────────────────┐  │
│  │ UI (Compose) │   │ ViewModel / Repo │   │ 权限 / 生命周期 / FGS     │  │
│  └──────┬───────┘   └────────┬─────────┘   └──────────────────────────┘  │
│         │                    │                                           │
│         │    ┌───────────────┴──────────────────┐                        │
│         │    │ App-owned Media Components       │                        │
│         │    │  AudioCaptureController          │  AudioRecord           │
│         │    │  AudioPlaybackController         │  AudioTrack + Jitter   │
│         │    │  CameraController                │  CameraX               │
│         │    │  ScreenCaptureController         │  MediaProjection       │
│         │    │  InterruptCoordinator            │  localVadState 订阅     │
│         │    └───────────────┬──────────────────┘                        │
│         │                    │ PCM in / PCM out / JPEG in                │
│         ▼                    ▼                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                           LivaChatSDK                              │  │
│  │  Public Facade                                                     │  │
│  │    pushAudioPcm / pushVideoBitmap / pushVideoJpeg                  │  │
│  │    connect / startSession / endSession / sendInterrupt             │  │
│  │    sendSessionChange                                               │  │
│  │    StateFlow: connectionState / connectionEpoch / sessionState     │  │
│  │              localVadState / bandwidthHint                         │  │
│  │  SessionManager · ConnectionManager · MessageCodec                 │  │
│  │  AudioSendPipeline · AudioRecvPipeline · VideoSendPipeline         │  │
│  │  OpusEncoder / OpusDecoder / VadDetector / JpegEncoder             │  │
│  │  JNI: opus + WebRTC VAD（liblivachat_native.so）                    │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| 职责单一 | SDK 只负责"协议 + 数据处理"；媒体 I/O 与平台资源由 App 持有 |
| 接口对称 | 上行接收 PCM/JPEG，下行回调 PCM；App 不感知 Opus/VAD 细节 |
| 编解码内聚 | Opus、VAD、JPEG 均为 SDK 内部组件，不作为公开处理器暴露 |
| 协程友好 | 公开状态使用 `StateFlow`；回调接口保留以适配传统调用方 |
| 平台无关核心 | `SessionManager`、`ConnectionManager`、`MessageCodec` 可在 JVM 单测 |
| JNI 隔离 | App 不直接调用 `System.loadLibrary`；由 SDK 内部处理器首次初始化时加载 |
| 服务端权威 | `SessionState` 只由服务端 `status` 驱动，SDK 与 App 不自行推断 |
| 无播放缓冲 | SDK 解码即回调 PCM；播放缓冲、抖动缓冲与 flush 完全由 App 承担 |

---

## 2. 公开接口

### 2.1 核心入口

```kotlin
class LivaChatSDK(
    private val config: LivaChatConfig,
    private val listener: LivaChatListener,
) {
    // ---- 连接 / 会话生命周期 ----
    fun connect()
    fun disconnect()
    fun startSession(resumeSessionId: String? = null)
    fun endSession(reason: SessionEndReason = SessionEndReason.USER_HANGUP)

    // ---- 上行音频 ----
    /**
     * 推送 16-bit mono PCM。SDK 内部完成拼帧、VAD、可选 AGC、Opus 编码与协议封装。
     * 任意线程可调用；SDK 先做防御性拷贝，再投递到 audioSend 单线程调度器。
     *
     * @param muted true = 麦克风静音补发帧，payload 标记 vad_state="muted"。
     */
    fun pushAudioPcm(
        pcm: ShortArray,
        offset: Int = 0,
        len: Int = pcm.size,
        muted: Boolean = false,
    )

    // ---- 上行视频 ----
    /** Bitmap → 等比缩放（长边 ≤ videoMaxLongSide）→ JPEG → 协议封装。 */
    fun pushVideoBitmap(
        bitmap: android.graphics.Bitmap,
        orientation: Orientation,
        quality: Int = config.videoQuality,
        cameraFacing: String = "front",
        turnId: Long = 0,
        text: String? = null,
    )

    /** 预先编码好的 JPEG 直通发送；不缩放、不重新压缩。 */
    fun pushVideoJpeg(
        jpeg: ByteArray,
        width: Int,
        height: Int,
        orientation: Orientation,
        cameraFacing: String = "front",
        turnId: Long = 0,
        text: String? = null,
    )

    // ---- 控制信令 ----
    fun sendInterrupt(reason: InterruptReason = InterruptReason.USER_VOICE)

    /** 请求切换管线模式："chat" / "translate" / "transcribe" / "omni"。 */
    fun sendSessionChange(
        mode: String,
        myLanguage: String? = null,
        localLanguage: String? = null,
        usecase: String? = null,
    )

    // ---- 状态观察 ----
    val connectionState: StateFlow<ConnectionState>
    val connectionEpoch: StateFlow<Long>
    val sessionState: StateFlow<SessionState>
    val bandwidthHint: StateFlow<BandwidthHint>
    val localVadState: StateFlow<VadState>

    // ---- 日志（logcat + 可选文件） ----
    fun logDebug(tag: String, message: String)
    fun logInfo(tag: String, message: String)
    fun logWarn(tag: String, message: String, throwable: Throwable? = null)
    fun logError(tag: String, message: String, throwable: Throwable? = null)
}
```

`connectionEpoch` 语义：

- 初值 `0`，每次 WebSocket `onOpen` 成功后单调 `+1`，包含首次连接与所有重连。
- 单调不回退，免疫 `StateFlow` 的值回归去重（`CONNECTED → RECONNECTING → CONNECTED`）。
- App 以它为可靠触发，在每次建连后调用 `startSession(...)`。

### 2.2 配置

```kotlin
data class LivaChatConfig(
    val serverUrl: String,
    val token: String,
    val deviceId: String,
    val clientVersion: String,

    // 音频
    val audioSampleRate: AudioSampleRate = AudioSampleRate.RATE_16000,
    val audioFrameMs: Int = 20,
    val vadSensitivity: VadSensitivity = VadSensitivity.MEDIUM,
    val uplinkAgc: Boolean = false,

    // 视频
    val videoQuality: Int = 70,
    val videoMaxLongSide: Int = 1080,
    val enableVideo: Boolean = true,

    // 网络
    val reconnectMaxAttempts: Int = 5,
    val reconnectBaseDelayMs: Long = 1_000L,
    val reconnectMaxDelayMs: Long = 30_000L,
    val keepaliveIntervalS: Int = 15,
    val keepaliveTimeoutS: Int = 35,

    // 诊断
    val logFilePath: String? = null,
    val timezone: String? = null,
    val language: String? = null,
    val languageProvider: (() -> String?)? = null,
)
```

字段说明：

| 字段 | 当前实现语义 |
|------|--------------|
| `serverUrl` | 不含查询参数的 WS 地址；SDK 拼接 `?token=...&device_id=...` |
| `token` | 线上传输的 wire token（明文 secret 的摘要）；SDK 只透传 |
| `clientVersion` | 写入 `session.start.config.client_version` |
| `audioSampleRate` | 决定 Opus/VAD 采样率；当前可用的公开取值为 16000（见 §10.1） |
| `audioFrameMs` | 拼帧长度；当前原生链路固定按 20ms 处理（见 §10.1） |
| `vadSensitivity` | WebRTC VAD 模式 LOW=0 / MEDIUM=1 / HIGH=2，并上报服务端 |
| `uplinkAgc` | makeup-only 上行电平补偿；默认关闭 |
| `videoMaxLongSide` | 仅约束 `pushVideoBitmap` 的缩放；`pushVideoJpeg` 直通不缩放 |
| `enableVideo` | 仅作为能力声明写入 `session.start.config`，不阻断任何上行 |
| `timezone` | 为 null 时每次 `session.start` 现取 `TimeZone.getDefault().id` |
| `language` | 静态 App UI 语言，写入 `session.start.config.language`；null 时省略 |
| `languageProvider` | 可选动态 provider，每次 `session.start` 执行并优先于 `language`；App 用它读取当前资源语言 |
| `logFilePath` | 非 null 时日志追加写入该文件，父目录自动创建 |

### 2.3 Listener 回调

```kotlin
interface LivaChatListener {
    fun onConnectionStateChanged(state: ConnectionState) {}
    fun onSessionStateChanged(state: SessionState) {}
    fun onSessionReady(sessionId: String, restored: Boolean) {}
    fun onSessionEnded(reason: String, finalSessionId: String) {}

    fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {}

    /** 设备上正在运行的后台任务全量快照（replace 语义）。 */
    fun onJobStatus(jobs: List<AgentJob>) {}

    fun onAiText(
        text: String,
        messageId: String,
        index: Long,
        isFinal: Boolean,
        image: AiImage? = null,
        source: String? = null,
    ) {}

    /** 解码后的 TTS PCM；App 必须消费，否则用户听不到声音。 */
    fun onAiAudioPcm(
        pcm: ShortArray,
        sampleRate: Int,
        sampleBits: Int,
        durationMs: Int,
        sequenceNumber: Long,
        turnId: String = "",
    ) {}

    fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {}

    fun onVideoAck(frameId: Long, processed: Boolean) {}

    fun onSessionChanged(
        mode: String,
        myLanguage: String?,
        localLanguage: String?,
        usecase: String? = null,
    ) {}

    fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {}
}
```

### 2.4 模型

```kotlin
enum class AudioSampleRate(val hz: Int) { RATE_16000(16_000), RATE_24000(24_000) }
enum class VadSensitivity(val mode: Int) { LOW(0), MEDIUM(1), HIGH(2) }
enum class VadState { SPEECH, SILENCE, UNKNOWN }
enum class InterruptReason { USER_VOICE, USER_TAP, APP_CANCEL }
enum class Orientation(val degrees: Int) {
    PORTRAIT_0(0), LANDSCAPE_90(90), PORTRAIT_180(180), LANDSCAPE_270(270),
}
enum class SessionEndReason { USER_HANGUP, APP_BACKGROUND, ERROR }
enum class SessionState { IDLE, LISTENING, THINKING, SPEAKING }
enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING }
enum class BandwidthHint { GOOD, FAIR, POOR, CRITICAL }
enum class PlaybackFlushReason { INTERRUPT_ACK, SESSION_ENDED, SERVER_ABORT }

data class AiImage(
    val data: String,
    val mime: String,
    val width: Int,
    val height: Int,
    val orientation: Int = 0,
    val cameraFacing: String = "front",
)

data class AgentJob(
    val jobId: String,
    val agentName: String,
    val summary: String,
    val startedAtMs: Long,
    val runningTimeMs: Long,
)
```

`AiImage` 仅在 `ai_text.is_final=true` 且服务端启用视频时出现，内容为 Base64 JPEG。
`AgentJob.startedAtMs` 已由 SDK 用 `session.ready` 建立的时钟偏移转换为设备本地 epoch ms；`runningTimeMs` 为服务端发送时计算的已运行时长。

### 2.5 线程约定

| 接口 | 执行线程 |
|------|----------|
| `pushAudioPcm` / `pushVideoBitmap` / `pushVideoJpeg` | 任意线程调用；音频与 JPEG 字节做防御性拷贝后投递到对应单线程调度器；Bitmap 不拷贝 |
| `onConnectionStateChanged` / `onSessionStateChanged` | Facade 的 Main observer scope |
| `onAiAudioPcm` | SDK 内部 `LivaChat-AudioRecv` 单线程调度器（非 Main） |
| 其余 Listener 回调 | OkHttp WebSocket reader 线程，同步调用 |
| `connectionState` / `connectionEpoch` / `sessionState` / `localVadState` / `bandwidthHint` | 任意线程可 collect（StateFlow） |

注意事项：

- `onAiAudioPcm` 之前的 `ai_audio.data` Base64 解码发生在 WebSocket reader 线程；PCM 解码发生在 audio recv 线程。
- `pushAudioPcm` 与 `pushVideo*` 不做背压；调度器队列无界，调用频率应由 App 控制。
- Listener 的普通回调运行在 WebSocket reader 线程，App 若更新 UI 需自行切换到主线程；写入 `StateFlow` 可直接进行。
- `onConnectionStateChanged` / `onSessionStateChanged` 通过 `StateFlow.collect` 在 Main 异步转发：快速连续的状态变化可能被合并，只保证最终值，不保证每个中间状态都收到回调。
- Main 转发回调与 WebSocket reader 线程上的其他回调之间不保证先后顺序；不要依赖跨线程回调顺序。

---

## 3. SDK 架构

### 3.1 分层

```
┌───────────────────────────────────────────────────────────────────────┐
│ Public API Layer                                                      │
│   LivaChatSDK (Facade)                                                │
│   push / control / Listener / StateFlow                               │
├───────────────────────────────────────────────────────────────────────┤
│ Core Logic Layer                                                      │
│   SessionManager       ConnectionManager                              │
│   AudioSendPipeline    AudioRecvPipeline    VideoSendPipeline         │
│   BandwidthEstimator                                                  │
├───────────────────────────────────────────────────────────────────────┤
│ Protocol Layer                                                        │
│   MessageCodec（kotlinx.serialization）                               │
│   generated DTOs（dev.livachat.protocol.generated）                    │
├───────────────────────────────────────────────────────────────────────┤
│ Transport Layer                                                       │
│   OkHttp WebSocket（WSS，token/device_id 查询参数）                    │
├───────────────────────────────────────────────────────────────────────┤
│ Native Layer                                                          │
│   OpusCodec（libopus.a）  WebRTC VAD（libwebrtc_vad_lite.a）           │
│   JPEG 走 Android Bitmap.compress（无 native 依赖）                     │
└───────────────────────────────────────────────────────────────────────┘
```

### 3.2 内部模块依赖

```
LivaChatSDK
 ├─→ ConnectionManager ─→ OkHttp
 ├─→ SessionManager ─→ MessageCodec ─→ generated DTOs
 │        ↑
 │        └─ ListenerDispatcher ─→ LivaChatListener
 ├─→ AudioSendPipeline ─→ VadDetector / OpusEncoder / UplinkAgc
 │        ├─→ SessionManager.sendAudio()
 │        └─→ LivaChatSDK.localVadState
 ├─→ AudioRecvPipeline ─→ OpusDecoder
 │        └─→ LivaChatListener.onAiAudioPcm
 ├─→ VideoSendPipeline ─→ JpegEncoder
 │        └─→ SessionManager.sendVideo()
 └─→ BandwidthEstimator
```

依赖规则：上层可依赖下层，下层不感知上层；核心逻辑不依赖 Android Audio/Camera HAL。
`android.graphics.Bitmap` 是 SDK 内部唯一直接使用的媒体平台类型。

### 3.3 线程与调度器

| 线程 / 调度器 | 用途 |
|----|----|
| OkHttp WebSocket reader | 收包、Listener 普通回调、进入 `SessionManager.handleInbound` |
| `LivaChat-AudioSend` | 帧拼装、VAD、AGC、Opus 编码、`localVadState` 更新 |
| `LivaChat-AudioRecv` | `ai_audio` 的 Opus 解码与 `onAiAudioPcm` |
| `LivaChat-VideoSend` | JPEG 压缩（Bitmap 路径）与视频 payload 组装 |
| `LivaChat-Heartbeat` | 1Hz 检查：ping 触发、入站静默超时判定 |
| `LivaChat-Reconnect` | 单线程 ScheduledExecutor，执行退避后的重连 |
| Main observer scope | 将 `connectionState` / `sessionState` 变化转发给 Listener |

所有 pipeline 都是单线程使用、非线程安全；跨线程访问由各自调度器隔离。

---

## 4. 组件详述

### 4.1 LivaChatSDK (Facade)

初始化时按以下顺序组装：

```
1. SdkLogger.init(config.logFilePath)
2. 创建 OpusEncoderImpl / OpusDecoderImpl / VadDetectorImpl / JpegEncoderImpl
   （首次创建时 System.loadLibrary("livachat_native")）
3. 创建 BandwidthEstimator、AudioRecvPipeline
4. 创建 ConnectionManager
5. 创建 ListenerDispatcherImpl（ai_audio → Base64 解码 → audioRecv 协程）
6. 创建 SessionManager（注入 sendRaw 与 configProvider）
7. 创建 AudioSendPipeline / VideoSendPipeline
8. wire:
     connectionManager.onMessage   → sessionManager.handleInbound
     connectionManager.onPingSender→ sessionManager.sendPing
     audioSendPipeline.onEncoded   → sessionManager.sendAudio
     audioSendPipeline.onVad       → localVadState
     videoSendPipeline.onEncoded   → sessionManager.sendVideo
```

Facade 的行为约定：

- `pushAudioPcm` 对输入做防御性拷贝后 `launch` 到 audio send 调度器；链路内异常只记录日志，不向 App 抛出。
- `pushVideoJpeg` 对 JPEG 字节做防御性拷贝；`pushVideoBitmap` **不拷贝 Bitmap**，App 不得在编码完成前回收或修改该 Bitmap。
- 音频失败日志按"失败段"节流：同一连续失败段只记首条，成功后复位。
- 视频帧异常逐帧记录日志（含 frameId）。
- `disconnect()` 仅断开连接；不发送 `session.end`，不重置会话状态，不释放 native 处理器。
- SDK 没有公开的 `close()`；在 App 中通常作为进程级单例长期存活。

### 4.2 ConnectionManager

```kotlin
internal class ConnectionManager(
    serverUrl: String,
    token: String,
    deviceId: String,
    reconnectConfig: ReconnectConfig,
    keepaliveIntervalS: Int = 15,
    keepaliveTimeoutS: Int = 35,
) {
    fun connect()
    fun disconnect()
    fun send(message: String): Boolean
    fun onMessage(handler: (String) -> Unit)
    fun onPingSender(sender: () -> Unit)
    val connectionState: StateFlow<ConnectionState>
    val connectionEpoch: StateFlow<Long>
}
```

**建连**

- 每次 `connectInternal()` 新建 `OkHttpClient`，连接 URL 为：

```
${serverUrl}?token=${token}&device_id=${deviceId}
```

- OkHttp 参数：`connectTimeout=10s`、`readTimeout=0`、`pingInterval=0`（心跳由 SDK 自行管理）。
- 当前实现配置了 trust-all `X509TrustManager` 与 `hostnameVerifier { _, _ -> true }`，并在 SDK Manifest 中允许 cleartext traffic。
- 每次建 socket 自增 `socketGeneration`；旧 socket 的迟到回调按代际丢弃。

**状态转换**

| 事件 | 结果 |
|------|------|
| `connect()` | `CONNECTING`，重置退避计数 |
| WebSocket `onOpen` | `CONNECTED`，随后 `connectionEpoch + 1`，启动心跳 |
| 服务端 close `1000` | `DISCONNECTED`，不重连 |
| 服务端 close 非 `1000` | 进入重连（`RECONNECTING`） |
| close `4001` / HTTP `401` | 认证终态：`DISCONNECTED`，不重连 |
| 连接失败 / 心跳超时 | 进入重连（`RECONNECTING`） |
| `disconnect()` | `DISCONNECTED`，停止心跳与重连调度 |

`onOpen` 内部顺序固定为：先置 `CONNECTED`，再自增 `connectionEpoch`，保证 observer 被唤醒时能读到 `CONNECTED`。

**心跳（入站静默判据）**

- `HeartbeatScheduler` 只记录"最后一次收到入站消息"的时间。
- 入站静默 ≥ `keepaliveIntervalS`（默认 15s）：发送一次 `ping`（每个静默窗口最多一次）。
- 入站静默 ≥ `keepaliveTimeoutS`（默认 35s）：判定连接死亡，以 close code `4000` 关闭并进入重连。
- 出站流量不影响判据；持续推流时仍会按时 ping。
- 服务端在 `session.ready` 下发的 `keepalive_interval_s` 当前未被采用。

**重连**

- 延迟：`baseDelayMs * 2^(attempt-1)`，封顶 `maxDelayMs`，叠加 ±500ms 抖动。
- 默认：1s、2s、4s、8s、16s，最多 5 次；超过上限进入 `DISCONNECTED`。
- 手动 `connect()` 与任意一次成功建连都会重置退避计数。
- `disconnect()` 会 `shutdownNow` 重连线程池；同一实例再次 `connect()` 时自动换新线程池。
- 重连成功后 SDK **不会**自动发送 `session.start`；由 App 观察 `connectionEpoch` 后调用 `startSession(...)`。

**发送**

- `send()` 在未处于 `CONNECTED` 时直接丢弃并返回 `false`，同一失败段只记录一条 `send dropped` 日志。
- 会话消息的序号分配与发送由 `SessionManager` 的发送闸口保证顺序（见 §4.3）。

### 4.3 SessionManager

```kotlin
internal class SessionManager(
    codec: MessageCodec,
    sendRaw: (String) -> Unit,
    listenerDispatcher: ListenerDispatcher,
    now: () -> Long,
    configProvider: () -> ClientSessionStartConfig,
) {
    val sessionState: StateFlow<SessionState>
    val onForceFlush: SharedFlow<PlaybackFlushReason>

    fun startSession(resumeSessionId: String?)
    fun endSession(reason: SessionEndReason)
    fun sendInterrupt(reason: InterruptReason)
    fun sendSessionChange(mode: String, myLanguage: String?, localLanguage: String?, usecase: String?)
    fun sendAudio(payload: ClientAudioPayload)
    fun sendVideo(payload: ClientVideoPayload)
    fun sendPing()
    fun handleInbound(json: String)
    fun reset()
}
```

**发送闸口**

- `SeqGenerator` 从 1 开始递增，`next()` 与 `sendRaw()` 在同一个 `sendLock` 临界区内完成。
- 目的：避免多线程（音频 50Hz、心跳、WS reader、主线程）先取号后发送造成上线顺序与序号不一致，从而被服务端序号守卫丢弃。
- 视频是唯一例外：`encodeAndSendOutsideGate` 在锁外取号与编码，锁内只送包，避免大 JPEG 的 JSON 序列化阻塞 50Hz 音频；服务端对 video 乱序容忍。
- 序号不在重连时自动归零；服务端每连接新建序号水位，并在收到 `session.start` 时重置。

**服务端状态机**

- 运行期只有 `status` 消息会更新 `sessionState`（`reset()` 未接入 Facade）；`session.ready` 不改变状态。
- 映射：`idle/listening/thinking/speaking` → `SessionState` 同名枚举。

**强制 flush**

`onPlaybackShouldFlush` 在以下事件通过 Listener 回调 App：

| 触发 | reason |
|------|--------|
| 收到 `status(session_status=thinking)` | `INTERRUPT_ACK` |
| 收到 `session.ended` | `SESSION_ENDED` |
| 收到 `error(fatal=true)` | `SERVER_ABORT` |

注意：`SPEAKING → LISTENING` 的转换当前**不**触发 flush；正常轮次中服务端进入 `THINKING` 时即会通知 App 清空播放队列。`SessionManager.onForceFlush` 也会同步发射，但 Facade 对外只使用 Listener 回调。

**消息分发**

- `handleInbound` 解析失败（非法 JSON / 未知 `type`）时静默忽略。
- `ping` 自动回 `pong`，`ping_seq` 回显服务端 ping 的 `seq`。
- 服务端 `pong` 被忽略（心跳只关心"是否有入站流量"）。
- 入站消息按到达顺序处理；`ReorderBuffer` 当前保留在代码中但不参与分发路径。

**会话生命周期**

- `startSession(resumeSessionId)`：清空内部重排缓冲，发送 `session.start`。
- `endSession(reason)`：发送 `session.end`；原因映射为 `USER_HANGUP→user_quit`、`APP_BACKGROUND→app_background`、`ERROR→error`。
- `sendInterrupt(reason)`：`USER_VOICE→user_voice`，`USER_TAP` / `APP_CANCEL→manual`。
- `sendSessionChange(mode)`：仅接受 `chat` / `translate` / `transcribe` / `omni`；未知 mode 记警告并忽略。`usecase` 仅识别 `lecture` / `dialogue`。
- `reset()` 会清空重排缓冲、序号、sessionId 与音频计数，并把状态置回 `IDLE`；Facade 当前未调用它。

**idle / reconnect 行为**

- `disconnect()` 不会重置 `SessionManager`。
- 重连后状态保持断开前的值，直到服务端下发新的 `status`。
- 会话恢复完全依赖 App 在 `connectionEpoch` 变化后重新发送 `session.start(resume_id)`。

### 4.4 AudioSendPipeline

```kotlin
internal class AudioSendPipeline(
    sampleRate: Int,
    frameMs: Int = 20,
    vad: VadDetector,
    encoder: OpusEncoder,
    onEncoded: (opus: ByteArray, durationMs: Int, vad: VadState, muted: Boolean) -> Unit,
    onVad: (VadState) -> Unit,
    uplinkAgc: Boolean = false,
    isAiSpeaking: () -> Boolean = { false },
) {
    fun pushPcm(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size, muted: Boolean = false)
    fun reset()
}
```

处理顺序（逐帧）：

```
PCM → FrameAssembler → VAD → （可选）UplinkAgc → Opus encode → SessionManager.sendAudio
```

- `FrameAssembler.frameSamples = sampleRate * frameMs / 1000`；不足一帧的数据保留在内部缓冲区，跨多次 `pushPcm` 拼帧。
- 每完成一帧：先 `vad.detect` 并回调 `onVad`（驱动 `localVadState`），再执行 AGC，最后 Opus 编码。
- `muted` 标记属于"完成该帧的那次 push"；若一帧跨越 mute 沿，取静音侧值。
- `localVadState` 的初值为 `UNKNOWN`，映射到线上 `vad_state` 时按 `SILENCE` 发送。
- `muted=true` 的帧在线上标记为 `vad_state="muted"`，服务端按 20ms 全零 PCM 替换下发。
- 输入 PCM 采样率必须与 `config.audioSampleRate` 一致；SDK 不做重采样。

**UplinkAgc（makeup-only）**

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

### 4.5 AudioRecvPipeline

```kotlin
internal class AudioRecvPipeline(
    sampleRate: Int,
    decoder: OpusDecoder,
    onPcm: (pcm: ShortArray, sampleRate: Int, durationMs: Int, sequenceNumber: Long) -> Unit,
) {
    fun handleAiAudio(opus: ByteArray, durationMs: Int, sequenceNumber: Long)
    fun reset()
}
```

- 入站 `ai_audio` 在 WebSocket reader 线程完成 Base64 解码，再投递到 `LivaChat-AudioRecv` 单线程解码。
- `sequenceNumber` 由 Facade 的 `AtomicLong` 在接收侧分配，从 1 单调递增，SDK 生命周期内不重置。
- SDK 不缓冲 PCM，解码后立即回调；播放顺序与缓冲完全由 App 负责。
- `reset()` 当前为空实现，Facade 未调用。

`ai_audio` 的 `message_id`、`index`、`is_final` 字段会参与协议解析，但当前不暴露给 Listener；公开回调只包含 PCM、采样率、位深（当前恒为 16）、时长、序号与 `turnId`（当前恒为空字符串）。

### 4.6 VideoSendPipeline

```kotlin
internal class VideoSendPipeline(
    defaultQuality: Int,
    defaultMaxLongSide: Int,
    jpegEncoder: JpegEncoder,
    onEncoded: (jpeg, frameId, width, height, orientation, cameraFacing, turnId, text) -> Unit,
) {
    fun pushBitmap(bitmap: Bitmap, frameId: Long, orientation: Orientation,
                   quality: Int = defaultQuality, cameraFacing: String = "front",
                   turnId: Long = 0, text: String? = null)
    fun pushJpeg(jpeg: ByteArray, frameId: Long, width: Int, height: Int,
                 orientation: Orientation, cameraFacing: String = "front",
                 turnId: Long = 0, text: String? = null)
}
```

- `frameId` 由 Facade 的 `AtomicLong` 分配，Bitmap 与 JPEG 两条路径共用同一计数器，从 1 递增。
- Bitmap 路径：质量限制在 1..100，按 `videoMaxLongSide` 等比缩放后 `Bitmap.compress(JPEG)`。
- JPEG 路径：字节直通，不缩放、不重新压缩；`width` / `height` 为调用方声明值。
- 不做帧率限制与发送节流，由 App 决定调用频率。
- `cameraFacing`：`"front"` / `"back"` / `"screen"` / `"gallery"`；未知值兜底为 `front`。
- `turnId` 与 `text` 原样写入 payload；`turnId=0` 表示未启用，`text` 为 null/空表示仅入服务端帧缓冲。
- 视频 payload 的 `quality` 字段固定写 `config.videoQuality`（两条路径均如此；Bitmap 路径的实际压缩质量可被单次调用覆盖，JPEG 直通路径没有 quality 参数）。
- Bitmap 路径的 payload 中 `width` / `height` 使用**原始 Bitmap 尺寸**；JPEG 直通路径使用调用方传入的 `width` / `height`。

### 4.7 内部处理器

```kotlin
internal interface OpusEncoder : Closeable {
    fun encode(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size): ByteArray
}
internal interface OpusDecoder : Closeable {
    fun decode(opus: ByteArray, offset: Int = 0, len: Int = opus.size): ShortArray
}
internal interface VadDetector : Closeable {
    fun detect(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size): VadState
}
internal interface JpegEncoder {
    fun encode(bitmap: Bitmap, quality: Int = 70, maxLongSide: Int? = null): ByteArray
}
```

**Opus（`opus_bridge.cpp`）**

| 参数 | 值 |
|------|-----|
| Application | `OPUS_APPLICATION_VOIP` |
| 采样率 | 16000 / 24000（由构造参数决定） |
| 通道 | mono |
| 帧长 | 固定 20ms（native 内部按 `sampleRate * 20 / 1000` 取样本） |
| 码率 | CBR 32 kbps |
| 复杂度 | 5 |
| 信号类型 | `OPUS_SIGNAL_VOICE` |
| 输出缓冲上限 | 256 bytes |

> Opus 处理器本身支持 16k / 24k；SDK 公开配置还受 WebRTC VAD 采样率限制，当前实际可用 16k（见 §10.1）。

**VAD（`vad_bridge.cpp`）**

- WebRTC VAD，模式 0/1/2 对应 LOW/MEDIUM/HIGH。
- Kotlin 层要求采样率属于 `{8000, 16000, 32000}`。
- native 初始化与检测均固定按 20ms 帧处理。

**JPEG（`JpegEncoderImpl`）**

- 使用 `Bitmap.compress(JPEG, quality, ...)`（Skia）。
- `maxLongSide` 非空且长边超限时 `Bitmap.createScaledBitmap`，缩略图用完立即 `recycle()`。
- 无 native JPEG 依赖。

### 4.8 Native 库加载

- 统一库名：`livachat_native`。
- CMake 链接预编译静态库：
  - `3rdparty/dist-android/${ANDROID_ABI}/lib/libopus.a`
  - `3rdparty/dist-android/${ANDROID_ABI}/lib/libwebrtc_vad_lite.a`
- JNI 在 `JNI_OnLoad` 中注册 `OpusEncoderImpl`、`OpusDecoderImpl`、`VadDetectorImpl` 的 native 方法。
- 每个处理器首次构造时调用 `System.loadLibrary("livachat_native")`；App 不直接加载。

### 4.9 BandwidthEstimator

- 每次 `SessionManager` 发送尝试记录 `json.length`（UTF-16 字符数；未区分底层是否实际写出）。
- 按 1 秒窗口计算速率，指数移动平均 `alpha=0.4`；阈值按字符数近似字节数。
- 阈值（近似字节/秒）：`>25000` GOOD、`>12500` FAIR、`>6250` POOR、其余 CRITICAL。

当前实现只记录并计算，`bandwidthHint` 这个公开 StateFlow 初始化后未被更新，始终为 `GOOD`（见 §10.2）。

### 4.10 SdkLogger

- 同时写 logcat 与可选文件；文件以追加方式打开，父目录自动创建。
- 每行格式：`yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z' <P>/LivaChat-<tag>: <message>`（UTC，微秒精度）。
- 文件写入 `synchronized` + 每行 `flush()`；写失败静默忽略，不向业务链路抛出。
- App 可通过 `logDebug/logInfo/logWarn/logError` 写入同一日志文件。

---

## 5. 协议

### 5.1 消息类型

C2S（`ClientMessage`）：

| type | 用途 |
|------|------|
| `session.start` | 新建或恢复会话，必须是建连后的第一条 C2S 消息 |
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
| `session.ready` | 更新 sessionId 与时钟偏移，回调 `onSessionReady` |
| `session.ended` | 回调 `onSessionEnded`，触发 `SESSION_ENDED` flush |
| `session.changed` | 回调 `onSessionChanged`（mode/usecase 小写化） |
| `transcription` | 回调 `onTranscription` |
| `ai_text` | 回调 `onAiText`，携带可选 `image` 与 `source` |
| `ai_audio` | Base64 解码 → Opus 解码 → `onAiAudioPcm` |
| `ai_video_ack` | 回调 `onVideoAck`，并在 SDK 内检测丢帧区间 |
| `job_status` | 全量替换快照 → `onJobStatus` |
| `status` | 更新 `sessionState`，必要时触发 flush |
| `error` | 回调 `onError`；fatal 时触发 `SERVER_ABORT` flush |
| `ping` | 自动回 `pong` |
| `pong` | 忽略（仅作为入站存活证据） |

DTO 的唯一来源是 `packages/api-schema/`，通过 `scripts/generate_kotlin_api.py` 生成到 `packages/sdk-kotlin`。修改协议时先改 schema，再执行 `make generate-android-api`。

回调值的规范化：

| 回调字段 | 取值 |
|----------|------|
| `onSessionEnded.reason` | 协议枚举名大写，如 `USER_QUIT`、`APP_BACKGROUND`、`SERVER_SHUTDOWN` |
| `onSessionChanged.mode` / `usecase` | 小写，如 `chat`、`translate`、`lecture` |
| `onAiText.source` | 小写，`llm` 或 `agent`；消息无 `source` 时为 null |
| `onAiText.image.cameraFacing` | 小写，`front` / `back` / `screen` / `gallery` |

### 5.2 MessageCodec

```kotlin
internal object MessageCodec {
    internal val json = Json {
        classDiscriminator = "type"
        ignoreUnknownKeys = true
        explicitNulls = true
    }
    fun encode(message: ClientMessage): String
    fun decode(jsonString: String): ServerMessage?
}
```

- `encodeDefaults` 保持 kotlinx 默认值 `false`：等于默认值的属性不落线。
  - 例：`ClientVideoPayload.text` 默认 `null` → 不出现该字段；`ClientSessionStartConfig.timezone` 默认 `null` → 不出现。
  - 无默认值的可空字段（如 `ClientSessionStartPayload.session_id`）在 `explicitNulls=true` 下会显式编码为 `null`。
- 编码失败抛 `CodecException`。
- 解码失败或未知类型返回 `null`，调用方静默忽略，不断开连接。

### 5.3 会话启动配置

`session.start.payload` 包含 `session_id`（恢复目标；null = 新建会话）与 `config`。

`session.start.payload.config` 内容：

```json
{
  "enable_video": true,
  "audio_sample_rate": 16000,
  "vad_sensitivity": "medium",
  "client_version": "android-1.0.0.139",
  "timezone": "Asia/Shanghai",
  "language": "zh-Hans"
}
```

- `timezone`：`config.timezone` 优先；为 null 时每次发送前读取系统时区。
- `language`：`config.languageProvider` 优先（每次发送前执行），否则用 `config.language`；两者都 null 时省略。
- `audio_sample_rate`：`AudioSampleRate.hz`。
- `vad_sensitivity`：`low` / `medium` / `high`。

`session.ready` 的关键字段：

| 字段 | SDK 处理 |
|------|----------|
| `session_id` | 保存并在 `onSessionReady` 回调；App 重连时作为 resume id |
| `session_restored` | 透传为 `onSessionReady.restored` |
| `server_time_ms` | 与本地时间一起计算 `ClockOffset`，用于 `job_status.startedAtMs` 换算 |
| `keepalive_interval_s` | 当前未使用 |
| `server_version` | 仅信息字段，SDK 不消费 |

### 5.4 序号与发送顺序

- C2S `seq` 从 1 开始，按发送闸口顺序递增。
- 除 `video` 外的所有消息在锁内完成"取号 → 构建 → 编码 → 交给传输层"。
- `video` 在锁外取号与编码，锁内只发送；因此视频序号可能小于已发送的音频序号，服务端对此类型容忍乱序。
- S2C 消息当前按到达顺序直接分发，不做重排序。

### 5.5 视频回合语义

- `frame_id`：SDK 级单调递增，用于 `ai_video_ack` 配对与丢帧检测。
- `turn_id`：App 维护的采集轮标识，从 1 起；0 = 未启用。
  - 服务端观察到变化时，把该帧视为新回合首帧，不等 ASR 立即启动 LLM/TTS。
  - SDK 无内部状态，逐帧原样透传。
- `text`：与该帧同行的用户指令。非空时服务端立即以该文本 + 本帧起一轮，不等待 ASR；null/空仅入帧缓冲。
- `camera_facing`：`front` / `back` / `screen` / `gallery`。

`ai_video_ack`：

- SDK 回调仅暴露 `frameId` 与 `processed`；`ack_frame_id` 会被解析，但不参与 SDK 的丢帧统计。
- SDK 使用每条 ack 回显的 `frame_id` 与上一次已确认值比较；间隔大于 1 时记录 `video frames lost` 警告，不向 App 暴露区间。
- 服务端收到的每个帧都会 ack（`processed=false` 也会 ack）。

### 5.6 状态与 flush

```
status(idle)      → SessionState.IDLE
status(listening) → SessionState.LISTENING
status(thinking)  → SessionState.THINKING + onPlaybackShouldFlush(INTERRUPT_ACK)
status(speaking)  → SessionState.SPEAKING
```

正常对话顺序示例：`listening → thinking → speaking → listening`。
服务端进入 `thinking` 表示上一轮 TTS 已被截断或已结束，App 应清空播放队列。

---

## 6. App 层集成现状

App 的 SDK 实例由 Hilt 作为进程级单例提供，通过 `CompositeLivaChatListener` 向多个订阅者分发回调。

### 6.1 配置装配（`AppModule`）

```kotlin
LivaChatConfig(
    serverUrl = wsUrl,                 // 由设置页地址推导
    token = wireToken(serverConfig.token),
    deviceId = deviceId,               // SharedPreferences 持久化 UUID v4
    clientVersion = "android-1.0.0.<versionCode>",
    logFilePath = filesDir/logs/livachat.log,
    uplinkAgc = true,
)
```

其余字段使用默认值：16kHz、20ms、MEDIUM VAD、JPEG q70、长边 1080、视频能力开启。

### 6.2 AudioCaptureController

- 使用 `AudioRecord`，默认源 `VOICE_COMMUNICATION`，16kHz、mono、16-bit，每 20ms 读取一帧。
- 转录模式可切换采集源（VOICE / RAW-MIC），切换通过重建 `AudioRecord` 完成。
- 权限缺失时跳过启动并记录警告；构建/启动异常进入最多 3 次重试，耗尽后停止采集与麦克风前台服务。
- 采集线程即使静音也持续 `read`，避免 unmute 后积压旧数据。
- 静音补发（`SilenceFlushGate`）：
  - mute 沿后先补发 50 帧（1s）全零 PCM，调用 `pushAudioPcm(..., muted=true)`；
  - 窗口耗尽后丢弃帧；
  - unmute 后立即恢复真实帧；
  - App 进入后台且处于静音状态时，先补满剩余静音窗口，再释放 `AudioRecord` 与麦克风前台服务；回前台重新获取。
- 麦克风电平表由 App 旁路计算；静音或停止时恒为 0。

### 6.3 AudioPlaybackController

- 使用 `AudioTrack`（`USAGE_VOICE_COMMUNICATION` / `CONTENT_TYPE_SPEECH`），16kHz mono 16-bit，流模式。
- `onAiAudioPcm` 在 SDK 音频接收线程把帧放入 App 的 `JitterBuffer`：
  - 目标缓冲 3 帧（60ms），最大积压 15000 帧；
  - 按 `sequenceNumber` 排序，迟到帧与溢出帧丢弃并计数。
- 播放循环运行在专用单线程 `AudioPlayback`（`THREAD_PRIORITY_URGENT_AUDIO`），10ms tick，`WRITE_BLOCKING` 写入。
- `onPlaybackShouldFlush` → 清空 `JitterBuffer`；播放循环在空缓冲时继续 tick，不阻塞。
- 支持输出设备选择、扬声器/听筒路由、蓝牙 SCO 联动与静音（`setVolume(0)`）。

### 6.4 InterruptCoordinator

- 订阅 `combine(sessionState, localVadState, vadInterruptEnabled)`。
- 条件：`sessionState == SPEAKING && localVadState == SPEECH`。
- 动作：调用 `sdk.sendInterrupt(InterruptReason.USER_VOICE)`，并触发轻震动。
- 500ms 节流，避免连续 VAD 帧重复发送。
- 当前实现**不**在本地 VAD 触发时直接 flush 播放队列；播放清空依赖服务端进入 `thinking` 后的 `onPlaybackShouldFlush(INTERRUPT_ACK)`。

### 6.5 摄像头 / 屏幕 / 图库

主 App（`LivaChatApp`）不使用 `pushVideoBitmap`，所有视频路径都在 App 内完成 JPEG 编码后调用 `pushVideoJpeg`（`LivaChatAppMock` 的相机路径仍使用 `pushVideoBitmap`）：

| 来源 | 编码 | cameraFacing | turnId | text |
|------|------|--------------|--------|------|
| CameraX 摄像头 | 长边 ≤1280、q70、按旋转角旋正 | `front` / `back` | `CaptureTurnCounter.current` | null |
| MediaProjection 录屏 | JPEG 缓存帧；门控放行时每 2s 采样一帧 | `screen` | `CaptureTurnCounter.current` | 持续录屏为 null；点按抓取时为本地化发图指令 `conv_image_action_text`（2026-10-27 已实施；omni 下为 null） |
| 图库照片 | 长边 ≤1920、q70、EXIF 旋正 | `gallery` | `CaptureTurnCounter.current` | 本地化发图指令 `conv_image_action_text`（2026-10-27 已实施）或 null（omni） |

`CaptureTurnCounter` 是进程级单例，从 0 开始；开相机、切换前后镜头、开始录屏时递增，跨会话与重连不重置。

摄像头路径的 App 侧约束：

- ImageAnalysis 目标 1280×960，`STRATEGY_KEEP_ONLY_LATEST`。
- 每轮开流前 1s 为 AE 暖机窗，帧丢弃。
- 帧间隔节流 900ms（约 1.1 FPS）。
- 切换镜头时递增 `turnId`，与新镜头首帧同帧生效。

### 6.6 会话与重连自动恢复

`ConversationCoordinator` 的启动顺序：

```
coordinator.start()
  ├─ 取/建本地会话（Room）
  ├─ sdk.connect()
  ├─ audioCapture.start()
  ├─ audioPlayback.start()
  ├─ interruptCoordinator.start()
  └─ 订阅 sdk.connectionEpoch
```

`connectionEpoch` 回调中：

```kotlin
if (sessionActive && sdk.connectionState.value == ConnectionState.CONNECTED) {
    sdk.startSession(listenerAdapter.serverSessionId)
}
```

- 首次连接：`serverSessionId == null`，服务端新建会话。
- 重连：回传服务端下发的 `sess_<...>` id，服务端尝试恢复（30s TTL）。
- App 本地 Room 的会话 id 是 UUID，不能作为协议 `session_id` 回传。
- `session.ready(restored=false)` 表示恢复失败或新会话，App 清空消息缓存。
- 重连期间音频采集继续运行；SDK 在非 `CONNECTED` 状态下会编码但丢弃发送帧（同一失败段只记一条日志）。

### 6.7 权限与前台服务

SDK Manifest 只声明 `INTERNET`，并设置 `usesCleartextTraffic=true`。
App Manifest 声明：

- `RECORD_AUDIO`、`CAMERA`、`VIBRATE`、`MODIFY_AUDIO_SETTINGS`
- `FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_MICROPHONE`、`FOREGROUND_SERVICE_MEDIA_PROJECTION`
- `POST_NOTIFICATIONS`、`SYSTEM_ALERT_WINDOW`
- `WRITE_EXTERNAL_STORAGE`（maxSdkVersion 28）

权限申请、前台服务启动/停止、音频路由与音频焦点均由 App 负责。

### 6.8 断开流程

`ConversationCoordinator.stop()`：

```
interruptCoordinator.stop()
audioCapture.stop()
audioPlayback.stop()
cameraController.unbind()
sdk.disconnect()
本地会话结束（Room）
```

当前 App 不调用 `sdk.endSession(...)`；SDK 侧以连接关闭结束传输，服务端会话清理由其断连/超时逻辑处理。
SDK `disconnect()` 停心跳、停重连调度、以 `1000` 关闭 WebSocket 并置 `DISCONNECTED`。

---

## 7. 典型流程

### 7.1 建连与启动会话

```
App                                   SDK                              Server
 │ sdk.connect()                        │                                 │
 │                                     ├ CONNECTING ────────────────────► │
 │                                     │ ◄────────── onOpen ───────────── │
 │                                     ├ CONNECTED, connectionEpoch++     │
 │ connectionEpoch collector           │                                 │
 ├ sdk.startSession(resumeId) ────────►│                                 │
 │                                     ├ session.start ─────────────────►│
 │                                     │ ◄──────────── session.ready ─────│
 │ ◄── onSessionReady(id, restored)    │  [WS reader]                    │
 │                                     │ ◄──────────── status(listening) ─│
 │ ◄── onSessionStateChanged(LISTENING)│  [Main, 异步；顺序不保证]         │
```

> `onSessionStateChanged` 经 Main 异步转发，与 `onSessionReady` 的先后不保证；见 §2.5。

### 7.2 一轮对话

```
App                                   SDK                              Server
 │ AudioCapture 每 20ms 读 PCM          │                                 │
 ├ sdk.pushAudioPcm(frame) ───────────►│                                 │
 │                                     ├ 拼帧 → VAD → AGC → Opus         │
 │                                     ├ audio (vad_state) ─────────────►│
 │                                     │ ◄────────── transcription ──────│
 │ ◄── onTranscription                  │  [WS reader]                    │
 │                                     │ ◄────────── status(thinking) ───│
 │ ◄── onPlaybackShouldFlush(INTERRUPT_ACK) [WS reader]                  │
 │ ◄── onSessionStateChanged(THINKING)      [Main, 异步；顺序不保证]      │
 │                                     │ ◄────────── ai_text ────────────│
 │ ◄── onAiText                        │  [WS reader]                    │
 │                                     │ ◄────────── status(speaking) ───│
 │ ◄── onSessionStateChanged(SPEAKING)  │  [Main, 异步；顺序不保证]        │
 │                                     │ ◄────────── ai_audio ───────────│
 │                                     ├ Opus 解码                       │
 │ ◄── onAiAudioPcm ───────────────────│  [AudioRecv]                    │
 │ AudioPlayback → JitterBuffer → AudioTrack                             │
 │                                     │ ◄────────── status(listening) ──│
```

> 上图为逻辑顺序。Main / WS reader / AudioRecv 三类线程之间不保证回调先后；见 §2.5。

### 7.3 本地 VAD 打断

```
用户说话
  │
  ├─ App 持续 pushAudioPcm
  │
  ├─ SDK 每帧 VAD → localVadState = SPEECH
  │
  ├─ InterruptCoordinator: sessionState=SPEAKING && vad=SPEECH
  │     └─ 500ms 节流后 sdk.sendInterrupt(USER_VOICE)
  │
  ├─ SDK: interrupt ─────────────────────────────► Server
  │
  ├─ Server: 停止 TTS / 取消 LLM
  │
  ├─ Server ── status(thinking)（新回合开始）──────► SDK
  │                                               │
  ├─ SDK ── onPlaybackShouldFlush(INTERRUPT_ACK) ─┤
  │                                               │
  └─ App: JitterBuffer.reset()（丢弃尚未播放的 PCM）
```

### 7.4 重连

```
网络断开
  │
  ├─ ConnectionManager → RECONNECTING
  ├─ 退避重连（1s, 2s, 4s, ...）
  │
  ├─ WS onOpen → CONNECTED，connectionEpoch++
  │
  ├─ App（connectionEpoch collector）
  │     └─ sdk.startSession(serverSessionId)
  │
  ├─ Server → session.ready(restored=true/false)
  │
  └─ 后续音频/视频继续推送
```

重连期间：

- 采集与播放由 App 自主决定是否继续；当前 App 保持采集继续运行。
- SDK 不自动重置会话状态、不自动清空未满音频帧、不自动重启媒体组件。
- 若恢复失败（`restored=false`），App 负责清理本地消息缓存与相关 UI 状态（当前实现清空 messageCache）。

### 7.5 静音补发

```
mute=true
  │
  ├─ 后续 50 帧：pushAudioPcm(zeroFrame, muted=true)
  │     └─ 线上 vad_state="muted"
  │
  ├─ 50 帧后：采集线程继续 read，但不再 push（DROP）
  │
  └─ unmute：立即恢复 push 真实 PCM
```

后台且静音时，App 在释放 `AudioRecord` 前补齐剩余静音帧，保证服务端能按连续静音结束当前 ASR 句。

### 7.6 断开

```
sdk.disconnect()
  ├─ 停止心跳线程
  ├─ shutdown 重连线程池
  ├─ WebSocket.close(1000)
  ├─ connectionState = DISCONNECTED
  └─ 不发送 session.end，不重置 SessionManager，不释放 native 处理器
```

App 侧在 §6.8 的 stop 序列中释放 `AudioRecord`、`AudioTrack`、Camera 与前台服务。

---

## 8. 项目结构与构建

### 8.1 目录

```
apps/android/
├── settings.gradle.kts                 # :LivaChatSDK / :LivaChatApp / :LivaChatAppMock / :sdk-kotlin
├── LivaChatSDK/
│   ├── build.gradle.kts
│   ├── CMakeLists.txt
│   └── src/
│       ├── main/
│       │   ├── AndroidManifest.xml     # 仅 INTERNET；usesCleartextTraffic=true
│       │   ├── kotlin/dev/livachat/sdk/
│       │   │   ├── LivaChatSDK.kt
│       │   │   ├── LivaChatConfig.kt
│       │   │   ├── LivaChatListener.kt
│       │   │   ├── model/              # 公开枚举与数据类
│       │   │   └── internal/
│       │   │       ├── network/        # ConnectionManager, Heartbeat/Reconnect
│       │   │       ├── pipeline/       # AudioSend/AudioRecv/VideoSend, FrameAssembler, AGC, Bandwidth
│       │   │       ├── processors/     # Opus, VAD, JPEG, NativeBridge
│       │   │       ├── protocol/       # MessageCodec, CodecException
│       │   │       ├── session/        # SessionManager, Seq, ClockOffset, ListenerDispatcher
│       │   │       └── util/           # SdkLogger, SdkDispatchers
│       │   └── cpp/
│       │       ├── livachat_native.cpp
│       │       ├── opus_bridge.cpp
│       │       └── vad_bridge.cpp
│       ├── test/                       # JVM 单测
│       └── androidTest/                # JNI / E2E / 性能测试
├── LivaChatApp/                        # 宿主 App
└── LivaChatAppMock/                    # 对照/模拟 App
```

### 8.2 构建配置

| 项 | 值 |
|----|----|
| `compileSdk` | 34 |
| `minSdk` | 26 |
| `targetSdk`（testOptions/lint） | 34 |
| Java / Kotlin target | 17 |
| NDK ABI | `arm64-v8a`、`x86_64` |
| Native build | CMake 3.22.1 |
| 协议模块 | `api(project(":sdk-kotlin"))` |

### 8.3 依赖

```kotlin
api(project(":sdk-kotlin"))
implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.1")
implementation("com.squareup.okhttp3:okhttp:4.12.0")
```

测试依赖：JUnit 4、kotlinx-coroutines-test、MockK、MockWebServer、AssertJ、AndroidX Test。

### 8.4 协议生成

- Schema：`packages/api-schema/`
- 生成器：`scripts/generate_kotlin_api.py`
- 输出：`packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated/`
- 命令：

```bash
make generate-android-api
make check-android-api-generated
```

---

## 9. 测试

### 9.1 JVM 单元测试

| 范围 | 测试类 |
|------|--------|
| 连接 / 心跳 / 重连 | `ConnectionManagerTest`、`HeartbeatSchedulerTest`、`ReconnectSchedulerTest` |
| 会话 / 序号 | `SessionManagerTest`、`SendOrderingTest`、`SeqGeneratorTest`、`ReorderBufferTest`（组件测试；当前未接入分发路径） |
| 协议 | `MessageCodecTest` |
| 音频上行 | `AudioSendPipelineTest`、`FrameAssemblerTest`、`UplinkAgcTest`、`UplinkAgcPerfTest` |
| 音频下行 | `AudioRecvPipelineTest` |
| 视频 payload | `VideoSendPipelineTest`、`VideoTurnPayloadTest` |
| 带宽 | `BandwidthEstimatorTest` |
| Facade 配置 | `LivaChatSDKIntegrationTest` |

特点：不依赖 Android 音频/相机硬件；`ConnectionManagerTest` 使用 MockWebServer。

### 9.2 Android Instrumentation 测试

| 范围 | 测试类 |
|------|--------|
| Opus 往返 | `OpusRoundtripTest` |
| VAD | `VadDetectorTest` |
| JPEG | `JpegEncoderTest`、`VideoSendPipelineTest` |
| 性能 | `OpusEncodingLatencyTest`、`VadLatencyTest`、`JpegCompressionLatencyTest`、`MemoryFootprintTest` |

### 9.3 E2E

使用进程内 MockWebServer（`MockServerHarness`）驱动真实 SDK：

- `ConversationE2ETest`：session.ready、音频推流触发转写、status 状态流转
- `InterruptE2ETest`：interrupt 到达服务端、localVadState 随 PCM 变化
- `ErrorHandlingE2ETest`：可恢复错误、fatal 错误触发 flush
- `ReconnectE2ETest`：新建会话恢复标志
- `LocalMockServerE2ETest`：建连、完整对话流

E2E 不需要真实 `AudioRecord` / `AudioTrack` / Camera。

### 9.4 性能基线

| 指标 | 断言值 | 测试 |
|------|--------|------|
| Opus 编码 p99（16k / 24k） | ≤ 5ms | `OpusEncodingLatencyTest` |
| Opus 解码 p99（16k） | ≤ 5ms | `OpusEncodingLatencyTest` |
| VAD 检测 p99（16k） | ≤ 1ms | `VadLatencyTest` |
| JPEG 1080p q70 p99 | ≤ 30ms | `JpegCompressionLatencyTest` |
| SDK native heap 增量 | ≤ 30MB | `MemoryFootprintTest` |

---

## 10. 当前实现边界与约定

以下为与代码一致的实际边界，接入时需按此预期设计。

### 10.1 音频参数

- `AudioSampleRate` 枚举包含 16000 与 24000，但 `VadDetectorImpl` 要求采样率属于 `{8000, 16000, 32000}`（native WebRTC VAD 限制）。当前公开配置中实际可用的是 **16000**；传 `RATE_24000` 会在 SDK 构造阶段抛出 `IllegalArgumentException`。
- `audioFrameMs` 建议保持 20。Opus native 编码按固定 20ms 帧处理，VAD 也固定按 20ms 处理；配置为 40 时 `FrameAssembler` 虽会按 40ms 聚帧，但原生链路只消费每帧前 20ms。
- SDK 不做重采样，也不在运行期校验输入 PCM 的采样率；App 必须保证 `AudioRecord` 采样率与 `config.audioSampleRate` 一致。

### 10.2 未接线的保留能力

- `bandwidthHint` 初始为 `GOOD`，`BandwidthEstimator.snapshot()` 未被 Facade 调用，运行期不会更新。
- `ReorderBuffer` 存在于 `SessionManager` 中并参与 reset，但 `handleInbound` 未使用它；S2C 消息按到达顺序分发。
- `AudioRecvPipeline.reset()` 为空实现；`AudioSendPipeline.reset()` 与 `SessionManager.reset()` 均未被 Facade 调用。
- 服务端 `session.ready` 下发的 `keepalive_interval_s` 未被使用，心跳周期固定取本地 `config.keepaliveIntervalS`。

### 10.3 生命周期

- SDK 无公开 `close()`；native 处理器随 SDK 实例存活，`disconnect()` 不释放。
- `disconnect()` 不发送 `session.end`。当前 App 的退出流程也不调用 `sdk.endSession(...)`。
- SDK 不自动在重连后发送 `session.start`；必须由 App 观察 `connectionEpoch` 后触发。
- 断开/重连不会自动清空未满音频帧、不会重置解码器状态、不会改变 `sessionState`。
- 非 `CONNECTED` 状态下调用 `pushAudioPcm` 仍会完成 VAD/AGC/Opus 编码，随后在 `ConnectionManager.send` 处被丢弃；同一失败段只记录一次日志。

### 10.4 传输安全

- 当前 `ConnectionManager` 接受任意服务端证书，并跳过主机名校验。
- SDK Manifest 设置 `usesCleartextTraffic=true`；App Manifest 同样设置。
- token 与 device_id 以明文查询参数拼接到 URL。

### 10.5 数据面细节

- `onAiAudioPcm` 不暴露 `message_id`、`index`、`is_final`；`turnId` 当前恒为空字符串。
- `onVideoAck` 不暴露 `ack_frame_id`；丢帧检测只写 SDK 日志。
- `ai_text` 的 `reply_to`、`agent_name`、`proactive` 字段会被解析但不暴露；Listener 仅收到 `text` / `messageId` / `index` / `isFinal` / `image` / `source`。
- `pushVideoBitmap` 不复制 Bitmap；编码在 video send 线程异步执行，App 在编码完成前不得回收/修改该 Bitmap。
- `pushVideoBitmap` payload 的 `width` / `height` 是原始 Bitmap 尺寸，`quality` 字段固定为 `config.videoQuality`。
- `pushVideoJpeg` 会复制 JPEG 字节后异步发送。
- 音频与视频发送队列无背压、无帧率限制；调用频率由 App 控制。
- `CompositeLivaChatListener` 当前未覆写 `onSessionChanged`，该回调不会经 composite 分发给订阅者。

---

## 11. 设计权衡

| 决策 | 选择 | 备选 | 理由 |
|------|------|------|------|
| 谁持有 AudioRecord | App | SDK | App 可对接 Oboe、SCO、外部设备，并统一管理权限与前台服务 |
| 谁持有 AudioTrack / 播放队列 | App | SDK | 音频焦点、路由、抖动缓冲与设备切换由 App 掌握 |
| 谁持有 Camera | App | SDK | 支持 CameraX、屏幕录制、图库等非相机来源 |
| 编解码是否公开 | 内部 | 公开 Processor 工厂 | 公开面最小化；App 只需 PCM/JPEG 接口 |
| 本地 VAD 结果如何暴露 | `StateFlow<VadState>` | Listener 回调 | 可与 `sessionState` combine，且无需 App 持有 native 句柄 |
| 是否保留 SDK 内 VAD | 是 | 移除 | 协议要求每帧携带 `vad_state`；同时驱动本地打断 |
| AGC 放在哪里 | SDK 内、VAD 之后、编码之前 | App 侧处理 | 避免抬高 VAD 输入电平导致假打断 |
| 视频输入接口 | Bitmap + JPEG 直通 | 仅 Bitmap | 屏幕/图库/相机已在上游编码，直通避免二次压缩 |
| 重连后是否自动重启会话 | 否，App 触发 | SDK 内部自动 | 让 App 掌握本地会话/历史/模式的恢复策略 |
| 会话状态是否本地推断 | 否 | 是 | 服务端是状态唯一权威，避免双端状态分叉 |
| S2C 是否重排序 | 当前按到达顺序 | ReorderBuffer 接入 | 简化现状；重排组件保留待用 |
| JPEG 是否走 native | 否，`Bitmap.compress` | libjpeg-turbo | 减少 native 依赖，性能满足 1080p q70 <30ms 基线 |
