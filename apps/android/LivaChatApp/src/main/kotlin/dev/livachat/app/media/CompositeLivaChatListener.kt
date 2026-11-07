package dev.livachat.app.media

import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.AiImage
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.PlaybackFlushReason
import dev.livachat.sdk.model.SessionState
import java.util.concurrent.CopyOnWriteArraySet
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class CompositeLivaChatListener @Inject constructor() : LivaChatListener {

    private val subscribers = CopyOnWriteArraySet<LivaChatListener>()

    fun register(listener: LivaChatListener) {
        subscribers.add(listener)
    }

    fun unregister(listener: LivaChatListener) {
        subscribers.remove(listener)
    }

    override fun onConnectionStateChanged(state: ConnectionState) {
        subscribers.forEach { it.onConnectionStateChanged(state) }
    }

    override fun onSessionStateChanged(state: SessionState) {
        subscribers.forEach { it.onSessionStateChanged(state) }
    }

    override fun onSessionReady(sessionId: String, restored: Boolean) {
        subscribers.forEach { it.onSessionReady(sessionId, restored) }
    }

    override fun onSessionEnded(reason: String, finalSessionId: String) {
        subscribers.forEach { it.onSessionEnded(reason, finalSessionId) }
    }

    override fun onJobStatus(jobs: List<AgentJob>) {
        subscribers.forEach { it.onJobStatus(jobs) }
    }

    override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {
        subscribers.forEach { it.onTranscription(text, isFinal, transcriptionId) }
    }

    override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: AiImage?, source: String?) {
        subscribers.forEach { it.onAiText(text, messageId, index, isFinal, image, source) }
    }

    override fun onAiAudioPcm(
        pcm: ShortArray,
        sampleRate: Int,
        sampleBits: Int,
        durationMs: Int,
        sequenceNumber: Long,
        turnId: String,
    ) {
        subscribers.forEach { it.onAiAudioPcm(pcm, sampleRate, sampleBits, durationMs, sequenceNumber, turnId) }
    }

    override fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {
        subscribers.forEach { it.onPlaybackShouldFlush(reason) }
    }

    override fun onVideoAck(frameId: Long, processed: Boolean) {
        subscribers.forEach { it.onVideoAck(frameId, processed) }
    }

    override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {
        subscribers.forEach { it.onError(code, message, fatal, recoverable) }
    }
}
