package dev.livachat.app.ui.components

import android.view.View
import android.widget.TextView
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.GeneralLocation
import androidx.test.espresso.action.GeneralSwipeAction
import androidx.test.espresso.action.Press
import androidx.test.espresso.action.Swipe
import androidx.test.espresso.UiController
import androidx.test.espresso.ViewAction
import androidx.test.espresso.matcher.ViewMatchers
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.R
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.ui.theme.LivaChatTheme
import java.time.Instant
import org.hamcrest.CoreMatchers.containsString
import org.hamcrest.Matcher
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 三期：超长 job 结果气泡折叠（docs/android_agent_job_view.md §11）——
 * 折叠形态（行边界裁剪 + 「查看全文 · 还有 N 字」）、短结果不折叠、**无全屏入口（历史页形态）不折叠**。
 */
@RunWith(AndroidJUnit4::class)
class MessageBubbleCollapseTest {

    @get:Rule
    val composeRule = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    /** 入口行前缀（locale 无关，取整句在标记符前的部分）：「查看全文 · 还有 」/「View full text · 」。 */
    private fun entryPrefix() =
        context.resources.getQuantityString(R.plurals.conv_job_remaining_chars, 5, "X").substringBefore("X")

    /** 入口行开头词（不带 · 与计数）：「查看全文」/「View full text」。 */
    private fun entryWord() = entryPrefix().substringBefore(" · ").trim()

    /** 长文本：超过 800 字阈值（§11.9-4）且渲染远超 24 行（测试作用域内固定，保证命中折叠）。 */
    private val longText = (1..40).joinToString("\n") {
        "第 $it 行：这是用于折叠测试的超长 agent job 结果正文内容。"
    }

    private val shortText = "已为你查询东京往返机票，最低价 MU 523，无需凌晨赶往机场。"

    private fun message(text: String) = ChatMessage(
        id = "ai-msg_agent_job_9f8c7b6a5d4e3f2a",
        sessionId = "s1",
        role = MessageRole.AI,
        text = text,
        isFinal = true,
        seq = 0,
        timestamp = Instant.now(),
        source = "agent",
    )

    private fun setContent(text: String, withEntry: Boolean) {
        composeRule.setContent {
            LivaChatTheme {
                MessageBubble(
                    message = message(text),
                    onLinkClick = {},
                    onJobResultClick = if (withEntry) ({ _ -> }) else null,
                )
            }
        }
        composeRule.waitForIdle()
    }

    /** 断言正文本体的裁剪状态：clamped=true 时高度应等于 `JOB_RESULT_COLLAPSE_LINES` 行上沿（行边界裁剪）。 */
    private fun assertBodyClamped(clamped: Boolean, anchor: String) {
        onView(withText(containsString(anchor))).perform(
            object : ViewAction {
                override fun getConstraints(): Matcher<View> =
                    ViewMatchers.isAssignableFrom(TextView::class.java)

                override fun getDescription(): String = "assert body TextView collapse clip"

                override fun perform(uiController: UiController?, view: View?) {
                    val textView = view as TextView
                    val layout = textView.layout!!
                    val clipTop = layout.getLineTop(minOf(JOB_RESULT_COLLAPSE_LINES, layout.lineCount))
                    val msg = "height=${textView.height} clipTop=$clipTop full=${layout.height} " +
                        "lineCount=${layout.lineCount}"
                    if (clamped) {
                        // 行边界裁剪：高度恰为第 24 行（JOB_RESULT_COLLAPSE_LINES）上沿
                        assertEquals(msg, clipTop, textView.height)
                    } else {
                        // 未裁剪：高于裁剪线（或内容本就短于裁剪线）；TextView 高度可能被屏幕高度钳制，
                        // 故不与 layout.height 比较
                        assertTrue(msg, textView.height > clipTop || layout.height <= clipTop)
                    }
                }
            },
        )
    }

