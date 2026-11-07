package dev.livachat.sdk.internal.processors

import dev.livachat.sdk.model.VadSensitivity
import dev.livachat.sdk.model.VadState
import java.io.Closeable

/**
 * Internal interface for WebRTC Voice Activity Detection. SDK-internal only.
 *
 * Detects speech/silence on 20ms PCM frames.
 * Not thread-safe; intended for single-threaded use on the audio send dispatcher.
 */
internal interface VadDetector : Closeable {
    /**
     * Detect voice activity on a single 20ms PCM frame.
     *
     * @param pcm    16-bit mono PCM (320 samples for 16kHz, 480 for 24kHz)
     * @param offset start index in the array
     * @param len    number of samples (must match 20ms frame size)
     * @return [VadState.SPEECH] or [VadState.SILENCE]
     */
    fun detect(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size): VadState
}

internal class VadDetectorImpl(
    sampleRate: Int,
    sensitivity: VadSensitivity = VadSensitivity.MEDIUM,
) : VadDetector {

    companion object {
        // WebRTC VAD only supports these sample rates
        val SUPPORTED_RATES = setOf(8000, 16000, 32000)

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
        require(sampleRate in SUPPORTED_RATES) {
            "VAD unsupported sample rate $sampleRate Hz; supported: $SUPPORTED_RATES"
        }
        handle = nativeInit(sampleRate, sensitivity.mode)
    }

    override fun detect(pcm: ShortArray, offset: Int, len: Int): VadState {
        checkHandle()
        val result = nativeDetect(handle, pcm, offset, len)
        return when (result) {
            1 -> VadState.SPEECH
            0 -> VadState.SILENCE
            else -> VadState.UNKNOWN
        }
    }

    override fun close() {
        if (handle != 0L) {
            nativeRelease(handle)
            handle = 0
        }
    }

    private fun checkHandle() {
        if (handle == 0L) {
            throw IllegalStateException("VadDetector already closed")
        }
    }

    protected fun finalize() {
        if (handle != 0L) {
            nativeRelease(handle)
        }
    }

    private external fun nativeInit(sr: Int, mode: Int): Long
    private external fun nativeDetect(handle: Long, pcm: ShortArray, off: Int, len: Int): Int
    private external fun nativeRelease(handle: Long)
}
