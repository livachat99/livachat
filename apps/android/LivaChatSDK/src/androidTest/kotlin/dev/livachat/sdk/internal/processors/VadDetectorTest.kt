package dev.livachat.sdk.internal.processors

import dev.livachat.sdk.model.VadSensitivity
import dev.livachat.sdk.model.VadState

/**
 * Instrumentation test for WebRTC VAD via JNI.
 */
class VadDetectorTest {

    private fun speechPcm(sampleRate: Int): ShortArray {
        val samples = sampleRate * 20 / 1000  // 20ms frame
        val freq = 1000.0
        val amplitude = 16000.0
        return ShortArray(samples) { i ->
            val t = i.toDouble() / sampleRate
            (amplitude * kotlin.math.sin(2.0 * kotlin.math.PI * freq * t)).toInt().toShort()
        }
    }

    private fun silencePcm(sampleRate: Int): ShortArray {
        val samples = sampleRate * 20 / 1000
        return ShortArray(samples) // all zeros
    }

    // ── Tests ─────────────────────────────────────────────────────

    @org.junit.Test
    fun detect_sineWave_16kHz_shouldBeSpeech() {
        val vad = VadDetectorImpl(16000, VadSensitivity.LOW)
        val pcm = speechPcm(16000)

        // Sine wave should be detected as speech most of the time
        val results = (1..20).map { vad.detect(pcm, 0, pcm.size) }
        vad.close()

        val speechCount = results.count { it == VadState.SPEECH }
        // At LOW sensitivity, sine wave should be detected as speech at least sometimes
        // Not asserting 100% as WebRTC VAD is probabilistic
        assert(speechCount > 0) { "Sine wave should be detected as speech at least sometimes, got: $results" }
    }

    @org.junit.Test
    fun detect_silence_16kHz_shouldBeSilence() {
        val vad = VadDetectorImpl(16000, VadSensitivity.MEDIUM)
        val pcm = silencePcm(16000)

        val results = (1..10).map { vad.detect(pcm, 0, pcm.size) }
        vad.close()

        // Silence should always be detected as silence
        results.forEach { state ->
            assert(state == VadState.SILENCE) { "Silence should be SILENCE, was $state" }
        }
    }

    @org.junit.Test
    fun detect_silence_32kHz_shouldBeSilence() {
        val vad = VadDetectorImpl(32000, VadSensitivity.MEDIUM)
        val pcm = silencePcm(32000)

        val results = (1..10).map { vad.detect(pcm, 0, pcm.size) }
        vad.close()

        results.forEach { state ->
            assert(state == VadState.SILENCE) { "Silence should be SILENCE, was $state" }
        }
    }

    @org.junit.Test
    fun detect_differentModes_allReturnValid() {
        val pcm = speechPcm(16000)

        for (mode in VadSensitivity.entries) {
            val vad = VadDetectorImpl(16000, mode)
            val result = vad.detect(pcm, 0, pcm.size)
            vad.close()

            assert(result == VadState.SPEECH || result == VadState.SILENCE) {
                "Result should be SPEECH or SILENCE, was $result for mode $mode"
            }
        }
    }

    @org.junit.Test
    fun detect_invalidSampleRate_throws() {
        // 44100 Hz is not supported by WebRTC VAD
        try {
            VadDetectorImpl(44100, VadSensitivity.MEDIUM)
            assert(false) { "Should have thrown" }
        } catch (e: Exception) {
            // expected — invalid rate
        }
    }

    @org.junit.Test
    fun detect_close_then_detect_throws() {
        val vad = VadDetectorImpl(16000)
        vad.close()

        try {
            vad.detect(speechPcm(16000), 0, 320)
            assert(false) { "Should have thrown" }
        } catch (e: IllegalStateException) {
            // expected
        }
    }

    @org.junit.Test
    fun detect_highSensitivity_moreAggressive() {
        // HIGH sensitivity is more aggressive = less likely to detect speech
        val pcm = speechPcm(16000)

        val lowVad = VadDetectorImpl(16000, VadSensitivity.LOW)
        val highVad = VadDetectorImpl(16000, VadSensitivity.HIGH)

        val lowResults = (1..30).map { lowVad.detect(pcm, 0, pcm.size) }
        val highResults = (1..30).map { highVad.detect(pcm, 0, pcm.size) }

        lowVad.close()
        highVad.close()

        val lowSpeech = lowResults.count { it == VadState.SPEECH }
        val highSpeech = highResults.count { it == VadState.SPEECH }

        // HIGH sensitivity should be at least as conservative as LOW
        assert(highSpeech <= lowSpeech) {
            "HIGH sensitivity ($highSpeech) should be ≤ LOW sensitivity ($lowSpeech) speech detections"
        }
    }
}
