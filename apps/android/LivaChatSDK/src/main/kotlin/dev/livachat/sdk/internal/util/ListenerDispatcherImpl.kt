package dev.livachat.sdk.internal.util

import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.internal.session.ListenerDispatcher
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.AiImage
import dev.livachat.sdk.model.PlaybackFlushReason
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers

/**
 * Bridges the internal [ListenerDispatcher] contract to the public [LivaChatListener].
 *
 * All callbacks fire on the calling thread by default. In production, the Facade
 * wires these through the appropriate dispatchers (Main for most, audio recv for PCM).
 */
internal class ListenerDispatcherImpl(
    private val listener: LivaChatListener,
    private val aiAudioHandler: ((String, String, Int) -> Unit)?,
    private val mainDispatcher: CoroutineDispatcher = Dispatchers.Main,
) : ListenerDispatcher {

    override fun onSessionReady(sessionId: String, sessionRestored: Boolean) {
        listener.onSessionReady(sessionId, sessionRestored)
    }

    override fun onSessionEnded(sessionId: String, reason: String) {
        listener.onSessionEnded(reason, sessionId)
    }

    override fun onJobStatus(jobs: List<AgentJob>) {
        listener.onJobStatus(jobs)
    }

    override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {
        listener.onTranscription(text, isFinal, transcriptionId)
    }

    override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: AiImage?, source: String?) {
        listener.onAiText(text, messageId, index, isFinal, image, source)
    }

    override fun onAiAudio(
        data: String, codec: String, durationMs: Int,
    ) {
        aiAudioHandler?.invoke(data, codec, durationMs)
    }

    override fun onAiVideoAck(frameId: Long, ackFrameId: Long, processed: Boolean) {
        listener.onVideoAck(frameId, processed)
    }

    override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {
        listener.onError(code, message, fatal, recoverable)
    }

    override fun onSessionChanged(mode: String, myLanguage: String?, localLanguage: String?, usecase: String?) {
        listener.onSessionChanged(mode, myLanguage, localLanguage, usecase)
    }

    override fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {
        listener.onPlaybackShouldFlush(reason)
    }
}
