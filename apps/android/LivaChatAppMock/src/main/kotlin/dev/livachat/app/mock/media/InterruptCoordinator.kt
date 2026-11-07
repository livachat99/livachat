package dev.livachat.app.mock.media

import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.InterruptReason
import dev.livachat.sdk.model.SessionState
import dev.livachat.sdk.model.VadState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/**
 * Observes the SDK's [sessionState] and [localVadState] flows and triggers
 * local interrupt when the user starts speaking while the AI is playing.
 *
 * Design reference: §5.3
 */
class InterruptCoordinator(
    private val sdk: LivaChatSDK,
    private val playback: AudioPlaybackController,
    private val scope: CoroutineScope,
) {
    fun start() {
        scope.launch {
            combine(sdk.sessionState, sdk.localVadState) { session, vad ->
                session to vad
            }.collect { (sessionState, vadState) ->
                if (sessionState == SessionState.SPEAKING &&
                    vadState == VadState.SPEECH) {
                    sdk.sendInterrupt(InterruptReason.USER_VOICE)
                }
            }
        }
    }
}
