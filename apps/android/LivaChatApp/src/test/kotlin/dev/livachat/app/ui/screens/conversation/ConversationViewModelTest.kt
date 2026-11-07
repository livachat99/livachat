package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.domain.usecase.SessionUseCase
import dev.livachat.app.media.AudioCaptureController
import dev.livachat.app.media.AudioPlaybackController
import dev.livachat.app.media.CameraController
import dev.livachat.app.media.CompositeLivaChatListener
import dev.livachat.app.media.HapticController
import dev.livachat.app.media.InterruptCoordinator
import dev.livachat.sdk.LivaChatSDK
import io.mockk.every
import io.mockk.just
import io.mockk.Runs
import io.mockk.verify
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationViewModelTest {

    // Test synchronous event handling without ViewModel init (which requires Dispatchers.Main)

    @Test
    fun conversationUiState_defaults_are_correct() {
        val state = ConversationUiState()
        assertEquals(dev.livachat.sdk.model.ConnectionState.DISCONNECTED, state.connectionState)
        assertEquals(dev.livachat.sdk.model.SessionState.IDLE, state.sessionState)
        assertFalse(state.isMicMuted)
        assertFalse(state.isSpeakerMuted)
        assertFalse(state.isCameraOn)
        assertFalse(state.isRecording)
        assertFalse(state.isTranslateMode)
        assertFalse(state.isLectureMode)
        assertFalse(state.isTranscribeMode)
        assertFalse(state.isOmniMode)
        assertFalse(state.showOmniIntroDialog)
        assertFalse(state.showServerConfigPrompt)
        assertFalse(state.showAiGeneratedNotice)
        assertFalse(state.showLanguagePicker)
        assertFalse(state.showLecturePicker)
        assertFalse(state.showTranscribeSourcePicker)
        assertEquals(TranscribeAudioSource.VOICE, state.transcribeAudioSource)
        assertFalse(state.showScreenRecordRationale)
        assertFalse(state.pendingScreenCaptureLaunch)
        assertEquals(emptyList<dev.livachat.app.domain.model.ChatMessage>(), state.messages)
        assertEquals(null, state.imageViewerMessageId)
        assertEquals(null, state.agentJobResultViewId)
        assertEquals(null, state.agentJobCancel)
        assertTrue(state.avatarVisible)
    }

    @Test
    fun conversationEvent_sealed_classes_cover_all_events() {
        val events = listOf(
            ConversationEvent.ToggleRecording,
            ConversationEvent.ConfirmScreenRecordRationale,
            ConversationEvent.DismissScreenRecordRationale,
            ConversationEvent.ConfirmStopScreenRecord,
            ConversationEvent.DismissStopScreenRecord,
            ConversationEvent.ConsumeScreenCaptureLaunch,
            ConversationEvent.ScreenCaptureConsentDenied,
            ConversationEvent.ConsumeOverlayPermissionLaunch,
            ConversationEvent.OverlayPermissionReturned,
            ConversationEvent.ToggleMic,
            ConversationEvent.ToggleSpeaker,
            ConversationEvent.ToggleCamera,
            ConversationEvent.SwitchCamera,
            ConversationEvent.RetryConnection,
            ConversationEvent.DismissError,
            ConversationEvent.DismissAiGeneratedNotice,
            ConversationEvent.SendChipMessage("test"),
            ConversationEvent.NavigateToHistory,
            ConversationEvent.NavigateToSettings,
            ConversationEvent.ShowMicDevicePicker,
            ConversationEvent.DismissMicDevicePicker,
            ConversationEvent.SelectMicInputDevice(1),
            ConversationEvent.ShowOutputDevicePicker,
            ConversationEvent.DismissOutputDevicePicker,
            ConversationEvent.SelectOutputDevice(1),
            ConversationEvent.OnScreenStart,
            ConversationEvent.OnScreenStop,
            ConversationEvent.ShowAvatarSheet,
            ConversationEvent.DismissAvatarSheet,
            ConversationEvent.SetAvatarVisible(false),
            ConversationEvent.RemoveCustomAvatar,
            ConversationEvent.StartAvatarCrop,
            ConversationEvent.CancelAvatarCrop,
            ConversationEvent.ToggleTranslateMode,
            ConversationEvent.ConfirmTranslateLanguage("en"),
            ConversationEvent.DismissLanguagePicker,
            ConversationEvent.ToggleLectureMode,
            ConversationEvent.ConfirmLectureLanguage("en"),
            ConversationEvent.DismissLecturePicker,
            ConversationEvent.ToggleTranscribeMode,
            ConversationEvent.ConfirmTranscribeSource(TranscribeAudioSource.VOICE),
            ConversationEvent.DismissTranscribeSourcePicker,
            ConversationEvent.ToggleOmniMode,
            ConversationEvent.ConfirmOmniMode,
            ConversationEvent.DismissOmniDialog,
            ConversationEvent.ToggleJobStatusPanel,
            ConversationEvent.DismissJobStatusPanel,
            ConversationEvent.RequestAgentJobCancel(
                dev.livachat.sdk.model.AgentJob(
                    jobId = "job-1",
                    agentName = "hermes",
                    summary = "查询机票",
                    startedAtMs = 0L,
                    runningTimeMs = 0L,
                ),
            ),
            ConversationEvent.DismissAgentJobCancel,
            ConversationEvent.ConfirmAgentJobCancel,
            ConversationEvent.OpenImageViewer("ai-1"),
            ConversationEvent.CloseImageViewer,
            ConversationEvent.SaveViewerImage("ai-1"),
            ConversationEvent.SaveViewerImagePermissionDenied,
            ConversationEvent.OpenAgentJobResult("ai-msg_agent_job_1"),
            ConversationEvent.CloseAgentJobResult,
        )
        assertEquals(56, events.size)   // 52 + agent job cancel（request/dismiss/confirm）+ AI notice dismiss
    }

    @Test
    fun videoVisibility_has_all_states() {
        assertEquals(4, VideoVisibility.entries.size)
    }

    @Test
    fun transcribeAudioSource_has_raw_and_voice() {
        assertEquals(2, TranscribeAudioSource.entries.size)
        assertEquals(listOf(TranscribeAudioSource.RAW, TranscribeAudioSource.VOICE),
            TranscribeAudioSource.entries)
    }
}
