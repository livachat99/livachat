package dev.livachat.sdk.perf

import android.util.Log
import dev.livachat.sdk.internal.processors.VadDetectorImpl
import dev.livachat.sdk.model.VadSensitivity
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Performance benchmark: VAD detection latency.
 *
 * Target (design §10): < 1ms (p99 over 100 frames).
 */
class VadLatencyTest {

    companion object {
        private const val TAG = "VadPerf"
        private const val WARMUP = 10
        private const val MEASURE = 100
        private const val TARGET_MS = 1.0
    }

    @Test
    fun detect_16kHz_p99_under1ms() {
        val vad = VadDetectorImpl(16000, VadSensitivity.MEDIUM)
        val pcm = ShortArray(320) { (it * 100 % 32767).toShort() }

        repeat(WARMUP) { vad.detect(pcm, 0, 320) }

        val times = mutableListOf<Long>()
        repeat(MEASURE) {
            val start = System.nanoTime()
            vad.detect(pcm, 0, 320)
            times.add(System.nanoTime() - start)
        }
        vad.close()

        val p99Ms = times.sorted()[(MEASURE * 0.99).toInt()] / 1_000_000.0
        Log.i(TAG, "VAD detect 16kHz: p99=%.3fms".format(p99Ms))
        assertTrue("VAD p99=%.3fms".format(p99Ms), p99Ms <= TARGET_MS)
    }
}
