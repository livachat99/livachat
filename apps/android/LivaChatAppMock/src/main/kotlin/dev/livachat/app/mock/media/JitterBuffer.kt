package dev.livachat.app.mock.media

import java.util.TreeMap

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

    @Synchronized
    fun push(frame: PcmFrame) {
        if (started && frame.sequenceNumber < nextExpected) return

        if (buffer.size >= maxLatencyFrames) return

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
    fun reset() {
        buffer.clear()
        started = false
        nextExpected = -1
    }
}
