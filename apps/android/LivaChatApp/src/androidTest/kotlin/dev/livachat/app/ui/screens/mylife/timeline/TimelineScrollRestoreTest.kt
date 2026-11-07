package dev.livachat.app.ui.screens.mylife.timeline

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeUp
import androidx.lifecycle.viewModelScope
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.data.remote.mylife.MylifeApi
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.ui.screens.mylife.MylifeTheme
import dev.livachat.sdk.LivaChatConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 时间轴滚动位置保持（2026-09-21 真机回归）：从 hotSlotView / Gallery 返回后**不得**重置到最新一天。
 * 根因：粒度归位 effect 在首次组合（返回时重建组合）也执行 → 无条件 scrollToItem(0)。
 * 真机/模拟器执行；MockWebServer + 真 Repository/VM（mockk 与 ART 不兼容）。
 */
@RunWith(AndroidJUnit4::class)
class TimelineScrollRestoreTest {

    @get:Rule
    val composeRule = createComposeRule()

    /**
     * 最新一天的日卡标题前缀（rangeLabel = "8月25日 · 周X"）。
     * 文案随设备语言变化（en = "Aug 25"），必须用 App 同款格式化函数生成，不能硬编码中文。
     */
    private val uiLocale =
        ApplicationProvider.getApplicationContext<android.content.Context>().resources.configuration.locales[0]
    private val newestDay = monthDayText(8, 25, uiLocale)

    private fun overviewJson(): String {
        val base = 1_787_000_000_000L
        val days = (1..25).map { d ->
            val date = "2026-08-%02d".format(d)
            val ts = base + d * 86_400_000L
            """{"date":"$date","counts":{"chat":1},"has_pending":false,""" +
                """"hot_slots":[{"start_ms":$ts,"end_ms":${ts + 60_000},"count":1}]}"""
        }
        return """{"schema_version":1,"timezone":"Asia/Shanghai","days":[${days.joinToString(",")}]}"""
    }

    /** 最新一天标题是否真的在屏内（LazyColumn 回收远处 item：不存在 或 存在但不在视口内都算"不在"。 */
    private fun newestDayVisible(): Boolean {
        val root = composeRule.onRoot().fetchSemanticsNode().boundsInRoot
        return composeRule.onAllNodesWithText(newestDay, substring = true)
            .fetchSemanticsNodes()
            .any { it.boundsInRoot.bottom > root.top && it.boundsInRoot.top < root.bottom }
    }

    /** 诊断用：当前在屏内的日卡标题集合（断言失败时随消息输出）。 */
    private fun visibleDayTitles(): List<String> {
        val root = composeRule.onRoot().fetchSemanticsNode().boundsInRoot
        return (1..25).mapNotNull { d ->
            val t = monthDayText(8, d, uiLocale)
            val onScreen = composeRule.onAllNodesWithText(t, substring = true)
                .fetchSemanticsNodes()
                .any { it.boundsInRoot.bottom > root.top && it.boundsInRoot.top < root.bottom }
            if (onScreen) t else null
        }
    }

    @Test
    fun viewerReturnKeepsScrollPosition() {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path.contains("overview") -> MockResponse()
                        .setHeader("Content-Type", "application/json")
                        .setBody(overviewJson())

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
        val vm = TimelineViewModel(repository, ApplicationProvider.getApplicationContext())
        try {
            composeRule.setContent {
                val nav = rememberNavController()
                MylifeTheme {
                    NavHost(navController = nav, startDestination = "home") {
                        composable("home") {
                            Box {
                                TimelineScreen(
                                    onNavigateToGallery = { _, _, _, _, _ -> },
                                    onNavigateToHotSlot = { _, _, _ -> nav.navigate("hot") },
                                    onNavigateToEventList = { _, _ -> },
                                    onNavigateToAudio = { _, _, _, _ -> },
                                    viewModel = vm,
                                )
                                // 固定入口（悬停右下角）：不依赖滚屏后 chip 的命中测试
                                Button(
                                    onClick = { nav.navigate("hot") },
                                    modifier = Modifier
                                        .align(Alignment.BottomEnd)
                                        .testTag("open_viewer"),
                                ) { Text("viewer") }
                            }
                        }
                        composable("hot") {
                            Column {
                                Text("HOT_STUB")
                                Button(
                                    onClick = { nav.popBackStack() },
                                    modifier = Modifier.testTag("back"),
                                ) { Text("back") }
                            }
                        }
                    }
                }
            }

            // 首屏：最新一天在最上
            composeRule.waitUntil(timeoutMillis = 5_000) {
                composeRule.onAllNodesWithText(newestDay, substring = true)
                    .fetchSemanticsNodes().isNotEmpty()
            }

            // 滚到更早的日期
            repeat(2) { composeRule.onNodeWithTag(TIMELINE_LIST_TAG).performTouchInput { swipeUp() } }
            composeRule.waitForIdle()
            assertFalse("滚离后最新一天不应在屏内（前置条件）；可见=${visibleDayTitles()}", newestDayVisible())

            // 进 hotSlotView 再返回：真实导航往返（home 组合被销毁并重建）
            composeRule.onNodeWithTag("open_viewer").performClick()
            composeRule.waitUntil(timeoutMillis = 5_000) {
                composeRule.onAllNodesWithText("HOT_STUB").fetchSemanticsNodes().isNotEmpty()
            }
            composeRule.onNodeWithTag("back").performClick()
            composeRule.waitUntil(timeoutMillis = 5_000) {
                composeRule.onAllNodesWithTag(TIMELINE_LIST_TAG).fetchSemanticsNodes().isNotEmpty()
            }
            composeRule.waitForIdle()

            // 回归：返回后仍停在原位置（缺陷行为=被重置回最新一天）
            assertFalse("返回后不应回到最新一天；可见=${visibleDayTitles()}", newestDayVisible())
        } finally {
            vm.viewModelScope.cancel()
            scope.cancel()
            server.shutdown()
        }
    }
}
