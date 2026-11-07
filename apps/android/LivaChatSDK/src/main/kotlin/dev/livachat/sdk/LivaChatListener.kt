package dev.livachat.sdk

import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.AiImage
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.PlaybackFlushReason
import dev.livachat.sdk.model.SessionState

/**
 * Callback interface consumed by App layer. Every method has a no-op default so App
 * can override only the events it cares about.
 *
 * **Threading contract:**
 * - [onConnectionStateChanged] and [onSessionStateChanged] are forwarded on
 *   the facade's Main observer scope (collected from connection/session flows).
 * - All other non-audio callbacks ([onSessionReady], [onSessionEnded],
 *   [onSessionChanged], [onTranscription], [onAiText], [onJobStatus],
 *   [onVideoAck], [onError], [onPlaybackShouldFlush]) fire on the inbound
 *   dispatch path — the WS reader thread, synchronously; writing to a
 *   StateFlow is safe.
 * - [onAiAudioPcm] is called on the SDK-internal audio-receive dispatcher
 *   (NOT the main thread). App should write to AudioTrack directly from here.
 */
interface LivaChatListener {
    /** Connection state changed. */
    fun onConnectionStateChanged(state: ConnectionState) {}

    /** Server-driven session state changed. */
    fun onSessionStateChanged(state: SessionState) {}

    /** Session is active and ready for audio/video streaming. */
    fun onSessionReady(sessionId: String, restored: Boolean) {}

    /** Session was terminated by server or a local endSession call. */
    fun onSessionEnded(reason: String, finalSessionId: String) {}

    /** Streaming ASR transcription result. */
    fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {}

    /**
     * Full snapshot of the device's running background jobs (replace semantics):
     * each call carries the complete current set — replace, never merge.
     * An empty list clears any previously reported jobs.
     *
     * Fired on the inbound dispatch path (WS reader thread), same as
     * [onTranscription]; writing to a StateFlow is safe.
     */
    fun onJobStatus(jobs: List<AgentJob>) {}

    /** Streaming AI text (e.g. displayed in a chat bubble).
     * @param image The camera frame shown to the LLM, present only on final chunk when video enabled.
     * @param source Origin of the message: "llm" (default) or "agent". Null when absent. */
    fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: AiImage? = null, source: String? = null) {}

    /**
     * Decoded AI TTS PCM audio. App MUST write these samples to its AudioTrack /
     * Oboe sink; otherwise the user hears nothing.
     *
     * Called on the audio-receive dispatcher (NOT Main).
     *
     * @param pcm Decoded 16-bit PCM samples
     * @param sampleRate Audio sample rate in Hz (e.g. 16000)
     * @param sampleBits Bits per sample (currently 16)
     * @param durationMs Playback duration of this chunk in milliseconds
     * @param sequenceNumber Monotonically-increasing frame sequence number
     * @param turnId Turn identifier this frame belongs to (empty for now, reserved)
     */
    fun onAiAudioPcm(
        pcm: ShortArray,
        sampleRate: Int,
        sampleBits: Int,
        durationMs: Int,
        sequenceNumber: Long,
        turnId: String = "",
    ) {}

    /** Server instructs App to flush its playback buffer immediately. */
    fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {}

    /** Server acknowledged a video frame. */
    fun onVideoAck(frameId: Long, processed: Boolean) {}

    /**
     * Server confirmed the pipeline mode has been changed.
     * @param mode The now-active pipeline mode ("chat" or "translate").
     * @param myLanguage User's native language (only present in translate mode).
     * @param localLanguage Local environment language (only present in translate mode).
     */
    fun onSessionChanged(mode: String, myLanguage: String?, localLanguage: String?, usecase: String? = null) {}

    /** Fatal or recoverable error reported by server. */
    fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {}
}
