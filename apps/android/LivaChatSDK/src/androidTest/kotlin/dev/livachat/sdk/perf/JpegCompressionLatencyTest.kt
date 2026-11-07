package dev.livachat.sdk.perf

import android.graphics.Bitmap
import android.util.Log
import dev.livachat.sdk.internal.processors.JpegEncoderImpl
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Performance benchmark: JPEG compression latency.
 *
 * Target (design §10): < 30ms (p99 over 50 frames at 1080p q=70).
 */
class JpegCompressionLatencyTest {

    companion object {
        private const val TAG = "JpegPerf"
        private const val WARMUP = 5
        private const val MEASURE = 50
        private const val TARGET_MS = 30.0
    }

    @Test
    fun encode_1080p_q70_p99_under30ms() {
        val encoder = JpegEncoderImpl()
        val bitmap = Bitmap.createBitmap(1080, 1920, Bitmap.Config.ARGB_8888)
        // Fill with non-uniform content for realistic compression
        for (y in 0 until 1920 step 10) {
            for (x in 0 until 1080 step 10) {
                bitmap.setPixel(x, y, ((x * 31 + y * 17) % 0xFFFFFF) or 0xFF000000.toInt())
            }
        }

        repeat(WARMUP) { encoder.encode(bitmap, 70) }

        val times = mutableListOf<Long>()
        repeat(MEASURE) {
            val start = System.nanoTime()
            val jpeg = encoder.encode(bitmap, 70)
            times.add(System.nanoTime() - start)
            jpeg // touch the result to prevent optimization
        }

        bitmap.recycle()

        val p99Ms = times.sorted()[(MEASURE * 0.99).toInt()] / 1_000_000.0
        val avgMs = times.average() / 1_000_000.0
        Log.i(TAG, "JPEG 1080p q=70: avg=%.1fms p99=%.1fms size=%d".format(
            avgMs, p99Ms, encoder.encode(bitmap, 70).size))

        assertTrue("JPEG p99=%.1fms exceeds %.0fms target".format(p99Ms, TARGET_MS),
                   p99Ms <= TARGET_MS)
    }
}
