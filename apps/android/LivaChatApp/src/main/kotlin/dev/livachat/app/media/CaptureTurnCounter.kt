package dev.livachat.app.media

import java.util.concurrent.atomic.AtomicLong
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 采集轮标识（docs/fd_pipeline_video_process.md §8）：开相机 / 切换前后摄像头 / 开始录屏的动作点
 * 调 [next] 递增；视频帧 push 时逐帧携带 [current]（App 层维护状态，SDK 直通落线）。
 *
 * 进程级单例——跨会话/重连不重置（服务端只判"变化"，绝对值无语义）；计数从 1 起，
 * 0 保留为"未启用"哨兵（SDK push 参数的默认值，服务端视同缺省）。
 */
@Singleton
class CaptureTurnCounter @Inject constructor() {
    private val gen = AtomicLong(0)

    @Volatile
    var current: Long = 0
        private set

    fun next(): Long {
        val id = gen.incrementAndGet()
        current = id
        return id
    }
}
