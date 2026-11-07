package dev.livachat.app.ui.screens.mylife.hotslot

import androidx.activity.ComponentActivity
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.matcher.ViewMatchers.withText
import org.hamcrest.CoreMatchers.containsString
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.MylifeApi
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.ImageGallerySaver
import dev.livachat.sdk.LivaChatConfig
import java.time.Instant
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.runner.RunWith

/**
 * eventListViewer 屏幕级验证（docs/android_timeline_topics.md §6 仪器）：锚点定位/高亮
 * （含锚点在后续页的追击）、帧查看器、空窗口空态、返回链路。
 * 真机/模拟器执行（前置见 docs/android_timeline_hotslot.md §8.2）；
 * mockk 与 ART 不兼容，故用 MockWebServer + 真 Repository（同 HotSlotComponentsTest）。
 */
@RunWith(AndroidJUnit4::class)
class EventListViewerScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    /** 被点事件时刻（09:12 +08）。 */
    private val anchorTs = Instant.parse("2026-08-21T01:12:00Z").toEpochMilli()

    private fun chatEvent(ts: Long, text: String) =
        """{"ts_ms":$ts,"type":"chat","role":"user","text":"$text","event_ref":"r-$ts"}"""

    private fun agentEvent(ts: Long, text: String) =
        """{"ts_ms":$ts,"type":"agent_job","role":"assistant","text":"$text","event_ref":"a-$ts"}"""

    private fun videoEvent(ts: Long, caption: String) =
        """{"ts_ms":$ts,"type":"video","media_url":"/api/mylife/media/d/s/video/f$ts.jpg",""" +
            """"frame_id":"f$ts","event_ref":"v-$ts","caption":"$caption"}"""

    private fun pageBody(events: List<String>, nextCursor: String?): String =
        """{"days":[{"date":"2026-08-21","events":[${events.joinToString(",")}]}],""" +
            """"total":${events.size},"next_cursor":${nextCursor?.let { "\"$it\"" } ?: "null"}}"""

    /**
     * 起 MockWebServer（/timeline 按 `cursor` query 参数回不同页）并渲染真 Screen；
     * `cursorPages` 未命中的 cursor → 空页（翻页终止）。
     */
    private fun withScreen(
        cursorPages: Map<String?, String>,
        failCursors: Set<String> = emptySet(),
        onBack: () -> Unit = {},
        tsState: MutableState<Long>? = null,
        block: (MutableList<String?>) -> Unit,
    ) {
        // 调度线程（MockWebServer）与测试线程并发读写 → 用线程安全容器
        val cursors = java.util.concurrent.CopyOnWriteArrayList<String?>()
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path.contains("timeline") -> {
                        val cursor = request.requestUrl?.queryParameter("cursor")
                        cursors.add(cursor)
                        if (cursor != null && cursor in failCursors) {
                            MockResponse().setResponseCode(500)   // 翻页失败（风暴用例）
                        } else {
                            MockResponse()
                                .setHeader("Content-Type", "application/json")
                                .setBody(cursorPages[cursor] ?: pageBody(emptyList(), null))
                        }
                    }

                    path.contains("/video/") -> MockResponse()
                        .setHeader("Content-Type", "image/jpeg")
                        .setBody("not-an-image")

                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val config = LivaChatConfig(
            serverUrl = "wss://example.com/v1/ws/stream",
            token = "dev-token",
            deviceId = "dev-1",
            clientVersion = "android-1.0.0",
            logFilePath = "/tmp/livachat.log",
        )
        val repository = MylifeRepository(
            MylifeApi(OkHttpClient(), server.url("/api/mylife"), config, ApplicationProvider.getApplicationContext()),
            server.url("/api/mylife"),
            config,
            scope,
        )
        val vm = HotSlotViewModel(repository, ImageGallerySaver(ApplicationProvider.getApplicationContext()), ApplicationProvider.getApplicationContext())
        try {
            composeRule.setContent {
                EventListViewerScreen(
                    tsMs = tsState?.value ?: anchorTs,
                    pending = false,
                    onNavigateBack = onBack,
                    onOpenLink = {},
                    viewModel = vm,
                )
            }
            block(cursors)
        } finally {
            vm.viewModelScope.cancel()
            scope.cancel()
            server.shutdown()
        }
    }

    private fun awaitText(text: String) {
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty()
        }
    }

    private fun awaitContentDescription(description: String) {
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithContentDescription(description).fetchSemanticsNodes().isNotEmpty()
        }
    }

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)

    @Test
    fun anchorInLaterPageChasesAndHighlights() {
        // 首页只含锚点前的窗口内事件；锚点事件在 cursor=c1 的第二页（R1 追击路径）
        withScreen(
            mapOf(
                null to pageBody(listOf(chatEvent(anchorTs - 5 * 60_000L, "锚点前事件")), nextCursor = "c1"),
                "c1" to pageBody(listOf(chatEvent(anchorTs, "命中事件")), nextCursor = "c2"),
            ),
        ) { cursors ->
            awaitText("命中事件")
            composeRule.waitUntil(timeoutMillis = 5_000) {
                composeRule.onAllNodesWithTag(ANCHOR_HIGHLIGHT_TAG).fetchSemanticsNodes().isNotEmpty()
            }
            composeRule.onNodeWithText("命中事件").assertIsDisplayed()
            composeRule.onNodeWithText("锚点前事件").assertIsDisplayed()
            assertTrue(cursors.contains("c1"))   // 追击确实续页
        }
    }

    @Test
    fun frameRowClickOpensViewerAndDismisses() {
        withScreen(mapOf(null to pageBody(listOf(videoEvent(anchorTs, "画面描述")), nextCursor = null))) {
            composeRule.waitUntil(timeoutMillis = 5_000) {
                composeRule.onAllNodesWithTag("hotSlotFrame0").fetchSemanticsNodes().isNotEmpty()
            }
            composeRule.onNodeWithTag("hotSlotFrame0").performClick()
            awaitText("画面描述")
            composeRule.onNodeWithTag(VIEWER_IMAGE_TAG).performClick()
            composeRule.onNodeWithText("画面描述").assertDoesNotExist()
        }
    }

    @Test
    fun emptyWindowShowsEmptyState() {
        withScreen(mapOf(null to pageBody(emptyList(), nextCursor = null))) {
            awaitText(str(R.string.mylife_hotslot_empty))
        }
    }

    @Test
    fun failedNextPageDoesNotAutoRetry() {
        // 首页含锚点（追击不再介入）+ next_cursor=c1；c1 页 500 ⇒ 翻页失败
        val page1 = pageBody(listOf(chatEvent(anchorTs, "锚点事件")), nextCursor = "c1")
        withScreen(
            cursorPages = mapOf(null to page1),
            failCursors = setOf("c1"),
        ) { cursors ->
            awaitText("锚点事件")
            composeRule.waitUntil(timeoutMillis = 5_000) { cursors.count { it == "c1" } >= 1 }
            // 静置：修复前 loadingMore 回落 → effect 重入 → 立即重发（风暴）
            Thread.sleep(1_500)
            assertEquals("失败后不得自动重试（须滚离阈值区再回来）", 1, cursors.count { it == "c1" })
        }
    }

    @Test
    fun backButtonInvokesCallback() {
        var back = false
        withScreen(
            mapOf(null to pageBody(listOf(chatEvent(anchorTs, "事件")), nextCursor = null)),
            onBack = { back = true },
        ) {
            awaitText("事件")
            composeRule.onNodeWithContentDescription(str(R.string.common_back)).performClick()
            assertEquals(true, back)
        }
    }

    @Test
    fun agentRowClickOpensOverlayAndDismisses() {
        withScreen(mapOf(null to pageBody(listOf(agentEvent(anchorTs, "汇总结果：MU 523 最低价")), null))) {
            // 正文在 AndroidView(TextView) 内、不进 Compose 语义树 → 以行头「Agent任务」判就绪（同
            // HotSlotComponentsTest 的 Espresso 断言口径）
            awaitText(str(R.string.mylife_agent_task))

            // 点 agent 行正文（Espresso 走真实触摸链路）→ 覆盖层出现（宿主身份解析命中）
            // 偶发 INJECT_EVENTS（注入时活动窗口未获焦）→ 先等获焦再注入
            composeRule.waitUntil(timeoutMillis = 5_000) { composeRule.activity.hasWindowFocus() }
            onView(withText(containsString("汇总结果"))).perform(click())
            awaitContentDescription(str(R.string.common_close))
            composeRule.onNodeWithContentDescription(str(R.string.common_close)).performClick()
            composeRule.onNodeWithContentDescription(str(R.string.common_close)).assertDoesNotExist()
        }
    }

    @Test
    fun agentOverlay_closesWhenWindowReloads() {
        // 窗口移出锚点 → 重取（走 load() 入口）→ 覆盖层随之关闭（§12.4：tz 变化/重取 → 清；
        // 纯「身份解析未命中」守卫由 AgentJobResultHostTest 覆盖，O22）
        val tsState = mutableStateOf(anchorTs)
        withScreen(
            mapOf(null to pageBody(listOf(agentEvent(anchorTs, "汇总结果：MU 523 最低价")), null)),
            tsState = tsState,
        ) {
            awaitText(str(R.string.mylife_agent_task))
            composeRule.waitUntil(timeoutMillis = 5_000) { composeRule.activity.hasWindowFocus() }
            onView(withText(containsString("汇总结果"))).perform(click())
            awaitContentDescription(str(R.string.common_close))

            composeRule.runOnIdle { tsState.value = anchorTs + 3_600_000L }
            composeRule.waitUntil(timeoutMillis = 5_000) {
                composeRule.onAllNodesWithContentDescription(str(R.string.common_close)).fetchSemanticsNodes().isEmpty()
            }
            awaitText(str(R.string.mylife_hotslot_empty))   // 重取确实发生（新窗口无事件）
        }
    }
}
