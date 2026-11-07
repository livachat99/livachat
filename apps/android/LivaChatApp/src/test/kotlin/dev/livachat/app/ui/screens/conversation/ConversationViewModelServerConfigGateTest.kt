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
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * 服务器配置门控（docs/setting_server_address.md §3.4 二改）：地址/密钥不齐备时
 * OnScreenStart 不启动连接与采集、弹「去设置」引导弹窗；运行中转为不齐备 → stop 已启动的连接。
 */
class ConversationViewModelServerConfigGateTest {

    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher()

    private lateinit var adapter: ConversationListenerAdapter
    private lateinit var sdk: LivaChatSDK
    private lateinit var screenCapture: ScreenCaptureController
    private lateinit var overlayPermissions: OverlayPermissions
    private lateinit var cameraController: CameraController
    private lateinit var turnCounter: CaptureTurnCounter
    private lateinit var audioCapture: AudioCaptureController
    private lateinit var audioPlayback: AudioPlaybackController
    private lateinit var settingsRepository: SettingsRepository
    private lateinit var coordinator: ConversationCoordinator
    private lateinit var coordinatorActive: MutableStateFlow<Boolean>
    private val stores = mutableListOf<ViewModelStore>()

    private val completeConfig = ServerConfig("example.com:8080", "dev-livachat-token")

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)

        adapter = ConversationListenerAdapter(mockk(relaxed = true))
        sdk = mockk<LivaChatSDK>(relaxed = true)
        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        // 初始非 IDLE：避免 VM init 的 D11 联动 collector 立即触发一次 stop()
        every { sdk.sessionState } returns MutableStateFlow(SessionState.LISTENING)

        screenCapture = mockk<ScreenCaptureController>(relaxed = true)
        overlayPermissions = mockk<OverlayPermissions>(relaxed = true)
        every { overlayPermissions.canDraw() } returns true
        every { screenCapture.isRecording } returns MutableStateFlow(false)
        every { screenCapture.startErrors } returns MutableSharedFlow(extraBufferCapacity = 4)

        cameraController = mockk<CameraController>(relaxed = true)
        every { cameraController.isFrontCamera } returns MutableStateFlow(true)
        turnCounter = CaptureTurnCounter()

        // 未 stub 的 relaxed StateFlow 永不发射，会卡死 VM 的 combine 镜像链（同族测试约定）
        audioCapture = mockk<AudioCaptureController>(relaxed = true)
        every { audioCapture.isMuted } returns MutableStateFlow(false)
        every { audioCapture.preferredInputDeviceId } returns MutableStateFlow<Int?>(null)
        audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        every { audioPlayback.isMuted } returns MutableStateFlow(false)
        every { audioPlayback.preferredOutputDeviceId } returns MutableStateFlow<Int?>(null)

        settingsRepository = mockk<SettingsRepository>(relaxed = true)
        every { settingsRepository.isFirstLaunch } returns MutableStateFlow(false)
        every { settingsRepository.observePreferences() } returns flowOf(UserPreferences())
        every { settingsRepository.observeServerConfig() } returns flowOf(completeConfig)

        coordinator = mockk<ConversationCoordinator>(relaxed = true)
        every { coordinator.currentSessionId } returns MutableStateFlow<String?>(null)
        coordinatorActive = MutableStateFlow(false)
        every { coordinator.isActive } returns coordinatorActive
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
            galleryPhotoSender = mockk(relaxed = true),
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
            deviceManager = mockk<AudioDeviceManager>(relaxed = true).also { m ->
                every { m.inputDevices } returns MutableStateFlow(emptyList())
                every { m.outputDevices } returns MutableStateFlow(emptyList())
            },
            agentJobRepository = mockk<AgentJobRepository>(relaxed = true),
        )
        ViewModelStore().apply {
            put("vm-${stores.size}", vm)
            stores += this
        }
        return vm
    }

    @Test
    fun `incomplete config prompts once on run and not on screen re-entry`() {
        every { settingsRepository.observeServerConfig() } returns flowOf(ServerConfig("", ""))

        val vm = createViewModel()
        // 运行首次检测到不齐备 → 提示一次
        assertTrue(vm.uiState.value.showServerConfigPrompt)
        vm.dismissServerConfigPrompt()
        assertFalse(vm.uiState.value.showServerConfigPrompt)

        // 重进页面不再提示，也不启动连接/采集
        vm.onEvent(ConversationEvent.OnScreenStart)
        assertFalse(vm.uiState.value.showServerConfigPrompt)
        verify(exactly = 0) { coordinator.start() }
        assertFalse(vm.uiState.value.isCameraOn)
    }

    @Test
    fun `retry connection with incomplete config shows prompt instead of connecting`() {
        every { settingsRepository.observeServerConfig() } returns flowOf(ServerConfig("", ""))

        val vm = createViewModel()
        vm.dismissServerConfigPrompt()

        vm.onEvent(ConversationEvent.RetryConnection)

        assertTrue(vm.uiState.value.showServerConfigPrompt)
        verify(exactly = 0) { sdk.connect() }
    }

    @Test
    fun `retry connection with complete config connects without prompt`() {
        val vm = createViewModel()
        assertFalse(vm.uiState.value.showServerConfigPrompt)

        vm.onEvent(ConversationEvent.RetryConnection)

        verify(exactly = 1) { sdk.connect() }
        assertFalse(vm.uiState.value.showServerConfigPrompt)
    }

    @Test
    fun `complete config starts coordinator without prompt`() {
        val vm = createViewModel()
        vm.onEvent(ConversationEvent.OnScreenStart)

        verify(exactly = 1) { coordinator.start() }
        assertFalse(vm.uiState.value.showServerConfigPrompt)
    }

    @Test
    fun `screen start before first emission defers decision until config arrives`() {
        val configFlow = MutableSharedFlow<ServerConfig>(extraBufferCapacity = 1)
        every { settingsRepository.observeServerConfig() } returns configFlow

        val vm = createViewModel()
        vm.onEvent(ConversationEvent.OnScreenStart)
        // 首发射前不决策：不启动、不弹窗（防用未加载默认值误启动占位连接）
        verify(exactly = 0) { coordinator.start() }
        assertFalse(vm.uiState.value.showServerConfigPrompt)

        configFlow.tryEmit(completeConfig)

        verify(exactly = 1) { coordinator.start() }
        assertFalse(vm.uiState.value.showServerConfigPrompt)
    }

    @Test
    fun `deferred start turns into prompt when config arrives incomplete`() {
        val configFlow = MutableSharedFlow<ServerConfig>(extraBufferCapacity = 1)
        every { settingsRepository.observeServerConfig() } returns configFlow

        val vm = createViewModel()
        vm.onEvent(ConversationEvent.OnScreenStart)
        configFlow.tryEmit(ServerConfig("", ""))

        verify(exactly = 0) { coordinator.start() }
        assertTrue(vm.uiState.value.showServerConfigPrompt)
    }

    @Test
    fun `retry connection before config loads is ignored`() {
        val configFlow = MutableSharedFlow<ServerConfig>(extraBufferCapacity = 1)
        every { settingsRepository.observeServerConfig() } returns configFlow

        val vm = createViewModel()
        vm.onEvent(ConversationEvent.RetryConnection)

        // 首发射未到：既不发连接、也不弹窗（防占位/旧默认值误发起）
        verify(exactly = 0) { sdk.connect() }
        assertFalse(vm.uiState.value.showServerConfigPrompt)
    }

    @Test
    fun `config turning incomplete while active stops coordinator`() {
        val configFlow = MutableStateFlow(completeConfig)
        every { settingsRepository.observeServerConfig() } returns configFlow
        coordinatorActive.value = true

        val vm = createViewModel()
        verify(exactly = 0) { coordinator.stop() }

        configFlow.value = ServerConfig("", "")

        verify(exactly = 1) { coordinator.stop() }
        assertTrue(vm.uiState.value.showServerConfigPrompt)
    }
}
