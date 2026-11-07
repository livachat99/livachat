package dev.livachat.sdk.internal.pipeline

import dev.livachat.sdk.model.BandwidthHint
import org.junit.Assert.assertEquals
import org.junit.Test

class BandwidthEstimatorTest {

    @Test
    fun snapshot_initial_returnsCritical() {
        val est = BandwidthEstimator { 0 }
        assertEquals(BandwidthHint.CRITICAL, est.snapshot())
    }

    @Test
    fun record_goodRate_convergesToGood() {
        val est = BandwidthEstimator { 0 }
        // 300 Kbps = 37500 B/s
        est.recordBytesSent(0, 0)
        est.recordBytesSent(37500, 1000)
        est.recordBytesSent(37500, 2000)
        est.recordBytesSent(37500, 3000)

        assertEquals(BandwidthHint.GOOD, est.snapshot())
    }

    @Test
    fun record_fairRate_convergesToFair() {
        val est = BandwidthEstimator { 0 }
        est.recordBytesSent(0, 0)
        est.recordBytesSent(18750, 1000)
        est.recordBytesSent(18750, 2000)
        est.recordBytesSent(18750, 3000)

        assertEquals(BandwidthHint.FAIR, est.snapshot())
    }

    @Test
    fun record_poorRate_convergesToPoor() {
        val est = BandwidthEstimator { 0 }
        est.recordBytesSent(0, 0)
        est.recordBytesSent(9375, 1000)
        est.recordBytesSent(9375, 2000)
        est.recordBytesSent(9375, 3000)

        assertEquals(BandwidthHint.POOR, est.snapshot())
    }

    @Test
    fun record_criticalRate_staysCritical() {
        val est = BandwidthEstimator { 0 }
        est.recordBytesSent(0, 0)
        est.recordBytesSent(3750, 1000)
        est.recordBytesSent(3750, 2000)

        assertEquals(BandwidthHint.CRITICAL, est.snapshot())
    }

    @Test
    fun ewma_smoothsTransitions() {
        val est = BandwidthEstimator { 0 }
        // Steady GOOD for 4 windows
        est.recordBytesSent(0, 0)
        est.recordBytesSent(37500, 1000)
        est.recordBytesSent(37500, 2000)
        est.recordBytesSent(37500, 3000)
        est.recordBytesSent(37500, 4000)
        assertEquals(BandwidthHint.GOOD, est.snapshot())

        // Two low windows at 30 Kbps shouldn't immediately collapse to CRITICAL
        est.recordBytesSent(3750, 5000)
        est.recordBytesSent(3750, 6000)

        val hint = est.snapshot()
        assert(hint == BandwidthHint.FAIR || hint == BandwidthHint.POOR) {
            "Expected FAIR or POOR after dips, got $hint"
        }
    }

    @Test
    fun rateWithinWindow_accumulatesCorrectly() {
        val est = BandwidthEstimator { 0 }
        // Multiple sends within one window totaling 30000 bytes = 240 Kbps
        est.recordBytesSent(0, 0)
        est.recordBytesSent(7500, 250)
        est.recordBytesSent(7500, 500)
        est.recordBytesSent(7500, 750)
        est.recordBytesSent(7500, 1000) // rate = 30000 B/s = 240 Kbps

        // Converge toward GOOD
        est.recordBytesSent(37500, 2000)
        est.recordBytesSent(37500, 3000)
        est.recordBytesSent(37500, 4000)
        est.recordBytesSent(37500, 5000)

        assertEquals(BandwidthHint.GOOD, est.snapshot())
    }

    @Test
    fun record_noUpdateWithinSameMillisecond() {
        val est = BandwidthEstimator { 0 }
        est.recordBytesSent(0, 1000)
        // All at same timestamp — no elapsed time, no EWMA update
        est.recordBytesSent(50000, 1000)
        est.recordBytesSent(50000, 1000)
        assertEquals(BandwidthHint.CRITICAL, est.snapshot())
    }
}
