package dev.livachat.sdk

import android.util.Base64
import dev.livachat.protocol.generated.AvDetectorSwitch
import dev.livachat.protocol.generated.ClientAudioPayload
import dev.livachat.protocol.generated.ClientMessage
import dev.livachat.protocol.generated.ClientSessionStartConfig
import dev.livachat.protocol.generated.CameraFacing
import dev.livachat.protocol.generated.ClientVideoPayload
import dev.livachat.protocol.generated.VadSensitivity as ProtoVadSensitivity
import dev.livachat.protocol.generated.VadState as ProtoVadState
import dev.livachat.sdk.internal.network.ConnectionManager
import dev.livachat.sdk.internal.network.ReconnectConfig
import dev.livachat.sdk.internal.pipeline.AudioRecvPipeline
import dev.livachat.sdk.internal.pipeline.AudioSendPipeline
import dev.livachat.sdk.internal.pipeline.BandwidthEstimator
import dev.livachat.sdk.internal.pipeline.VideoSendPipeline
import dev.livachat.sdk.internal.processors.JpegEncoderImpl
import dev.livachat.sdk.internal.processors.OpusDecoderImpl
import dev.livachat.sdk.internal.processors.OpusEncoderImpl
import dev.livachat.sdk.internal.processors.VadDetectorImpl
import dev.livachat.sdk.internal.session.SessionManager
import dev.livachat.sdk.internal.util.ListenerDispatcherImpl
import dev.livachat.sdk.internal.util.SdkDispatchers
import dev.livachat.sdk.internal.util.SdkLogger
import dev.livachat.sdk.model.BandwidthHint
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.InterruptReason
import dev.livachat.sdk.model.Orientation
import dev.livachat.sdk.model.SessionEndReason
import dev.livachat.sdk.model.SessionState
import dev.livachat.sdk.model.VadState
import java.util.TimeZone
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/**
 * LivaChat SDK — protocol transport and audio/video processing facade.
 *
 * Usage (App layer):
 * ```
 * val sdk = LivaChatSDK(config, object : LivaChatListener { ... })
 * sdk.connect()
 * sdk.startSession()
 * sdk.pushAudioPcm(pcmFrame)
 * ```
 */
