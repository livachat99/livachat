package dev.livachat.sdk.internal.pipeline

import kotlin.math.PI
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * UplinkAgc 热路径成本护栏（对齐 androidTest/perf/OpusEncodingLatencyTest 风格）。
 *
 * 该调用在音频发送 dispatcher 的每帧路径上（20ms/帧预算），必须远低于帧预算。
 * 纯 Kotlin 无 JNI：JVM 侧 p99 用作数量级护栏（设备 ART 上同量级）。
 */
class UplinkAgcPerfTest {

    companion object {
        private const val WARMUP_FRAMES = 5_000
        private const val MEASURE_FRAMES = 20_000

        /** 护栏：20ms 帧预算的 1%（200µs）；实测应在亚微秒级。 */
        private const val P99_BUDGET_US = 200.0
        private const val FRAME_BUDGET_US = 20_000.0
    }

    /** -30 dBFS 整周期正弦（250Hz@16k，320 样本），保证走完整增益应用路径。 */
    private fun speechFrame(): ShortArray {
        val amp = 32768.0 * 10.0.pow(-30.0 / 20.0) * sqrt(2.0)
        return ShortArray(320) { i ->
            (amp * sin(2 * PI * 250.0 * i / 16000.0)).roundToInt().toShort()
        }
    }

    /** 每帧喂**新构造**的原始帧：真实链路每帧拿到未增益 PCM（复用会改变 AGC 稳态，见 UplinkAgcTest 注）。 */
    @Test
    fun speechFrames_p99_wellUnderFrameBudget() {
        val agc = UplinkAgc()
        val frames = Array(WARMUP_FRAMES + MEASURE_FRAMES) { speechFrame() }

        repeat(WARMUP_FRAMES) { agc.process(frames[it]) }

        val times = LongArray(MEASURE_FRAMES)
        for (i in 0 until MEASURE_FRAMES) {
            val start = System.nanoTime()
            agc.process(frames[WARMUP_FRAMES + i])
            times[i] = System.nanoTime() - start
        }

        val sorted = times.sortedArray()
        val avgUs = times.average() / 1000.0
        val p99Us = sorted[(MEASURE_FRAMES * 0.99).toInt()] / 1000.0
        val maxUs = sorted.last() / 1000.0
        println(
            "UplinkAgc perf(语音帧): avg=%.2fµs p99=%.2fµs max=%.2fµs | 帧预算占用 avg=%.3f%% p99=%.3f%%"
                .format(avgUs, p99Us, maxUs,
                    avgUs / FRAME_BUDGET_US * 100, p99Us / FRAME_BUDGET_US * 100),
        )

        assertEquals("增益应已收敛到 -30 → -20 目标", 10.0, agc.currentGainDb, 1.0)
        assertTrue(
            "AGC p99=%.2fµs 超过护栏 %.0fµs".format(p99Us, P99_BUDGET_US),
            p99Us <= P99_BUDGET_US,
        )
    }

    /** 数字静音帧（门控 + peak==0 早退）：不进入逐样本循环，且输出仍恒零。 */
    @Test
    fun silenceFrames_fastPathNotSlowerThanSpeech() {
        val agc = UplinkAgc()
        val silence = ShortArray(320)
        val speech = Array(MEASURE_FRAMES) { speechFrame() }

        repeat(WARMUP_FRAMES) { agc.process(silence) }

        val t0 = System.nanoTime()
        repeat(MEASURE_FRAMES) { agc.process(silence) }
        val silenceUs = (System.nanoTime() - t0).toDouble() / MEASURE_FRAMES / 1000.0

        val t1 = System.nanoTime()
        for (f in speech) agc.process(f)
        val speechUs = (System.nanoTime() - t1).toDouble() / MEASURE_FRAMES / 1000.0

        println("UplinkAgc perf: 静音=%.2fµs/帧 语音=%.2fµs/帧".format(silenceUs, speechUs))
        assertTrue("静音帧应早退（不快于语音帧）", silenceUs <= speechUs)
        assertTrue("静音不得被增益放大（全零恒零）", silence.all { it == 0.toShort() })
    }
}
