package dev.livachat.sdk.internal.session

import dev.livachat.protocol.generated.*
import dev.livachat.sdk.internal.protocol.MessageCodec
import dev.livachat.sdk.internal.util.SdkLogger
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.PlaybackFlushReason
import dev.livachat.sdk.model.SessionState
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Session-layer state machine — the brain of the SDK.
 *
 * Coordinates:
 * - C2S seq generation ([SeqGenerator])
 * - S2C message reordering ([ReorderBuffer])
 * - Server-clock offset tracking ([ClockOffset])
 * - Session state transitions ([SessionState])
 * - Force-flush signalling for App-level playback interruption ([onForceFlush])
 *
 * Does **not** own a [dev.livachat.sdk.internal.network.ConnectionManager];
 * all I/O goes through the injected [sendRaw] callback.
 */
internal class SessionManager(
    private val codec: MessageCodec = MessageCodec,
    private val sendRaw: (String) -> Unit,
    private val listenerDispatcher: ListenerDispatcher,
    private val now: () -> Long = { System.currentTimeMillis() },
    private val configProvider: () -> ClientSessionStartConfig,
    private val avdetectorEnabled: AtomicBoolean = AtomicBoolean(true),
) {

    private val _sessionState = MutableStateFlow(SessionState.IDLE)
    val sessionState: StateFlow<SessionState> = _sessionState.asStateFlow()

    private val _onForceFlush = MutableSharedFlow<PlaybackFlushReason>(extraBufferCapacity = 8)
    val onForceFlush: SharedFlow<PlaybackFlushReason> = _onForceFlush.asSharedFlow()

    private val seqGenerator = SeqGenerator()

    /** C2S 发送闸口锁：`next()` 与传输层发送必须同处一个临界区（见 [encodeAndSend]；
     *  video 走 [encodeAndSendOutsideGate] 宽容路径，闸口内只送包）。 */
    private val sendLock = Any()

    private val reorderBuffer = ReorderBuffer<ServerMessage>(clock = now)
    private val clockOffset = ClockOffset()

    @Volatile
    private var sessionId: String? = null

    // ── AVDetector session config state ─────────────────────────────
    // 保护 sessionStartSent/sessionReady/pending/configInFlight/queued/
    // sessionConfigUnsupported：WS reader 线程（ready/ended/error）与 App 主线程
    // （setAvdetectorEnabled）共同访问。
    private val avStateLock = Any()
    private var sessionStartSent = false
    private var sessionReady = false
    private var pendingAvdetectorChange: Boolean? = null
    private var configInFlight = false
    private var queuedConfigValue: Boolean? = null
    private var sessionConfigUnsupported = false

    // Per-session audio message counters for sampled logging.
    private var audioInCount: Int = 0
    private var audioOutCount: Int = 0

    // Video frame loss tracking: highest frame id attempted to send and the
    // highest id the server has acked. Server acks every frame it receives
    // (processed=false still acks), so a gap in ack ids = frame lost in
    // transit or dropped by the client send path before reaching the server.
    private var lastSentVideoFrameId: Long = 0
    private var lastAckedVideoFrameId: Long = 0

    // ── Message logging ───────────────────────────────────────────

    private fun logInbound(msg: ServerMessage) {
        val msgType = when (msg) {
            is ServerMessage.SessionReady -> "session.ready"
            is ServerMessage.SessionEnded -> "session.ended"
            is ServerMessage.SessionChanged -> "session.changed"
            is ServerMessage.Transcription -> "transcription"
            is ServerMessage.AiText -> "ai_text"
            is ServerMessage.AiAudio -> "ai_audio"
            is ServerMessage.AiVideoAck -> "ai_video_ack"
            is ServerMessage.JobStatus -> "job_status"
            is ServerMessage.Status -> "status"
            is ServerMessage.Error -> "error"
            is ServerMessage.Ping -> "ping"
            is ServerMessage.Pong -> "pong"
        }

        if (msgType == "ai_audio") {
            audioInCount++
            val n = audioInCount
            if (n != 1 && n % 50 != 0) return
            SdkLogger.d(TAG, "recv $msgType | seq=${msg.seq} | audio_n=$n")
        } else {
            SdkLogger.d(TAG, "recv $msgType | seq=${msg.seq}")
        }
    }

    private fun logOutbound(msg: ClientMessage) {
        val msgType = when (msg) {
            is ClientMessage.SessionStart -> "session.start"
            is ClientMessage.SessionEnd -> "session.end"
            is ClientMessage.SessionChange -> "session.change"
            is ClientMessage.Audio -> "audio"
            is ClientMessage.Video -> "video"
            is ClientMessage.Interrupt -> "interrupt"
            is ClientMessage.Ping -> "ping"
            is ClientMessage.Pong -> "pong"
        }

        if (msgType == "audio") {
            audioOutCount++
            val n = audioOutCount
            if (n != 1 && n % 50 != 0) return
            SdkLogger.d(TAG, "send $msgType | seq=${msg.seq} | audio_n=$n")
        } else {
            SdkLogger.d(TAG, "send $msgType | seq=${msg.seq}")
        }
    }

    // ── Outbound helpers ──────────────────────────────────────────

    /**
     * C2S 发送闸口：取号 → 构造 → 编码 → 交传输层，在 [sendLock] 内原子完成。
     *
     * 调用方分散在多个线程（audioSend 50Hz / heartbeat / OkHttp reader / 主线程），
     * 各自只保证线程内顺序。若在闸口外先 `seqGenerator.next()` 再发送，编码窗口内会被
     * 其他线程反超——到达服务端的顺序与取号顺序相反，`InboundSeqGuard`
     * （services/gateway/seq.py）判为 seq 回退并丢弃该帧（interrupt / session.change
     * 被丢则客户端意图落空）。故 `next()` 除 video 宽容路径外只在本临界区内调用。
     */
    private fun encodeAndSend(build: (seq: Long) -> ClientMessage) {
        val message: ClientMessage
        synchronized(sendLock) {
            message = build(seqGenerator.next())
            sendRaw(codec.encode(message))
        }
        // 锁外记录：SdkLogger 每行同步 flush 落盘，占住闸口会拖慢 50Hz 音频发送；
        // 代价是并发下日志行序可能与上线序交错（上线序本身由闸口保证）
        logOutbound(message)
    }

    /**
     * 闸口外取号+编码，锁内只送包——仅 video 可用。
     *
     * 视频大帧的 JSON 序列化不再占用闸口（否则会拖住 50Hz 音频路径）；代价是 seq 可能
     * 小于已上线的音频帧，服务端对 video 乱序记日志后照常处理，不丢帧
     * （services/gateway/seq.py 的 `_TOLERATED_TYPES`）。其他消息类型不得使用本路径。
     */
    private fun encodeAndSendOutsideGate(build: (seq: Long) -> ClientMessage) {
        val message = build(seqGenerator.next())
        val json = codec.encode(message)
        synchronized(sendLock) {
            sendRaw(json)
        }
        logOutbound(message)
    }

    // ── Public API ────────────────────────────────────────────────

    /** Start a new session or resume an existing one. */
    fun startSession(resumeSessionId: String? = null) {
        reorderBuffer.reset()
        synchronized(avStateLock) {
            // 新 start 视为新会话边界：ready 未到前不发送 config。
            sessionStartSent = true
            sessionReady = false
            pendingAvdetectorChange = null
            configInFlight = false
            queuedConfigValue = null
        }
        val config = configProvider()
        val payload = ClientSessionStartPayload(
            sessionId = resumeSessionId,
            config = config,
        )
        encodeAndSend { seq ->
            ClientMessage.SessionStart(seq = seq, timestamp = now(), payload = payload)
        }
    }

    /**
     * 更新 AVDetector 会话级开关：单飞合并，仅 ready 后发送 config；
     * start 已发/ready 未到时记 pending，ready 后补发；未连接时只更新下一次 start。
     */
    fun setAvdetectorEnabled(enabled: Boolean) {
        avdetectorEnabled.set(enabled)
        var sendValue: Boolean? = null
        synchronized(avStateLock) {
            if (sessionConfigUnsupported) return
            if (configInFlight) {
                queuedConfigValue = enabled
                return
            }
            if (sessionReady) {
                configInFlight = true
                queuedConfigValue = null
                sendValue = enabled
            } else if (sessionStartSent) {
                pendingAvdetectorChange = enabled
                return
            } else {
                return
            }
        }
        sendValue?.let { sendAvdetectorConfig(it) }
    }

    private fun sendAvdetectorConfig(enabled: Boolean) {
        val payload = ClientSessionChangePayload(
            mode = ClientSessionChangeMode.CONFIG,
            avdetector = if (enabled) AvDetectorSwitch.ENABLE else AvDetectorSwitch.DISABLE,
        )
        encodeAndSend { seq ->
            ClientMessage.SessionChange(seq = seq, timestamp = now(), payload = payload)
        }
        SdkLogger.d(TAG, "send session.change(config) | avdetector=$enabled")
    }

    /** 清 AVDetector 会话级生命周期状态；调用方必须持有 [avStateLock]。 */
    private fun resetAvdetectorSessionStateLocked() {
        sessionStartSent = false
        sessionReady = false
        pendingAvdetectorChange = null
        configInFlight = false
        queuedConfigValue = null
    }

    /** 连接断开/重连中：清会话就绪与单飞状态；最新开关值仍在 AtomicBoolean。 */
    fun onConnectionLost() {
        synchronized(avStateLock) {
            resetAvdetectorSessionStateLocked()
            sessionConfigUnsupported = false
        }
    }

    /** Gracefully end the current session. */
    fun endSession(reason: dev.livachat.sdk.model.SessionEndReason) {
        val protoReason = when (reason) {
            dev.livachat.sdk.model.SessionEndReason.USER_HANGUP -> SessionEndReason.USER_QUIT
            dev.livachat.sdk.model.SessionEndReason.APP_BACKGROUND -> SessionEndReason.APP_BACKGROUND
            dev.livachat.sdk.model.SessionEndReason.ERROR -> SessionEndReason.ERROR
        }
        val payload = ClientSessionEndPayload(protoReason)
        encodeAndSend { seq ->
            ClientMessage.SessionEnd(seq = seq, timestamp = now(), payload = payload)
        }
        // session.end 之后服务端不再处理本会话的业务消息：立即关闭 config 发送门，
        // 避免用户在“已挂断、连接尚未断开”窗口切换设置时发出无归属的 config。
        synchronized(avStateLock) {
            resetAvdetectorSessionStateLocked()
        }
    }

    /** Send an interrupt message. */
    fun sendInterrupt(reason: dev.livachat.sdk.model.InterruptReason) {
        val protoReason = when (reason) {
            dev.livachat.sdk.model.InterruptReason.USER_VOICE -> InterruptReason.USER_VOICE
            dev.livachat.sdk.model.InterruptReason.USER_TAP -> InterruptReason.MANUAL
            dev.livachat.sdk.model.InterruptReason.APP_CANCEL -> InterruptReason.MANUAL
        }
        val payload = ClientInterruptPayload(protoReason)
        encodeAndSend { seq ->
            ClientMessage.Interrupt(seq = seq, timestamp = now(), payload = payload)
        }
    }

    /** Send a keepalive ping (called by ConnectionManager heartbeat). */
    fun sendPing() {
        encodeAndSend { seq ->
            ClientMessage.Ping(seq = seq, timestamp = now(), payload = ClientPingPayload())
        }
    }

    /** Request a pipeline mode change (chat, translate, transcribe, or omni). */
    fun sendSessionChange(mode: String, myLanguage: String?, localLanguage: String?, usecase: String? = null) {
        val modeEnum = when (mode) {
            "chat" -> ClientSessionChangeMode.CHAT
            "translate" -> ClientSessionChangeMode.TRANSLATE
            "transcribe" -> ClientSessionChangeMode.TRANSCRIBE
            "omni" -> ClientSessionChangeMode.OMNI
            else -> {
                SdkLogger.w(TAG, "sendSessionChange ignored: invalid mode '$mode'")
                return
            }
        }
        val usecaseEnum = when (usecase) {
            "lecture" -> ClientSessionChangeUsecase.LECTURE
            "dialogue" -> ClientSessionChangeUsecase.DIALOGUE
            else -> null
        }
        val payload = ClientSessionChangePayload(
            mode = modeEnum,
            myLanguage = myLanguage,
            localLanguage = localLanguage,
            usecase = usecaseEnum,
        )
        encodeAndSend { seq ->
            ClientMessage.SessionChange(seq = seq, timestamp = now(), payload = payload)
        }
    }

    /** Send an audio frame (called by AudioSendPipeline in Phase 5). */
    fun sendAudio(payload: ClientAudioPayload) {
        encodeAndSend { seq ->
            ClientMessage.Audio(seq = seq, timestamp = now(), payload = payload)
        }
    }

    /** Send a video frame (called by VideoSendPipeline in Phase 5). 走宽容路径，见 [encodeAndSendOutsideGate]。 */
    fun sendVideo(payload: ClientVideoPayload) {
        if (payload.frameId > lastSentVideoFrameId) lastSentVideoFrameId = payload.frameId
        encodeAndSendOutsideGate { seq ->
            ClientMessage.Video(seq = seq, timestamp = now(), payload = payload)
        }
    }

    /** Handle an inbound JSON string (called by ConnectionManager.onMessage). */
    fun handleInbound(json: String) {
        val msg = codec.decode(json) ?: return // unknown / malformed → ignore

        logInbound(msg)

        when (msg) {
            is ServerMessage.SessionReady -> handleSessionReady(msg)
            is ServerMessage.SessionEnded -> handleSessionEnded(msg)
            is ServerMessage.SessionChanged -> handleSessionChanged(msg)
            is ServerMessage.Status -> handleStatus(msg)
            is ServerMessage.Error -> handleError(msg)
            is ServerMessage.Ping -> handleServerPing(msg)
            else -> dispatchMessage(msg)
        }
    }

    /** Reset all state for a fresh connection. */
    fun reset() {
        reorderBuffer.reset()
        seqGenerator.reset()
        sessionId = null
        audioInCount = 0
        audioOutCount = 0
        _sessionState.value = SessionState.IDLE
        onConnectionLost()
    }

    // ── Inbound handlers ──────────────────────────────────────────

    private fun handleSessionReady(msg: ServerMessage.SessionReady) {
        sessionId = msg.payload.sessionId
        clockOffset.update(msg.payload.serverTimeMs, now())
        var sendValue: Boolean? = null
        synchronized(avStateLock) {
            sessionReady = true
            val pending = pendingAvdetectorChange
            pendingAvdetectorChange = null
            if (pending != null && !sessionConfigUnsupported) {
                configInFlight = true
                sendValue = pending
            }
        }
        listenerDispatcher.onSessionReady(
            sessionId = msg.payload.sessionId,
            sessionRestored = msg.payload.sessionRestored,
        )
        sendValue?.let { sendAvdetectorConfig(it) }
    }

    private fun handleSessionEnded(msg: ServerMessage.SessionEnded) {
        synchronized(avStateLock) {
            sessionStartSent = false
            sessionReady = false
            pendingAvdetectorChange = null
            configInFlight = false
            queuedConfigValue = null
            sessionConfigUnsupported = false
        }
        val reason = msg.payload.reason.name
        listenerDispatcher.onSessionEnded(msg.payload.sessionId, reason)
        emitForceFlush(PlaybackFlushReason.SESSION_ENDED)
    }

    private fun handleStatus(msg: ServerMessage.Status) {
        val newState = mapSessionState(msg.payload.sessionStatus)
        val oldState = _sessionState.value
        _sessionState.value = newState

        // Design §4.4: transition from SPEAKING to LISTENING = interrupt acknowledged
        // if (oldState == SessionState.SPEAKING && newState == SessionState.LISTENING) {
        //     emitForceFlush(PlaybackFlushReason.INTERRUPT_ACK)
        // }
        if (newState == SessionState.THINKING) {
            emitForceFlush(PlaybackFlushReason.INTERRUPT_ACK)
        }
    }

    private fun handleError(msg: ServerMessage.Error) {
        SdkLogger.w(
            TAG,
            "recv error | seq=${msg.seq} | code=${msg.payload.code} | message=${msg.payload.message} | " +
                "fatal=${msg.payload.fatal} | recoverable=${msg.payload.recoverable}",
        )
        // config 错误在 SDK 层消费：不得进入 App 的 1009 模式回滚逻辑，也不弹通用错误。
        if (!msg.payload.fatal && msg.payload.code == SESSION_CONFIG_FAILED) {
            synchronized(avStateLock) {
                configInFlight = false
                queuedConfigValue = null
            }
            SdkLogger.w(TAG, "session.config rejected (1011): ${msg.payload.message}")
            return
        }
        if (!msg.payload.fatal && msg.payload.code == MALFORMED_JSON) {
            var wasConfig = false
            synchronized(avStateLock) {
                if (configInFlight) {
                    wasConfig = true
                    configInFlight = false
                    queuedConfigValue = null
                    sessionConfigUnsupported = true
                }
            }
            if (wasConfig) {
                SdkLogger.w(TAG, "session.config unsupported by server (1006); disabling live config")
                return
            }
        }
        listenerDispatcher.onError(
            code = msg.payload.code,
            message = msg.payload.message,
            fatal = msg.payload.fatal,
            recoverable = msg.payload.recoverable,
        )
        if (msg.payload.fatal) {
            emitForceFlush(PlaybackFlushReason.SERVER_ABORT)
        }
    }

    private fun handleServerPing(msg: ServerMessage.Ping) {
        val payload = ClientPongPayload(pingSeq = msg.seq)
        encodeAndSend { seq ->
            ClientMessage.Pong(seq = seq, timestamp = now(), payload = payload)
        }
    }

    private fun handleSessionChanged(msg: ServerMessage.SessionChanged) {
        if (msg.payload.mode == ServerSessionChangedMode.CONFIG) {
            val confirmed = msg.payload.avdetector != AvDetectorSwitch.DISABLE
            var nextValue: Boolean? = null
            synchronized(avStateLock) {
                configInFlight = false
                val queued = queuedConfigValue
                queuedConfigValue = null
                if (queued != null && queued != confirmed) {
                    configInFlight = true
                    nextValue = queued
                }
            }
            SdkLogger.d(TAG, "recv session.changed(config) | avdetector=$confirmed")
            nextValue?.let { sendAvdetectorConfig(it) }
            // config 回声是控制确认，不进入 App 的模式状态机
            return
        }
        listenerDispatcher.onSessionChanged(
            mode = msg.payload.mode.name.lowercase(),
            myLanguage = msg.payload.myLanguage,
            localLanguage = msg.payload.localLanguage,
            usecase = msg.payload.usecase?.name?.lowercase(),
        )
    }

    private fun dispatchMessage(msg: ServerMessage) {
        when (msg) {
            is ServerMessage.Transcription -> listenerDispatcher.onTranscription(
                text = msg.payload.text,
                isFinal = msg.payload.isFinal,
                transcriptionId = msg.payload.transcriptionId,
            )
            is ServerMessage.AiText -> {
                val image = msg.payload.image?.let {
                    dev.livachat.sdk.model.AiImage(
                        data = it.data,
                        mime = it.mime,
                        width = it.width,
                        height = it.height,
                        orientation = it.orientation,
                        cameraFacing = it.cameraFacing.name.lowercase(),
                    )
                }
                listenerDispatcher.onAiText(
                    text = msg.payload.text,
                    messageId = msg.payload.messageId,
                    index = msg.payload.index,
                    isFinal = msg.payload.isFinal,
                    image = image,
                    source = msg.payload.source?.name?.lowercase(),
                )
            }
            is ServerMessage.AiAudio -> listenerDispatcher.onAiAudio(
                data = msg.payload.data,
                codec = msg.payload.codec,
                durationMs = msg.payload.durationMs,
            )
            is ServerMessage.AiVideoAck -> {
                val ack = msg.payload.frameId
                if (!msg.payload.processed) {
                    SdkLogger.d(TAG, "video frame dropped by server | frameId=$ack | processed=false")
                }
                if (lastAckedVideoFrameId > 0 && ack > lastAckedVideoFrameId + 1) {
                    val from = lastAckedVideoFrameId + 1
                    val to = minOf(ack - 1, lastSentVideoFrameId)
                    if (to >= from) {
                        SdkLogger.w(TAG, "video frames lost | range=${from}..${to} | count=${to - from + 1} | ack=$ack")
                    }
                }
                if (ack > lastAckedVideoFrameId) lastAckedVideoFrameId = ack
                listenerDispatcher.onAiVideoAck(
                    frameId = msg.payload.frameId,
                    ackFrameId = msg.payload.ackFrameId,
                    processed = msg.payload.processed,
                )
            }
            is ServerMessage.JobStatus -> listenerDispatcher.onJobStatus(
                msg.payload.jobs.map { item ->
                    AgentJob(
                        jobId = item.jobId,
                        agentName = item.agentName,
                        summary = item.summary,
                        // ClockOffset calibrated at session.ready (earlier than the
                        // session.started-triggered first frame); offset 0 falls back
                        // to raw server clock and App-side negative-time guard.
                        startedAtMs = item.startedAtMs - clockOffset.offsetMs(),
                        runningTimeMs = item.runningTimeMs,
                    )
                },
            )
            is ServerMessage.Pong -> { /* ignore — heartbeat is transport-layer concern */ }
            else -> { /* unknown / future types */ }
        }
    }

    private fun emitForceFlush(reason: PlaybackFlushReason) {
        _onForceFlush.tryEmit(reason)
        listenerDispatcher.onPlaybackShouldFlush(reason)
    }

    // ── Helpers ───────────────────────────────────────────────────

    private fun mapSessionState(status: SessionStatusCode): SessionState = when (status) {
        SessionStatusCode.IDLE -> SessionState.IDLE
        SessionStatusCode.LISTENING -> SessionState.LISTENING
        SessionStatusCode.THINKING -> SessionState.THINKING
        SessionStatusCode.SPEAKING -> SessionState.SPEAKING
    }

    private companion object {
        const val TAG = "Session"
        // 协议 §4.3：config 请求业务失败专用（不得与模式切换 1009 混用）
        const val SESSION_CONFIG_FAILED = 1011
        const val MALFORMED_JSON = 1006
    }
}