class LivaChatSDK(
    private val config: LivaChatConfig,
    private val listener: LivaChatListener,
) {
    // ── Observer scope for forwarding state changes to listener ────

    private val observerScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    // ── State flows ─────────────────────────────────────────────────

    private val _localVadState = MutableStateFlow(VadState.UNKNOWN)
    val localVadState: StateFlow<VadState> = _localVadState.asStateFlow()

    private val _bandwidthHint = MutableStateFlow(BandwidthHint.GOOD)
    val bandwidthHint: StateFlow<BandwidthHint> = _bandwidthHint.asStateFlow()

    val sessionState: StateFlow<SessionState>
        get() = sessionManager.sessionState

    val connectionState: StateFlow<ConnectionState>
        get() = connectionManager.connectionState

    /**
     * 连接代际：每次成功建连（含重连）单调 +1，初值 0 = 本进程尚无成功连接。
     * 单调不回退 ⇒ 免疫 StateFlow 值回归去重（CONNECTED→RECONNECTING→CONNECTED
     * 回归 flap 也必被观察到），供 App 做重连自动恢复的可靠触发。
     */
    val connectionEpoch: StateFlow<Long>
        get() = connectionManager.connectionEpoch

    // ── Internal components ─────────────────────────────────────────

    private val opusEncoder = OpusEncoderImpl(config.audioSampleRate.hz)
    private val opusDecoder = OpusDecoderImpl(config.audioSampleRate.hz)
    private val vadDetector = VadDetectorImpl(config.audioSampleRate.hz, config.vadSensitivity)
    private val jpegEncoder = JpegEncoderImpl()
    private val bandwidthEstimator = BandwidthEstimator()
    private val audioSeqGen = AtomicLong(0)

    private val audioRecvPipeline = AudioRecvPipeline(
        sampleRate = config.audioSampleRate.hz,
        decoder = opusDecoder,
        onPcm = { pcm, sampleRate, durationMs, sequenceNumber ->
            listener.onAiAudioPcm(
                pcm = pcm,
                sampleRate = sampleRate,
                sampleBits = 16,
                durationMs = durationMs,
                sequenceNumber = sequenceNumber,
                turnId = "",
            )
        },
    )

    private val connectionManager = ConnectionManager(
        serverUrl = config.serverUrl,
        token = config.token,
        deviceId = config.deviceId,
        reconnectConfig = ReconnectConfig(
            maxAttempts = config.reconnectMaxAttempts,
            baseDelayMs = config.reconnectBaseDelayMs,
            maxDelayMs = config.reconnectMaxDelayMs,
        ),
        keepaliveIntervalS = config.keepaliveIntervalS,
        keepaliveTimeoutS = config.keepaliveTimeoutS,
        allowSelfSigned = config.allowSelfSigned,
    )

    private val listenerDispatcherImpl = ListenerDispatcherImpl(
        listener = listener,
        aiAudioHandler = { data, _, durationMs ->
            val seq = audioSeqGen.incrementAndGet()
            val opus = Base64.decode(data, Base64.DEFAULT)
            CoroutineScope(SdkDispatchers.audioRecv).launch {
                audioRecvPipeline.handleAiAudio(opus, durationMs, seq)
            }
        },
    )

    // AVDetector 运行期开关：构造时取持久化初始值；setAvdetectorEnabled 更新。
    // 必须在 sessionManager 构造前创建，供 configProvider 与 SessionManager 共享。
    private val avdetectorEnabled = AtomicBoolean(config.avdetectorEnabled)

    private val sessionManager = SessionManager(
        sendRaw = { json ->
            bandwidthEstimator.recordBytesSent(json.length.toLong())
            connectionManager.send(json)
        },
        listenerDispatcher = listenerDispatcherImpl,
        configProvider = {
            val timezone = resolveTimezone(config.timezone)
            val language = config.languageProvider?.invoke() ?: config.language
            SdkLogger.d("SDK", "session.start config | " +
                "timezone_override=${config.timezone ?: "null"} | timezone_sent=$timezone | " +
                "language=${language ?: "null"} | avdetector=${avdetectorEnabled.get()}")
            ClientSessionStartConfig(
                enableVideo = config.enableVideo,
                audioSampleRate = config.audioSampleRate.hz,
                vadSensitivity = mapVadSensitivity(config.vadSensitivity),
                clientVersion = config.clientVersion,
                timezone = timezone,
                language = language,
                avdetector = if (avdetectorEnabled.get()) {
                    AvDetectorSwitch.ENABLE
                } else {
                    AvDetectorSwitch.DISABLE
                },
            )
        },
        avdetectorEnabled = avdetectorEnabled,
    )

    private val audioSendScope = CoroutineScope(SdkDispatchers.audioSend)
    private val videoSendScope = CoroutineScope(SdkDispatchers.videoSend)
    private val videoFrameIdGen = AtomicLong(0)

    // 音频 50Hz 失败日志节流：同一失败段只记首条（成功后复位），与
    // ConnectionManager.drop 同策略——否则持续失败会以 50Hz 刷 logcat / 日志文件
    @Volatile private var audioPushFailedLogged = false

    private val audioSendPipeline = AudioSendPipeline(
        sampleRate = config.audioSampleRate.hz,
        frameMs = config.audioFrameMs,
        vad = vadDetector,
        encoder = opusEncoder,
        onEncoded = { opus, durationMs, vad, muted ->
            val b64 = Base64.encodeToString(opus, Base64.NO_WRAP)
            val payload = ClientAudioPayload(
                data = b64, codec = "opus",
                durationMs = durationMs,
                // muted 补发帧标 muted（服务端按 20ms 全零替换下发，
                // docs/android_audio_capture.md §5.2）
                vadState = if (muted) ProtoVadState.MUTED else mapVadState(vad),
            )
            sessionManager.sendAudio(payload)
        },
        onVad = { state -> _localVadState.value = state },
        uplinkAgc = config.uplinkAgc,
        // AI 说话期间保持 unity：外放残留回声被放大→ASR 转成文本→假打断
        //（docs/android_audio_capture.md §4.3）
        isAiSpeaking = { sessionManager.sessionState.value == SessionState.SPEAKING },
    )

    private val videoSendPipeline = VideoSendPipeline(
        defaultQuality = config.videoQuality,
        defaultMaxLongSide = config.videoMaxLongSide,
        jpegEncoder = jpegEncoder,
        onEncoded = { jpeg, frameId, width, height, orientation, cameraFacing, turnId, text ->
            val b64 = Base64.encodeToString(jpeg, Base64.NO_WRAP)
            sessionManager.sendVideo(
                buildVideoPayload(
                    jpegB64 = b64, frameId = frameId, width = width, height = height,
                    orientation = orientation, cameraFacing = cameraFacing,
                    quality = config.videoQuality, turnId = turnId, text = text,
                )
            )
        },
    )

    init {
        SdkLogger.init(config.logFilePath)

        // Wire WebSocket → SessionManager
        connectionManager.onMessage { json -> sessionManager.handleInbound(json) }

        // Wire heartbeat → delegate to SessionManager for proper seq numbering
        connectionManager.onPingSender {
            sessionManager.sendPing()
        }

        // Forward state changes to listener
        observerScope.launch {
            connectionManager.connectionState.collect { state ->
                if (state != ConnectionState.CONNECTED) {
                    // 连接断开/重连中：清 sessionReady/startSent/pending，防止旧连接的
                    // ready 状态残留导致 config 在 session.start 之前发出。
                    sessionManager.onConnectionLost()
                }
                listener.onConnectionStateChanged(state)
            }
        }
        observerScope.launch {
            sessionManager.sessionState.collect { state ->
                listener.onSessionStateChanged(state)
            }
        }
    }

    // ── Public API ──────────────────────────────────────────────────

    fun connect() = connectionManager.connect()
    fun disconnect() = connectionManager.disconnect()

    fun startSession(resumeSessionId: String? = null) {
        sessionManager.startSession(resumeSessionId)
    }

    /**
     * 更新 AVDetector 会话级开关（“允许 AI 主动搭话”）。
     *
     * - 更新下一次 session.start 携带的 config.avdetector；
     * - 已收到 session.ready 时发送 session.change(mode="config") 立即生效；
     * - start 已发/ready 未到时在 ready 后补发；未连接时只更新下一次 start。
     */
    fun setAvdetectorEnabled(enabled: Boolean) {
        avdetectorEnabled.set(enabled)
        sessionManager.setAvdetectorEnabled(enabled)
    }

    fun endSession(reason: SessionEndReason = SessionEndReason.USER_HANGUP) {
        sessionManager.endSession(reason)
    }

    /**
     * Push a chunk of PCM from the app（SDK 内部完成 VAD/AGC/Opus 编码 + 协议封装 + 发送）。
     *
     * @param muted true = mic 静音补发帧：payload 标 `vad_state="muted"`，
     *   服务端替换为零 PCM（lecture 分支 5×，omni/dialogue 1:1；docs/android_audio_capture.md §5.2）
     */
    fun pushAudioPcm(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size, muted: Boolean = false) {
        // Defensive copy: caller may reuse the buffer on its own thread before
        // the coroutine dispatches and FrameAssembler copies into its internal buffer.
        val chunk = if (offset == 0 && len == pcm.size) pcm.copyOf() else pcm.copyOfRange(offset, offset + len)
        audioSendScope.launch {
            try {
                audioSendPipeline.pushPcm(chunk, 0, chunk.size, muted)
                audioPushFailedLogged = false
            } catch (e: Exception) {
                if (!audioPushFailedLogged) {
                    audioPushFailedLogged = true
                    // 链路内任一点异常（VAD/Opus 编码/序列化/发送）不得静默吞掉；裸协程的
                    // 未捕获异常会直达进程默认处理器（crash），与 video 线对齐
                    SdkLogger.e("SDK", "audio frame push failed | len=$len | ${e.message}", e)
                }
            }
        }
    }

    /**
     * Push a Bitmap frame（SDK 内部完成等比缩放 + JPEG 压缩 + 协议封装 + 发送）。
     *
     * @param turnId 采集轮标识（**App 层维护**——开相机 / 切换前后摄像头 / 开始录屏时递增，
     *   有效值从 1 起；docs/fd_pipeline_video_process.md §8）。默认 0 = 未启用（等同旧版行为）。
     *   SDK 直通落线，无内部状态。
     */
    fun pushVideoBitmap(
        bitmap: android.graphics.Bitmap,
        orientation: Orientation,
        quality: Int = config.videoQuality,
        cameraFacing: String = "front",
        turnId: Long = 0,
        text: String? = null,
    ) {
        val frameId = videoFrameIdGen.incrementAndGet()
        videoSendScope.launch {
            try {
                videoSendPipeline.pushBitmap(bitmap, frameId, orientation, quality, cameraFacing, turnId, text)
            } catch (e: Exception) {
                // 链路内任一点异常（编码/序列化/发送）不得静默吞掉：记日志供丢帧定位
                SdkLogger.e("SDK", "video frame push failed | type=bitmap | frameId=$frameId | ${e.message}", e)
            }
        }
    }

    /**
     * Push a pre-encoded JPEG frame. Bypasses [JpegEncoder] entirely — no re-compression,
     * no scaling. Caller is responsible for encoding and sizing.
     *
     * @param turnId 采集轮标识（**App 层维护**——开相机 / 切换前后摄像头 / 开始录屏时递增，
     *   有效值从 1 起；docs/fd_pipeline_video_process.md §8）。默认 0 = 未启用（等同旧版行为）。
     *   SDK 直通落线，无内部状态。
     * @param text 可选：与该帧同行的用户指令（屏幕抓取点按传"描述图片内容"）。非空时服务端
     *   不等 ASR、直接以该文本 + 本帧起一轮 LLM/TTS（docs/android_screen_recording.md §5.2）；
     *   null/空 = 仅入服务端帧缓冲（现状语义）。
     */
    fun pushVideoJpeg(
        jpeg: ByteArray,
        width: Int,
        height: Int,
        orientation: Orientation,
        cameraFacing: String = "front",
        turnId: Long = 0,
        text: String? = null,
    ) {
        val chunk = jpeg.copyOf()
        val frameId = videoFrameIdGen.incrementAndGet()
        videoSendScope.launch {
            try {
                videoSendPipeline.pushJpeg(chunk, frameId, width, height, orientation, cameraFacing, turnId, text)
            } catch (e: Exception) {
                // 链路内任一点异常（编码/序列化/发送）不得静默吞掉：记日志供丢帧定位
                SdkLogger.e("SDK", "video frame push failed | type=jpeg | frameId=$frameId | ${e.message}", e)
            }
        }
    }

    fun sendInterrupt(reason: InterruptReason = InterruptReason.USER_VOICE) {
        sessionManager.sendInterrupt(reason)
    }

    /**
     * Request a pipeline mode change.
     * @param mode Target mode: "chat", "translate", or "transcribe" (ASR-only live transcription).
     * @param myLanguage User's native language code (e.g. "zh", "en"). Required for translate mode.
     * @param localLanguage Local environment language code (e.g. "ja", "fr"). Required for translate mode.
     */
    fun sendSessionChange(mode: String, myLanguage: String? = null, localLanguage: String? = null, usecase: String? = null) {
        sessionManager.sendSessionChange(mode, myLanguage, localLanguage, usecase)
    }

    // ── Public logging API ──────────────────────────────────────────

    /** Write a DEBUG-level log to logcat and, if configured, the SDK log file. */
    fun logDebug(tag: String, message: String) = SdkLogger.d(tag, message)

    /** Write an INFO-level log to logcat and, if configured, the SDK log file. */
    fun logInfo(tag: String, message: String) = SdkLogger.i(tag, message)

    /** Write a WARN-level log to logcat and, if configured, the SDK log file. */
    fun logWarn(tag: String, message: String, throwable: Throwable? = null) =
        SdkLogger.w(tag, message, throwable)

    /** Write an ERROR-level log to logcat and, if configured, the SDK log file. */
    fun logError(tag: String, message: String, throwable: Throwable? = null) =
        SdkLogger.e(tag, message, throwable)

    // ── Enum mappings ───────────────────────────────────────────────

    private fun mapVadState(sdk: VadState): ProtoVadState = when (sdk) {
        VadState.SPEECH -> ProtoVadState.SPEECH
        VadState.SILENCE -> ProtoVadState.SILENCE
        VadState.UNKNOWN -> ProtoVadState.SILENCE
    }

    private fun mapVadSensitivity(sdk: dev.livachat.sdk.model.VadSensitivity) = when (sdk) {
        dev.livachat.sdk.model.VadSensitivity.LOW -> ProtoVadSensitivity.LOW
        dev.livachat.sdk.model.VadSensitivity.MEDIUM -> ProtoVadSensitivity.MEDIUM
        dev.livachat.sdk.model.VadSensitivity.HIGH -> ProtoVadSensitivity.HIGH
    }
}

