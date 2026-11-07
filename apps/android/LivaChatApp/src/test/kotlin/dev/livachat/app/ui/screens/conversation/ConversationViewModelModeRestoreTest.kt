package dev.livachat.app.ui.screens.conversation

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
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * 断网重连自动恢复：adapter.onSessionReady 触发 restoreModeRequest，
 * VM 补发 session.change（confirm 已发 1 次 + 恢复补发 1 次 = 共 2 次同参调用）。
 */
class ConversationViewModelModeRestoreTest {

    private lateinit var sdk: LivaChatSDK
    private lateinit var adapter: ConversationListenerAdapter
    private lateinit var viewModel: ConversationViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        adapter = ConversationListenerAdapter(mockk(relaxed = true))
        sdk = mockk<LivaChatSDK>(relaxed = true)

        val audioCapture = mockk<AudioCaptureController>(relaxed = true)
        val audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        val cameraController = mockk<CameraController>(relaxed = true)
        val screenCaptureController = mockk<ScreenCaptureController>(relaxed = true)
        val settingsRepository = mockk<SettingsRepository>(relaxed = true)
        val coordinator = mockk<ConversationCoordinator>(relaxed = true)

        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        every { sdk.sessionState } returns MutableStateFlow(SessionState.IDLE)
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

    private fun simulateReconnect() {
        adapter.onSessionReady("sess-2", restored = true)
    }

    @Test
    fun reconnect_after_transcribe_confirm_resends_transcribe_change() {
        viewModel.onEvent(ConversationEvent.ToggleTranscribeMode)
        viewModel.onEvent(ConversationEvent.ConfirmTranscribeSource(TranscribeAudioSource.VOICE))
        assertTrue(viewModel.uiState.value.isTranscribeMode)

        simulateReconnect()

        // confirm 1 次 + 恢复补发 1 次
        verify(exactly = 2) { sdk.sendSessionChange("transcribe", null, null, null) }
        assertTrue(viewModel.uiState.value.isTranscribeMode)
    }

    @Test
    fun reconnect_after_translate_confirm_resends_translate_change() {
        viewModel.onEvent(ConversationEvent.ConfirmTranslateLanguage("ja"))

        simulateReconnect()

        verify(exactly = 2) { sdk.sendSessionChange("translate", any(), "ja", "dialogue") }
        assertTrue(viewModel.uiState.value.isTranslateMode)
    }

    @Test
    fun reconnect_after_lecture_confirm_resends_lecture_change() {
        viewModel.onEvent(ConversationEvent.ConfirmLectureLanguage("ja"))

        simulateReconnect()

        verify(exactly = 2) { sdk.sendSessionChange("translate", any(), "ja", "lecture") }
        assertTrue(viewModel.uiState.value.isTranslateMode)
        assertTrue(viewModel.uiState.value.isLectureMode)
    }

    @Test
    fun reconnect_after_auto_translate_confirm_resends_auto() {
        // 对话模式「自动识别」：localLanguage 保留字 auto 原样下发 + 重连重放
        viewModel.onEvent(ConversationEvent.ConfirmTranslateLanguage("auto"))

        simulateReconnect()

        verify(exactly = 2) { sdk.sendSessionChange("translate", any(), "auto", "dialogue") }
    }
}
