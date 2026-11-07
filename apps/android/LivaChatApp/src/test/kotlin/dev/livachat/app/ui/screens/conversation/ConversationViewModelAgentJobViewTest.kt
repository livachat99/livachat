package dev.livachat.app.ui.screens.conversation

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
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import io.mockk.every
import io.mockk.mockk
import java.time.Instant
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

/** agent job 结果全屏查看的状态流（docs/android_agent_job_view.md §4.2/§6）。 */
class ConversationViewModelAgentJobViewTest {

    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher()

    private lateinit var viewModel: ConversationViewModel
    private lateinit var sessionId: MutableStateFlow<String?>
    private lateinit var messages: MutableStateFlow<List<ChatMessage>>

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        sessionId = MutableStateFlow(null)
        messages = MutableStateFlow(emptyList())
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
            imageGallerySaver = mockk<ImageGallerySaver>(relaxed = true),
            deviceManager = mockk<AudioDeviceManager>(relaxed = true).also { m -> every { m.inputDevices } returns MutableStateFlow(emptyList()); every { m.outputDevices } returns MutableStateFlow(emptyList()) },
            agentJobRepository = mockk<AgentJobRepository>(relaxed = true),
        )
    }

    private fun jobResultMessage(id: String = "ai-msg_agent_job_jk1", seq: Int = 0) = ChatMessage(
        id = id, sessionId = "s1", role = MessageRole.AI, text = "结果正文",
        isFinal = true, seq = seq, timestamp = Instant.now(), source = "agent",
    )

    @Test
    fun agentJobResultViewId_defaults_to_null() {
        assertNull(viewModel.uiState.value.agentJobResultViewId)
        assertNull(viewModel.uiState.value.agentJobResultInitialCharOffset)
    }

    @Test
    fun openAgentJobResult_sets_message_id() {
        viewModel.onEvent(ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_jk1"))

        assertEquals("ai-msg_agent_job_jk1", viewModel.uiState.value.agentJobResultViewId)
    }

    @Test
    fun openAgentJobResult_carries_charOffset_forLanding() {
        viewModel.onEvent(
            ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_jk1", charOffset = 42),
        )

        assertEquals(42, viewModel.uiState.value.agentJobResultInitialCharOffset)
    }

    @Test
    fun openAgentJobResult_withoutOffset_leavesLandingNull() {
        viewModel.onEvent(ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_jk1"))

        assertNull(viewModel.uiState.value.agentJobResultInitialCharOffset)
    }

    @Test
    fun closeAgentJobResult_clears_message_id_and_offset() {
        viewModel.onEvent(
            ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_jk1", charOffset = 42),
        )

        viewModel.onEvent(ConversationEvent.CloseAgentJobResult)

        assertNull(viewModel.uiState.value.agentJobResultViewId)
        assertNull(viewModel.uiState.value.agentJobResultInitialCharOffset)
    }

    @Test
    fun session_switch_clears_view_state() {
        sessionId.value = "sess-1"
        viewModel.onEvent(
            ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_jk1", charOffset = 42),
        )

        sessionId.value = "sess-2"

        assertNull(viewModel.uiState.value.agentJobResultViewId)
        assertNull(viewModel.uiState.value.agentJobResultInitialCharOffset)
    }

    @Test
    fun session_end_clears_view_state() {
        sessionId.value = "sess-1"
        viewModel.onEvent(ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_jk1"))

        sessionId.value = null

        assertNull(viewModel.uiState.value.agentJobResultViewId)
    }

    @Test
    fun session_switch_clears_both_viewers_together() {
        sessionId.value = "sess-1"
        viewModel.onEvent(ConversationEvent.OpenImageViewer("ai-1"))
        viewModel.onEvent(ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_jk1"))

        sessionId.value = "sess-2"

        assertNull(viewModel.uiState.value.imageViewerMessageId)
        assertNull(viewModel.uiState.value.agentJobResultViewId)
    }

    @Test
    fun jobView_state_survives_until_explicitly_closed() {
        sessionId.value = "sess-1"
        messages.value = listOf(jobResultMessage())

        viewModel.onEvent(ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_jk1"))

        // 新消息到达（覆盖层内容随 Room flow 更新）不影响打开状态
        messages.value = listOf(jobResultMessage(), jobResultMessage("ai-msg_agent_job_jk2", seq = 1))
        assertEquals("ai-msg_agent_job_jk1", viewModel.uiState.value.agentJobResultViewId)
    }
}
