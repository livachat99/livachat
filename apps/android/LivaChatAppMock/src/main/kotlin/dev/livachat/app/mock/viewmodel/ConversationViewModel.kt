package dev.livachat.app.mock.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.livachat.app.mock.BuildConfig
import dev.livachat.app.mock.media.AudioCaptureController
import dev.livachat.app.mock.media.AudioFocusManager
import dev.livachat.app.mock.media.AudioPlaybackController
import dev.livachat.app.mock.media.CameraController
import dev.livachat.app.mock.media.InterruptCoordinator
import dev.livachat.sdk.LivaChatConfig
import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.AudioSampleRate
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import dev.livachat.sdk.model.VadSensitivity
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class ConversationViewModel(application: Application) : AndroidViewModel(application) {

    // ── Server config (runtime-overridable) ─────────────────────────
    var serverUrl: String = BuildConfig.DEFAULT_SERVER_URL
    var token: String = BuildConfig.DEFAULT_TOKEN

    // ── SDK (recreated when serverUrl/token changes) ────────────────
    lateinit var sdk: LivaChatSDK
        private set

    // ── Media ───────────────────────────────────────────────────────
    private val audioFocus = AudioFocusManager(application)
    val playback = AudioPlaybackController()
    lateinit var capture: AudioCaptureController
        private set
    lateinit var camera: CameraController
        private set

    private var initialized = false

    // ── UI state ────────────────────────────────────────────────────

    data class UiState(
        val connectionState: ConnectionState = ConnectionState.DISCONNECTED,
        val sessionState: SessionState = SessionState.IDLE,
        val transcription: String = "",
        val aiText: String = "",
        val messages: List<ChatMessage> = emptyList(),
        val isCapturing: Boolean = false,
        val errorMessage: String? = null,
    )

    data class ChatMessage(
        val text: String,
        val isUser: Boolean,
        val isFinal: Boolean = true,
    )

    private val _uiState = MutableStateFlow(UiState())
    val uiState: StateFlow<UiState> = _uiState.asStateFlow()

    // ── Init / re-init ──────────────────────────────────────────────

    fun init(lifecycleOwner: androidx.lifecycle.LifecycleOwner) {
        if (initialized) return
        initialized = true

        initSdk()
        observeSdkState()
    }

    /** Rebuild SDK with current serverUrl/token (e.g. after user changes config). */
    fun reconnect() {
        sdk.disconnect()
        initSdk()
        observeSdkState()
    }

    private fun initSdk() {
        val config = LivaChatConfig(
            serverUrl = serverUrl,
            token = token,
            deviceId = java.util.UUID.randomUUID().toString(),
            clientVersion = "android-${BuildConfig.VERSION_NAME}.${BuildConfig.VERSION_CODE}",
            audioSampleRate = AudioSampleRate.RATE_16000,
            vadSensitivity = VadSensitivity.MEDIUM,
        )

        sdk = LivaChatSDK(config, SdkListener())

        capture = AudioCaptureController(sdk).apply {
            onError = { msg ->
                _uiState.value = _uiState.value.copy(errorMessage = msg)
            }
        }

        InterruptCoordinator(sdk, playback, viewModelScope).start()
    }

    var cameraInitialized: Boolean = false
        private set

    fun initCamera(application: Application, lifecycleOwner: androidx.lifecycle.LifecycleOwner) {
        camera = CameraController(sdk, application, lifecycleOwner)
        cameraInitialized = true
    }

    private fun observeSdkState() {
        viewModelScope.launch {
            sdk.connectionState.collect { state ->
                _uiState.value = _uiState.value.copy(
                    connectionState = state,
                    errorMessage = if (state == ConnectionState.DISCONNECTED)
                        _uiState.value.errorMessage else null,
                )
            }
        }
        viewModelScope.launch {
            sdk.sessionState.collect { state ->
                _uiState.value = _uiState.value.copy(sessionState = state)
            }
        }
    }

    // ── Public actions ──────────────────────────────────────────────

    fun connect() {
        audioFocus.request()
        sdk.connect()
    }

    fun disconnect() {
        audioFocus.abandon()
        sdk.disconnect()
        // Force immediate UI update (observerScope may be slow on some devices)
        _uiState.value = _uiState.value.copy(connectionState = ConnectionState.DISCONNECTED)
    }

    fun startSession() {
        playback.start()
        sdk.startSession()
    }

    fun toggleCapture() {
        if (capture.isCapturing) {
            capture.stop()
            _uiState.value = _uiState.value.copy(isCapturing = false)
        } else {
            capture.start()
            _uiState.value = _uiState.value.copy(isCapturing = true)
        }
    }

    fun dismissError() {
        _uiState.value = _uiState.value.copy(errorMessage = null)
    }

    // ── SDK Listener ────────────────────────────────────────────────

    private inner class SdkListener : LivaChatListener {
        override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {
            val msgs = _uiState.value.messages.toMutableList()
            msgs.removeAll { !it.isFinal && it.isUser }
            msgs.add(ChatMessage(text = text, isUser = true, isFinal = isFinal))
            _uiState.value = _uiState.value.copy(
                transcription = if (isFinal) "" else text,
                messages = msgs,
            )
        }

        override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: dev.livachat.sdk.model.AiImage?, source: String?) {
            val current = _uiState.value.aiText
            val appended = if (index == 0L) text else current + text
            _uiState.value = _uiState.value.copy(aiText = appended)
            if (isFinal) {
                val msgs = _uiState.value.messages.toMutableList()
                msgs.add(ChatMessage(text = appended, isUser = false))
                _uiState.value = _uiState.value.copy(messages = msgs, aiText = "")
            }
        }

        override fun onAiAudioPcm(
            pcm: ShortArray, sampleRate: Int, sampleBits: Int,
            durationMs: Int, sequenceNumber: Long, turnId: String,
        ) = playback.onAiAudioPcm(pcm, sampleRate, sampleBits, durationMs, sequenceNumber, turnId)

        override fun onPlaybackShouldFlush(
            reason: dev.livachat.sdk.model.PlaybackFlushReason,
        ) = playback.onPlaybackShouldFlush(reason)

        override fun onSessionStateChanged(state: SessionState) {
            _uiState.value = _uiState.value.copy(sessionState = state)
        }

        override fun onConnectionStateChanged(state: ConnectionState) {
            _uiState.value = _uiState.value.copy(connectionState = state)
        }

        override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {
            _uiState.value = _uiState.value.copy(errorMessage = "[$code] $message")
        }
    }

    override fun onCleared() {
        super.onCleared()
        capture.stop()
        playback.release()
        sdk.disconnect()
        audioFocus.abandon()
    }
}
