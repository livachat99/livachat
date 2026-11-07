package dev.livachat.app.media

import java.util.TreeMap

/**
 * Thread-safe jitter buffer for incoming PCM audio frames.
 *
 * Accepts frames from any thread via [push], delivers them in
 * monotonically-increasing [sequenceNumber] order via [pull].
 *
 * Designed for a single-producer (SDK callback), single-consumer
 * (playback loop) model. All public methods are synchronized.
 */
class JitterBuffer(
    val targetFrames: Int = 3,
    val maxLatencyFrames: Int = 10,
) {
    data class PcmFrame(
        val pcm: ShortArray,
        val sampleRate: Int,
        val sampleBits: Int,
        val durationMs: Int,
        val sequenceNumber: Long,
        val turnId: String,
    )

    private val buffer = TreeMap<Long, PcmFrame>()
    private var started: Boolean = false
    private var nextExpected: Long = -1

    var droppedLate: Long = 0
        private set
    var droppedOverflow: Long = 0
        private set

    @Synchronized
    fun push(frame: PcmFrame) {
        if (started && frame.sequenceNumber < nextExpected) {
            droppedLate++
            return
        }

        if (buffer.size >= maxLatencyFrames) {
            droppedOverflow++
            return
        }

        buffer[frame.sequenceNumber] = frame
    }

    @Synchronized
    fun pull(): PcmFrame? {
        if (buffer.isEmpty()) return null

        if (!started) {
            started = true
            nextExpected = buffer.firstKey()
        }

        if (!buffer.containsKey(nextExpected)) {
            nextExpected = buffer.firstKey()
        }

        val frame = buffer.remove(nextExpected) ?: return null
        nextExpected = frame.sequenceNumber + 1
        return frame
    }

    @Synchronized
    fun isReady(): Boolean = buffer.size >= targetFrames

    @Synchronized
    fun size(): Int = buffer.size

    @Synchronized
    fun isEmpty(): Boolean = buffer.isEmpty()

    @Synchronized
    fun reset() {
        buffer.clear()
        started = false
        nextExpected = -1
        droppedLate = 0
        droppedOverflow = 0
    }
}