    @Test
    fun longJobResult_clippedAtLineBoundary_withEntryRow() {
        setContent(longText, withEntry = true)

        assertBodyClamped(clamped = true, anchor = "第 1 行")
        composeRule.onNodeWithText(entryPrefix(), substring = true).assertExists()
        // 省略号按实机反馈去掉（2026-09-25）：不再有手绘 "…"
        composeRule.onNodeWithText("…").assertDoesNotExist()
    }

    @Test
    fun shortJobResult_notCollapsed_withoutEntryRow() {
        setContent(shortText, withEntry = true)

        assertBodyClamped(clamped = false, anchor = "MU 523")
        composeRule.onNodeWithText(entryWord(), substring = true).assertDoesNotExist()
    }

    @Test
    fun collapsibleByChars_butFewLines_notClipped() {
        // 字数 ≥ 阈值（collapsible 判定为真）但渲染仅 1 行 → 不裁剪、无入口行（fail-open）：
        // 用「长 URL 的短链接文本」撑字数——markdown 长度过阈值，渲染文本只有个位数字符
        val longUrl = "https://example.com/" + "a".repeat(900)
        val text = "[x]($longUrl)"
        setContent(text, withEntry = true)

        assertBodyClamped(clamped = false, anchor = "x")
        composeRule.onNodeWithText(entryWord(), substring = true).assertDoesNotExist()
    }

    @Test
    fun dragFromLink_doesNotScrollBubbleInternally() {
        // 确定性版本：直接派发「链接上 DOWN → 越过 slop 的 MOVE → UP」原始事件。
        // 基类（Touch）的 MOVE 分支会对已登记 DragState 的手势做 widget.scrollTo 内部自滚；
        // 折叠后 layout 高于视图，若自滚会让气泡内容错位（§11 review）。断言 scrollY 保持 0。
        val link = "https://example.com/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        val text = "[$link]($link)" + "补充说明内容。".repeat(80)
        setContent(text, withEntry = true)

        onView(withText(containsString("example.com"))).perform(
            object : ViewAction {
                override fun getConstraints(): Matcher<View> =
                    ViewMatchers.isAssignableFrom(TextView::class.java)

                override fun getDescription(): String = "dispatch link-down drag"

                override fun perform(uiController: UiController?, view: View?) {
                    val textView = view as TextView
                    val t = android.os.SystemClock.uptimeMillis()
                    fun event(action: Int, y: Float) =
                        android.view.MotionEvent.obtain(t, t, action, textView.width / 2f, y, 0)

                    textView.dispatchTouchEvent(event(android.view.MotionEvent.ACTION_DOWN, 12f))
                    textView.dispatchTouchEvent(event(android.view.MotionEvent.ACTION_MOVE, 400f))
                    textView.dispatchTouchEvent(event(android.view.MotionEvent.ACTION_MOVE, 800f))
                    textView.dispatchTouchEvent(event(android.view.MotionEvent.ACTION_UP, 800f))
                }
            },
        )
        composeRule.waitForIdle()

        onView(withText(containsString("example.com"))).perform(
            object : ViewAction {
                override fun getConstraints(): Matcher<View> =
                    ViewMatchers.isAssignableFrom(TextView::class.java)

                override fun getDescription(): String = "assert TextView did not scroll internally"

                override fun perform(uiController: UiController?, view: View?) {
                    val textView = view as TextView
                    assertEquals(
                        "TextView 不得内部自滚（scrollY=${textView.scrollY} layout=${textView.layout?.height} " +
                            "view=${textView.height}）",
                        0,
                        textView.scrollY,
                    )
                }
            },
        )
    }

    @Test
    fun longJobResult_withoutEntry_notCollapsed() {
        // 历史页形态（无 onJobResultClick）：折叠会导致看不全 → 必须不折叠
        setContent(longText, withEntry = false)

        assertBodyClamped(clamped = false, anchor = "第 1 行")
        composeRule.onNodeWithText(entryWord(), substring = true).assertDoesNotExist()
    }
}
