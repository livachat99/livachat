package dev.livachat.app.media

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * toggle 状态机（docs/android_timeline_audio_gallery.md §4.5/§4.6、§6-2）：
 * 播放器实现（ExoAudioPlayer，需真机）与这里共用同一纯函数决策。
 */
class AudioPlayerToggleTest {

    private val idle = AudioPlayer.State()

    @Test
    fun `空闲态首次点击从 0 起播`() {
        assertEquals(ToggleAction.Start("a", "u"), decideToggle(idle, "a", "u"))
    }

    @Test
    fun `播放中同 key 点击 = 暂停`() {
        val playing = AudioPlayer.State(key = "a", isPlaying = true, positionMs = 23_000)
        assertEquals(ToggleAction.Pause, decideToggle(playing, "a", "u"))
    }

    @Test
    fun `暂停态同 key 点击 = 续播（不回到 0）`() {
        val paused = AudioPlayer.State(key = "a", isPlaying = false, positionMs = 23_000)
        assertEquals(ToggleAction.Resume, decideToggle(paused, "a", "u"))
    }

    @Test
    fun `异 key 点击 = 切段从头播`() {
        val playing = AudioPlayer.State(key = "a", isPlaying = true, positionMs = 23_000)
        assertEquals(ToggleAction.Start("b", "u2"), decideToggle(playing, "b", "u2"))
    }

    @Test
    fun `error 态同 key 点击 = 清错误从头播（重试语义）`() {
        val error = AudioPlayer.State(key = "a", isPlaying = false, positionMs = 40_000, error = "播放失败")
        assertEquals(ToggleAction.Start("a", "u"), decideToggle(error, "a", "u"))
    }

    @Test
    fun `播完复位后再点 = 从头播`() {
        // STATE_ENDED 后实现把 isPlaying/position 复位（key 保留）→ 走 Resume 分支（位置已在 0）
        val ended = AudioPlayer.State(key = "a", isPlaying = false, positionMs = 0L)
        assertEquals(ToggleAction.Resume, decideToggle(ended, "a", "u"))
    }
}
