package dev.livachat.app.media

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 进程级前后台事实（docs/android_audio_capture.md §3.6）。
 *
 * Application 只发布（onStart/onStop 各一行），消费方按需 collect——后台释放麦克风的
 * 门控决策留在会话与 controller 层（docs/android_audio_capture.md §3.6）。
 */
@Singleton
class AppForegroundState @Inject constructor() {
    // 初始 true 安全：进程只能由前台组件拉起（两 FGS 均 START_NOT_STICKY、无 boot receiver），
    // 不存在"进程诞生于后台"的路径
    private val _isForeground = MutableStateFlow(true)
    val isForeground: StateFlow<Boolean> = _isForeground.asStateFlow()

    fun onEnterForeground() {
        _isForeground.value = true
    }

    fun onEnterBackground() {
        _isForeground.value = false
    }
}
