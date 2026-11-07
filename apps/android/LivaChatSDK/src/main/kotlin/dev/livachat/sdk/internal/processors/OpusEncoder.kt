package dev.livachat.sdk.internal.processors

import java.io.Closeable

/**
 * Internal interface for Opus encoding. SDK-internal only.
 *
 * Encodes 16-bit mono PCM frames into Opus packets using CBR @ 32 kbps.
 * Not thread-safe; intended for single-threaded use on the audio send dispatcher.
 */
internal interface OpusEncoder : Closeable {
    /**
     * Encode a single 20ms PCM frame into an Opus packet.
     *
     * @param pcm    16-bit mono PCM samples (320 for 16kHz, 480 for 24kHz)
     * @param offset start index in the array
     * @param len    number of samples (must match the configured frame size)
     * @return the compressed Opus packet (typically 40–80 bytes)
     */
    fun encode(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size): ByteArray
}

internal class OpusEncoderImpl(
    sampleRate: Int,
    channels: Int = 1,
) : OpusEncoder {

    companion object {
        private var nativeLoaded = false
        private fun ensureLoaded() {
            if (!nativeLoaded) {
                System.loadLibrary("livachat_native")
                nativeLoaded = true
            }
        }
    }

    private var handle: Long = 0

    init {
        ensureLoaded()
        handle = nativeInit(sampleRate, channels)
    }

    override fun encode(pcm: ShortArray, offset: Int, len: Int): ByteArray {
        checkHandle()
        return nativeEncode(handle, pcm, offset, len)
    }

    override fun close() {
        if (handle != 0L) {
            nativeRelease(handle)
            handle = 0
        }
    }

    private fun checkHandle() {
        if (handle == 0L) {
            throw IllegalStateException("OpusEncoder already closed")
        }
    }

    protected fun finalize() {
        if (handle != 0L) {
            nativeRelease(handle)
        }
    }

    private external fun nativeInit(sr: Int, ch: Int): Long
    private external fun nativeEncode(handle: Long, pcm: ShortArray, off: Int, len: Int): ByteArray
    private external fun nativeRelease(handle: Long)
}
