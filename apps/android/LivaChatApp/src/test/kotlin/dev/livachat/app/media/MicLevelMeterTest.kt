package dev.livachat.app.media

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MicLevelMeterTest {
    private val meter = MicLevelMeter()

    private val frameSamples = 320

    private fun frame(amplitude: Int): ShortArray = ShortArray(frameSamples) { amplitude.toShort() }

    private fun fullScaleFrame(): ShortArray =
        ShortArray(frameSamples) { if (it and 1 == 0) 32767 else -32768 }

    // 全 0 帧（RMS=0 → dBFS≈−90，低于 −45 窗口下限）
    @Test
    fun silenceReturnsZero() {
        assertEquals(0, meter.onPcmFrame(frame(0), frameSamples, muted = false))
    }

    // 满幅 ±32767（dBFS≈0，超出 −8 上限 → 归一 1.0）
    @Test
    fun fullScaleReturnsTopLevel() {
        assertEquals(MicLevelMeter.LEVELS, meter.onPcmFrame(fullScaleFrame(), frameSamples, muted = false))
    }

    // 安静环境振幅 30（≈−60.7dB，低于 −45 下限）
    @Test
    fun quietEnvironmentReturnsZero() {
        assertEquals(0, meter.onPcmFrame(frame(30), frameSamples, muted = false))
    }

    // 正常语音振幅 ≈3277（≈−20dB，窗口内 norm≈0.676 → ×4 ≈ 2.7 → 3）
    @Test
    fun normalSpeechLevelsInMiddleRange() {
        val level = meter.onPcmFrame(frame(3277), frameSamples, muted = false)
        assertTrue("expected 2..3 but was $level", level in 2..3)
    }

    @Test
    fun mutedForcesZeroAndClearsSmoothing() {
        // 先建立满幅平滑态（攻击到顶）
        assertEquals(MicLevelMeter.LEVELS, meter.onPcmFrame(fullScaleFrame(), frameSamples, muted = false))
        // muted 期间满幅帧也被强制归零（不因真实拾音跳动）
        repeat(5) {
            assertEquals(0, meter.onPcmFrame(fullScaleFrame(), frameSamples, muted = true))
        }
        // unmute 下帧从 0 起跳、无残留：振幅 1920（≈−24.6dB，norm≈0.55）冷启电平=2；
        // 若平滑态未清（残留 1.0 → release 0.85 → ×4 ≈ 3.4 → 3），断言 2 证明已清零
        assertEquals(2, meter.onPcmFrame(frame(1920), frameSamples, muted = false))
    }

    @Test
    fun fastAttackHitsTopOnSingleLoudFrame() {
        repeat(10) {
            meter.onPcmFrame(frame(0), frameSamples, muted = false)
        }
        assertEquals(MicLevelMeter.LEVELS, meter.onPcmFrame(fullScaleFrame(), frameSamples, muted = false))
    }

    @Test
    fun slowReleaseDecaysMonotonicallyAndZeroesWithinHalfSecond() {
        meter.onPcmFrame(fullScaleFrame(), frameSamples, muted = false)
        var previous = MicLevelMeter.LEVELS
        var zeroAt: Int? = null
        for (i in 1..40) {
            val level = meter.onPcmFrame(frame(0), frameSamples, muted = false)
            assertTrue("level rose from $previous to $level", level <= previous)
            previous = level
            if (level == 0 && zeroAt == null) zeroAt = i
        }
        // 0.85^k·4 < 0.5 → k=13（4 段归零阈值 0.125，≈260ms）；±2 帧容浮点误差
        assertTrue("expected zero around 13 frames, first zero at $zeroAt", zeroAt != null && zeroAt in 11..15)
    }

    @Test
    fun emptyFrameReturnsZero() {
        assertEquals(0, meter.onPcmFrame(fullScaleFrame(), 0, muted = false))
    }

    @Test
    fun resetClearsSmoothedState() {
        meter.onPcmFrame(fullScaleFrame(), frameSamples, muted = false)
        meter.reset()
        assertEquals(0, meter.onPcmFrame(frame(0), frameSamples, muted = false))
    }
}
