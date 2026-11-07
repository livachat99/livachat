package dev.livachat.sdk.perf

import android.util.Log
import dev.livachat.sdk.internal.processors.OpusDecoderImpl
import dev.livachat.sdk.internal.processors.OpusEncoderImpl
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Performance benchmark: Opus encode/decode latency.
 *
 * Target (design §10): encode < 5ms, decode < 5ms (p99 over 100 frames).
 */
class OpusEncodingLatencyTest {

    companion object {
        private const val TAG = "OpusPerf"
        private const val WARMUP_FRAMES = 10
        private const val MEASURE_FRAMES = 100
        private const val ENCODE_TARGET_MS = 5.0
        private const val DECODE_TARGET_MS = 5.0
    }

    @Test
    fun encode_16kHz_p99_under5ms() {
        val encoder = OpusEncoderImpl(16000)
        val pcm = ShortArray(320) { (it * 100 % 32767).toShort() }

        // Warmup
        repeat(WARMUP_FRAMES) { encoder.encode(pcm, 0, 320) }

        // Measure
        val times = mutableListOf<Long>()
        repeat(MEASURE_FRAMES) {
            val start = System.nanoTime()
            encoder.encode(pcm, 0, 320)
            times.add(System.nanoTime() - start)
        }
        encoder.close()

        val avgUs = times.average() / 1000.0
        val p99Us = times.sorted()[(MEASURE_FRAMES * 0.99).toInt()] / 1000.0
        val p99Ms = p99Us / 1000.0

        Log.i(TAG, "Opus encode 16kHz: avg=%.1fµs p99=%.1fµs (%.2fms)".format(avgUs, p99Us, p99Ms))
        assertTrue("Encode p99=%.2fms exceeds %.0fms target".format(p99Ms, ENCODE_TARGET_MS),
                   p99Ms <= ENCODE_TARGET_MS)
    }

    @Test
    fun encode_24kHz_p99_under5ms() {
        val encoder = OpusEncoderImpl(24000)
        val pcm = ShortArray(480) { (it * 100 % 32767).toShort() }

        repeat(WARMUP_FRAMES) { encoder.encode(pcm, 0, 480) }

        val times = mutableListOf<Long>()
        repeat(MEASURE_FRAMES) {
            val start = System.nanoTime()
            encoder.encode(pcm, 0, 480)
            times.add(System.nanoTime() - start)
        }
        encoder.close()

        val p99Ms = times.sorted()[(MEASURE_FRAMES * 0.99).toInt()] / 1_000_000.0
        Log.i(TAG, "Opus encode 24kHz: p99=%.2fms".format(p99Ms))
        assertTrue("Encode 24kHz p99=%.2fms".format(p99Ms), p99Ms <= ENCODE_TARGET_MS)
    }

    @Test
    fun decode_16kHz_p99_under5ms() {
        val encoder = OpusEncoderImpl(16000)
        val decoder = OpusDecoderImpl(16000)
        val pcm = ShortArray(320) { (it * 100 % 32767).toShort() }
        val opus = encoder.encode(pcm, 0, 320)
        encoder.close()

        // Warmup
        repeat(WARMUP_FRAMES) { decoder.decode(opus, 0, opus.size) }

        val times = mutableListOf<Long>()
        repeat(MEASURE_FRAMES) {
            val start = System.nanoTime()
            decoder.decode(opus, 0, opus.size)
            times.add(System.nanoTime() - start)
        }
        decoder.close()

        val p99Ms = times.sorted()[(MEASURE_FRAMES * 0.99).toInt()] / 1_000_000.0
        Log.i(TAG, "Opus decode 16kHz: p99=%.2fms".format(p99Ms))
        assertTrue("Decode p99=%.2fms".format(p99Ms), p99Ms <= DECODE_TARGET_MS)
    }
}
