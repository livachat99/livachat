package dev.livachat.app.ui.screens.conversation

import androidx.activity.ComponentActivity
import androidx.compose.foundation.ScrollState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeUp
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.ui.theme.LivaChatTheme
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 覆盖层的点击处落位（docs/android_agent_job_view.md §10.2）：
 * 给定 `initialCharOffset` 应滚到命中行附近；为 null 时保持顶部。
 * 通过注入 `scrollState` 做断言（生产默认自持）。
 */
@RunWith(AndroidJUnit4::class)
class AgentJobResultOverlayScrollTest {

    @get:Rule
    val rule = createAndroidComposeRule<ComponentActivity>()

    // 40 行：覆盖 900dp+ 高屏（API 36 模拟器）也能产生可滚动内容，避免落位测试因屏幕过高而不滚动。
    private val longText = (1..40).joinToString("\n") { "第 $it 行：这是用于全屏落位测试的长文本正文内容。" }

    private fun setContent(initialCharOffset: Int?, scrollState: ScrollState) {
        rule.setContent {
            LivaChatTheme {
                AgentJobResultOverlay(
                    text = longText,
                    timestamp = java.time.Instant.now(),
                    onDismiss = {},
                    onLinkClick = {},
                    initialCharOffset = initialCharOffset,
                    scrollState = scrollState,
                )
            }
        }
    }

    @Test
    fun initialCharOffset_scrollsToTargetLine() {
        val scrollState = ScrollState(0)
        setContent(initialCharOffset = longText.indexOf("第 18 行"), scrollState = scrollState)

        rule.waitUntil(5_000) { scrollState.value > 0 }

        assertTrue("应滚到后段命中行（实际 ${scrollState.value}）", scrollState.value > 0)
    }

    @Test
    fun initialCharOffset_earlyLine_staysAtTop() {
        val scrollState = ScrollState(0)
        setContent(initialCharOffset = 0, scrollState = scrollState)

        rule.waitForIdle()

        // 文首行减去 1/3 视口后为负 → clamp 到 0（§10.2 边界）
        assertEquals(0, scrollState.value)
    }

    @Test
    fun nullOffset_keepsTopPosition() {
        val scrollState = ScrollState(0)
        setContent(initialCharOffset = null, scrollState = scrollState)

        rule.waitForIdle()

        assertEquals(0, scrollState.value)
    }

    // ---- D17（§13）：滚动位置跨导航保持 ----

    private val scrollText =
        (1..80).joinToString("\n") { "第 $it 行：这是用于全屏落位往返测试的长文本正文内容。" }

    @Test
    fun scrollPosition_survivesRecreation_andIsNotReplaced() {
        val scrollState = ScrollState(0)
        var providerKey by mutableStateOf("overlay")
        rule.setContent {
            val holder = rememberSaveableStateHolder()
            LivaChatTheme {
                holder.SaveableStateProvider(providerKey) {
                    if (providerKey == "overlay") {
                        AgentJobResultOverlay(
                            text = scrollText,
                            timestamp = java.time.Instant.now(),
                            onDismiss = {},
                            onLinkClick = {},
                            initialCharOffset = scrollText.indexOf("第 30 行"),
                            scrollState = scrollState,
                        )
                    }
                }
            }
        }

        // 首次打开：落位生效
        rule.waitUntil(5_000) { scrollState.value > 0 }
        val placedValue = scrollState.value

        // 用户继续下滑到别处
        rule.onRoot().performTouchInput { swipeUp() }
        rule.waitForIdle()
        val scrolledValue = scrollState.value
        assertTrue(
            "应滚离落位点（placed=$placedValue, scrolled=$scrolledValue）",
            scrolledValue > placedValue,
        )

        // 导航离开（目的地销毁：saveable 交 holder 保存）→ 返回（目的地重建）
        rule.runOnUiThread { providerKey = "gone" }
        rule.waitForIdle()
        rule.runOnUiThread { providerKey = "overlay" }
        rule.waitForIdle()
        rule.mainClock.advanceTimeBy(1_500)
        rule.waitForIdle()

        // D17：恢复的位置不得被落位重放顶掉（回归时会被 scrollTo 回 placedValue）
        assertEquals(scrolledValue, scrollState.value)
    }

    @Test
    fun reopen_afterClose_placesAgain() {
        val scrollState = ScrollState(0)
        var show by mutableStateOf(true)
        var offset by mutableStateOf<Int?>(scrollText.indexOf("第 30 行"))
        rule.setContent {
            LivaChatTheme {
                if (show) {
                    AgentJobResultOverlay(
                        text = scrollText,
                        timestamp = java.time.Instant.now(),
                        onDismiss = {},
                        onLinkClick = {},
                        initialCharOffset = offset,
                        scrollState = scrollState,
                    )
                }
            }
        }
        rule.waitUntil(5_000) { scrollState.value > 0 }

        // 关闭覆盖层（离开组合 → placed/滚动注册注销）
        rule.runOnUiThread { show = false }
        rule.waitForIdle()
        // 再次打开（偏移 0 → 目标 clamp 到 0）：必须重新落位
        rule.runOnUiThread { offset = 0; show = true }
        rule.waitForIdle()
        rule.mainClock.advanceTimeBy(1_500)
        rule.waitForIdle()

        // 若 placed 未随关闭注销，此处会停在旧落位值（>0）→ fail
        assertEquals(0, scrollState.value)
    }
}
