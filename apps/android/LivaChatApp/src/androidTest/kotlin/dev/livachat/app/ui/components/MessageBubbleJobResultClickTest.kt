package dev.livachat.app.ui.components

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.ui.theme.LivaChatTheme
import java.time.Instant
import org.hamcrest.CoreMatchers.containsString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * agent job 结果气泡的整卡可点入口（docs/android_agent_job_view.md §6）：
 * 正文非链接触摸必须到达气泡 Box（D13 守护主用例——无 D13 或半套改动时必红），
 * 链接命中仍只走 onLinkClick（无双触发），未传回调时气泡无 clickable 语义。
 *
 * AndroidView 内文本不进 Compose 语义树 → 触摸用 Espresso 直接操作 View 层。
 */
@RunWith(AndroidJUnit4::class)
class MessageBubbleJobResultClickTest {

    @get:Rule
    val composeRule = createComposeRule()

    private var jobClicks = 0
    private var lastOffset: Int? = null
    private val links = mutableListOf<String>()

    private fun jobResultMessage(text: String) = ChatMessage(
        id = "ai-msg_agent_job_9f8c7b6a5d4e3f2a",
        sessionId = "s1",
        role = MessageRole.AI,
        text = text,
        isFinal = true,
        seq = 0,
        timestamp = Instant.now(),
        source = "agent",
    )

    private fun setContent(text: String, onJobResultClick: ((Int?) -> Unit)?) {
        composeRule.setContent {
            LivaChatTheme {
                // targetSdk 36 下测试 Activity 在 API 35+ 强制 edge-to-edge，内容顶到状态栏下方。
                // 单行链接气泡高度小，中心点会落进系统状态栏窗口（输入被系统吞掉，Espresso 点击无效）。
                // 模拟真实页面（ConversationScreen 顶部已有状态栏避让）加 inset，保证点击落在 App 窗口内。
                Box(modifier = Modifier.fillMaxSize().statusBarsPadding()) {
                    MessageBubble(
                        message = jobResultMessage(text),
                        onLinkClick = { links.add(it) },
                        onJobResultClick = onJobResultClick,
                    )
                }
            }
        }
        composeRule.waitForIdle()
    }

    @Test
    fun nonLinkBodyTap_invokesJobResultClick() {
        setContent("已为你查询东京往返机票，最低价 MU 523。") { offset ->
            jobClicks++
            lastOffset = offset
        }

        onView(withText(containsString("MU 523"))).perform(click())
        composeRule.waitForIdle()

        assertEquals("正文非链接触摸必须到达气泡 clickable（D13）", 1, jobClicks)
        assertNotNull("点击处字符偏移应被记录（§10.2 全屏落位用）", lastOffset)
    }

    @Test
    fun longTextBodyTap_recordsOffsetFromTapPosition() {
        // 长文本（12 行）：Espresso 点 TextView 中心 → 偏移应落在中段，证明偏移来自点击位置
        val text = (1..12).joinToString("\n") { "第 $it 行：这是用于长文本点击定位测试的正文内容。" }
        setContent(text) { offset -> lastOffset = offset }

        onView(withText(containsString("第 12 行"))).perform(click())
        composeRule.waitForIdle()

        val offset = lastOffset
        assertNotNull("应记录偏移", offset)
        assertTrue(
            "长文本中心点击的偏移应落在中段（实际 $offset / 共 ${text.length} 字）",
            offset!! in (text.length / 4)..(text.length * 3 / 4),
        )
    }

    @Test
    fun withoutCallback_bubbleHasNoClickAction() {
        setContent("已为你查询东京往返机票，最低价 MU 523。", onJobResultClick = null)

        composeRule.onNode(hasClickAction()).assertDoesNotExist()
    }

    @Test
    fun linkTap_opensLinkOnly_notJobResult() {
        setContent("[点我打开链接](https://example.com)") { jobClicks++ }

        // AndroidView 内 Markwon 的 setMarkdown 在 Compose idle 之后才落到 TextView（渲染前文本为原始
        // Markdown，无 ClickableSpan）。测试先等渲染完成，否则点击命中的是原始文本 → 落到整卡点击。
        // 真机较慢时渲染早已完成，掩盖了该竞态（API 36 模拟器稳定复现）。
        composeRule.waitUntil(timeoutMillis = 5_000) {
            runCatching {
                onView(withText("点我打开链接")).check(matches(isDisplayed()))
            }.isSuccess
        }

        onView(withText(containsString("点我打开链接"))).perform(click())
        composeRule.waitForIdle()

        assertEquals(listOf("https://example.com"), links)
        assertEquals("链接命中不得同时触发整卡点击", 0, jobClicks)
    }
}
