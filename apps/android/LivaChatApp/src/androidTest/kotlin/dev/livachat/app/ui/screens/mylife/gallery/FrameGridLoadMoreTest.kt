package dev.livachat.app.ui.screens.mylife.gallery

import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.data.remote.mylife.dto.FrameDto
import dev.livachat.app.ui.screens.mylife.gallery.components.FrameGrid
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 帧网格的触底预取（docs/android_timeline_audio_gallery.md §9.11 统一批）：
 * **每个「新触发」只发一次**——失败后 `loadingMore` 回落不得立即重发（否则断网重试风暴，
 * 既有债务 docs/android_timeline_topics.md §8）；上一页成功追加（frames.size 变化）后仍能续填。
 */
@RunWith(AndroidJUnit4::class)
class FrameGridLoadMoreTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun frame(i: Int) = FrameDto(
        tsMs = 1_000L * i,
        mediaUrl = "http://127.0.0.1:1/f$i.jpg",   // 不可达：图片加载失败不影响触发逻辑
        frameId = "f$i",
    )

    @Test
    fun failedLoadMoreDoesNotAutoRetryAndResumesAfterAppend() {
        var calls = 0
        var loadingMore by mutableStateOf(false)
        var frames by mutableStateOf(listOf(frame(1), frame(2), frame(3)))

        composeRule.setContent {
            FrameGrid(
                frames = frames,
                loadingMore = loadingMore,
                gridState = rememberLazyGridState(),
                onFrameClick = {},
                onLoadMore = { calls++ },
            )
        }

        // 3 帧全可见 ⇒ 立即触底 → 恰好触发一次
        composeRule.waitUntil(timeoutMillis = 5_000) { calls >= 1 }
        assertEquals(1, calls)

        // 模拟一次失败的翻页：在飞 → 回落 false
        loadingMore = true
        composeRule.waitForIdle()
        loadingMore = false
        composeRule.waitForIdle()
        Thread.sleep(800)
        assertEquals("失败后不得自动重试（须滚离阈值区再回来）", 1, calls)

        // 上一页成功追加 ⇒ 重新武装、可继续续填
        frames = frames + frame(4)
        composeRule.waitUntil(timeoutMillis = 5_000) { calls >= 2 }
        assertEquals(2, calls)
    }
}
