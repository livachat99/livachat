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
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * fd_pipeline_transcribe_mode.md §10 用例 16/18：转录开关互斥、弹窗
 * 确认/取消、ServerError 失败回落。
 */
class ConversationViewModelTranscribeModeTest {

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

    private fun enableTranscribe(source: TranscribeAudioSource = TranscribeAudioSource.VOICE) {
        viewModel.onEvent(ConversationEvent.ToggleTranscribeMode)
        viewModel.onEvent(ConversationEvent.ConfirmTranscribeSource(source))
    }

    @Test
    fun toggle_transcribe_when_off_opens_source_picker_only() {
        viewModel.onEvent(ConversationEvent.ToggleTranscribeMode)

        assertTrue(viewModel.uiState.value.showTranscribeSourcePicker)
        assertFalse(viewModel.uiState.value.isTranscribeMode)
        verify(exactly = 0) { sdk.sendSessionChange(any(), any(), any(), any()) }
    }

    @Test
    fun confirm_source_enables_transcribe_and_sends_change() {
        enableTranscribe(TranscribeAudioSource.RAW)

        val state = viewModel.uiState.value
        assertTrue(state.isTranscribeMode)
        assertFalse(state.isTranslateMode)
        assertFalse(state.isLectureMode)
        assertEquals(TranscribeAudioSource.RAW, state.transcribeAudioSource)
        assertFalse(state.showTranscribeSourcePicker)
        assertFalse(state.showAvatarSheet)
        assertTrue(adapter.transcribeMode.value)
        verify { sdk.sendSessionChange("transcribe", null, null, null) }
    }

    @Test
    fun dismiss_picker_does_not_enter_transcribe() {
        viewModel.onEvent(ConversationEvent.ToggleTranscribeMode)
        viewModel.onEvent(ConversationEvent.DismissTranscribeSourcePicker)

        assertFalse(viewModel.uiState.value.showTranscribeSourcePicker)
        assertFalse(viewModel.uiState.value.isTranscribeMode)
        assertFalse(adapter.transcribeMode.value)
        verify(exactly = 0) { sdk.sendSessionChange(any(), any(), any(), any()) }
    }

    @Test
    fun toggle_off_resets_all_modes_and_sends_chat() {
        enableTranscribe()

        viewModel.onEvent(ConversationEvent.ToggleTranscribeMode)

        val state = viewModel.uiState.value
        assertFalse(state.isTranscribeMode)
        assertFalse(state.isTranslateMode)
        assertFalse(state.isLectureMode)
        assertFalse(adapter.transcribeMode.value)
        assertFalse(adapter.translateMode.value)
        assertFalse(adapter.lectureMode.value)
        verify { sdk.sendSessionChange("chat", null, null, null) }
    }

    @Test
    fun enabling_translate_turns_transcribe_off() {
        // 三向互斥：translate 确认后 transcribe 全复位
        enableTranscribe()
        assertTrue(viewModel.uiState.value.isTranscribeMode)

        viewModel.onEvent(ConversationEvent.ConfirmTranslateLanguage("en"))

        val state = viewModel.uiState.value
        assertFalse(state.isTranscribeMode)
        assertTrue(state.isTranslateMode)
        assertFalse(adapter.transcribeMode.value)
        verify { sdk.sendSessionChange("translate", any(), "en", "dialogue") }
    }

    @Test
    fun server_error_rolls_back_transcribe_switch() {
        // §6.9：服务端 1009 拒绝 → 转录开关乐观置位回落为关
        enableTranscribe()
        assertTrue(viewModel.uiState.value.isTranscribeMode)

        adapter.onError(code = 1009, message = "session not started", fatal = false, recoverable = false)

        assertFalse(viewModel.uiState.value.isTranscribeMode)
        assertFalse(adapter.transcribeMode.value)
    }
}
