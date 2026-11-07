package dev.livachat.sdk.internal.session

/**
 * Generic S2C reordering buffer — re-assembles server messages that may arrive
 * out-of-order due to WebSocket frame interleaving or network jitter.
 *
 * @param maxSize       maximum number of buffered messages before forcing a flush.
 * @param gapTimeoutMs  how long to wait for a missing seq before skipping it.
 * @param clock         injectable clock for deterministic timeout testing.
 * @param onWarning     called when a gap is skipped (for production logging).
 */
internal class ReorderBuffer<T>(
    private val maxSize: Int = 64,
    private val gapTimeoutMs: Long = 200L,
    private val clock: () -> Long = { System.currentTimeMillis() },
    private val onWarning: (String) -> Unit = {},
) {

    private var nextExpected: Long = 1
    private val buffer = sortedMapOf<Long, T>()

    /** Timestamp (from [clock]) when the first gap was detected, or `null`. */
    private var gapStartedAtMs: Long? = null

    /**
     * Push a received message with its [seq] number.
     *
     * @return a list of messages that are now ready for in-order dispatch (may be empty).
     */
    fun push(seq: Long, item: T): List<T> {
        // Duplicate or late message — discard
        if (seq < nextExpected) return emptyList()

        // Exact match — dispatch immediately
        if (seq == nextExpected) {
            nextExpected++
            val result = mutableListOf(item)
            // Drain any buffered messages that are now in sequence
            while (true) {
                val next = buffer.remove(nextExpected)
                if (next != null) {
                    result.add(next)
                    nextExpected++
                } else {
                    break
                }
            }
            gapStartedAtMs = null
            return result
        }

        // seq > nextExpected — gap detected
        buffer[seq] = item

        // Start or maintain gap timer
        if (gapStartedAtMs == null) {
            gapStartedAtMs = clock()
        }

        // Flush if buffer exceeds maxSize or gap timeout has elapsed
        if (buffer.size > maxSize || (clock() - gapStartedAtMs!!) >= gapTimeoutMs) {
            return flushGap()
        }

        return emptyList()
    }

    private fun flushGap(): List<T> {
        var skipped = nextExpected
        val result = mutableListOf<T>()
        var first = true
        val iter = buffer.iterator()
        while (iter.hasNext()) {
            val (s, item) = iter.next()
            if (first) {
                onWarning("ReorderBuffer gap skip: expected=$nextExpected but next in buffer=$s; skipped ${s - nextExpected} messages")
                skipped = s
                first = false
            }
            result.add(item)
            iter.remove()
        }
        nextExpected = skipped + result.size
        gapStartedAtMs = null
        return result
    }

    /** Reset state for a new connection. */
    fun reset() {
        nextExpected = 1
        buffer.clear()
        gapStartedAtMs = null
    }
}
