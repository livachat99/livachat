package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.ConversationError
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.PlaybackFlushReason
import dev.livachat.sdk.model.SessionState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.time.Instant
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ConversationListenerAdapter @Inject constructor(
    private val chatRepository: ChatRepository,
) : LivaChatListener {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private val _transcription = MutableStateFlow("")
    val transcription: StateFlow<String> = _transcription.asStateFlow()

    private val _isTranscriptionFinal = MutableStateFlow(false)
    val isTranscriptionFinal: StateFlow<Boolean> = _isTranscriptionFinal.asStateFlow()

    private val _errors = MutableSharedFlow<ConversationError>(extraBufferCapacity = 16)
    val errors: SharedFlow<ConversationError> = _errors.asSharedFlow()

    // 断网重连自动恢复：onSessionReady 后触发 ViewModel 补发 session.change
    private val _restoreModeRequest = MutableSharedFlow<ModeChangeRequest>(extraBufferCapacity = 8)
    val restoreModeRequest: SharedFlow<ModeChangeRequest> = _restoreModeRequest.asSharedFlow()

    private val _translateMode = MutableStateFlow(false)
    val translateMode: StateFlow<Boolean> = _translateMode.asStateFlow()

    private val _lectureMode = MutableStateFlow(false)
    val lectureMode: StateFlow<Boolean> = _lectureMode.asStateFlow()

    private val _transcribeMode = MutableStateFlow(false)
    val transcribeMode: StateFlow<Boolean> = _transcribeMode.asStateFlow()

    private val _omniMode = MutableStateFlow(false)
    val omniMode: StateFlow<Boolean> = _omniMode.asStateFlow()

    private val _streamingAiText = MutableStateFlow("")
    val streamingAiText: StateFlow<String> = _streamingAiText.asStateFlow()

    private val _agentJobs = MutableStateFlow<List<AgentJob>>(emptyList())
    val agentJobs: StateFlow<List<AgentJob>> = _agentJobs.asStateFlow()

    // 跨线程：Main（setSessionId/clearSession）写、WS reader 线程（onTranscription/
    // onAiText/onSessionReady）读
    @Volatile private var currentSessionId: String? = null

    /**
     * 服务端下发的 session_id（`sess_<18>`，协议 §session.start 的形状要求）。
     * 重连自动恢复时由 [dev.livachat.app.domain.ConversationCoordinator] 读取作为
     * resume_id：本地 Room id 是 UUID，不能直接回传。onSessionReady 写入、
     * clearSession（启停/换会话）清除，故重连窗口内保持可用。
     */
    @Volatile
    var serverSessionId: String? = null
        private set

    private var messageSeq = 0
    private val messageCache = mutableMapOf<String, ChatMessage>()
    private var clearJob: Job? = null
    @Volatile var suppressMessages: Boolean = false

    // Written on Main (observer scope), read on the WS reader thread (onJobStatus).
    @Volatile
    private var lastConnectionState: ConnectionState = ConnectionState.DISCONNECTED

    // §6.9：session.change 已发出、回声未到（乐观窗口）。记录待回声的模式
    // （"translate" / "transcribe" / "omni"）。回声前收到 1009（切换被拒专属码）→
    // 按模式回落；其余错误与本次切换无关，不得回落、不得清 pending（由回声结算）。
    @Volatile
    private var pendingModeChange: String? = null

    companion object {
        // 协议 §4.3：session.change 被拒专用错误码。服务端拒绝时不再发 4001/4003，
        // 且请求不会再有 session.changed 回声 —— 本码是唯一的"切换失败"信号。
        private const val SESSION_CHANGE_FAILED = 1009
    }

    // 用户最近一次主动请求的模式（VM confirm 分支写入、off 分支清除）。与回声
    // 镜像 flag 不同，它跨 session.ready 存活——重连后据此自动恢复 translate/transcribe。
    @Volatile
    private var lastModeRequest: ModeChangeRequest? = null

    fun setSessionId(sessionId: String, startSeq: Int = 0) {
        currentSessionId = sessionId
        messageSeq = startSeq
        messageCache.clear()
    }

    fun setTranslateMode(enabled: Boolean) {
        _translateMode.value = enabled
    }

    fun setLectureMode(enabled: Boolean) {
        _lectureMode.value = enabled
    }

    fun setTranscribeMode(enabled: Boolean) {
        _transcribeMode.value = enabled
    }

    fun setOmniMode(enabled: Boolean) {
        _omniMode.value = enabled
    }

    /** Mark an in-flight session.change(mode) awaiting server echo (§6.9). */
    fun markModeChangePending(mode: String?) {
        pendingModeChange = mode
    }

    /** 记录用户最近一次主动模式请求（null = 回到 chat 默认，无需恢复）。 */
    fun rememberModeRequest(request: ModeChangeRequest?) {
        lastModeRequest = request
    }

    fun clearSession() {
        currentSessionId = null
        serverSessionId = null
        messageSeq = 0
        messageCache.clear()
        pendingModeChange = null
        lastModeRequest = null
        _translateMode.value = false
        _lectureMode.value = false
        _transcribeMode.value = false
        _omniMode.value = false
        clearJob?.cancel()
        _transcription.value = ""
        _isTranscriptionFinal.value = false
        _streamingAiText.value = ""
        _agentJobs.value = emptyList()
    }

    override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {
        if (suppressMessages) return
        // D4 守卫：断连后迟到的非 final 帧不得复活已清理的气泡（final 放行，仍入史）
        if (lastConnectionState != ConnectionState.CONNECTED && !isFinal) return
        // Cancel any pending clear from the previous utterance so it doesn't
        // wipe out the new transcription text.
        clearJob?.cancel()

        val inTranslateMode = _translateMode.value
        val inTranscribeMode = _transcribeMode.value
        _transcription.value = text
        _isTranscriptionFinal.value = isFinal

        if (isFinal && text.isNotBlank()) {
            val sessionId = currentSessionId ?: return
            // §2.3.3 D4=B：正文原样落库、模式标记随行；后缀由 MessageBubble 按当前语言展示时拼
            val mode = when {
                inTranscribeMode -> ChatMessage.MODE_TRANSCRIBE
                inTranslateMode -> ChatMessage.MODE_TRANSLATE
                else -> null
            }
            val message = ChatMessage(
                id = "user-$transcriptionId",
                sessionId = sessionId,
                role = MessageRole.USER,
                text = text,
                isFinal = true,
                seq = messageSeq++,
                timestamp = Instant.now(),
                mode = mode,
            )
            scope.launch { chatRepository.appendMessage(message) }
            // Defer clearing so the UI has a frame to consume the final text.
            clearJob = scope.launch {
                _transcription.value = ""
                _isTranscriptionFinal.value = false
                _streamingAiText.value = ""
            }
        }
    }

    override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: dev.livachat.sdk.model.AiImage?, source: String?) {
        if (suppressMessages) return
        // 同上传写守卫（D4）：防断连后迟到 partial 复活 lecture 第二行
        if (lastConnectionState != ConnectionState.CONNECTED && !isFinal) return
        val sessionId = currentSessionId ?: return
        val id = "ai-$messageId"

        // Server sends individual token deltas. Accumulate them into full text.
        val accumulated = if (index == 0L) {
            text
        } else {
            (messageCache[id]?.text ?: "") + text
        }

        // 空行折叠已停用：Markwon 解析表格要求表头前有空行，\n{2,}->\n 会破坏表格渲染。
        // 未来若想恢复紧凑段落样式可重新启用，同时需回归 table 渲染用例。
        // val normalized = accumulated.replace(Regex("\\n{2,}"), "\n")
        val normalized = accumulated

        val inTranslateMode = _translateMode.value
        // §2.3.3 D4=B：正文原样落库（含流式增量重建）、模式标记随行；后缀展示时本地化拼
        val mode = if (inTranslateMode && isFinal) ChatMessage.MODE_TRANSLATE else null
        val message = ChatMessage(
            id = id,
            sessionId = sessionId,
            role = MessageRole.AI,
            text = normalized,
            isFinal = isFinal,
            seq = messageSeq,
            timestamp = Instant.now(),
            image = image ?: messageCache[id]?.image,
            source = source ?: messageCache[id]?.source,
            mode = mode,
        )
        messageCache[id] = message
        _streamingAiText.value = normalized

        if (isFinal) {
            messageSeq++
            if (normalized.isNotBlank()) {
                scope.launch { chatRepository.appendMessage(message) }
            }
        }
    }

    override fun onSessionReady(sessionId: String, restored: Boolean) {
        // 仅在本地会话有效期内登记：clearSession()（End/换会话，主线程）之后到达的
        // 迟到 session.ready（WS reader 线程）不得复活旧服务端 id——否则下次 start()
        // 会 resume 上一个已结束的会话（两种到达顺序下都收敛为 null）
        if (currentSessionId != null) {
            serverSessionId = sessionId
        }
        // Keep the local session ID (set via setSessionId) for Room FK consistency.
        // The server's session ID may differ from the local UUID.
        if (currentSessionId == null) {
            currentSessionId = sessionId
        }
        if (!restored) {
            messageCache.clear()
        }
        // fd_pipeline_transcribe_mode.md §4.6.4：服务端每次 session.start（含断线
        // 重连/恢复）都把模式无条件重置回 chat——这里同步复位模式开关，否则残留
        // 的 transcribe/translate 标志会让本会话后续轮按错误模式显示（如加"(转录)"
        // 后缀而服务端实际在跑 chat + LLM）。
        pendingModeChange = null
        _translateMode.value = false
        _lectureMode.value = false
        _transcribeMode.value = false

        // 断网重连自动恢复：若重连前用户处于 translate/transcribe 模式，按用户
        // 意图乐观置位并请求 ViewModel 补发 session.change（回声为准，失败回落）。
        val request = lastModeRequest ?: return
        when (request.mode) {
            "translate" -> {
                _translateMode.value = true
                _lectureMode.value = request.usecase == "lecture"
            }
            "transcribe" -> {
                _transcribeMode.value = true
            }
            "omni" -> {
                _omniMode.value = true
            }
            else -> return
        }
        pendingModeChange = request.mode
        _restoreModeRequest.tryEmit(request)
    }

    override fun onConnectionStateChanged(state: ConnectionState) {
        lastConnectionState = state
        // D6: disconnect/self-clear — covers server silent corners (§2.8): relay
        // restart, lost session.started first frame, tick not covering dirty-but-empty.
        if (state != ConnectionState.CONNECTED) {
            _agentJobs.value = emptyList()
            // 断连后 final 不再达，重连恢复也不补发：清掉无法收尾的转写气泡
            //（含 lecture 第二行）
            clearJob?.cancel()
            _transcription.value = ""
            _isTranscriptionFinal.value = false
            _streamingAiText.value = ""
        }
    }

    override fun onSessionEnded(reason: String, finalSessionId: String) {
        // D6: session.ended self-clear — the server may end the session without
        // closing the connection, leaving the list stale until disconnect otherwise.
        _agentJobs.value = emptyList()
        // 服务端已结束该会话：不得在后续重连时把它当 resume 目标
        serverSessionId = null
    }

    override fun onJobStatus(jobs: List<AgentJob>) {
        // Full replace semantics; job status is device-level transient state,
        // not a chat message — not gated by suppressMessages (D8).
        // Drop frames arriving after a disconnect: onConnectionStateChanged runs
        // on Main while onJobStatus runs on the WS reader thread, so a frame in
        // flight during the transition could otherwise land after the D6 clear
        // and resurrect a ghost list.
        if (lastConnectionState != ConnectionState.CONNECTED) return
        _agentJobs.value = jobs
    }
    override fun onSessionStateChanged(state: SessionState) {}
    override fun onAiAudioPcm(
        pcm: ShortArray, sampleRate: Int, sampleBits: Int, durationMs: Int,
        sequenceNumber: Long, turnId: String,
    ) {}
    override fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {}
    override fun onVideoAck(frameId: Long, processed: Boolean) {}

    override fun onSessionChanged(mode: String, myLanguage: String?, localLanguage: String?, usecase: String?) {
        pendingModeChange = null
        _translateMode.value = mode == "translate"
        _lectureMode.value = mode == "translate" && usecase == "lecture"
        _transcribeMode.value = mode == "transcribe"
        _omniMode.value = mode == "omni"
    }

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
}
