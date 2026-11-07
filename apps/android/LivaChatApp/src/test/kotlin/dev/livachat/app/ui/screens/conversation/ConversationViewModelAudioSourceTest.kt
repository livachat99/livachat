package dev.livachat.app.ui.screens.conversation

import android.media.MediaRecorder
import dev.livachat.app.data.repository.AgentJobRepository
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.media.AudioCaptureController
import dev.livachat.app.media.AudioDeviceManager
import dev.livachat.app.media.AudioPlaybackController
import dev.livachat.app.media.CameraController
import dev.livachat.app.media.CaptureTurnCounter
import dev.livachat.app.media.HapticController
import dev.livachat.app.media.ImageGallerySaver
import dev.livachat.app.media.OverlayPermissions
import dev.livachat.app.media.ScreenCaptureController
import dev.livachat.app.testStringContext
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * docs/android_audio_capture.md §7.1 VM 用例 8-12：UiState 派生观测下发采集源。
 *
 * 派生观测下 controller 会收到 VOICE 调用（初始 emission / 归位），
 * 断言走捕获参数序列而非"零调用"。
 */
class ConversationViewModelAudioSourceTest {

    private lateinit var audioCapture: AudioCaptureController
    private lateinit var adapter: ConversationListenerAdapter
    private lateinit var viewModel: ConversationViewModel
    private val switchCalls = mutableListOf<Int>()

    private val voice = MediaRecorder.AudioSource.VOICE_COMMUNICATION
    private val mic = MediaRecorder.AudioSource.MIC

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        adapter = ConversationListenerAdapter(mockk(relaxed = true))
        audioCapture = mockk<AudioCaptureController>(relaxed = true)
        every { audioCapture.switchAudioSource(capture(switchCalls)) } returns Unit
        every { audioCapture.isCapturing } returns MutableStateFlow(true)

        val sdk = mockk<LivaChatSDK>(relaxed = true)
        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        every { sdk.sessionState } returns MutableStateFlow(SessionState.IDLE)

        val audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        val cameraController = mockk<CameraController>(relaxed = true)
        val screenCaptureController = mockk<ScreenCaptureController>(relaxed = true)
        val settingsRepository = mockk<SettingsRepository>(relaxed = true)
        val coordinator = mockk<ConversationCoordinator>(relaxed = true)

        every { audioCapture.isMuted } returns MutableStateFlow(false)
        every { audioCapture.preferredInputDeviceId } returns MutableStateFlow<Int?>(null)
        every { audioPlayback.isMuted } returns MutableStateFlow(false)
        every { audioPlayback.preferredOutputDeviceId } returns MutableStateFlow<Int?>(null)
        every { cameraController.isCameraOn } returns MutableStateFlow(false)
        every { cameraController.isFrontCamera } returns MutableStateFlow(true)
        every { screenCaptureController.isRecording } returns MutableStateFlow(false)
        every { screenCaptureController.startErrors } returns MutableSharedFlow()
        every { settingsRepository.isFirstLaunch } returns MutableStateFlow(false)
        every { settingsRepository.observePreferences() } returns flowOf(UserPreferences())
        every { coordinator.currentSessionId } returns MutableStateFlow<String?>(null)

