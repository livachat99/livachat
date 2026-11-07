package dev.livachat.app.ui.screens.mylife.hotslot

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.width
import android.view.View
import android.widget.TextView
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.UiController
import androidx.test.espresso.ViewAction
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.hasLinks
import androidx.test.espresso.matcher.ViewMatchers.isAssignableFrom
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.MylifeApi
import dev.livachat.app.data.remote.mylife.dto.DayTopicDto
import dev.livachat.app.data.remote.mylife.dto.EventType
import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.ImageGallerySaver
import dev.livachat.app.ui.screens.mylife.hotslot.components.FrameRowItem
import dev.livachat.app.ui.screens.mylife.hotslot.components.HotSlotRowItem
import dev.livachat.app.ui.screens.mylife.timeline.Granularity
import dev.livachat.app.ui.screens.mylife.timeline.HotSlot
import dev.livachat.app.ui.screens.mylife.timeline.PeriodDigest
import dev.livachat.app.ui.screens.mylife.timeline.components.PeriodCard
import dev.livachat.sdk.LivaChatConfig
import java.time.Instant
import java.util.TimeZone
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.hamcrest.CoreMatchers.containsString
import org.hamcrest.CoreMatchers.not
import org.hamcrest.Description
import org.hamcrest.TypeSafeMatcher
import org.junit.Assert.assertEquals
import org.hamcrest.Matcher
import dev.livachat.app.ui.components.JOB_RESULT_COLLAPSE_LINES
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.runner.RunWith

/**
 * hotSlotView 渲染级验证（docs/android_timeline_hotslot.md §6）：chip 文案/回调、文本行、
 * 帧行末行 3 列等宽、pending 空态。设备/模拟器上运行（JVM 侧同类断言见 src/test 纯逻辑用例）。
 */
@RunWith(AndroidJUnit4::class)
class HotSlotComponentsTest {

    @get:Rule
    val composeRule = createComposeRule()

    private val slot = HotSlot(
        startMs = Instant.parse("2026-08-21T01:12:00Z").toEpochMilli(), // 09:12 +08
        endMs = Instant.parse("2026-08-21T01:30:00Z").toEpochMilli(),
    )

    private fun digest(pending: Boolean, audioCount: Int = 0, hotSlots: List<HotSlot> = listOf(slot)) =
        PeriodDigest(
            key = "2026-08-21",
            granularity = Granularity.DAY,
            rangeLabel = "8月21日 · 周五",
            hotSlots = hotSlots,
            summaryLines = emptyList(),
            summaryLimit = 3,
            topics = emptyList(),
            images = emptyList(),
            imageTotal = 0,
            hasPending = pending,
            audioCount = audioCount,
        )

