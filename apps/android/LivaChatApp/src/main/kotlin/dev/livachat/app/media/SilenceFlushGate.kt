package dev.livachat.app.media

internal enum class FrameAction { SEND_REAL, SEND_SILENCE, DROP }

/**
 * 静音补发窗口:服务端 VAD 需 ≥600ms 连续静音才收尾当前 ASR 句,
 * mute 后若立即掐断上行,正在说的句子永远不会 finalize。
 * 因此观察到 muted false→true 沿后,先补发 [flushFrames] 帧
 * (默认 50 × 20ms = 1s)全零静音,窗口耗尽后才 DROP;unmute 下帧即恢复真实上行。
 *
 * 线程约定:稳态下所有状态只在读帧线程读写([decide]/[resetBaseline]);
 * 例外是 switchAudioSource 重建瞬间——旧循环退出前最后一帧可能与新循环首帧
 * 并发触达本类(≤1 帧,@Volatile + 幂等写保证收敛,至多补发窗口 ±1~2 帧,
 * 见 docs/android_audio_capture.md §3.5);
 * 另有挂起 top-up 在挂起线程只读 [remainingFrames](@Volatile 可见,与读循环
 * 并发下读数漂移只多不少,docs/android_audio_capture.md §3.5);
 * muted 事实源是 AudioCaptureController 的 _isMuted StateFlow,逐帧以参数传入。
 * [resetBaseline] 必须在每次读循环启动时调用一次:若启动时已处于静音
 * (无新 mute 沿),不触发窗口;读错误重启发生在窗口内时,剩余帧续发不重置。
 */
internal class SilenceFlushGate(private val flushFrames: Int = 50) {
    @Volatile private var wasMuted = false
    @Volatile private var remaining = 0

    /** 剩余补发帧数(只读):挂起 top-up 补齐 mute 静音窗口用(docs/android_audio_capture.md §3.5)。 */
    val remainingFrames: Int get() = remaining

    fun resetBaseline(mutedNow: Boolean) {
        wasMuted = mutedNow
        if (!mutedNow) remaining = 0
    }

    /**
     * 会话 teardown 时调用(stop):清空跨会话残留窗口。
     * 与 [resetBaseline] 的分工:读错误重开循环走基线(续发剩余),
     * 完整 stop/start 走 reset(新会话不继承旧窗口)。
     */
    fun reset() {
        wasMuted = false
        remaining = 0
    }

    fun decide(muted: Boolean): FrameAction {
        if (!muted) {
            wasMuted = false
            remaining = 0
            return FrameAction.SEND_REAL
        }
        if (!wasMuted) {
            wasMuted = true
            remaining = flushFrames
        }
        return if (remaining > 0) {
            remaining--
            FrameAction.SEND_SILENCE
        } else {
            FrameAction.DROP
        }
    }
}