        viewModel = ConversationViewModel(
            appContext = testStringContext(),
            sdk = sdk,
            audioCapture = audioCapture,
            audioPlayback = audioPlayback,
            cameraController = cameraController,
            galleryPhotoSender = mockk(relaxed = true),
            turnCounter = CaptureTurnCounter(),
            screenCaptureController = screenCaptureController,
            overlayPermissions = mockk<OverlayPermissions>(relaxed = true),
            chatRepository = mockk<ChatRepository>(relaxed = true),
            settingsRepository = settingsRepository,
            listenerAdapter = adapter,
            haptic = mockk<HapticController>(relaxed = true),
            coordinator = coordinator,
            avatarRepository = mockk<AvatarRepository>(relaxed = true),
            imageGallerySaver = mockk<ImageGallerySaver>(relaxed = true),
            deviceManager = mockk<AudioDeviceManager>(relaxed = true).also { m -> every { m.inputDevices } returns MutableStateFlow(emptyList()); every { m.outputDevices } returns MutableStateFlow(emptyList()) },
            agentJobRepository = mockk<AgentJobRepository>(relaxed = true),
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun enableTranscribe(source: TranscribeAudioSource) {
        viewModel.onEvent(ConversationEvent.ToggleTranscribeMode)
        viewModel.onEvent(ConversationEvent.ConfirmTranscribeSource(source))
    }

    // ── 11. 初始 emission ────────────────────────────────────────

    @Test
    fun `collector start emits initial VOICE`() {
        // controller 幂等短路，初始 (false, VOICE) → VOICE 一次无害调用
        assertTrue(switchCalls.first() == voice)
    }

    // ── 8. 确认 RAW/VOICE ────────────────────────────────────────

    @Test
    fun `confirm RAW switches to MIC`() {
        enableTranscribe(TranscribeAudioSource.RAW)
        assertTrue(switchCalls.last() == mic)
    }

    @Test
    fun `confirm VOICE keeps VOICE and never uses MIC`() {
        enableTranscribe(TranscribeAudioSource.VOICE)
        assertEquals(voice, switchCalls.last())
        assertTrue(switchCalls.none { it == mic })
    }

    // ── 9. flag→false 归位 VOICE ─────────────────────────────────

    @Test
    fun `toggling off after RAW returns to VOICE`() {
        enableTranscribe(TranscribeAudioSource.RAW)
        assertTrue(switchCalls.last() == mic)

        viewModel.onEvent(ConversationEvent.ToggleTranscribeMode)

        assertEquals(voice, switchCalls.last())
    }

    @Test
    fun `switching to translate after RAW returns to VOICE`() {
        enableTranscribe(TranscribeAudioSource.RAW)
        viewModel.onEvent(ConversationEvent.ConfirmTranslateLanguage("ja"))
        assertEquals(voice, switchCalls.last())
    }

    @Test
    fun `clearSession after RAW returns to VOICE`() {
        // 离开会话/新建会话（clearSession 清请求 + 清 flag）→ 终态归位 VOICE
        enableTranscribe(TranscribeAudioSource.RAW)
        assertTrue(switchCalls.last() == mic)

        adapter.clearSession()

        assertEquals(voice, switchCalls.last())
    }

    // ── 10. 重连自动恢复（RAW 记忆）→ 回到 MIC ───────────────────

    @Test
    fun `reconnect restore with RAW returns to MIC`() {
        enableTranscribe(TranscribeAudioSource.RAW)
        assertTrue(switchCalls.last() == mic)

        // 同一 onSessionReady 内：ready 复位 → VOICE，随后自动恢复（记忆 RAW）
        // → transcribeMode=true + UiState 残留 RAW → MIC；最终应为 MIC
        adapter.onSessionReady("sess-2", restored = true)

        assertTrue(switchCalls.last() == mic)
        // 恢复前确实经过一次 VOICE（说明不是"从未复位"）
        val firstMic = switchCalls.indexOfFirst { it == mic }
        assertTrue(firstMic in 0 until switchCalls.lastIndex)
        assertTrue(switchCalls.subList(firstMic + 1, switchCalls.size).contains(voice))
    }

    // ── 12. 依赖锁定：承重链 adapter → UiState → 派生观测 ─────────

    @Test
    fun `MIC requires RAW in UiState even when transcribe mode on`() {
        // 开启转录但选「语音」：transcribeMode=true 但 source=VOICE → 永不 MIC
        enableTranscribe(TranscribeAudioSource.VOICE)

        // 模拟回声重复置位 transcribeMode（仍 VOICE）
        adapter.setTranscribeMode(true)

        assertTrue(switchCalls.none { it == mic })
    }

    @Test
    fun `adapter-side mode change propagates through UiState to switch`() {
        // §3.3 承重依赖正向锁定：派生观测的唯一触发源是 UiState 快照，而 adapter 侧
        // 变化全靠既有 transcribeMode collector 回写汇聚进 UiState。若未来删除/绕过
        // 该回写（如直写 UiState），此处无新 emission → 测试变红，防静默失效。
        val before = switchCalls.size
        adapter.setTranscribeMode(true)   // 仅经回写 collector → UiState → 派生观测
        assertEquals(voice, switchCalls.last())
        assertTrue(switchCalls.size > before)
    }
}