    private fun frame(ts: Long, tag: String) = HotSlotEvent(
        tsMs = ts, type = EventType.VIDEO, frameId = tag,
        mediaUrl = "http://localhost/video/$tag.jpg", eventRef = "ref-$tag",
    )

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)

    /** 入口行前缀（locale 无关）：「查看全文 · 还有 」/「View full text · 」。 */
    private fun entryPrefix() =
        context.resources.getQuantityString(R.plurals.conv_job_remaining_chars, 5, "X").substringBefore("X")

    /** 入口行开头词：「查看全文」/「View full text」。 */
    private fun entryWord() = entryPrefix().substringBefore(" · ").trim()

    @Test
    fun chipShowsStartTimeOnlyAndClickCarriesPending() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            var clicked: Pair<HotSlot, Boolean>? = null
            composeRule.setContent {
                PeriodCard(
                    digest = digest(pending = true),
                    onBodyClick = null,
                    onHotSlotClick = { s, p -> clicked = s to p },
                    onImageClick = {},
                )
            }
            composeRule.onNodeWithText("▸ 09:12").assertIsDisplayed()
            composeRule.onNodeWithText("▸ 09:12").performClick()
            assertEquals(slot to true, clicked)
        } finally {
            TimeZone.setDefault(original)
        }
    }

    @Test
    fun topicChipsRenderAllAndReportClick() {
        var clicked: DayTopicDto? = null
        var bodyClicked = false
        composeRule.setContent {
            PeriodCard(
                digest = digest(pending = false).copy(
                    topics = listOf("主题一", "主题二", "主题三", "主题四").map { DayTopicDto(it) },
                ),
                onBodyClick = { bodyClicked = true },
                onHotSlotClick = { _, _ -> },
                onImageClick = {},
                onTopicClick = { clicked = it },
            )
        }
        // 全量渲染（>3 个的回归保护，§3.1）
        composeRule.onNodeWithText("主题四").assertIsDisplayed()
        composeRule.onNodeWithText("主题二").performClick()
        assertEquals("主题二", clicked?.topic)
        // 内层 chip 先消费点击：不触发卡片本体（矩阵 #18）
        assertEquals(false, bodyClicked)
    }

    @Test
    fun bubbleRowRendersMinuteLabelAndBody() {
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text(
                    key = "t-1", timeLabel = "09:12", tsMs = 0L,
                    side = BubbleSide.USER, body = "你好世界 (转录)",
                ),
            )
        }
        composeRule.onNodeWithText("09:12").assertIsDisplayed()
        composeRule.onNodeWithText("你好世界 (转录)").assertIsDisplayed()
        // D4=A：不再有「(用户)」前缀标签
        composeRule.onNodeWithText("(用户) 你好世界 (转录)").assertDoesNotExist()
    }

    @Test
    fun bubblesAlignBySideAndMinuteLabelAppearsOncePerMinute() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            val start = Instant.parse("2026-08-21T01:12:00Z").toEpochMilli() // 09:12 +08
            val ctx = ApplicationProvider.getApplicationContext<android.content.Context>()
            val rows = buildHotSlotRows(
                listOf(
                    HotSlotEvent(tsMs = start, type = EventType.CHAT, role = "user", text = "右侧", eventRef = "r1"),
                    HotSlotEvent(tsMs = start + 10_000, type = EventType.CHAT, role = "assistant", text = "左侧", eventRef = "r2"),
                ),
                ctx.resources.configuration.locales[0],
                ctx.getString(R.string.conv_msg_suffix_transcript),
                ctx.getString(R.string.conv_msg_suffix_original),
                ctx.getString(R.string.conv_msg_suffix_translation),
            )
            composeRule.setContent {
                Column { rows.forEach { HotSlotRowItem(it) } }
            }
            // 分钟标签整列只出现一次（同分钟第二行无标签）
            composeRule.onAllNodesWithText("09:12").assertCountEquals(1)
            // 左右对齐：user 气泡起点在 assistant 气泡右侧
            val userLeft = composeRule.onNodeWithText("右侧").fetchSemanticsNode().boundsInRoot.left
            val aiLeft = composeRule.onNodeWithText("左侧").fetchSemanticsNode().boundsInRoot.left
            assertTrue("user 气泡应在右、assistant 在左", userLeft > aiLeft)
        } finally {
            TimeZone.setDefault(original)
        }
    }

    // ---- §12 agent_job 正文 Markdown（AndroidView 内文本不进 compose 语义树 → Espresso 断言）----

    @Test
    fun agentRowRendersMarkdown() {
        // 传了 onLinkClick → agent 行走 MarkdownContent（AndroidView 文本不进 compose 语义树 → Espresso 断言）
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text(
                    key = "t-a", timeLabel = null, tsMs = 0L, side = BubbleSide.AGENT,
                    body = "见 [文档](https://example.com) 与 **加粗**",
                ),
                onLinkClick = { },
            )
        }
        composeRule.waitForIdle()

        // Markdown 生效：渲染后无字面标记（链接语法与强调标记均被吃掉）
        onView(withText(containsString("加粗"))).check(matches(not(withText(containsString("**")))))
        onView(withText(containsString("文档"))).check(matches(not(withText(containsString("](")))))
    }

    @Test
    fun agentTableOnlyBodyRendersTable() {
        // 回归保护（2026-09-22 实证）：气泡 wrap-content 下 Markwon 表格会因 0 宽首次测量把列宽坍缩为 0，
        // 此时 TextView 宽度只剩 Agent 头行宽度（~64dp）→ 用宽度断言判别（文本断言看不见该故障）
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text(
                    key = "t-tbl", timeLabel = null, tsMs = 0L, side = BubbleSide.AGENT,
                    body = "| 项 | 值 |\n|---|---|\n| 耗时 | 12s |",
                ),
                onLinkClick = { },
            )
        }
        composeRule.waitForIdle()
        // 锚点用「唯一的 TextView」而非文本匹配：表格由 TableRowSpan 绘制，TextView 的 text 为空串
        onView(isAssignableFrom(TextView::class.java)).check(matches(hasWidthAtLeastDp(140f)))
    }

    /** TextView 宽度 ≥ minDp（密度无关）：Markwon 表格坍缩时宽度远低于此（见用例注释）。 */
    private fun hasWidthAtLeastDp(minDp: Float) = object : TypeSafeMatcher<View>() {
        override fun describeTo(description: Description) {
            description.appendText("view width >= ${minDp}dp")
        }

        override fun matchesSafely(view: View): Boolean =
            view.width >= minDp * view.resources.displayMetrics.density
    }

    @Test
    fun agentNumbersAreNotLinkified() {
        // §13：Linkify 掩码收为 WEB_URLS —— 数字/日期串不再被当成电话成链（真机 Linkify 才会匹配，
        // Robolectric 下号码匹配不生效 → 守卫必须放在 androidTest；判别方式 = TextView 无链接（hasLinks））
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text(
                    key = "t-num", timeLabel = null, tsMs = 0L, side = BubbleSide.AGENT,
                    body = "收 7,764.70 与 27,122.09，9/21 15:17 快照（9/14-9/18）",
                ),
                onLinkClick = { },
            )
        }
        composeRule.waitForIdle()
        onView(withText(containsString("7,764.70"))).check(matches(not(hasLinks())))
    }

    @Test
    fun chatAssistantRowStaysPlainText() {
        // R1=A：非 agent 行仍为纯 Text（正文进 compose 语义树，字面标记原样可见）
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text(
                    key = "t-c", timeLabel = null, tsMs = 0L, side = BubbleSide.AI,
                    body = "**加粗** 文本",
                ),
                onLinkClick = { },
            )
        }
        composeRule.onNodeWithText("**加粗** 文本").assertIsDisplayed()
    }

    @Test
    fun agentRowFallsBackToPlainTextWithoutLinkCallback() {
        // R4=A：无 onLinkClick（组件默认）→ agent 行回退纯 Text
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text(
                    key = "t-a2", timeLabel = null, tsMs = 0L, side = BubbleSide.AGENT,
                    body = "**加粗** 文本",
                ),
            )
        }
        composeRule.onNodeWithText("**加粗** 文本").assertIsDisplayed()
    }

    @Test
    fun frameRowKeepsThreeColumnWidthOnPartialLastRow() {
        composeRule.setContent {
            Box(modifier = Modifier.width(300.dp)) {
                FrameRowItem(frames = listOf(frame(1000L, "a"), frame(2000L, "b")))
            }
        }
        val density = composeRule.density
        val expectedWidthPx = with(density) { ((300f - 2 * 4f) / 3f).dp.toPx() } // 两处 4dp 间距
        val tolerancePx = with(density) { 1.dp.toPx() }

        val first = composeRule.onNodeWithTag("hotSlotFrame0").fetchSemanticsNode().boundsInRoot
        val second = composeRule.onNodeWithTag("hotSlotFrame1").fetchSemanticsNode().boundsInRoot
        assertEquals(0f, first.left, tolerancePx)
        assertEquals(expectedWidthPx, first.width, tolerancePx)
        // 第二张紧邻第一张（间距 4dp），右侧留出等宽占位 → 每行恒 3 列
        assertEquals(with(density) { 4.dp.toPx() }, second.left - first.right, tolerancePx)
        assertEquals(expectedWidthPx, second.width, tolerancePx)
    }

    @Test
    fun pendingEmptyHintShowsCopyAndRefreshEntry() {
        var retried = false
        composeRule.setContent { PendingEmptyHint(onRetry = { retried = true }) }
        composeRule.onNodeWithText(str(R.string.mylife_analyzing_content)).assertIsDisplayed()
        composeRule.onNodeWithText(str(R.string.mylife_refresh)).performClick()
        assertEquals(true, retried)
    }

    // ---- §9 帧全屏查看器 ----

    /** 查看器用帧：mediaUrl 置空 → AsyncImage 空 model 不触网（§9.6）。 */
    private fun viewerFrame(caption: String?) = HotSlotEvent(
        tsMs = 1000L, type = EventType.VIDEO, frameId = "a",
        mediaUrl = null, caption = caption, eventRef = "ref-a",
    )

    @Test
    fun frameRowClickReportsTappedFrame() {
        var clicked: HotSlotEvent? = null
        composeRule.setContent {
            Box(modifier = Modifier.width(300.dp)) {
                FrameRowItem(
                    frames = listOf(frame(1000L, "a"), frame(2000L, "b")),
                    onFrameClick = { clicked = it },
                )
            }
        }
        composeRule.onNodeWithTag("hotSlotFrame1").performClick()
        assertEquals("b", clicked?.frameId)
    }

    @Test
    fun viewerRendersCaptionAndTapOnImageDismisses() {
        var dismissed = false
        composeRule.setContent {
            HotSlotFrameViewer(
                frame = viewerFrame("在浏览器中查看项目文档"),
                onDismiss = { dismissed = true },
            )
        }
        composeRule.onNodeWithText("在浏览器中查看项目文档").assertIsDisplayed()
        composeRule.onNodeWithTag(VIEWER_IMAGE_TAG).performClick()
        assertEquals(true, dismissed)
    }

    @Test
    fun shortCaptionBarTapDoesNotDismiss() {
        // bar 恒拦截点击（disabled clickable 亦拦截）：短 caption 点文字 = 无效果、不退出（§9.5 #6）
        var dismissed = false
        composeRule.setContent {
            HotSlotFrameViewer(
                frame = viewerFrame("短描述"),
                onDismiss = { dismissed = true },
            )
        }
        composeRule.onNodeWithText("短描述").performClick()
        assertEquals(false, dismissed)
    }

    // ---- §9 屏幕级接线（覆盖层挂载 + 保存链路端到端）----
    // mockk 与 ART 不兼容（JvmMockKGateway 初始化失败并污染同进程后续用例，同 AudioCaptureControllerTest），
    // 故此处用 MockWebServer + 真 Repository + 真 ImageGallerySaver。

    /**
     * 起 MockWebServer（/timeline 回 eventsJson，帧图回非 JPEG 字节）并渲染真 Screen。
     * `nextCursor` 非空时首页带游标；带 `cursor` 的请求回 `cursorStatus`（默认 500 = 翻页失败）
     * 并回调 `onCursorRequest` 计数 ——「失败不风暴」用例据此断言只发一次。
     */
    private fun withScreen(
        eventsJson: String,
        frameBytes: String = "not-an-image",
        nextCursor: String? = null,
        cursorStatus: Int = 500,
        onCursorRequest: () -> Unit = {},
        block: (HotSlotViewModel) -> Unit,
    ) {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path.contains("timeline") -> {
                        val cursor = request.requestUrl?.queryParameter("cursor")
                        if (cursor != null) {
                            onCursorRequest()
                            MockResponse().setResponseCode(cursorStatus)
                        } else {
                            MockResponse()
                                .setHeader("Content-Type", "application/json")
                                .setBody(
                                    """{"days":[{"date":"2026-08-21","events":[$eventsJson]}],""" +
                                        """"total":1,"next_cursor":${nextCursor?.let { "\"$it\"" } ?: "null"}}""",
                                )
                        }
                    }

                    path.contains("/video/") -> MockResponse()
                        .setHeader("Content-Type", "image/jpeg")
                        .setBody(Buffer().write(frameBytes.toByteArray()))

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
                HotSlotScreen(
                    startMs = 1000L, endMs = 2000L, pending = false,
                    onNavigateBack = {}, onOpenLink = {}, viewModel = vm,
                )
            }
            block(vm)
        } finally {
            vm.viewModelScope.cancel()
            scope.cancel()
            server.shutdown()
        }
    }

    private fun videoEventJson(caption: String?) =
        """{"ts_ms":1000,"type":"video","media_url":"/api/mylife/media/d/s/video/a.jpg","frame_id":"a","event_ref":"r1"""" +
            (caption?.let { ""","caption":"$it"""" } ?: "") + "}"

    /** 等首帧渲染（timeline 请求是异步的，直接点会撞上 Loading 竞态）。 */
    private fun awaitFrameRow() {
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithTag("hotSlotFrame0").fetchSemanticsNodes().isNotEmpty()
        }
    }

    @Test
    fun screenFrameClickOpensViewerAndTapDismisses() = withScreen(videoEventJson("在浏览器中查看项目文档")) { _ ->
        awaitFrameRow()
        composeRule.onNodeWithTag("hotSlotFrame0").performClick()
        // 覆盖层真的挂在列表之上（挂在 Column 内时会塌高/不可见 → 此处必失败）
        composeRule.onNodeWithText("在浏览器中查看项目文档").assertIsDisplayed()
        composeRule.onNodeWithTag(VIEWER_IMAGE_TAG).performClick()
        composeRule.onNodeWithText("在浏览器中查看项目文档").assertDoesNotExist()
    }

    @Test
    fun screenViewerWithoutCaptionKeepsSaveEntry() = withScreen(videoEventJson(null)) { _ ->
        awaitFrameRow()
        composeRule.onNodeWithTag("hotSlotFrame0").performClick()
        composeRule.onNodeWithContentDescription(str(R.string.common_save_image)).assertIsDisplayed() // 无 caption 不丢保存入口
    }

    @Test
    fun screenSaveFromViewerShowsTip() = withScreen(videoEventJson("描述")) { _ ->
        awaitFrameRow()
        composeRule.onNodeWithTag("hotSlotFrame0").performClick()
        composeRule.onNodeWithContentDescription(str(R.string.common_save_image)).performClick()
        // 全链路：屏 → VM → downloadFrame → saver（非 JPEG 被 magic 闸拒 → 「保存失败」，不落相册）
        // 文案取资源（设备语言 en/zh 均成立），勿硬编码中文
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText(str(R.string.common_save_failed)).fetchSemanticsNodes().isNotEmpty()
        }
    }

    @Test
    fun viewerSaveIconReportsClick() {
        var saved = false
        composeRule.setContent {
            HotSlotFrameViewer(
                frame = viewerFrame("描述"),
                onDismiss = {},
                onSaveClick = { saved = true },
            )
        }
        composeRule.onNodeWithContentDescription(str(R.string.common_save_image)).performClick()
        assertEquals(true, saved)
    }

    // ---- 触底失败不风暴（docs/android_timeline_audio_gallery.md §9.11 统一批）----

    @Test
    fun failedNextPageDoesNotAutoRetry() {
        val cursorCalls = java.util.concurrent.atomic.AtomicInteger(0)
        withScreen(
            eventsJson = videoEventJson("描述"),
            nextCursor = "c1",
            onCursorRequest = { cursorCalls.incrementAndGet() },
        ) { _ ->
            awaitFrameRow()
            // 单行全可见 ⇒ 立即触底 → 恰好发起一次翻页
            composeRule.waitUntil(timeoutMillis = 5_000) { cursorCalls.get() >= 1 }
            // 静置：修复前 loadingMore 回落 → effect 重入 → 立即重发（风暴）
            Thread.sleep(1_500)
            assertEquals("失败后不得自动重试（须滚离阈值区再回来）", 1, cursorCalls.get())
        }
    }

    // ---- 日卡音频入口（docs/android_timeline_audio_gallery.md §4.1 / §6-3）----

    @Test
    fun audioEntryRendersWithCountAndReportsClick() {
        var clicked = false
        composeRule.setContent {
            PeriodCard(
                digest = digest(pending = false, audioCount = 14),
                onBodyClick = null,
                onHotSlotClick = { _, _ -> },
                onImageClick = {},
                onAudioClick = { clicked = true },
            )
        }
        // 图标 + 段数（TalkBack 语义含段数）
        composeRule.onNodeWithContentDescription(
            context.resources.getQuantityString(R.plurals.mylife_audio_entry_desc, 14, 14),
        ).assertIsDisplayed()
        composeRule.onNodeWithText("14").assertIsDisplayed()
        // 与 chips 同一行：chip 仍可见
        composeRule.onNodeWithText("▸ 09:12").assertIsDisplayed()

        composeRule.onNodeWithContentDescription(
            context.resources.getQuantityString(R.plurals.mylife_audio_entry_desc, 14, 14),
        ).performClick()
        assertEquals(true, clicked)
    }

    @Test
    fun audioEntryHiddenWhenCountZero() {
        composeRule.setContent {
            PeriodCard(
                digest = digest(pending = false, audioCount = 0),
                onBodyClick = null,
                onHotSlotClick = { _, _ -> },
                onImageClick = {},
                onAudioClick = { },
            )
        }
        composeRule.onAllNodesWithText("0").assertCountEquals(0)
        composeRule.onAllNodesWithContentDescription(
            context.resources.getQuantityString(R.plurals.mylife_audio_entry_desc, 0, 0),
        ).assertCountEquals(0)
        composeRule.onNodeWithText("▸ 09:12").assertIsDisplayed()
    }

    @Test
    fun audioEntryRowStillRenderedWithoutHotSlots() {
        composeRule.setContent {
            PeriodCard(
                digest = digest(pending = false, audioCount = 4, hotSlots = emptyList()),
                onBodyClick = null,
                onHotSlotClick = { _, _ -> },
                onImageClick = {},
                onAudioClick = { },
            )
        }
        // Q4：无 hotSlot 日仍出行，入口可点（chips 不存在）
        composeRule.onNodeWithContentDescription(
            context.resources.getQuantityString(R.plurals.mylife_audio_entry_desc, 4, 4),
        ).assertIsDisplayed()
        composeRule.onAllNodesWithText("▸ 09:12").assertCountEquals(0)
    }

    @Test
    fun audioEntryHiddenWithoutCallback() {
        // onAudioClick = null（默认）→ 即使有段数也不渲染（可选注入语义）
        composeRule.setContent {
            PeriodCard(
                digest = digest(pending = false, audioCount = 7),
                onBodyClick = null,
                onHotSlotClick = { _, _ -> },
                onImageClick = {},
            )
        }
        composeRule.onAllNodesWithContentDescription("语音记录，7 段").assertCountEquals(0)
    }

    // ── agent 结果行：折叠 + 整行点击（docs/android_agent_job_view.md §12.3.3）──

    private fun longAgentBody() = (1..40).joinToString("\n") {
        "第 $it 行：这是用于折叠测试的超长 agent job 结果正文内容。"
    }

    @Test
    fun agentLongRowCollapses_withEntryRowAndTapOffset() {
        var clickedKey: String? = null
        var clickedOffset: Int? = null
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text("t-a", null, 0L, BubbleSide.AGENT, longAgentBody()),
                onLinkClick = { },
                onAgentJobClick = { key, offset -> clickedKey = key; clickedOffset = offset },
            )
        }
        composeRule.waitForIdle()

        // 行边界裁剪：TextView 高度 == 第 24 行上沿（共享折叠块，§11.8）
        onView(withText(containsString("第 1 行"))).perform(
            object : ViewAction {
                override fun getConstraints(): Matcher<View> =
                    isAssignableFrom(TextView::class.java)
                override fun getDescription(): String = "assert row clipped at line boundary"
                override fun perform(uiController: UiController?, view: View?) {
                    val tv = view as TextView
                    val layout = tv.layout!!
                    val clipTop = layout.getLineTop(JOB_RESULT_COLLAPSE_LINES)
                    assertEquals("height=${tv.height} clipTop=$clipTop", clipTop, tv.height)
                }
            },
        )
        composeRule.onNodeWithText(entryPrefix(), substring = true).assertIsDisplayed()

        // 整行可点：Espresso 点正文（D13 冒泡）→ 回调带该行 key 与字符偏移
        onView(withText(containsString("第 1 行"))).perform(click())
        composeRule.waitForIdle()
        assertEquals("t-a", clickedKey)
        assertTrue("应记录行内点击偏移（实际 $clickedOffset）", (clickedOffset ?: -1) >= 0)
    }

    @Test
    fun agentShortRow_noCollapse_noEntryRow() {
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text("t-s", null, 0L, BubbleSide.AGENT, "已为你查好，明天晴 22 度。"),
                onLinkClick = { },
                onAgentJobClick = { _, _ -> },
            )
        }
        composeRule.waitForIdle()

        composeRule.onNodeWithText(entryWord(), substring = true).assertDoesNotExist()
    }

    @Test
    fun agentLongRowWithoutCallback_noCollapseNoClickable() {
        composeRule.setContent {
            HotSlotRowItem(
                HotSlotRow.Text("t-n", null, 0L, BubbleSide.AGENT, longAgentBody()),
                onLinkClick = { },
            )
        }
        composeRule.waitForIdle()

        composeRule.onNodeWithText(entryWord(), substring = true).assertDoesNotExist()
        composeRule.onNode(hasClickAction()).assertDoesNotExist()
    }
}
