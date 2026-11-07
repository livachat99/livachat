package dev.livachat.app.ui.screens.conversation

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.R
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.ui.theme.LivaChatTheme
import java.time.Instant
import org.hamcrest.CoreMatchers.containsString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * agent job 结果全屏覆盖层（docs/android_agent_job_view.md §6）：正文可见（长文本 + 表格）、
 * close 按钮与系统返回键都能关闭。方向效应见 [AgentJobResultOrientationTest]。
 */
@RunWith(AndroidJUnit4::class)
class AgentJobResultOverlayTest {

    @get:Rule
    val rule = createAndroidComposeRule<ComponentActivity>()

    private var dismissCount = 0

    private val resultText = """
        已为你查询东京 9/28–10/2 的往返机票，结果如下：

        | 航班 | 出发 | 到达 | 含税价格 |
        |---|---|---|---|
        | MU 523 | 09-28 10:15 | 09-28 14:30 | ¥2,150 |
        | NH 920 | 09-28 09:05 | 09-28 13:20 | ¥2,480 |

        **推荐 MU 523**：价格最低，无需凌晨赶往机场。
    """.trimIndent()

    private fun setContent() {
        rule.setContent {
            LivaChatTheme {
                AgentJobResultOverlay(
                    text = resultText,
                    timestamp = java.time.Instant.now(),
                    onDismiss = { dismissCount++ },
                    onLinkClick = {},
                )
            }
        }
        rule.waitForIdle()
    }

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)

    @Test
    fun rendersHeader_andFullMarkdownBody() {
        setContent()

        // 标题文字按实机反馈去掉（2026-09-25）：头行只剩 Agent 图标 + 到达时间 + 关闭钮
        val jobsTitlePrefix =
            context.resources.getQuantityString(R.plurals.conv_agent_jobs_title, 1, 1).substringBefore("(").trim()
        rule.onNodeWithText(jobsTitlePrefix).assertDoesNotExist()
        rule.onNodeWithContentDescription(str(R.string.common_close)).assertExists()
        // 正文为 AndroidView 文本（不进 Compose 语义树）→ Espresso 断言表格内容可见
        onView(withText(containsString("MU 523"))).check(matches(isDisplayed()))
        onView(withText(containsString("无需凌晨赶往机场"))).check(matches(isDisplayed()))
    }

    @Test
    fun closeButton_dismisses() {
        setContent()

        rule.onNodeWithContentDescription(str(R.string.common_close)).performClick()
        rule.waitForIdle()

        assertEquals(1, dismissCount)
    }

    @Test
    fun systemBack_dismisses() {
        setContent()

        rule.runOnUiThread { rule.activity.onBackPressedDispatcher.onBackPressed() }
        rule.waitForIdle()

        assertEquals(1, dismissCount)
    }
}
