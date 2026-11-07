package dev.livachat.sdk.internal.pipeline

import dev.livachat.sdk.model.BandwidthHint

/**
 * Estimates uplink bandwidth using per-second EWMA rate smoothing.
 *
 * Thread-safe; call [recordBytesSent] from the send path and [snapshot]
 * from the quality-control path.
 */
internal class BandwidthEstimator(
    private val clock: () -> Long = { System.currentTimeMillis() },
) {

    private val alpha = 0.4

    @Volatile
    private var ewmaBytesPerSec: Double = 0.0

    private var windowStartMs: Long = -1
    private var windowBytes: Long = 0

    @Synchronized
    fun recordBytesSent(bytes: Long, nowMs: Long = clock()) {
        if (windowStartMs < 0) {
            windowStartMs = nowMs
            return
        }

        windowBytes += bytes
        val elapsed = nowMs - windowStartMs

        if (elapsed >= 1000L) {
            val bytesPerSec = windowBytes * 1000.0 / elapsed
            if (ewmaBytesPerSec == 0.0) {
                ewmaBytesPerSec = bytesPerSec
            } else {
                ewmaBytesPerSec = alpha * bytesPerSec + (1.0 - alpha) * ewmaBytesPerSec
            }
            windowStartMs = nowMs
            windowBytes = 0
        }
    }

    fun snapshot(): BandwidthHint {
        val rate = ewmaBytesPerSec
        // 200 Kbps = 25000 B/s, 100 Kbps = 12500 B/s, 50 Kbps = 6250 B/s
        return when {
            rate > 25000.0 -> BandwidthHint.GOOD
            rate > 12500.0 -> BandwidthHint.FAIR
            rate > 6250.0  -> BandwidthHint.POOR
            else           -> BandwidthHint.CRITICAL
        }
    }
}