/** session.start 上报时区：显式覆盖优先，否则每次现取系统时区（timezone-support.md §4）。 */
internal fun resolveTimezone(override: String?): String = override ?: TimeZone.getDefault().id

/** 帧来源字符串 → 协议枚举（"screen" = 屏幕采集；"gallery" = 图库照片；未知值兜底 FRONT，旧行为保留）。 */
internal fun mapCameraFacing(facing: String): CameraFacing = when (facing) {
    "back" -> CameraFacing.BACK
    "screen" -> CameraFacing.SCREEN
    "gallery" -> CameraFacing.GALLERY
    else -> CameraFacing.FRONT
}

/**
 * video 帧 payload 组装（fd_pipeline_video_process.md §8）：turnId 由 App 经 push 传入、直通落线
 * （服务端判变化直起轮；0 = 未启用，服务端视同缺省）。text 默认 null + encodeDefaults=false
 * → 字段整体不落线（服务端仅入帧缓冲）。
 */
internal fun buildVideoPayload(
    jpegB64: String,
    frameId: Long,
    width: Int,
    height: Int,
    orientation: Orientation,
    cameraFacing: String,
    quality: Int,
    turnId: Long,
    text: String?,
): ClientVideoPayload = ClientVideoPayload(
    data = jpegB64,
    frameId = frameId,
    width = width,
    height = height,
    orientation = orientation.degrees,
    cameraFacing = mapCameraFacing(cameraFacing),
    quality = quality,
    turnId = turnId,
    text = text,
)
