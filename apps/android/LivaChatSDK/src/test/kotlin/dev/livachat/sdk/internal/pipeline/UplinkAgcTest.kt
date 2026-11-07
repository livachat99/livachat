package dev.livachat.sdk.internal.pipeline

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * docs/android_audio_capture.md §7.1 用例 1-7：
 * unity 边界（静音/底噪/大声）、目标收敛、峰值保护、平滑速率、reset。
 *
 * 除 case 3 外每帧都喂**新构造**的原始帧——真实链路每帧拿到的是未增益的麦克风 PCM，
 * 复用同一数组会形成"测已增益信号"的反馈回路，与线上行为不符。
 */
class UplinkAgcTest {

    private val frameSamples = 320

    /** 精确整周期正弦（250Hz@16k → 每帧 5 周期），帧 RMS = amp/√2。 */
    private fun sine(rmsDbfs: Double, samples: Int = frameSamples): ShortArray {
        val amp = 32768.0 * 10.0.pow(rmsDbfs / 20.0) * sqrt(2.0)
        return ShortArray(samples) { i ->
            (amp * sin(2 * PI * 250.0 * i / 16000.0)).roundToInt()
                .coerceIn(-32768, 32767).toShort()
        }
    }

    private fun rmsDbfs(f: ShortArray): Double {
        var sum = 0.0
        for (s in f) sum += s.toDouble() * s
        return 20 * log10(max(sqrt(sum / f.size), 1e-9) / 32768.0)
    }

    private fun peakDbfs(f: ShortArray): Double {
        var p = 0
        for (s in f) p = max(p, abs(s.toInt()))
        return 20 * log10(max(p.toDouble(), 1.0) / 32768.0)
    }

    // 1. 全零帧 → unity，输出不变
    @Test
    fun silenceFrame_untouched() {
        val agc = UplinkAgc()
        val f = ShortArray(frameSamples)
        assertEquals(0.0, agc.process(f), 1e-9)
        assertTrue(f.all { it == 0.toShort() })
        assertEquals(0.0, agc.currentGainDb, 1e-9)
    }

    // 2. -35 dBFS 稳定语音 → 输出收敛到 -20 dBFS（+15 dB ≤ maxGain 18）
    @Test
    fun quietSpeech_convergesToTarget() {
        val agc = UplinkAgc()
        var out = ShortArray(0)
        repeat(40) {
            out = sine(-35.0)
            agc.process(out)
        }
        assertEquals(15.0, agc.currentGainDb, 0.5)
        assertEquals(-20.0, rmsDbfs(out), 0.5)
    }

    // 3. -10 dBFS 大声 → makeup-only，恒 unity，逐样本不变
    @Test
    fun loudSpeech_neverAttenuated() {
        val agc = UplinkAgc()
        val f = sine(-10.0)
        val original = f.copyOf()
        repeat(40) { agc.process(f) }
        assertTrue(f.contentEquals(original))
        assertEquals(0.0, agc.currentGainDb, 1e-9)
    }

    // 4. 低于噪声门（-70 dBFS）→ 不提升，输出不变
    @Test
    fun belowGate_untouched() {
        val agc = UplinkAgc()
        val f = sine(-70.0)
        val original = f.copyOf()
        repeat(40) { agc.process(f) }
        assertTrue(f.contentEquals(original))
        assertEquals(0.0, agc.currentGainDb, 1e-9)
    }

    // 5. 峰值保护：高波峰因数帧（RMS 低、峰值 -4.3 dBFS）→ 增益被限到"峰值恰好到 ceiling"
    @Test
    fun peakGuard_capsGainBelowTarget() {
        val agc = UplinkAgc()
        fun spikeFrame() = ShortArray(frameSamples) { if (it == 0) 20000.toShort() else 0.toShort() }

        val probe = spikeFrame()
        assertTrue("frame must be high-crest", rmsDbfs(probe) < -25.0 && peakDbfs(probe) > -5.0)

        var out = ShortArray(0)
        var applied = 0.0
        repeat(40) {
            out = spikeFrame()
            applied = agc.process(out)
        }

        assertTrue("peak must stay under ceiling", peakDbfs(out) <= -3.0 + 0.1)
        // 平滑增益已收敛到目标所需 ~9.3 dB，但本帧只允许 ceiling-peak ≈ 1.3 dB
        assertTrue("smoothing reached target", agc.currentGainDb > 8.0)
        assertTrue("applied limited by peak guard", applied in 0.5..3.0)
    }

    // 6. 平滑：首帧只走 attack 系数的一部分；转大声后走 release 缓慢回落
    @Test
    fun smoothing_attackThenRelease() {
        val agc = UplinkAgc()
        // 首帧：desired 15 dB，attackCoef = e^-1 ≈ 0.368 → 增益 ≈ 15 × 0.632 = 9.48 dB
        agc.process(sine(-35.0))
        assertEquals(9.48, agc.currentGainDb, 0.6)

        repeat(40) { agc.process(sine(-35.0)) }
        val settled = agc.currentGainDb
        assertEquals(15.0, settled, 0.5)

        // 大声帧：desired = 0，releaseCoef = e^(-20/400) ≈ 0.951 → 单帧最多回落 ~5%
        agc.process(sine(-10.0))
        assertTrue("release is gradual", agc.currentGainDb in (settled * 0.9)..settled)
    }

    // 7. reset → 增益回 unity，重新从 attack 起算
    @Test
    fun reset_returnsToUnity() {
        val agc = UplinkAgc()
        repeat(40) { agc.process(sine(-35.0)) }
        agc.reset()
        assertEquals(0.0, agc.currentGainDb, 1e-9)
        agc.process(sine(-35.0))
        assertEquals(9.48, agc.currentGainDb, 0.6)
    }
}
