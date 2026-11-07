package dev.livachat.sdk.internal.processors

import java.io.Closeable

/**
 * Internal interface for Opus decoding. SDK-internal only.
 *
 * Decodes Opus packets back into 16-bit mono PCM.
 * Not thread-safe; intended for single-threaded use on the audio recv dispatcher.
 */
internal interface OpusDecoder : Closeable {
    /**
     * Decode an Opus packet into PCM samples.
     *
     * @param opus   the compressed Opus packet
     * @param offset start index in the array
     * @param len    length of the Opus data
     * @return decoded 16-bit mono PCM (typically 320 or 480 samples per 20ms)
     */
    fun decode(opus: ByteArray, offset: Int = 0, len: Int = opus.size): ShortArray
}

internal class OpusDecoderImpl(
    sampleRate: Int,
    channels: Int = 1,
) : OpusDecoder {

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

    override fun decode(opus: ByteArray, offset: Int, len: Int): ShortArray {
        checkHandle()
        return nativeDecode(handle, opus, offset, len)
    }

    override fun close() {
        if (handle != 0L) {
            nativeRelease(handle)
            handle = 0
        }
    }

    private fun checkHandle() {
        if (handle == 0L) {
            throw IllegalStateException("OpusDecoder already closed")
        }
    }

    protected fun finalize() {
        if (handle != 0L) {
            nativeRelease(handle)
        }
    }

    private external fun nativeInit(sr: Int, ch: Int): Long
    private external fun nativeDecode(handle: Long, opus: ByteArray, off: Int, len: Int): ShortArray
    private external fun nativeRelease(handle: Long)
}
