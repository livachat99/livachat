package dev.livachat.sdk.internal.session

import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.PlaybackFlushReason

/**
 * Internal abstraction that decouples [SessionManager] from the concrete
 * [dev.livachat.sdk.LivaChatListener] (created in Phase 6).
 *
 * Every callback is invoked on whatever thread the event originated from;
 * the implementation is responsible for thread-hopping if needed.
 */
internal interface ListenerDispatcher {
    fun onSessionReady(sessionId: String, sessionRestored: Boolean)
    fun onSessionEnded(sessionId: String, reason: String)
    fun onJobStatus(jobs: List<AgentJob>)
    fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String)
    fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: dev.livachat.sdk.model.AiImage? = null, source: String? = null)
    fun onAiAudio(data: String, codec: String, durationMs: Int)
    fun onAiVideoAck(frameId: Long, ackFrameId: Long, processed: Boolean)
    fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean)
    fun onSessionChanged(mode: String, myLanguage: String?, localLanguage: String?, usecase: String? = null)
    fun onPlaybackShouldFlush(reason: PlaybackFlushReason)
}
