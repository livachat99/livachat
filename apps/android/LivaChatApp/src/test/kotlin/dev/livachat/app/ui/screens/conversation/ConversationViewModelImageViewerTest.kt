package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.R
import dev.livachat.app.data.repository.AgentJobRepository
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
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
import dev.livachat.sdk.model.AiImage
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import java.time.Instant
import kotlinx.coroutines.CompletableDeferred
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
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class ConversationViewModelImageViewerTest {

    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher()

    private lateinit var viewModel: ConversationViewModel
    private lateinit var sessionId: MutableStateFlow<String?>
    private lateinit var messages: MutableStateFlow<List<ChatMessage>>
    private lateinit var saver: ImageGallerySaver

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        sessionId = MutableStateFlow(null)
        messages = MutableStateFlow(emptyList())
        saver = mockk(relaxed = true)
        viewModel = createViewModel()
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun createViewModel(): ConversationViewModel {
        val sdk = mockk<LivaChatSDK>(relaxed = true)
        val audioCapture = mockk<AudioCaptureController>(relaxed = true)
        val audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
        val cameraController = mockk<CameraController>(relaxed = true)
        val screenCaptureController = mockk<ScreenCaptureController>(relaxed = true)
        val settingsRepository = mockk<SettingsRepository>(relaxed = true)
        val coordinator = mockk<ConversationCoordinator>(relaxed = true)
        val chatRepository = mockk<ChatRepository>(relaxed = true)

        every { sdk.connectionState } returns MutableStateFlow(ConnectionState.DISCONNECTED)
        every { sdk.sessionState } returns MutableStateFlow(SessionState.IDLE)
        every { audioCapture.isMuted } returns MutableStateFlow(false)
        every { audioCapture.preferredInputDeviceId } returns MutableStateFlow<Int?>(null)
        every { audioPlayback.isMuted } returns MutableStateFlow(false)
        every { audioPlayback.preferredOutputDeviceId } returns MutableStateFlow<Int?>(null)
        every { cameraController.isFrontCamera } returns MutableStateFlow(true)
        every { screenCaptureController.isRecording } returns MutableStateFlow(false)
        every { screenCaptureController.startErrors } returns MutableSharedFlow()
        every { settingsRepository.isFirstLaunch } returns MutableStateFlow(false)
        every { settingsRepository.observePreferences() } returns flowOf(UserPreferences())
        every { coordinator.currentSessionId } returns sessionId
        every { chatRepository.observeMessages(any()) } returns messages

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
            chatRepository = chatRepository,
            settingsRepository = settingsRepository,
            listenerAdapter = ConversationListenerAdapter(mockk(relaxed = true)),
            haptic = mockk<HapticController>(relaxed = true),
            coordinator = coordinator,
            avatarRepository = mockk<AvatarRepository>(relaxed = true),
            imageGallerySaver = saver,
            deviceManager = mockk<AudioDeviceManager>(relaxed = true).also { m -> every { m.inputDevices } returns MutableStateFlow(emptyList()); every { m.outputDevices } returns MutableStateFlow(emptyList()) },
            agentJobRepository = mockk<AgentJobRepository>(relaxed = true),
        )
    }

    private fun imageMessage(id: String, seq: Int = 0) = ChatMessage(
        id = id, sessionId = "s1", role = MessageRole.AI, text = "图片 $id",
        isFinal = true, seq = seq, timestamp = Instant.now(),
        image = AiImage(data = "x", mime = "image/jpeg", width = 1, height = 1),
    )

    private fun startSessionWith(vararg msgs: ChatMessage) {
        sessionId.value = "sess-1"
        messages.value = msgs.toList()
    }

    // ── 查看器开关 ──────────────────────────────────────────────────

    @Test
    fun imageViewerMessageId_defaults_to_null() {
        assertNull(viewModel.uiState.value.imageViewerMessageId)
    }

    @Test
    fun openImageViewer_sets_message_id() {
        viewModel.onEvent(ConversationEvent.OpenImageViewer("ai-1"))
        assertEquals("ai-1", viewModel.uiState.value.imageViewerMessageId)
    }

    @Test
    fun closeImageViewer_clears_message_id() {
        viewModel.onEvent(ConversationEvent.OpenImageViewer("ai-1"))
        viewModel.onEvent(ConversationEvent.CloseImageViewer)
        assertNull(viewModel.uiState.value.imageViewerMessageId)
    }

    @Test
    fun session_switch_clears_viewer_state() {
        sessionId.value = "sess-1"
        viewModel.onEvent(ConversationEvent.OpenImageViewer("ai-1"))

        sessionId.value = "sess-2"

        assertNull(viewModel.uiState.value.imageViewerMessageId)
    }

    @Test
    fun session_end_clears_viewer_state() {
        sessionId.value = "sess-1"
        viewModel.onEvent(ConversationEvent.OpenImageViewer("ai-1"))

        sessionId.value = null

        assertNull(viewModel.uiState.value.imageViewerMessageId)
    }

    // ── 保存到相册（§9.6）──────────────────────────────────────────

    @Test
    fun saveViewerImage_success_showsTip_andClearsAfterTtl() {
        startSessionWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } returns true

        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))

        assertEquals("res:${R.string.common_saved_to_gallery}", viewModel.uiState.value.imageSaveTip)
        dispatcher.scheduler.advanceTimeBy(2_001)
        assertNull(viewModel.uiState.value.imageSaveTip)
    }

    @Test
    fun saveViewerImage_failure_showsFailureTip() {
        startSessionWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } returns false

        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))

        assertEquals("res:${R.string.common_save_failed}", viewModel.uiState.value.imageSaveTip)
    }

    @Test
    fun saveViewerImage_saverThrows_collapsesToFailureTip_notCrash() {
        startSessionWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } throws RuntimeException("boom")

        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))

        assertEquals("违约异常须收敛为失败提示（runCatching 本地化契约）", "res:${R.string.common_save_failed}", viewModel.uiState.value.imageSaveTip)
        // finally 已复位重入 flag：再次点击仍能进入保存（共两次调用）
        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))
        coVerify(exactly = 2) { saver.save(any()) }
    }

    @Test
    fun saveViewerImage_unknownId_noSaverCall_noTip() {
        startSessionWith(imageMessage("ai-1"))

        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-999"))

        coVerify(exactly = 0) { saver.save(any()) }
        assertNull(viewModel.uiState.value.imageSaveTip)
    }

    @Test
    fun saveViewerImage_reentryDuringInFlight_secondClickIgnored() {
        startSessionWith(imageMessage("ai-1"))
        val gate = CompletableDeferred<Boolean>()
        coEvery { saver.save(any()) } coAnswers { gate.await() }

        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))
        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))
        gate.complete(true)
        dispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 1) { saver.save(any()) }
    }

    @Test
    fun consecutiveSaves_secondTipSurvivesFullTtl() {
        startSessionWith(imageMessage("ai-1"), imageMessage("ai-2", seq = 1))
        coEvery { saver.save(any()) } returns true

        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))   // t=0：提示 A（定时器 2000 到期）
        dispatcher.scheduler.advanceTimeBy(500)
        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-2"))   // t=500：提示 B（同文案），A 的定时器须被取消
        dispatcher.scheduler.advanceTimeBy(1_700)                       // t=2200：越过 A 的到期点

        assertEquals(
            "第二次保存的提示不得被第一次的定时器提前清掉",
            "res:${R.string.common_saved_to_gallery}", viewModel.uiState.value.imageSaveTip,
        )

        dispatcher.scheduler.advanceTimeBy(400)                         // t=2600：B 的到期点（2500）之后
        assertNull(viewModel.uiState.value.imageSaveTip)
    }

    @Test
    fun savePermissionDenied_showsTip_andClearsAfterTtl() {
        viewModel.onEvent(ConversationEvent.SaveViewerImagePermissionDenied)

        assertEquals("res:${R.string.common_save_failed_storage}", viewModel.uiState.value.imageSaveTip)
        dispatcher.scheduler.advanceTimeBy(2_001)
        assertNull(viewModel.uiState.value.imageSaveTip)
    }

    @Test
    fun openImageViewer_clearsStaleTip() {
        startSessionWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } returns true
        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))
        assertEquals("res:${R.string.common_saved_to_gallery}", viewModel.uiState.value.imageSaveTip)

        viewModel.onEvent(ConversationEvent.OpenImageViewer("ai-1"))   // 2s 内重开查看器

        assertNull(viewModel.uiState.value.imageSaveTip)
    }

    @Test
    fun sessionSwitch_clearsStaleTip() {
        startSessionWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } returns true
        viewModel.onEvent(ConversationEvent.SaveViewerImage("ai-1"))
        assertEquals("res:${R.string.common_saved_to_gallery}", viewModel.uiState.value.imageSaveTip)

        sessionId.value = "sess-2"

        assertNull(viewModel.uiState.value.imageSaveTip)
    }
}
