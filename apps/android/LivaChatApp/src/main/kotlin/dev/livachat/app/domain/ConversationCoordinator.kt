package dev.livachat.app.domain

import dev.livachat.app.domain.usecase.SessionUseCase
import dev.livachat.app.media.AppForegroundState
import dev.livachat.app.media.AudioCaptureController
import dev.livachat.app.media.AudioPlaybackController
import dev.livachat.app.media.CameraController
import dev.livachat.app.media.CompositeLivaChatListener
import dev.livachat.app.media.InterruptCoordinator
import dev.livachat.app.ui.screens.conversation.ConversationListenerAdapter
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ConversationCoordinator @Inject constructor(
    private val sdk: LivaChatSDK,
    private val audioCapture: AudioCaptureController,
    private val audioPlayback: AudioPlaybackController,
    private val cameraController: CameraController,
    private val interruptCoordinator: InterruptCoordinator,
    private val sessionUseCase: SessionUseCase,
    private val listenerAdapter: ConversationListenerAdapter,
    private val compositeListener: CompositeLivaChatListener,
    private val appForegroundState: AppForegroundState,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private val _isActive = MutableStateFlow(false)
    val isActive: StateFlow<Boolean> = _isActive.asStateFlow()

    private val _currentSessionId = MutableStateFlow<String?>(null)
    val currentSessionId: StateFlow<String?> = _currentSessionId.asStateFlow()

    private var sessionActive = false
    private var connectionObservationLaunched = false
    private var lifecycleObserveJob: Job? = null

    /**
     * Deletes today's mapping and creates a fresh session, then swaps the
     * coordinator to use it without stopping audio / SDK. Returns the old
     * session ID (which the caller is expected to clean up).
     */
    suspend fun replaceWithNewSession(): String? {
        val oldSessionId = _currentSessionId.value
        val newSession = sessionUseCase.forceNewTodaySession()
        _currentSessionId.value = newSession.id
        listenerAdapter.clearSession()
        listenerAdapter.setSessionId(newSession.id)
        return oldSessionId
    }

    fun start() {
        if (_isActive.value) return

        compositeListener.register(listenerAdapter)
        compositeListener.register(audioPlayback)

        // 前后台门控订阅（docs/android_audio_capture.md §3.6）：先取消旧订阅再注册——
        // 防 start() 快速重入（_isActive 置位在下方 launch 内，存在双调窗口）产生重复 collector
        lifecycleObserveJob?.cancel()
        lifecycleObserveJob = scope.launch {
            appForegroundState.isForeground.collect { fg ->
                if (fg) {
                    audioCapture.onAppForegrounded()
                    // 回前台自愈（docs/android_background_support.md §4.1）：会话存活
                    // 但采集/连接已死时前台重建——采集死态=读错误重试耗尽后停录停 FGS（全静默，
                    // 无其他恢复路径）；连接死态=重试风暴耗尽进 DISCONNECTED。两调用均幂等；
                    // sessionActive 门控保证 stop()（离场/换会话）后不复活。
                    if (sessionActive) {
                        if (!audioCapture.isCapturing.value) {
                            sdk.logInfo("ConversationCoordinator", "foreground self-heal: capture restart")
                            audioCapture.start()
                        }
                        if (sdk.connectionState.value == ConnectionState.DISCONNECTED) {
                            sdk.logInfo("ConversationCoordinator", "foreground self-heal: connect")
                            sdk.connect()
                        }
                    }
                } else {
                    audioCapture.onAppBackgrounded()
                }
            }
        }

        scope.launch {
            val session = sessionUseCase.createOrGetTodaySession()
            val startSeq = sessionUseCase.getNextSeq(session.id)
            _currentSessionId.value = session.id
            listenerAdapter.setSessionId(session.id, startSeq = startSeq)

            sessionActive = true
            sdk.connect()

            audioCapture.start()
            audioPlayback.start()
            interruptCoordinator.start(scope)

            startObservingConnectionForAutoSession()
            _isActive.value = true
        }
    }

    /** 设置“允许 AI 主动搭话”；SDK 自己决定发 session.change(config) 还是只影响下一次 start。 */
    fun setAvdetectorEnabled(enabled: Boolean) {
        sdk.setAvdetectorEnabled(enabled)
    }

    fun stop() {
        // 先取消前后台订阅再走既有停止序列（挂起决策不会晚于 stop 到达；docs/android_audio_capture.md §3.6）
        lifecycleObserveJob?.cancel()
        lifecycleObserveJob = null
        sessionActive = false
        interruptCoordinator.stop()
        audioCapture.stop()
        audioPlayback.stop()
        cameraController.unbind()
        sdk.disconnect()

        val sessionId = _currentSessionId.value
        if (sessionId != null) {
            scope.launch { sessionUseCase.endSession(sessionId) }
        }

        listenerAdapter.clearSession()
        compositeListener.unregister(listenerAdapter)
        compositeListener.unregister(audioPlayback)

        _currentSessionId.value = null
        _isActive.value = false
    }

    private fun startObservingConnectionForAutoSession() {
        if (connectionObservationLaunched) return
        connectionObservationLaunched = true

        scope.launch {
            sdk.connectionEpoch.collect {
                // 代际单调自增：每次成功建连（含 CONNECTED→RECONNECTING→CONNECTED 回归
                // flap）必被观察到——StateFlow 值去重会把这类"回归到已发射值"的整段
                // 变化吞掉（值回归去重）。
                if (sessionActive && sdk.connectionState.value == ConnectionState.CONNECTED) {
                    // 自动恢复沿用服务端下发的 session_id 作为 resume_id：服务端
                    // session_id 延续（不再每轮重连开新会话；recording/agent relay/历史
                    // 全部对齐）。首次连接为 null → 服务端新建会话。
                    // 注意协议要求 session_id 形如 sess_<18>，本地 Room id 是 UUID，
                    // 不能直接回传（见 ConversationListenerAdapter.serverSessionId）。
                    sdk.startSession(listenerAdapter.serverSessionId)
                }
            }
        }
    }
}
