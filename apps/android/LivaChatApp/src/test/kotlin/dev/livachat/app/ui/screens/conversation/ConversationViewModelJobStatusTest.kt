package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.data.remote.mylife.MylifeApiException
import dev.livachat.app.data.repository.AgentJobRepository
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
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.testStringContext
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ConversationViewModelJobStatusTest {

    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher()

    private lateinit var adapter: ConversationListenerAdapter
    private lateinit var viewModel: ConversationViewModel
    private lateinit var agentJobRepository: AgentJobRepository

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        adapter = ConversationListenerAdapter(mockk(relaxed = true))
        // Mirror production: the SDK forwards CONNECTED via the Main observer
        // scope before job_status frames flow (frames are dropped otherwise).
        adapter.onConnectionStateChanged(ConnectionState.CONNECTED)
        agentJobRepository = mockk(relaxed = true)
        viewModel = createViewModel(adapter, agentJobRepository)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun createViewModel(
        adapter: ConversationListenerAdapter,
        agentJobRepository: AgentJobRepository,
    ): ConversationViewModel {
        val sdk = mockk<LivaChatSDK>(relaxed = true)
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
            chatRepository = mockk<ChatRepository>(relaxed = true),
            settingsRepository = settingsRepository,
            listenerAdapter = adapter,
            haptic = mockk<HapticController>(relaxed = true),
            coordinator = coordinator,
            avatarRepository = mockk<AvatarRepository>(relaxed = true),
            imageGallerySaver = mockk<ImageGallerySaver>(relaxed = true),
            deviceManager = mockk<AudioDeviceManager>(relaxed = true).also { m -> every { m.inputDevices } returns MutableStateFlow(emptyList()); every { m.outputDevices } returns MutableStateFlow(emptyList()) },
            agentJobRepository = agentJobRepository,
        )
    }

    private fun job(id: String) = AgentJob(
        jobId = id, agentName = "hermes", summary = "任务 $id",
        startedAtMs = 1_000L, runningTimeMs = 10L,
    )

    // ── job status ────────────────────────────────────────────────

    @Test
    fun agentJobs_flow_into_uiState() {
        adapter.onJobStatus(listOf(job("job-1"), job("job-2")))
        assertEquals(listOf("job-1", "job-2"), viewModel.uiState.value.agentJobs.map { it.jobId })
    }

    @Test
    fun toggleJobStatusPanel_opens_and_closes() {
        adapter.onJobStatus(listOf(job("job-1")))
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        assertTrue(viewModel.uiState.value.showJobStatusPanel)

        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        assertFalse(viewModel.uiState.value.showJobStatusPanel)
    }

    @Test
    fun toggleJobStatusPanel_with_empty_jobs_does_not_open() {
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        assertFalse(viewModel.uiState.value.showJobStatusPanel)
    }

    @Test
    fun empty_jobs_auto_close_open_panel() {
        adapter.onJobStatus(listOf(job("job-1")))
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        assertTrue(viewModel.uiState.value.showJobStatusPanel)

        adapter.onJobStatus(emptyList())
        assertFalse(viewModel.uiState.value.showJobStatusPanel)
    }

    @Test
    fun dismissJobStatusPanel_closes_panel() {
        adapter.onJobStatus(listOf(job("job-1")))
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        viewModel.onEvent(ConversationEvent.DismissJobStatusPanel)
        assertFalse(viewModel.uiState.value.showJobStatusPanel)
    }

    // ── agent job cancel（docs/android_agent_job_status.md §5.3）────

    private fun requestCancel(jobId: String) {
        viewModel.onEvent(ConversationEvent.RequestAgentJobCancel(job(jobId)))
    }

    @Test
    fun requestCancel_setsTarget() {
        adapter.onJobStatus(listOf(job("job-1")))
        requestCancel("job-1")
        assertEquals("job-1", viewModel.uiState.value.agentJobCancel?.job?.jobId)
        assertFalse(viewModel.uiState.value.agentJobCancel!!.inFlight)
        assertNull(viewModel.uiState.value.agentJobCancel!!.error)
    }

    @Test
    fun requestCancel_idle_switchesTarget() {
        requestCancel("job-1")
        requestCancel("job-2")
        assertEquals("job-2", viewModel.uiState.value.agentJobCancel?.job?.jobId)
    }

    @Test
    fun dismissAgentJobCancel_clearsIdleState() {
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.DismissAgentJobCancel)
        assertNull(viewModel.uiState.value.agentJobCancel)
    }

    @Test
    fun confirm_success_clearsState_andCallsRepository() {
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)
        assertNull(viewModel.uiState.value.agentJobCancel)
        coVerify(exactly = 1) { agentJobRepository.cancel("job-1") }
    }

    @Test
    fun doubleConfirm_onlyOneRequest() {
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)
        coVerify(exactly = 1) { agentJobRepository.cancel("job-1") }
    }

    @Test
    fun confirm_503_keepsStateWithError() {
        coEvery { agentJobRepository.cancel("job-1") } throws
            MylifeApiException(503, "cancel_incomplete", "job is still active")
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)

        val state = viewModel.uiState.value.agentJobCancel
        assertEquals("job-1", state?.job?.jobId)
        assertFalse(state!!.inFlight)
        assertTrue(state.error != null)
    }

    @Test
    fun confirm_404_not_found_clearsSilently() {
        coEvery { agentJobRepository.cancel("job-1") } throws
            MylifeApiException(404, "not_found", "job not found")
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)
        assertNull(viewModel.uiState.value.agentJobCancel)
    }

    @Test
    fun confirm_409_conflict_clearsSilently() {
        coEvery { agentJobRepository.cancel("job-1") } throws
            MylifeApiException(409, "conflict", "job is not active")
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)
        assertNull(viewModel.uiState.value.agentJobCancel)
    }

    @Test
    fun confirm_route_level_404_showsError() {
        coEvery { agentJobRepository.cancel("job-1") } throws
            MylifeApiException(404, "internal", "HTTP 404")
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)

        val state = viewModel.uiState.value.agentJobCancel
        assertEquals("job-1", state?.job?.jobId)
        assertTrue(state!!.error != null)
    }

    @Test
    fun jobDisappears_clearsCancelState() {
        adapter.onJobStatus(listOf(job("job-1")))
        requestCancel("job-1")
        adapter.onJobStatus(emptyList())
        assertNull(viewModel.uiState.value.agentJobCancel)
    }

    @Test
    fun dismissPanel_clearsIdleConfirm() {
        adapter.onJobStatus(listOf(job("job-1")))
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.DismissJobStatusPanel)
        assertFalse(viewModel.uiState.value.showJobStatusPanel)
        assertNull(viewModel.uiState.value.agentJobCancel)
    }

    @Test
    fun toggleOff_clearsIdleConfirm() {
        adapter.onJobStatus(listOf(job("job-1")))
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        assertFalse(viewModel.uiState.value.showJobStatusPanel)
        assertNull(viewModel.uiState.value.agentJobCancel)
    }

    @Test
    fun toggleOff_keepsInFlightConfirm() {
        val gate = CompletableDeferred<Unit>()
        coEvery { agentJobRepository.cancel("job-1") } coAnswers { gate.await() }
        adapter.onJobStatus(listOf(job("job-1")))
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)

        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        assertFalse(viewModel.uiState.value.showJobStatusPanel)
        assertEquals("job-1", viewModel.uiState.value.agentJobCancel?.job?.jobId)

        gate.complete(Unit)
        assertNull(viewModel.uiState.value.agentJobCancel)
    }

    @Test
    fun dismissPanel_keepsInFlightConfirm() {
        val gate = CompletableDeferred<Unit>()
        coEvery { agentJobRepository.cancel("job-1") } coAnswers { gate.await() }
        adapter.onJobStatus(listOf(job("job-1")))
        viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel)
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)
        assertTrue(viewModel.uiState.value.agentJobCancel!!.inFlight)

        viewModel.onEvent(ConversationEvent.DismissJobStatusPanel)
        assertFalse(viewModel.uiState.value.showJobStatusPanel)
        assertEquals("job-1", viewModel.uiState.value.agentJobCancel?.job?.jobId)

        gate.complete(Unit)
        assertNull(viewModel.uiState.value.agentJobCancel)
    }

    @Test
    fun requestCancel_whileInFlight_isIgnored() {
        val gate = CompletableDeferred<Unit>()
        coEvery { agentJobRepository.cancel("job-1") } coAnswers { gate.await() }
        requestCancel("job-1")
        viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)

        requestCancel("job-2")
        assertEquals("job-1", viewModel.uiState.value.agentJobCancel?.job?.jobId)

        gate.complete(Unit)
        assertNull(viewModel.uiState.value.agentJobCancel)
    }
}
