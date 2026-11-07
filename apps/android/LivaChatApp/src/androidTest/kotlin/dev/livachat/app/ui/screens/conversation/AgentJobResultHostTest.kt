package dev.livachat.app.ui.screens.conversation

import androidx.activity.ComponentActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.R
import dev.livachat.app.ui.theme.LivaChatTheme
import java.time.Instant
import org.hamcrest.CoreMatchers.containsString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 覆盖层宿主（docs/android_agent_job_view.md §12.3.3 / O22）：身份解析 + 未命中自关窗 + 渲染。
 * 4 个调用点（会话 / History / eventListViewer / hotslotView）的守卫由此一处用例守护。
 */
@RunWith(AndroidJUnit4::class)
class AgentJobResultHostTest {

    @get:Rule
    val rule = createAndroidComposeRule<ComponentActivity>()

    private var dismissCount = 0

    private fun setContent(
        viewId: String?,
        resolve: (String) -> AgentJobPayload?,
    ) {
        rule.setContent {
            LivaChatTheme {
                AgentJobResultHost(
                    viewId = viewId,
                    initialCharOffset = null,
                    resolve = resolve,
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
    fun resolvedPayload_rendersOverlay() {
        setContent(viewId = "k1") { id ->
            if (id == "k1") AgentJobPayload("已为你查询东京往返机票，最低价 MU 523。", Instant.now()) else null
        }

        rule.onNodeWithContentDescription(str(R.string.common_close)).assertExists()
        onView(withText(containsString("MU 523"))).check(matches(isDisplayed()))
        assertEquals("解析命中不应触发关窗", 0, dismissCount)
    }

    @Test
    fun unresolvedIdentity_autoDismisses() {
        // 重取后事件消失 / 消息被删：身份解析未命中 → 自动关窗，防状态悬空
        setContent(viewId = "gone") { null }

        rule.waitForIdle()
        assertEquals(1, dismissCount)
        rule.onNodeWithContentDescription(str(R.string.common_close)).assertDoesNotExist()
    }

    @Test
    fun nullViewId_rendersNothing() {
        setContent(viewId = null) { AgentJobPayload("不应被解析", Instant.now()) }

        rule.onNodeWithContentDescription(str(R.string.common_close)).assertDoesNotExist()
        assertEquals("未打开时不应触发关窗", 0, dismissCount)
    }

    @Test
    fun missAfterHit_keepsLastPayload_whenOptedIn() {
        // 时间线两屏（分页窗口，§12.9）：Q11 轮询重取把 rows 截回首页 → 未命中 ≠ 事件消失，
        // 用最近一次载荷兜底继续展示，不得把正在阅读的用户踢出
        var present by mutableStateOf(true)
        rule.setContent {
            LivaChatTheme {
                AgentJobResultHost(
                    viewId = "k1",
                    initialCharOffset = null,
                    resolve = { if (present) AgentJobPayload("兜底正文 MU 523", Instant.now()) else null },
                    onDismiss = { dismissCount++ },
                    onLinkClick = {},
                    keepLastPayloadOnMiss = true,
                )
            }
        }
        rule.waitForIdle()
        rule.runOnIdle { present = false }
        rule.waitForIdle()

        assertEquals("分页窗口下未命中不得关窗", 0, dismissCount)
        onView(withText(containsString("兜底正文"))).check(matches(isDisplayed()))
    }

    @Test
    fun reopenedDifferentId_doesNotReusePreviousPayload() {
        // 兜底载荷以 viewId 为键 remember：关闭再换一条（未命中）不得复用上一条正文
        var viewId by mutableStateOf<String?>("k1")
        rule.setContent {
            LivaChatTheme {
                AgentJobResultHost(
                    viewId = viewId,
                    initialCharOffset = null,
                    resolve = { key -> if (key == "k1") AgentJobPayload("第一条正文", Instant.now()) else null },
                    onDismiss = { dismissCount++ },
                    onLinkClick = {},
                    keepLastPayloadOnMiss = true,
                )
            }
        }
        rule.waitForIdle()
        onView(withText(containsString("第一条正文"))).check(matches(isDisplayed()))

        rule.runOnIdle { viewId = null }
        rule.waitForIdle()
        rule.runOnIdle { viewId = "k2" }
        rule.waitForIdle()

        assertEquals("换身份 + 未命中 → 关窗（不得显示上一条）", 1, dismissCount)
        rule.onNodeWithContentDescription(str(R.string.common_close)).assertDoesNotExist()
    }

    @Test
    fun missAfterHit_dismisses_byDefault() {
        // 会话/History（会话全量列表）：重取后事件真消失 → 关窗（默认口径）
        var present by mutableStateOf(true)
        rule.setContent {
            LivaChatTheme {
                AgentJobResultHost(
                    viewId = "k1",
                    initialCharOffset = null,
                    resolve = { if (present) AgentJobPayload("会话正文", Instant.now()) else null },
                    onDismiss = { dismissCount++ },
                    onLinkClick = {},
                )
            }
        }
        rule.waitForIdle()
        rule.runOnIdle { present = false }
        rule.waitForIdle()

        assertEquals(1, dismissCount)
        rule.onNodeWithContentDescription(str(R.string.common_close)).assertDoesNotExist()
    }
}
