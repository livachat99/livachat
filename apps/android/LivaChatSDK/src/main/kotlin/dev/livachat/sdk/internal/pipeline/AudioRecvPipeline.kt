package dev.livachat.sdk.internal.pipeline

import dev.livachat.sdk.internal.processors.OpusDecoder

/**
 * Downstream audio pipeline: Opus → decode → PCM callback.
 *
 * Does NOT hold AudioTrack or buffer PCM; App consumes PCM via [onPcm] callback
 * immediately after decoding. Not thread-safe; single-threaded on audio recv
 * dispatcher.
 */
internal class AudioRecvPipeline(
    private val sampleRate: Int,
    private val decoder: OpusDecoder,
    private val onPcm: (pcm: ShortArray, sampleRate: Int, durationMs: Int, sequenceNumber: Long) -> Unit,
) {
    /**
     * Decode one Opus audio chunk and emit PCM through [onPcm].
     */
    fun handleAiAudio(
        opus: ByteArray,
        durationMs: Int,
        sequenceNumber: Long,
    ) {
        val pcm = decoder.decode(opus, 0, opus.size)
        onPcm(pcm, sampleRate, durationMs, sequenceNumber)
    }

    /** Reset decoder state (called on reconnect / session switch). */
    fun reset() {
        // Opus decoder state is self-contained; recreating the decoder
        // on reset would be handled by the owning Facade. No internal
        // buffer to clear here.
    }
}
