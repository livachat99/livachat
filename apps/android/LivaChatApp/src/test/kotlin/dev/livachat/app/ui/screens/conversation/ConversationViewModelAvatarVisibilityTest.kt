package dev.livachat.app.ui.screens.conversation

import android.net.Uri
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
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * docs/fd_avator_customization.md「头像隐藏到标题栏」：`SetAvatarVisible` 事件处理与
 * prefs 镜像恢复（与 [ConversationViewModelOutputDeviceTest] 同型构造）。
 */
class ConversationViewModelAvatarVisibilityTest {

    private lateinit var viewModel: ConversationViewModel
    private lateinit var settingsRepository: SettingsRepository
    private lateinit var avatarRepository: AvatarRepository
    private val prefsFlow = MutableStateFlow(UserPreferences())

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        val audioCapture = mockk<AudioCaptureController>(relaxed = true)
        every { audioCapture.isCapturing } returns MutableStateFlow(true)
        every { audioCapture.isMuted } returns MutableStateFlow(false)
        every { audioCapture.preferredInputDeviceId } returns MutableStateFlow<Int?>(null)
        val deviceManager = mockk<AudioDeviceManager>(relaxed = true)
        every { deviceManager.inputDevices } returns MutableStateFlow(emptyList())
        every { deviceManager.outputDevices } returns MutableStateFlow(emptyList())
        val audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        every { audioPlayback.isMuted } returns MutableStateFlow(false)
        every { audioPlayback.preferredOutputDeviceId } returns MutableStateFlow<Int?>(null)

        val sdk = mockk<LivaChatSDK>(relaxed = true)
        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        every { sdk.sessionState } returns MutableStateFlow(SessionState.IDLE)

        val cameraController = mockk<CameraController>(relaxed = true)
        every { cameraController.isCameraOn } returns MutableStateFlow(false)
        every { cameraController.isFrontCamera } returns MutableStateFlow(true)
        val screenCaptureController = mockk<ScreenCaptureController>(relaxed = true)
        every { screenCaptureController.isRecording } returns MutableStateFlow(false)
        every { screenCaptureController.startErrors } returns MutableSharedFlow()

        settingsRepository = mockk(relaxed = true)
        every { settingsRepository.isFirstLaunch } returns MutableStateFlow(false)
        every { settingsRepository.observePreferences() } returns prefsFlow
        avatarRepository = mockk(relaxed = true)
        val coordinator = mockk<ConversationCoordinator>(relaxed = true)
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
            avatarRepository = avatarRepository,
            imageGallerySaver = mockk<ImageGallerySaver>(relaxed = true),
            deviceManager = deviceManager,
            agentJobRepository = mockk<AgentJobRepository>(relaxed = true),
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `set avatar invisible updates state keeps sheet open and persists`() {
        viewModel.onEvent(ConversationEvent.ShowAvatarSheet)

        viewModel.onEvent(ConversationEvent.SetAvatarVisible(false))

        assertFalse(viewModel.uiState.value.avatarVisible)
        assertTrue(viewModel.uiState.value.showAvatarSheet)
        coVerify { settingsRepository.setAvatarVisible(false) }
    }

    @Test
    fun `set avatar visible true restores`() {
        viewModel.onEvent(ConversationEvent.SetAvatarVisible(false))
        viewModel.onEvent(ConversationEvent.SetAvatarVisible(true))

        assertTrue(viewModel.uiState.value.avatarVisible)
    }

    @Test
    fun `prefs avatarVisible mirrors into UiState on restore`() {
        prefsFlow.value = UserPreferences(avatarVisible = false)

        assertFalse(viewModel.uiState.value.avatarVisible)
    }

    @Test
    fun `discard camera source forwards to repository`() {
        val uri = mockk<Uri>()

        viewModel.discardCameraSource(uri)

        coVerify { avatarRepository.deleteCameraSourceIfOwned(uri) }
    }
}
