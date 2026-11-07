package dev.livachat.app.ui.screens.conversation

import android.net.Uri
import androidx.lifecycle.ViewModelStore
import dev.livachat.app.data.repository.AgentJobRepository
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.ConversationError
import dev.livachat.app.domain.model.ServerConfig
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.media.AudioCaptureController
import dev.livachat.app.media.AudioDeviceManager
import dev.livachat.app.media.AudioPlaybackController
import dev.livachat.app.media.CameraController
import dev.livachat.app.media.CaptureTurnCounter
import dev.livachat.app.media.GalleryPhotoSender
import dev.livachat.app.media.GallerySendResult
import dev.livachat.app.media.HapticController
import dev.livachat.app.media.ImageGallerySaver
import dev.livachat.app.media.OverlayPermissions
import dev.livachat.app.media.ScreenCaptureController
import dev.livachat.app.testStringContext
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * video turn 递增矩阵（docs/fd_pipeline_video_process.md §8.2.2）：动作点递增——开相机 /
 * autoCameraOnJoin（+ 录屏开始见 ScreenCaptureController）；关相机不递增；
 * **前后切换不在动作点递增**（P2-1：下沉到 CameraController 的"切换后首帧 facing
 * 变化检测"，见 CameraControllerTest.shouldBumpTurn 矩阵）；自动重绑（回前台/页面重进）
 * 同镜头 facing 不变 → 天然不递增。
 */
class ConversationViewModelVideoTurnTest {

    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher()

    private lateinit var adapter: ConversationListenerAdapter
    private lateinit var sdk: LivaChatSDK
    private lateinit var screenCapture: ScreenCaptureController
    private lateinit var overlayPermissions: OverlayPermissions
    private lateinit var cameraController: CameraController
    private lateinit var turnCounter: CaptureTurnCounter
    private lateinit var galleryPhotoSender: GalleryPhotoSender
    private lateinit var audioCapture: AudioCaptureController
    private lateinit var audioPlayback: AudioPlaybackController
    private lateinit var settingsRepository: SettingsRepository
    private lateinit var coordinator: ConversationCoordinator
    private lateinit var viewModel: ConversationViewModel
    private lateinit var sessionStateFlow: MutableStateFlow<SessionState>
    private lateinit var recordingFlow: MutableStateFlow<Boolean>
    private lateinit var startErrorsFlow: MutableSharedFlow<String>
    private val stores = mutableListOf<ViewModelStore>()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        adapter = ConversationListenerAdapter(mockk(relaxed = true))
        sdk = mockk<LivaChatSDK>(relaxed = true)
        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        // 初始非 IDLE：避免 VM init 的 D11 联动 collector 立即触发一次 stop()
        sessionStateFlow = MutableStateFlow(SessionState.LISTENING)
        every { sdk.sessionState } returns sessionStateFlow

        screenCapture = mockk<ScreenCaptureController>(relaxed = true)
        overlayPermissions = mockk<OverlayPermissions>(relaxed = true)
        every { overlayPermissions.canDraw() } returns true
        recordingFlow = MutableStateFlow(false)
        every { screenCapture.isRecording } returns recordingFlow
        startErrorsFlow = MutableSharedFlow(extraBufferCapacity = 4)
        every { screenCapture.startErrors } returns startErrorsFlow

        cameraController = mockk<CameraController>(relaxed = true)
        every { cameraController.isFrontCamera } returns MutableStateFlow(true)
        // 真实计数（非 mock）：断言 next() 的递增语义与"不递增"路径更行为化
        turnCounter = CaptureTurnCounter()
        galleryPhotoSender = mockk(relaxed = true)

        // 未 stub 的 relaxed StateFlow 永不发射，会卡死 VM 的 combine 镜像链
        audioCapture = mockk<AudioCaptureController>(relaxed = true)
        every { audioCapture.isMuted } returns MutableStateFlow(false)
        every { audioCapture.preferredInputDeviceId } returns MutableStateFlow<Int?>(null)
        audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        every { audioPlayback.isMuted } returns MutableStateFlow(false)
        every { audioPlayback.preferredOutputDeviceId } returns MutableStateFlow<Int?>(null)

        settingsRepository = mockk<SettingsRepository>(relaxed = true)
        every { settingsRepository.isFirstLaunch } returns MutableStateFlow(false)
        every { settingsRepository.observePreferences() } returns flowOf(UserPreferences())
        // 服务器配置门控（setting_server_address.md §3.4）：OnScreenStart 决策依赖首发射，缺 stub 会延后启动
        every { settingsRepository.observeServerConfig() } returns
            flowOf(ServerConfig("example.com:8080", "dev-livachat-token"))

