package dev.livachat.app.media

import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.InterruptReason
import dev.livachat.sdk.model.SessionState
import dev.livachat.sdk.model.VadState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class InterruptCoordinator @Inject constructor(
    private val sdk: LivaChatSDK,
    private val playback: AudioPlaybackController,
    private val haptic: HapticController,
    private val settings: SettingsRepository,
) {
    private var observeJob: Job? = null
    private var lastInterruptAt: Long = 0L

    fun start(scope: CoroutineScope) {
        stop()
        observeJob = scope.launch {
            combine(
                sdk.sessionState,
                sdk.localVadState,
                settings.observePreferences().map { it.vadInterruptEnabled },
            ) { sessionState, vadState, enabled ->
                Triple(sessionState, vadState, enabled)
            }.collect { (sessionState, vadState, enabled) ->
                if (enabled
                    && sessionState == SessionState.SPEAKING
                    && vadState == VadState.SPEECH
                ) {
                    val now = System.currentTimeMillis()
                    if (now - lastInterruptAt >= 500) {
                        lastInterruptAt = now
                        sdk.sendInterrupt(InterruptReason.USER_VOICE)
                        haptic.lightTap()
                    }
                }
            }
        }
    }

    fun stop() {
        observeJob?.cancel()
        observeJob = null
    }
}
