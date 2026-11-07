package dev.livachat.app.ui.screens.mylife.audiogallery

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.click
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.ui.screens.mylife.audiogallery.components.PROGRESS_BAR_TAG
import dev.livachat.app.ui.screens.mylife.audiogallery.components.SegmentProgressBar
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 进度条手势的回调新鲜度（docs/android_timeline_audio_gallery.md §9.9 F1）：
 * `pointerInput(Unit)` 的手势协程只建一次 ⇒ duration/onScrub/onScrubEnd 必须走
 * `rememberUpdatedState`；否则重组后手势仍调用**首次组合**的闭包（陈旧捕获，静默失效）。
 */
@RunWith(AndroidJUnit4::class)
class SegmentProgressBarTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun gestureCallsLatestCallbacksAfterRecomposition() {
        var fraction by mutableStateOf(0f)   // 驱动重组合
        var generation = 0                   // 普通变量：每次组合读取一次 → 陈旧闭包可观测
        var scrubGen = -1L
        var endGen = -1L

        composeRule.setContent {
            val gen = generation             // 本代闭包携带的值
            SegmentProgressBar(
                fraction = fraction,
                durationMs = 60_000L,
                onScrub = { scrubGen = gen.toLong() },
                onScrubEnd = { endGen = gen.toLong() },
                modifier = Modifier.fillMaxWidth().height(32.dp),
            )
        }
        composeRule.waitForIdle()

        // 重组一次：新 lambda 的 gen=1（fraction 变化驱动重组合）
        generation = 1
        fraction = 0.5f
        composeRule.waitForIdle()

        // 点击（无位移）→ onScrubEnd 分支
        composeRule.onNodeWithTag(PROGRESS_BAR_TAG).performTouchInput { click(center) }
        composeRule.waitForIdle()
        assertEquals("onScrubEnd 应为重组后的新闭包（gen=1）", 1L, endGen)

        // 横向拖动 → onScrub 分支（过 touch slop 后才计入）
        composeRule.onNodeWithTag(PROGRESS_BAR_TAG).performTouchInput { swipeLeft() }
        composeRule.waitForIdle()
        assertEquals("onScrub 应为重组后的新闭包（gen=1）", 1L, scrubGen)
    }
}
