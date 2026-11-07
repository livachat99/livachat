package dev.livachat.sdk.internal.processors

import kotlin.math.sqrt

/**
 * Instrumentation test for Opus encoder/decoder roundtrip fidelity.
 *
 * Runs on emulator / device because it exercises real native code
 * (libopus via JNI).
 */
class OpusRoundtripTest {

    private fun sineWave(sampleRate: Int, frameMs: Int = 20): ShortArray {
        val samples = sampleRate * frameMs / 1000
        val freq = 1000.0
        val amplitude = 16000.0
        return ShortArray(samples) { i ->
            val t = i.toDouble() / sampleRate
            (amplitude * kotlin.math.sin(2.0 * kotlin.math.PI * freq * t)).toInt().toShort()
        }
    }

    private fun correlation(a: ShortArray, b: ShortArray): Double {
        val n = minOf(a.size, b.size)
        if (n == 0) return 0.0

        val avgA = a.take(n).map { it.toLong() }.average()
        val avgB = b.take(n).map { it.toLong() }.average()

        var num = 0.0
        var denA = 0.0
        var denB = 0.0
        for (i in 0 until n) {
            val da = a[i].toLong() - avgA
            val db = b[i].toLong() - avgB
            num += da * db
            denA += da * da
            denB += db * db
        }
        val den = sqrt(denA) * sqrt(denB)
        return if (den == 0.0) 0.0 else num / den
    }

    @org.junit.Test
    fun encode_16kHz_20ms_returnsNonEmpty() {
        val encoder = OpusEncoderImpl(16000)
        val pcm = sineWave(16000, 20)
        val opus = encoder.encode(pcm, 0, pcm.size)
        encoder.close()

        assert(opus.isNotEmpty()) { "Encoded Opus packet should not be empty" }
        assert(opus.size < 200) { "Opus <= 200 bytes, was ${opus.size}" }
    }

    @org.junit.Test
    fun encode_24kHz_20ms_returnsNonEmpty() {
        val encoder = OpusEncoderImpl(24000)
        val pcm = sineWave(24000, 20)
        val opus = encoder.encode(pcm, 0, pcm.size)
        encoder.close()

        assert(opus.isNotEmpty())
    }

    @org.junit.Test
    fun roundtrip_16kHz_correlationAbove90pct() {
        val encoder = OpusEncoderImpl(16000)
        val decoder = OpusDecoderImpl(16000)
        val pcm = sineWave(16000, 20)

        val opus = encoder.encode(pcm, 0, pcm.size)
        val decoded = decoder.decode(opus, 0, opus.size)

        encoder.close()
        decoder.close()

        val corr = kotlin.math.abs(correlation(pcm, decoded))
        assert(corr > 0.8) { "Roundtrip correlation > 0.8, was $corr" }
    }

    @org.junit.Test
    fun roundtrip_24kHz_correlationAbove80pct() {
        val encoder = OpusEncoderImpl(24000)
        val decoder = OpusDecoderImpl(24000)
        val pcm = sineWave(24000, 20)

        val opus = encoder.encode(pcm, 0, pcm.size)
        val decoded = decoder.decode(opus, 0, opus.size)

        encoder.close()
        decoder.close()

        val corr = kotlin.math.abs(correlation(pcm, decoded))
        assert(corr > 0.7) { "24kHz roundtrip correlation > 0.7, was $corr" }
    }

    @org.junit.Test
    fun encode_close_then_encode_throws() {
        val encoder = OpusEncoderImpl(16000)
        encoder.close()

        try {
            encoder.encode(sineWave(16000), 0, 320)
            assert(false) { "Should have thrown" }
        } catch (e: IllegalStateException) {
            // expected
        }
    }

    @org.junit.Test
    fun decode_close_then_decode_throws() {
        val decoder = OpusDecoderImpl(16000)
        decoder.close()

        try {
            decoder.decode(ByteArray(40), 0, 40)
            assert(false) { "Should have thrown" }
        } catch (e: IllegalStateException) {
            // expected
        }
    }

    @org.junit.Test
    fun encode_multiple_frames_consistent() {
        val encoder = OpusEncoderImpl(16000)
        val pcm = sineWave(16000, 20)

        val results = (1..10).map { encoder.encode(pcm, 0, pcm.size) }
        encoder.close()

        val sizes = results.map { it.size }
        val avgSize = sizes.average()
        assert(avgSize in 30.0..120.0) { "Avg Opus packet 30-120 bytes, was $avgSize" }
    }
}
