package dev.livachat.app.media

import kotlin.math.log10
import kotlin.math.max
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * 麦克风输入音量电平表（docs/android_mic_meter.md §2）。
 *
 * 纯 Kotlin、无 Android 依赖：读帧循环每 20ms 调用一次 [onPcmFrame]，完成
 * 整帧 RMS → dBFS → [floorDb, ceilDb] 窗口归一 → 快攻慢放平滑 → 量化 0..levels。
 *
 * 量化到 Int 段由 controller 侧 StateFlow 等值去重承载：稳态零发射，
 * UI 只消费离散段、无浮点抖动。段数/窗口/释放系数均为构造参数，OEM 增益
 * 差异只调参数不改逻辑（docs/android_mic_meter.md §2）。
 */
class MicLevelMeter(
    private val levels: Int = LEVELS,
    private val floorDb: Float = -45f,
    private val ceilDb: Float = -8f,
    private val releasePerFrame: Float = 0.85f,
) {
    companion object {
        /** 量化段数——UI 点亮条数的事实源（docs/android_mic_meter.md §2.1） */
        const val LEVELS = 4
    }

    private var smoothed = 0f

    /** 每读帧循环调用一次；muted 或空帧 → 归零并清空平滑态。返回 0..levels。 */
    fun onPcmFrame(frame: ShortArray, length: Int, muted: Boolean): Int {
        if (muted || length <= 0) {
            smoothed = 0f
            return 0
        }
        var sum = 0L
        for (i in 0 until length) {
            val s = frame[i].toInt()
            sum += s * s
        }
        val rms = sqrt(sum.toDouble() / length)
        val db = 20 * log10(max(rms, 1.0) / 32768.0)
        val norm = ((db - floorDb) / (ceilDb - floorDb)).toFloat().coerceIn(0f, 1f)
        smoothed = if (norm >= smoothed) norm else smoothed * releasePerFrame
        return (smoothed * levels).roundToInt().coerceIn(0, levels)
    }

    fun reset() {
        smoothed = 0f
    }
}
