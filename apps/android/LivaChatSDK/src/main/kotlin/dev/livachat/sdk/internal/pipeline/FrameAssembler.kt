package dev.livachat.sdk.internal.pipeline

/**
 * Accumulates arbitrary-length PCM chunks into complete audio frames.
 *
 * Not thread-safe; intended for single-threaded use on the audio send dispatcher.
 */
internal class FrameAssembler(
    private val sampleRate: Int,
    private val frameMs: Int = 20,
    private val onFrame: (ShortArray) -> Unit,
) {
    /** Number of samples in one complete frame. */
    val frameSamples: Int = sampleRate * frameMs / 1000

    private var pending = ShortArray(frameSamples * 2)
    private var pendingLen: Int = 0

    /**
     * Push a chunk of PCM samples. May trigger zero or more [onFrame] callbacks
     * when complete frames are assembled.
     */
    fun push(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size) {
        var off = offset
        var remaining = len

        while (remaining > 0) {
            val copyCount = minOf(frameSamples - pendingLen, remaining)
            pcm.copyInto(pending, pendingLen, off, off + copyCount)
            pendingLen += copyCount
            off += copyCount
            remaining -= copyCount

            if (pendingLen == frameSamples) {
                val frame = pending.copyOf(frameSamples)
                // 先复位再回调：onFrame 抛异常时 pendingLen 不得停留在 frameSamples——
                // 否则后续 push 会反复重发同一帧（调用方吞掉异常时表现为永久卡死）
                pendingLen = 0
                onFrame(frame)
            }
        }
    }

    /** Discard any partial frame and reset state. */
    fun reset() {
        pendingLen = 0
    }
}
