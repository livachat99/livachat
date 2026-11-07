package dev.livachat.app.ui.screens.conversation

import android.app.Activity
import android.content.Intent
import androidx.lifecycle.ViewModelStore
import dev.livachat.app.data.repository.AgentJobRepository
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.ConversationError
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
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ConversationViewModelScreenCaptureTest {

    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher()

    private lateinit var adapter: ConversationListenerAdapter
    private lateinit var sdk: LivaChatSDK
    private lateinit var screenCapture: ScreenCaptureController
    private lateinit var overlayPermissions: OverlayPermissions
    private lateinit var cameraController: CameraController
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
        // 污染各用例的 interaction 计数
        sessionStateFlow = MutableStateFlow(SessionState.LISTENING)
        every { sdk.sessionState } returns sessionStateFlow

        screenCapture = mockk<ScreenCaptureController>(relaxed = true)
        // 默认已授予悬浮窗权限：v1 时代的既有用例语义不变（缺权限分支见下方专用用例）
        overlayPermissions = mockk<OverlayPermissions>(relaxed = true)
        every { overlayPermissions.canDraw() } returns true
        recordingFlow = MutableStateFlow(false)
        every { screenCapture.isRecording } returns recordingFlow
        startErrorsFlow = MutableSharedFlow(extraBufferCapacity = 4)
        every { screenCapture.startErrors } returns startErrorsFlow

        cameraController = mockk<CameraController>(relaxed = true)
        every { cameraController.isFrontCamera } returns MutableStateFlow(true)

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

        // VM init 会对 coordinator.currentSessionId 执行 collect；
        // relaxed mock 的 StateFlow 上调用 collect 会抛 KotlinNothingValueException
        // 成为未捕获异常，污染同 JVM 内后续 runTest（UncaughtExceptionsBeforeTest）
        coordinator = mockk<ConversationCoordinator>(relaxed = true)
        every { coordinator.currentSessionId } returns MutableStateFlow<String?>(null)

        viewModel = createViewModel()
    }

    @After
    fun tearDown() {
        // VM init 的 viewModelScope 协程（combine/collect）永不自行结束；
        // clear 触发 onCleared 以取消 scope，避免协程泄漏串扰同 JVM 内后续测试类
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
            turnCounter = CaptureTurnCounter(),
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

    // ── 首次 / 非首次分流（D10） ────────────────────────────────────

    @Test
    fun `toggleRecording first time shows rationale dialog`() {
        viewModel.onEvent(ConversationEvent.ToggleRecording)

        val state = viewModel.uiState.value
        assertTrue(state.showScreenRecordRationale)
        assertFalse(state.pendingScreenCaptureLaunch)
        verify(exactly = 0) { screenCapture.stop() }
    }

    @Test
    fun `toggleRecording after rationale shown goes straight to consent launch`() {
        every { settingsRepository.observePreferences() } returns
            flowOf(UserPreferences(screenRecordRationaleShown = true))
        val vm = createViewModel()

        vm.onEvent(ConversationEvent.ToggleRecording)

        val state = vm.uiState.value
        assertFalse(state.showScreenRecordRationale)
        assertTrue(state.pendingScreenCaptureLaunch)
    }

    @Test
    fun `toggleRecording while recording opens stop confirmation instead of stopping`() {
        recordingFlow.value = true

        viewModel.onEvent(ConversationEvent.ToggleRecording)

        verify(exactly = 0) { screenCapture.stop() }
        assertTrue(viewModel.uiState.value.showStopRecordingConfirm)
        assertFalse(viewModel.uiState.value.showScreenRecordRationale)
        assertFalse(viewModel.uiState.value.pendingScreenCaptureLaunch)
    }

    @Test
    fun `toggleRecording with camera on turns camera off first`() {
        // camera_enhance 后 uiState.isCameraOn 由事件路径（乐观写）驱动，
        // 不再经 controller 流镜像——先经 ToggleCamera 打开相机模拟 UI 点击
        viewModel.onEvent(ConversationEvent.ToggleCamera)
        assertTrue(viewModel.uiState.value.isCameraOn)

        viewModel.onEvent(ConversationEvent.ToggleRecording)

        verify { cameraController.unbind() }
        val state = viewModel.uiState.value
        assertEquals(VideoVisibility.HIDDEN, state.videoVisibility)
        assertFalse(state.isCameraOn)
        assertTrue(state.showScreenRecordRationale)
    }

    // ── rationale 确认 / 取消 ───────────────────────────────────────

    @Test
    fun `confirmRationale persists preference and signals consent launch`() {
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        viewModel.onEvent(ConversationEvent.ConfirmScreenRecordRationale)

        coVerify { settingsRepository.setScreenRecordRationaleShown() }
        val state = viewModel.uiState.value
        assertFalse(state.showScreenRecordRationale)
        assertTrue(state.pendingScreenCaptureLaunch)

        // 再次点击不再走 rationale（本地标志已翻转，不等 DataStore 回写）
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        assertFalse(viewModel.uiState.value.showScreenRecordRationale)
    }

    @Test
    fun `dismissRationale closes dialog without launching`() {
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        viewModel.onEvent(ConversationEvent.DismissScreenRecordRationale)

        val state = viewModel.uiState.value
        assertFalse(state.showScreenRecordRationale)
        assertFalse(state.pendingScreenCaptureLaunch)
    }

    // ── v2：悬浮窗权限三段式（docs/android_screen_recording.md §3） ────

    @Test
    fun `toggleRecording without overlay permission shows rationale even if shown before`() {
        every { overlayPermissions.canDraw() } returns false
        every { settingsRepository.observePreferences() } returns
            flowOf(UserPreferences(screenRecordRationaleShown = true))
        val vm = createViewModel()

        vm.onEvent(ConversationEvent.ToggleRecording)

        val state = vm.uiState.value
        assertTrue(state.showScreenRecordRationale)
        assertFalse(state.pendingScreenCaptureLaunch)
        assertFalse(state.pendingOverlayPermissionLaunch)
    }

    @Test
    fun `confirmRationale without overlay permission requests overlay settings page`() {
        every { overlayPermissions.canDraw() } returns false

        viewModel.onEvent(ConversationEvent.ToggleRecording)
        viewModel.onEvent(ConversationEvent.ConfirmScreenRecordRationale)

        val state = viewModel.uiState.value
        assertFalse(state.showScreenRecordRationale)
        assertFalse(state.pendingScreenCaptureLaunch)
        assertTrue(state.pendingOverlayPermissionLaunch)
    }

    @Test
    fun `overlayPermissionReturned granted continues to consent launch`() {
        every { overlayPermissions.canDraw() } returns false
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        viewModel.onEvent(ConversationEvent.ConfirmScreenRecordRationale)
        assertTrue(viewModel.uiState.value.pendingOverlayPermissionLaunch)

        // 用户在系统设置页开启后返回 → VM 复查通过，继续投屏授权
        every { overlayPermissions.canDraw() } returns true
        viewModel.onEvent(ConversationEvent.OverlayPermissionReturned)

        val state = viewModel.uiState.value
        assertTrue(state.pendingScreenCaptureLaunch)
        assertNull(state.error)
    }

    @Test
    fun `overlayPermissionReturned denied shows error and does not launch`() {
        every { overlayPermissions.canDraw() } returns false
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        viewModel.onEvent(ConversationEvent.ConfirmScreenRecordRationale)

        viewModel.onEvent(ConversationEvent.OverlayPermissionReturned)

        val state = viewModel.uiState.value
        assertFalse(state.pendingScreenCaptureLaunch)
        assertTrue(state.error is ConversationError.Unknown)
    }

    @Test
    fun `consumeOverlayPermissionLaunch clears the pending flag`() {
        every { overlayPermissions.canDraw() } returns false
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        viewModel.onEvent(ConversationEvent.ConfirmScreenRecordRationale)
        assertTrue(viewModel.uiState.value.pendingOverlayPermissionLaunch)

        viewModel.onEvent(ConversationEvent.ConsumeOverlayPermissionLaunch)

        assertFalse(viewModel.uiState.value.pendingOverlayPermissionLaunch)
    }

    // ── 一次性启动信号与授权结果 ────────────────────────────────────

    @Test
    fun `consumeScreenCaptureLaunch clears the pending flag`() {
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        viewModel.onEvent(ConversationEvent.ConfirmScreenRecordRationale)
        assertTrue(viewModel.uiState.value.pendingScreenCaptureLaunch)

        viewModel.onEvent(ConversationEvent.ConsumeScreenCaptureLaunch)
        assertFalse(viewModel.uiState.value.pendingScreenCaptureLaunch)
    }

    @Test
    fun `startScreenCapture forwards consent result to controller`() {
        viewModel.startScreenCapture(Activity.RESULT_OK, mockk<Intent>(relaxed = true))

        verify { screenCapture.start(any(), any()) }
    }

    @Test
    fun `consentDenied is silent and stays stopped`() {
        viewModel.onEvent(ConversationEvent.ScreenCaptureConsentDenied)

        val state = viewModel.uiState.value
        assertFalse(state.isRecording)
        assertNull(state.error)
        assertFalse(state.showScreenRecordRationale)
        assertFalse(state.pendingScreenCaptureLaunch)
    }

    // ── 停止确认弹窗（录制中点击按钮 → 确认后停） ───────────────────

    @Test
    fun `confirmStopScreenRecord stops recording and closes dialog`() {
        recordingFlow.value = true
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        assertTrue(viewModel.uiState.value.showStopRecordingConfirm)

        viewModel.onEvent(ConversationEvent.ConfirmStopScreenRecord)

        verify { screenCapture.stop() }
        assertFalse(viewModel.uiState.value.showStopRecordingConfirm)
    }

    @Test
    fun `dismissStopScreenRecord closes dialog and keeps recording`() {
        recordingFlow.value = true
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        assertTrue(viewModel.uiState.value.showStopRecordingConfirm)

        viewModel.onEvent(ConversationEvent.DismissStopScreenRecord)

        verify(exactly = 0) { screenCapture.stop() }
        assertFalse(viewModel.uiState.value.showStopRecordingConfirm)
        assertTrue(viewModel.uiState.value.isRecording)
    }

    @Test
    fun `stop confirmation flag resets when recording stops externally`() {
        // 确认框打开后录制被非确认路径停掉（相机互斥/系统停投屏）→ 标志必须复位，
        // 否则残留 true 会在下次开录瞬间无点击自动弹出确认框
        recordingFlow.value = true
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        assertTrue(viewModel.uiState.value.showStopRecordingConfirm)

        recordingFlow.value = false

        assertFalse(viewModel.uiState.value.showStopRecordingConfirm)

        // 再次开录不自动弹确认框
        recordingFlow.value = true
        assertFalse(viewModel.uiState.value.showStopRecordingConfirm)
    }

    @Test
    fun `confirmStop when recording already stopped externally is a no-op`() {
        // 系统侧停投屏等路径已把录制停掉（isRecording=false），陈旧弹窗的
        // "停止"确认不得触发第二次 stop（幂等之外的重复调用防护）
        recordingFlow.value = true
        viewModel.onEvent(ConversationEvent.ToggleRecording)
        assertTrue(viewModel.uiState.value.showStopRecordingConfirm)
        recordingFlow.value = false

        viewModel.onEvent(ConversationEvent.ConfirmStopScreenRecord)

        verify(exactly = 0) { screenCapture.stop() }
        assertFalse(viewModel.uiState.value.showStopRecordingConfirm)
    }

    // ── 互斥（D9） ──────────────────────────────────────────────────

    @Test
    fun `toggleCamera while recording stops recording then turns camera on`() {
        recordingFlow.value = true

        viewModel.onEvent(ConversationEvent.ToggleCamera)

        verify { screenCapture.stop() }
        assertTrue(viewModel.uiState.value.isCameraOn)
        assertEquals(VideoVisibility.VISIBLE, viewModel.uiState.value.videoVisibility)
    }

    @Test
    fun `recording state flows into uiState`() {
        recordingFlow.value = true
        assertTrue(viewModel.uiState.value.isRecording)

        recordingFlow.value = false
        assertFalse(viewModel.uiState.value.isRecording)
    }

    // ── 联动停录（D11） ─────────────────────────────────────────────

    @Test
    fun `session returning to IDLE stops recording`() {
        verify(exactly = 0) { screenCapture.stop() }

        sessionStateFlow.value = SessionState.IDLE

        verify { screenCapture.stop() }
    }

    @Test
    fun `viewModel onCleared stops recording`() {
        verify(exactly = 0) { screenCapture.stop() }

        stores.last().clear()

        verify { screenCapture.stop() }
    }
}
