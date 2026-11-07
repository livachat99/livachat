package dev.livachat.app.ui.screens.conversation

import androidx.lifecycle.ViewModelStore
import dev.livachat.app.data.repository.AgentJobRepository
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.ServerConfig
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
import io.mockk.coVerify
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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** 一次性 AI 提示：显示一次、停留到关闭、关闭后持久化（对齐 iOS ConversationView.aiNoticeView）。 */
class ConversationViewModelAiNoticeTest {

    private lateinit var settingsRepository: SettingsRepository
    private val stores = mutableListOf<ViewModelStore>()

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        // VM init 的 viewModelScope 协程永不自行结束；clear 取消 scope，避免跨测试泄漏
        stores.forEach { it.clear() }
        stores.clear()
        Dispatchers.resetMain()
    }

    private fun createViewModel(aiNoticeShown: Boolean): ConversationViewModel {
        val prefsFlow = MutableStateFlow(UserPreferences(aiGeneratedNoticeShown = aiNoticeShown))

        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        val audioCapture = mockk<AudioCaptureController>(relaxed = true)
        every { audioCapture.isMuted } returns MutableStateFlow(false)
        every { audioCapture.preferredInputDeviceId } returns MutableStateFlow<Int?>(null)
        val audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        every { audioPlayback.isMuted } returns MutableStateFlow(false)
        every { audioPlayback.preferredOutputDeviceId } returns MutableStateFlow<Int?>(null)
        val deviceManager = mockk<AudioDeviceManager>(relaxed = true)
        every { deviceManager.inputDevices } returns MutableStateFlow(emptyList())
        every { deviceManager.outputDevices } returns MutableStateFlow(emptyList())

        val sdk = mockk<LivaChatSDK>(relaxed = true)
        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        every { sdk.sessionState } returns MutableStateFlow(SessionState.IDLE)

        val cameraController = mockk<CameraController>(relaxed = true)
        every { cameraController.isFrontCamera } returns MutableStateFlow(true)
        val screenCaptureController = mockk<ScreenCaptureController>(relaxed = true)
        every { screenCaptureController.isRecording } returns MutableStateFlow(false)
        every { screenCaptureController.startErrors } returns MutableSharedFlow()

        settingsRepository = mockk(relaxed = true)
        every { settingsRepository.isFirstLaunch } returns MutableStateFlow(false)
        every { settingsRepository.observePreferences() } returns prefsFlow
        every { settingsRepository.observeServerConfig() } returns
            flowOf(ServerConfig("example.com:9000", "dev-livachat-token"))
        val coordinator = mockk<ConversationCoordinator>(relaxed = true)
        every { coordinator.currentSessionId } returns MutableStateFlow<String?>(null)

        return ConversationViewModel(
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
            deviceManager = deviceManager,
            agentJobRepository = mockk<AgentJobRepository>(relaxed = true),
        ).also { vm ->
            ViewModelStore().apply {
                put("vm-${stores.size}", vm)
                stores += this
            }
        }
    }

    @Test
    fun `notice shows when preference is false`() {
        val viewModel = createViewModel(aiNoticeShown = false)

        assertTrue(viewModel.uiState.value.showAiGeneratedNotice)
    }

    @Test
    fun `dismiss hides notice and persists preference`() {
        val viewModel = createViewModel(aiNoticeShown = false)

        viewModel.onEvent(ConversationEvent.DismissAiGeneratedNotice)

        assertFalse(viewModel.uiState.value.showAiGeneratedNotice)
        coVerify { settingsRepository.setAiGeneratedNoticeShown() }
    }

    @Test
    fun `notice does not show when preference is true`() {
        val viewModel = createViewModel(aiNoticeShown = true)

        assertFalse(viewModel.uiState.value.showAiGeneratedNotice)
    }
}