        // VM init 会对 coordinator.currentSessionId 执行 collect；
        // relaxed mock 的 StateFlow 上调用 collect 会抛 KotlinNothingValueException
        coordinator = mockk<ConversationCoordinator>(relaxed = true)
        every { coordinator.currentSessionId } returns MutableStateFlow<String?>(null)

        viewModel = createViewModel()
    }

    @After
    fun tearDown() {
        // VM init 的 viewModelScope 协程永不自行结束；clear 触发 onCleared 取消 scope
        stores.forEach { it.clear() }
        stores.clear()
        Dispatchers.resetMain()
    }

    private fun createViewModel(): ConversationViewModel {
        val vm = ConversationViewModel(
            appContext = testStringContext(),
            sdk = sdk,
            audioCapture = audioCapture,
            audioPlayback = audioPlayback,
            cameraController = cameraController,
            galleryPhotoSender = galleryPhotoSender,
            turnCounter = turnCounter,
            screenCaptureController = screenCapture,
            overlayPermissions = overlayPermissions,
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
        ViewModelStore().apply {
            put("vm-${stores.size}", vm)
            stores += this
        }
        return vm
    }

    @Test
    fun `toggleCamera open bumps video turn`() {
        viewModel.onEvent(ConversationEvent.ToggleCamera)

        assertTrue(viewModel.uiState.value.isCameraOn)
        assertEquals(1L, turnCounter.current)
    }

    @Test
    fun `toggleCamera close does not bump video turn`() {
        viewModel.onEvent(ConversationEvent.ToggleCamera)  // open → 1
        viewModel.onEvent(ConversationEvent.ToggleCamera)  // close → 不递增

        assertFalse(viewModel.uiState.value.isCameraOn)
        assertEquals(1L, turnCounter.current)
    }

    @Test
    fun `switchCamera does not bump at action point (bump moved to first-frame facing detection)`() {
        viewModel.onEvent(ConversationEvent.ToggleCamera)  // open → 1
        viewModel.onEvent(ConversationEvent.SwitchCamera)  // 动作点不递增（P2-1，见 CameraControllerTest）

        assertEquals(1L, turnCounter.current)
    }

    @Test
    fun `autoCameraOnJoin bumps video turn`() {
        every { settingsRepository.observePreferences() } returns
            flowOf(UserPreferences(autoCameraOnJoin = true))
        val vm = createViewModel()

        vm.onEvent(ConversationEvent.OnScreenStart)

        assertTrue(vm.uiState.value.isCameraOn)
        assertEquals(1L, turnCounter.current)
    }

    @Test
    fun `gallery photo FAILED result shows error banner`() {
        coEvery { galleryPhotoSender.send(any(), any(), any()) } returns GallerySendResult.FAILED

        viewModel.onEvent(ConversationEvent.SendGalleryPhoto(mockk<Uri>()))

        assertTrue(viewModel.uiState.value.error is ConversationError.Unknown)
    }

    @Test
    fun `gallery photo REJECTED result is silent`() {
        coEvery { galleryPhotoSender.send(any(), any(), any()) } returns GallerySendResult.REJECTED

        viewModel.onEvent(ConversationEvent.SendGalleryPhoto(mockk<Uri>()))

        assertNull(viewModel.uiState.value.error)
    }

    @Test
    fun `open gallery preview stores uri`() {
        val uri = mockk<Uri>()

        viewModel.onEvent(ConversationEvent.OpenGalleryPreview(uri))

        assertEquals(uri, viewModel.uiState.value.galleryPreviewUri)
    }

    @Test
    fun `dismiss gallery preview clears uri without sending`() {
        val uri = mockk<Uri>()
        viewModel.onEvent(ConversationEvent.OpenGalleryPreview(uri))

        viewModel.onEvent(ConversationEvent.DismissGalleryPreview)

        assertNull(viewModel.uiState.value.galleryPreviewUri)
        coVerify(exactly = 0) { galleryPhotoSender.send(any(), any(), any()) }
    }

    @Test
    fun `send gallery photo clears preview and calls sender once`() {
        val uri = mockk<Uri>()
        coEvery { galleryPhotoSender.send(any(), any(), any()) } returns GallerySendResult.SENT
        viewModel.onEvent(ConversationEvent.OpenGalleryPreview(uri))

        viewModel.onEvent(ConversationEvent.SendGalleryPhoto(uri))

        assertNull(viewModel.uiState.value.galleryPreviewUri)
        coVerify(exactly = 1) { galleryPhotoSender.send(uri, false, any()) }
    }
}
