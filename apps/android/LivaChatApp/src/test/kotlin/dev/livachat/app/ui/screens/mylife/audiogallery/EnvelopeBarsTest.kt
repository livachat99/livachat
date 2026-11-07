package dev.livachat.app.ui.screens.mylife.audiogallery

import dev.livachat.app.data.remote.mylife.dto.EnergyEnvelope
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 能量包络 → 柱高（docs/android_timeline_audio_gallery.md §10.4/E5）：
 * 每根条取对应帧区间 **max**、按段峰值归一（floor = 峰值 − 40 dB）、
 * **不假设 `values.size == durationMs/20`**（索引按比例映射并 clamp）。
 */
class EnvelopeBarsTest {

    /** 便捷构造：dbMin=-70、dbMax=0（与 pipeline 一致）；值 = 量化 uint8。 */
    private fun env(values: IntArray, frameMs: Int = 20) = EnergyEnvelope(
        frameMs = frameMs, dbMin = -70f, dbMax = 0f,
        values = ByteArray(values.size) { values[it].toByte() },
    )

    private fun quantized(db: Float): Int =
        (((db - (-70f)) / 70f * 255f) + 0.5f).toInt().coerceIn(0, 255)

    @Test
    fun `每根条取窗口 max（保起音）`() {
        // 4 帧（-70/-20/-60/-30 dBFS）压成 2 根：根0 = max(-70,-20) = -20；根1 = max(-60,-30) = -30
        val e = env(intArrayOf(quantized(-70f), quantized(-20f), quantized(-60f), quantized(-30f)))
        val bars = envelopeBars(e, 2)
        assertEquals(2, bars.size)
        assertEquals(1f, bars[0], 1e-3f)              // 峰值 −20 → 最高
        assertEquals(0.75f, bars[1], 1e-2f)           // −30 ⇒ (−30 − (−60)) / 40
    }

    @Test
    fun `归一化 floor = 峰值 − 40dB`() {
        // 峰值 −20 → floor −60：−60 → 0，−40 → 0.5，−20 → 1
        val e = env(intArrayOf(quantized(-60f), quantized(-40f), quantized(-20f)))
        val bars = envelopeBars(e, 3)
        assertEquals(0f, bars[0], 1e-2f)
        assertEquals(0.5f, bars[1], 1e-2f)
        assertEquals(1f, bars[2], 1e-3f)
    }

    @Test
    fun `条数由调用方按宽度决定，帧数不整除时按比例映射且不越界`() {
        // 实测形态：60s 段 2999 帧 → 3 根（2999 不整除 3，末段不得越界）
        val values = IntArray(2999) { quantized(-50f) }
        values[2998] = quantized(-10f).toByte().toInt() and 0xFF   // 末帧最响
        val bars = envelopeBars(env(values), 3)
        assertEquals(3, bars.size)
        assertEquals(1f, bars[2], 1e-3f)              // 末帧落在第 3 根
        assertTrue(bars.all { it in 0f..1f })
    }

    @Test
    fun `平直段（全帧 = dbMin）返回空数组：降级纯轨道，不给误导性满高`() {
        // §10.10：floor 有绝对下界 dbMin ⇒ 平直段 peak == floor ⇒ 全柱 0 ⇒ 空数组（不是满高、
        // 也不是除零/NaN）
        assertEquals(0, envelopeBars(env(IntArray(100) { 0 }), 5).size)
    }

    @Test
    fun `安静段（峰值低于 dbMin+40）让真静音贴底`() {
        // 峰值 −50 dBFS（量化 73）、静音帧 −70（量化 0）：后者贴底 0，前者满高 1
        val quiet = quantized(-50f)
        val bars = envelopeBars(env(intArrayOf(0, quiet, 0, quiet)), 4)
        assertEquals(4, bars.size)
        assertEquals(0f, bars[0], 1e-2f)
        assertEquals(1f, bars[1], 1e-2f)
        assertEquals(0f, bars[2], 1e-2f)
        assertEquals(1f, bars[3], 1e-2f)
        // 对照：旧口径（floor = 峰值 − 40 = −90）下静音帧会悬在半高 0.5 —— 现已贴底
        assertTrue(bars.none { it in 0.4f..0.6f })
    }

    @Test
    fun `空包络与非法条数返回空数组（调用方降级纯轨道）`() {
        assertEquals(0, envelopeBars(env(IntArray(0)), 10).size)
        assertEquals(0, envelopeBars(env(intArrayOf(128)), 0).size)
    }

    @Test
    fun `小尺寸包络（帧数少于条数）仍能出条数`() {
        val bars = envelopeBars(env(intArrayOf(quantized(-20f), quantized(-40f))), 8)
        assertEquals(8, bars.size)
        assertTrue(bars.all { it in 0f..1f })
    }
}
