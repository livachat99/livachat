package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.data.repository.AgentJobRepository
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.media.AudioCaptureController
import dev.livachat.app.media.AudioDeviceManager
import dev.livachat.app.media.AudioOutputDevice
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
 * docs/android_audio_output_devices.md §8.1：扬声器输出设备选择事件与状态镜像（与
 * [ConversationViewModelMicDeviceTest] 同型，D2=B 的测试侧镜像）——
 * Show/Dismiss 开关弹窗；SelectOutputDevice 关窗并下发控制器（id=null=系统默认）；
 * manager.outputDevices / controller.preferredOutputDeviceId 镜像进 UiState。
 */
class ConversationViewModelOutputDeviceTest {

    private lateinit var audioPlayback: AudioPlaybackController
    private lateinit var deviceManager: AudioDeviceManager
    private lateinit var viewModel: ConversationViewModel

    private val outputDevicesFlow = MutableStateFlow<List<AudioOutputDevice>>(emptyList())
    private val preferredOutputFlow = MutableStateFlow<Int?>(null)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        val audioCapture = mockk<AudioCaptureController>(relaxed = true)
        every { audioCapture.isCapturing } returns MutableStateFlow(true)
        every { audioCapture.isMuted } returns MutableStateFlow(false)
        every { audioCapture.preferredInputDeviceId } returns MutableStateFlow<Int?>(null)
        deviceManager = mockk<AudioDeviceManager>(relaxed = true)
        every { deviceManager.inputDevices } returns MutableStateFlow(emptyList())
        every { deviceManager.outputDevices } returns outputDevicesFlow
        audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        every { audioPlayback.isMuted } returns MutableStateFlow(false)
        every { audioPlayback.preferredOutputDeviceId } returns preferredOutputFlow

        val sdk = mockk<LivaChatSDK>(relaxed = true)
        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        every { sdk.sessionState } returns MutableStateFlow(SessionState.IDLE)

        val cameraController = mockk<CameraController>(relaxed = true)
        val screenCaptureController = mockk<ScreenCaptureController>(relaxed = true)
        val settingsRepository = mockk<SettingsRepository>(relaxed = true)
        val coordinator = mockk<ConversationCoordinator>(relaxed = true)

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
    fun `output device list mirrors into UiState`() {
        val dev = AudioOutputDevice(id = 3, type = 2, productName = "LIO-AN00", address = null)
        outputDevicesFlow.value = listOf(dev)

        assertEquals(listOf(dev), viewModel.uiState.value.outputDevices)
    }

    @Test
    fun `show then dismiss toggles picker visibility`() {
        viewModel.onEvent(ConversationEvent.ShowOutputDevicePicker)
        assertTrue(viewModel.uiState.value.showOutputDevicePicker)

        viewModel.onEvent(ConversationEvent.DismissOutputDevicePicker)
        assertFalse(viewModel.uiState.value.showOutputDevicePicker)
    }

    @Test
    fun `select device closes picker and forwards id to controller`() {
        viewModel.onEvent(ConversationEvent.ShowOutputDevicePicker)
        viewModel.onEvent(ConversationEvent.SelectOutputDevice(7))

        assertFalse(viewModel.uiState.value.showOutputDevicePicker)
        verify { audioPlayback.setPreferredOutputDevice(7) }
    }

    @Test
    fun `select default forwards null to controller`() {
        viewModel.onEvent(ConversationEvent.SelectOutputDevice(null))

        verify { audioPlayback.setPreferredOutputDevice(null) }
    }

    @Test
    fun `selected output device id mirrors from controller flow`() {
        preferredOutputFlow.value = 5

        assertEquals(5, viewModel.uiState.value.selectedOutputDeviceId)
    }
}
