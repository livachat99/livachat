package dev.livachat.app.media

import kotlinx.coroutines.flow.StateFlow

/**
 * 语音段播放器（docs/android_timeline_audio_gallery.md §4.6）：全局单播——同一时刻只有一段在播。
 * 抽接口是为了单测可注入 fake（既有 mylife 单测是纯 JVM + MockK，无 Robolectric）。
 */
interface AudioPlayer {

    val state: StateFlow<State>

    /** 同 key → 暂停/续播（error 态 = 清错误 + 从头播）；异 key → 切段从 0 起播。 */
    fun toggle(key: String, url: String)

    /** 定位（拖动松手 / 点击轨道）。 */
    fun seekTo(ms: Long)

    /** 复位并释放（离屏、错误恢复）。 */
    fun stop()

    data class State(
        val key: String? = null,          // 播放身份（调用方传 eventRef）
        val isPlaying: Boolean = false,
        val isBuffering: Boolean = false,
        val positionMs: Long = 0L,
        val durationMs: Long = 0L,
        val error: String? = null,
    )
}

/** toggle 决策（纯函数，JVM 可单测）：实现与测试共用同一状态机（§6-2「toggle 状态机」用例）。 */
internal sealed interface ToggleAction {
    /** 从 0 起播：异 key 切段；同 key 且 error = 清错误 + 从头播（§4.6 重试语义）。 */
    data class Start(val key: String, val url: String) : ToggleAction
    data object Pause : ToggleAction
    data object Resume : ToggleAction
}

internal fun decideToggle(state: AudioPlayer.State, key: String, url: String): ToggleAction = when {
    state.key != key -> ToggleAction.Start(key, url)
    state.error != null -> ToggleAction.Start(key, url)
    state.isPlaying -> ToggleAction.Pause
    else -> ToggleAction.Resume
}
