package dev.livachat.sdk.internal.session

/**
 * Maintains the offset between server UTC time and local monotonic time for
 * one-way delay calculations (design §4.4).
 */
internal class ClockOffset {

    @Volatile
    private var offsetMs: Long = 0L

    /** Recompute the offset from session.ready's [serverTimeMs] and the local clock. */
    fun update(serverTimeMs: Long, localTimeMs: Long) {
        offsetMs = serverTimeMs - localTimeMs
    }

    /** Convert a local timestamp to an approximate server timestamp. */
    fun toServerTime(localTimeMs: Long): Long = localTimeMs + offsetMs

    /** The current offset in milliseconds. */
    fun offsetMs(): Long = offsetMs
}
