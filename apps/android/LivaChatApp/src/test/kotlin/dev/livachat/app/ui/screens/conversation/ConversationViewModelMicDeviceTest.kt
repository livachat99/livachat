package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.data.repository.AgentJobRepository
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.media.AudioCaptureController
import dev.livachat.app.media.AudioDeviceManager
import dev.livachat.app.media.AudioInputDevice
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
 * docs/android_audio_input_devices.md §7.1 ⑧：麦克风设备选择事件与状态镜像——
 * Show/Dismiss 开关弹窗；SelectMicInputDevice 关窗并下发控制器（id=null=系统默认）；
 * manager.inputDevices / controller.preferredInputDeviceId 镜像进 UiState。
 */
class ConversationViewModelMicDeviceTest {

    private lateinit var audioCapture: AudioCaptureController
    private lateinit var deviceManager: AudioDeviceManager
    private lateinit var viewModel: ConversationViewModel

    private val devicesFlow = MutableStateFlow<List<AudioInputDevice>>(emptyList())
    private val preferredFlow = MutableStateFlow<Int?>(null)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        audioCapture = mockk<AudioCaptureController>(relaxed = true)
        every { audioCapture.isCapturing } returns MutableStateFlow(true)
        every { audioCapture.isMuted } returns MutableStateFlow(false)
        every { audioCapture.preferredInputDeviceId } returns preferredFlow
        deviceManager = mockk<AudioDeviceManager>(relaxed = true)
        every { deviceManager.inputDevices } returns devicesFlow
        every { deviceManager.outputDevices } returns MutableStateFlow(emptyList())

        val sdk = mockk<LivaChatSDK>(relaxed = true)
        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        every { sdk.sessionState } returns MutableStateFlow(SessionState.IDLE)

        val audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        val cameraController = mockk<CameraController>(relaxed = true)
        val screenCaptureController = mockk<ScreenCaptureController>(relaxed = true)
        val settingsRepository = mockk<SettingsRepository>(relaxed = true)
        val coordinator = mockk<ConversationCoordinator>(relaxed = true)

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
            deviceManager = deviceManager,
            agentJobRepository = mockk<AgentJobRepository>(relaxed = true),
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `device list mirrors into UiState`() {
        val dev = AudioInputDevice(id = 13, type = 15, productName = "LIO-AN00", address = "bottom")
        devicesFlow.value = listOf(dev)

        assertEquals(listOf(dev), viewModel.uiState.value.micInputDevices)
    }

    @Test
    fun `show then dismiss toggles picker visibility`() {
        viewModel.onEvent(ConversationEvent.ShowMicDevicePicker)
        assertTrue(viewModel.uiState.value.showMicDevicePicker)

        viewModel.onEvent(ConversationEvent.DismissMicDevicePicker)
        assertFalse(viewModel.uiState.value.showMicDevicePicker)
    }

    @Test
    fun `select device closes picker and forwards id to controller`() {
        viewModel.onEvent(ConversationEvent.ShowMicDevicePicker)
        viewModel.onEvent(ConversationEvent.SelectMicInputDevice(7))

        assertFalse(viewModel.uiState.value.showMicDevicePicker)
        verify { audioCapture.setPreferredInputDevice(7) }
    }

    @Test
    fun `select default forwards null to controller`() {
        viewModel.onEvent(ConversationEvent.SelectMicInputDevice(null))

        verify { audioCapture.setPreferredInputDevice(null) }
    }

    @Test
    fun `selected device id mirrors from controller flow`() {
        preferredFlow.value = 5

        assertEquals(5, viewModel.uiState.value.selectedMicInputDeviceId)
    }
}
