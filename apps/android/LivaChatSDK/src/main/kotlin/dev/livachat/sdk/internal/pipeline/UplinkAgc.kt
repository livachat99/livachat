package dev.livachat.sdk.internal.pipeline

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * 上行电平补偿（makeup-only AGC，docs/android_audio_capture.md §4.3）。
 *
 * 只提升、不衰减：安静语音向 [targetDbfs] 抬升，最大 [maxGainDb]；低于 [gateDbfs]
 * 视为静音/底噪不提升；单帧增益受 [ceilingDbfs] 峰值约束（不削顶）。
 * 逐帧处理，帧内原位修改 [pcm]（调用方按帧持有独立数组）。
 *
 * 承重约束：必须跑在 VAD 之后（服务端消费 vad_state 驱动轮次/打断，
 * 见 docs/android_audio_capture.md §4.3）——本类不感知 VAD，落点由 [AudioSendPipeline] 保证。
 * 非线程安全：与 VAD/编码同线程（音频发送 dispatcher）。
 */
internal class UplinkAgc(
    private val targetDbfs: Double = -20.0,
    private val maxGainDb: Double = 18.0,
    private val gateDbfs: Double = -55.0,
    private val ceilingDbfs: Double = -3.0,
    private val attackMs: Int = 20,
    private val releaseMs: Int = 400,
    frameMs: Int = 20,
) {
    private val attackCoef = exp(-frameMs.toDouble() / attackMs)
    private val releaseCoef = exp(-frameMs.toDouble() / releaseMs)

    /** 平滑后的增益（dB，恒 ≥ 0）。internal 供单测断言。 */
    internal var currentGainDb: Double = 0.0
        private set

    /**
     * 处理一帧 16-bit mono PCM（原位修改）。返回本帧实际施加的增益（dB，≥ 0）。
     */
    fun process(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size): Double {
        if (len <= 0) return 0.0

        var sum = 0.0
        var peak = 0
        for (i in offset until offset + len) {
            val s = pcm[i].toInt()
            sum += s.toDouble() * s
            val a = abs(s)
            if (a > peak) peak = a
        }
        val rms = sqrt(sum / len)
        val rmsDb = 20 * log10(max(rms, 1e-9) / 32768.0)
        val peakDb = 20 * log10(max(peak.toDouble(), 1.0) / 32768.0)

        val desiredDb =
            if (rmsDb < gateDbfs) 0.0 // 静音/底噪：不提升（增益经 release 平滑回落）
            else (targetDbfs - rmsDb).coerceIn(0.0, maxGainDb)

        val coef = if (desiredDb > currentGainDb) attackCoef else releaseCoef
        currentGainDb = desiredDb + (currentGainDb - desiredDb) * coef

        // 数字静音（含 mute 补发窗口的全零帧）：乘任何增益仍是 0，跳过逐样本循环
        if (peak == 0) return 0.0

        // 峰值保护 + makeup-only：本帧增益取 min(平滑增益, 到 ceiling 的余量)，且不低于 0
        val appliedDb = max(0.0, min(currentGainDb, ceilingDbfs - peakDb))
        if (appliedDb <= 0.0) return 0.0

        val gain = 10.0.pow(appliedDb / 20.0)
        for (i in offset until offset + len) {
            val v = (pcm[i] * gain).roundToInt()
            pcm[i] = v.coerceIn(-32768, 32767).toShort()
        }
        return appliedDb
    }

    /** 复位到 unity（会话/流边界调用，新流从无增益起算）。 */
    fun reset() {
        currentGainDb = 0.0
    }
}
