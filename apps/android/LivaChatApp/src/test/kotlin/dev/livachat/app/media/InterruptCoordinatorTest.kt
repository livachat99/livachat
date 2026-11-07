package dev.livachat.app.media

import app.cash.turbine.Turbine
import app.cash.turbine.test
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.media.AudioPlaybackController
import dev.livachat.app.media.HapticController
import dev.livachat.app.media.InterruptCoordinator
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.InterruptReason
import dev.livachat.sdk.model.SessionState
import dev.livachat.sdk.model.VadState
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class InterruptCoordinatorTest {

    private val sdk = mockk<LivaChatSDK>(relaxed = true)
    private val playback = mockk<AudioPlaybackController>(relaxed = true)
    private val haptic = mockk<HapticController>(relaxed = true)
    private val settings = mockk<SettingsRepository>(relaxed = true)

    private val sessionFlow = MutableStateFlow(SessionState.LISTENING)
    private val vadFlow = MutableStateFlow(VadState.SILENCE)

    @Test
    fun `SPEAKING plus SPEECH triggers interrupt`() = runTest {
        every { sdk.sessionState } returns sessionFlow
        every { sdk.localVadState } returns vadFlow
        every { settings.observePreferences() } returns flowOf(
            UserPreferences(vadInterruptEnabled = true),
        )

        val coordinator = InterruptCoordinator(sdk, playback, haptic, settings)
        coordinator.start(this)

        sessionFlow.value = SessionState.SPEAKING
        vadFlow.value = VadState.SPEECH

        advanceUntilIdle()

        // e6f05f7（jitter buffer 重构）后 flush 职责移入 AudioPlaybackController 播放循环，
        // InterruptCoordinator 不再调用；此处仅断言其直接副作用
        verify(atLeast = 1) { sdk.sendInterrupt(InterruptReason.USER_VOICE) }
        verify(atLeast = 1) { haptic.lightTap() }

        coordinator.stop()
    }

    @Test
    fun `LISTENING plus SPEECH does not trigger`() = runTest {
        every { sdk.sessionState } returns sessionFlow
        every { sdk.localVadState } returns vadFlow
        every { settings.observePreferences() } returns flowOf(
            UserPreferences(vadInterruptEnabled = true),
        )

        val coordinator = InterruptCoordinator(sdk, playback, haptic, settings)
        coordinator.start(this)

        sessionFlow.value = SessionState.LISTENING
        vadFlow.value = VadState.SPEECH

        advanceUntilIdle()

        verify(exactly = 0) { sdk.sendInterrupt(any<InterruptReason>()) }
        verify(exactly = 0) { haptic.lightTap() }

        coordinator.stop()
    }

    @Test
    fun `vadEnabled false does not trigger`() = runTest {
        every { sdk.sessionState } returns sessionFlow
        every { sdk.localVadState } returns vadFlow
        every { settings.observePreferences() } returns flowOf(
            UserPreferences(vadInterruptEnabled = false),
        )

        val coordinator = InterruptCoordinator(sdk, playback, haptic, settings)
        coordinator.start(this)

        sessionFlow.value = SessionState.SPEAKING
        vadFlow.value = VadState.SPEECH

        advanceUntilIdle()

        verify(exactly = 0) { sdk.sendInterrupt(any<InterruptReason>()) }
        verify(exactly = 0) { haptic.lightTap() }

        coordinator.stop()
    }

    @Test
    fun `consecutive SPEECH within 500ms triggers only once`() = runTest {
        every { sdk.sessionState } returns sessionFlow
        every { sdk.localVadState } returns vadFlow
        every { settings.observePreferences() } returns flowOf(
            UserPreferences(vadInterruptEnabled = true),
        )

        val coordinator = InterruptCoordinator(sdk, playback, haptic, settings)
        coordinator.start(this)

        sessionFlow.value = SessionState.SPEAKING
        vadFlow.value = VadState.SPEECH

        advanceUntilIdle()

        // Second SPEECH emission within 500ms should be throttled
        vadFlow.value = VadState.SILENCE
        advanceUntilIdle()
        vadFlow.value = VadState.SPEECH
        advanceUntilIdle()

        verify(exactly = 1) { sdk.sendInterrupt(InterruptReason.USER_VOICE) }

        coordinator.stop()
    }
}
