package dev.livachat.sdk.internal.network

import kotlin.random.Random

/**
 * Pure-function exponential backoff calculator with jitter.
 *
 * ZERO Android dependencies — testable on plain JVM with an injected [clock].
 *
 * Backoff formula: `min(baseDelayMs * 2^(attempt-1), maxDelayMs) + random([-jitterMs, +jitterMs])`.
 * Returns -1 when [maxAttempts] is exceeded to signal the caller to stop retrying.
 */
internal class ReconnectScheduler(
    private val config: ReconnectConfig = ReconnectConfig(),
    private val clock: () -> Long = { System.currentTimeMillis() },
) {

    private var attempt = 0

    /** Next exponential delay in milliseconds, or -1 if max attempts exceeded. */
    fun nextDelay(): Long {
        attempt++
        if (attempt > config.maxAttempts) return STOP
        val exponential = config.baseDelayMs * (1L shl (attempt - 1))
        val capped = minOf(exponential, config.maxDelayMs)
        val jitter = (Random.nextDouble() * 2 - 1) * config.jitterMs
        return (capped + jitter.toLong()).coerceAtLeast(0)
    }

    /** Reset attempt counter (e.g. after a successful connection). */
    fun reset() {
        attempt = 0
    }

    /** Current attempt count (1-based, 0 before first call). */
    fun currentAttempt(): Int = attempt

    companion object {
        const val STOP: Long = -1
    }
}
