package dev.livachat.app.media

import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicInteger

/**
 * 上行采集 PCM 落盘（临时排查工具，docs/android_audio_capture.md §3.7）。
 *
 * 在 [AudioCaptureController] 读帧循环按帧写入 s16le mono 原始 PCM，供 adb pull 后离线分析
 * （电平百分位、频段能量、与采集源/模式 A/B 对照）。仅调试期开启：[ENABLED] 置 true 重新构建。
 *
 * **一个实例 = 一次采集代 = 一个文件**（源切换/重建时由 controller 换新实例，旧实例异步收敛）：
 * 队列、写线程、running 标志全部实例私有，任何一条路径卡住都只影响自己那个文件，
 * 不会串写/串停（跨实例共享状态是本工具最初版本的缺陷）。
 *
 * 契约：
 * - **非阻塞**：读循环只做 `copyOf` + `offer`，写盘在独立线程；队列满即丢帧并计数，不拖慢采集；
 * - 目录不可建（外部存储缺失）→ [file] 为 null，静默降级，不抛不崩；
 * - 单文件上限 [maxBytes] 后停写（防爆盘）；`close()` 返回丢弃帧数，[bytesWritten] 供日志核对；
 * - muted 期间的帧由调用方不写（隐私契约与电平表一致，见 docs/android_mic_meter.md §3.3）；
 * - 写的是**增益/编码前**的真实拾音；增益后的真值看服务端同段 ogg。
 */
internal class PcmDump(
    dir: File,
    tag: String,
    private val maxBytes: Long = MAX_FILE_BYTES,
    queueFrames: Int = QUEUE_FRAMES,
) {
    companion object {
        /** 临时埋点总开关：true = 每次采集落盘到 `<externalFiles>/audiodump/`（仅调试用）。 */
        // 2026-09-22 真机验证（VOICE 20cm/1m + MIC 1m）完毕，已关闭；
        // 再次采集时置 true 重新构建即可（docs/android_audio_capture.md §3.7）
        const val ENABLED = false

        const val SUBDIR = "audiodump"
        private const val QUEUE_FRAMES = 64
        private const val MAX_FILE_BYTES = 100L * 1024 * 1024
        private const val JOIN_TIMEOUT_MS = 500L
        private val SENTINEL = ShortArray(0)
    }

    private val queue = ArrayBlockingQueue<ShortArray>(queueFrames)
    private val dropped = AtomicInteger(0)

    @Volatile private var running = false
    @Volatile private var capped = false
    @Volatile private var writer: Thread? = null

    /** 本实例落盘文件；目录不可建时为 null（本实例即空转）。 */
    val file: File?

    @Volatile var bytesWritten: Long = 0L
        private set

    init {
        val usable = dir.isDirectory || dir.mkdirs()
        val target = if (usable) File(dir, "pcm_${tag}_${System.currentTimeMillis()}.raw") else null
        file = target
        if (target != null) startWriter(target)
    }

    private fun startWriter(target: File) {
        running = true
        val thread = Thread({
            var out: OutputStream? = null
            var written = 0L
            var buf = ByteArray(0)
            try {
                out = FileOutputStream(target, false)
                while (true) {
                    val frame = queue.take()
                    if (frame === SENTINEL) break
                    if (frame.size * 2 > buf.size) buf = ByteArray(frame.size * 2)
                    for (i in frame.indices) {
                        val s = frame[i].toInt()
                        buf[i * 2] = (s and 0xFF).toByte()
                        buf[i * 2 + 1] = ((s shr 8) and 0xFF).toByte()
                    }
                    out.write(buf, 0, frame.size * 2)
                    written += frame.size * 2
                    bytesWritten = written
                    if (written >= maxBytes) {
                        capped = true
                        break
                    }
                }
                out.flush()
            } catch (_: Exception) {
                // 临时工具：写盘失败静默停写，绝不打断采集热路径
            } finally {
                try { out?.close() } catch (_: Exception) {}
                running = false
            }
        }, "pcm-dump")
        thread.isDaemon = true
        writer = thread
        thread.start()
    }

    /** 读帧线程调用：非阻塞投递一帧（[len] 为有效样本数）。 */
    fun write(frame: ShortArray, len: Int) {
        if (!running || len <= 0) return
        val copy = frame.copyOf(len) // 读循环复用同一 buffer，必须拷贝
        if (!queue.offer(copy)) dropped.incrementAndGet()
    }

    /**
     * 收敛写线程并关闭文件；返回因队列满丢弃的帧数。幂等。
     * 磁盘卡死时最多阻塞 [JOIN_TIMEOUT_MS] 后放弃——写线程 daemon 且只碰本实例的文件，
     * 晚退无害（不会污染后继实例）。
     */
    fun close(): Int {
        val t = writer
        if (t != null) {
            writer = null
            // 队列可能已满：腾位直到哨兵入队，否则写线程永远 take() 不到终止信号
            while (!queue.offer(SENTINEL)) {
                queue.poll() ?: break
            }
            try { t.join(JOIN_TIMEOUT_MS) } catch (_: InterruptedException) {}
        }
        running = false
        return dropped.getAndSet(0)
    }

    /** 单文件达上限后是否已停写（调用方可据此告警）。 */
    val isCapped: Boolean get() = capped
}
