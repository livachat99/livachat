package dev.livachat.app.ui.screens.mylife.audiogallery

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.click
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.MylifeApi
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.AudioPlayer
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.audiogallery.components.PROGRESS_BAR_TAG
import dev.livachat.sdk.LivaChatConfig
import java.time.Instant
import java.util.TimeZone
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
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
 * AudioGallery 渲染级验证（docs/android_timeline_audio_gallery.md §6-3）：每段一个时间标签、
 * 卡片文案层级、播放按钮回调。mockk 与 ART 不兼容 → MockWebServer + 真 Repository + 手写 fake player。
 */
@RunWith(AndroidJUnit4::class)
class AudioGalleryScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    /** 手写 fake（androidTest 禁 mockk）：记录 toggle/seek/stop，state 为真 StateFlow。 */
    private class FakeAudioPlayer : AudioPlayer {
        private val _state = MutableStateFlow(AudioPlayer.State())
        override val state: StateFlow<AudioPlayer.State> = _state
        val toggles = mutableListOf<Pair<String, String>>()
        val seeks = mutableListOf<Long>()
        var stops = 0

        override fun toggle(key: String, url: String) {
            toggles += key to url
            _state.value = AudioPlayer.State(key = key, isPlaying = true, durationMs = 60_000)
        }

        override fun seekTo(ms: Long) {
            seeks += ms
            _state.value = _state.value.copy(positionMs = ms)
        }

        override fun stop() {
            stops++
            _state.value = AudioPlayer.State()
        }
    }

    // 2026-09-22 上海本地：09:12 / 09:13（与 09:12 同 5 分钟桶）/ 09:31（下一个桶）
    private val ts1 = Instant.parse("2026-09-22T01:12:00Z").toEpochMilli()
    private val ts2 = Instant.parse("2026-09-22T01:13:00Z").toEpochMilli()
    private val ts3 = Instant.parse("2026-09-22T01:31:00Z").toEpochMilli()

    private fun event(ts: Long, id: String, transcript: String?, summary: String?,
                      extra: String = "") =
        """{"ts_ms":$ts,"type":"audio",""" +
            """"media_url":"/api/mylife/media/dev/rs1/audio/$id.ogg",""" +
            """"event_ref":"rs1:audio:$id","transcript":${transcript?.let { "\"$it\"" } ?: "null"},""" +
            """"audio_summary":${summary?.let { "\"$it\"" } ?: "null"},"duration_s":60$extra}"""

    private fun pageBody(events: List<String>, nextCursor: String? = null): String =
        """{"days":[{"date":"2026-09-22","events":[${events.joinToString(",")}]}],""" +
            """"total":${events.size},"next_cursor":${nextCursor?.let { "\"$it\"" } ?: "null"}}"""

    /**
     * 起 MockWebServer（/timeline 回 audio 事件页）并渲染真 Screen + 真 VM。
     * 带 `cursor` 的请求走 `cursorStatus`（默认 500 = 翻页失败），并回调 `onCursorRequest` 计数
     * ——「失败不风暴」用例据此断言只发一次。
     */
    private fun withScreen(
        body: String,
        pending: Boolean = false,
        cursorStatus: Int = 500,
        onCursorRequest: () -> Unit = {},
        onNavigateToEventList: (Long, Boolean) -> Unit = { _, _ -> },
        onBack: () -> Unit = {},
        block: (FakeAudioPlayer) -> Unit,
    ) {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                if (!request.path.orEmpty().contains("timeline")) {
                    return MockResponse().setResponseCode(404)
                }
                val cursor = request.requestUrl?.queryParameter("cursor")
                return if (cursor == null) {
                    MockResponse()
                        .setHeader("Content-Type", "application/json")
                        .setBody(body)
                } else {
                    onCursorRequest()
                    MockResponse().setResponseCode(cursorStatus)
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
        val player = FakeAudioPlayer()
        val vm = AudioGalleryViewModel(repository, player, ApplicationProvider.getApplicationContext())
        try {
            composeRule.setContent {
                AudioGalleryScreen(
                    date = "2026-09-22",
                    label = "9月22日 · 周二",
                    count = 14,
                    pending = pending,
                    onNavigateToEventList = onNavigateToEventList,
                    onNavigateBack = onBack,
                    viewModel = vm,
                )
            }
            block(player)
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

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)

    @Test
    fun rendersBucketsCardsAndReportsToggle() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            val body = pageBody(
                listOf(
                    // 长转写（>5 字）→ 显示转写，摘要不渲染
                    event(ts1, "s1", "今天天气不错我们下午三点在会议室碰面", "长段摘要不该出现"),
                    // 短转写（≤5 字）→ 回退摘要
                    event(ts2, "s2", "短句", "短段回退摘要"),
                    // 无转写 → 回退摘要
                    event(ts3, "s3", null, "无转写摘要"),
                ),
            )
            withScreen(body) { player ->
                awaitText("短段回退摘要")

                // 顶栏：label + 段数（首屏完成后 = total 3）
                composeRule.onNodeWithText("9月22日 · 周二").assertIsDisplayed()
                composeRule.onNodeWithText(context.resources.getQuantityString(R.plurals.mylife_segments_count, 3, 3)).assertIsDisplayed()

                // 每段一个时间标签（2026-09-24）：09:12 / 09:13 / 09:31 各出现一次（不再按桶去重）
                composeRule.onAllNodesWithText("09:12").assertCountEquals(1)
                composeRule.onAllNodesWithText("09:13").assertCountEquals(1)
                composeRule.onAllNodesWithText("09:31").assertCountEquals(1)

                // 正文单区（§4.4）：>5 字显转写、≤5 字/无转写显摘要，摘要区不再单独存在
                composeRule.onNodeWithText("今天天气不错我们下午三点在会议室碰面").assertIsDisplayed()
                composeRule.onAllNodesWithText("长段摘要不该出现").assertCountEquals(0)
                composeRule.onNodeWithText("短段回退摘要").assertIsDisplayed()
                composeRule.onAllNodesWithText("短句").assertCountEquals(0)
                composeRule.onNodeWithText("无转写摘要").assertIsDisplayed()

                // 点击首卡播放按钮 → 回调带 eventRef + 已绝对化 mediaUrl（文案取资源，兼容设备语言）
                composeRule.onAllNodesWithContentDescription(str(R.string.common_play))[0].performClick()
                composeRule.waitForIdle()
                assertTrue("应转发 toggle（同卡再点变暂停）", player.toggles.isNotEmpty())
                assertEquals("rs1:audio:s1", player.toggles.first().first)
                assertTrue(
                    "mediaUrl 应绝对化并附 token",
                    player.toggles.first().second.contains("/api/mylife/media/dev/rs1/audio/s1.ogg") &&
                        player.toggles.first().second.contains("token="),
                )
                composeRule.onAllNodesWithContentDescription(str(R.string.common_pause)).assertCountEquals(1)
            }
        } finally {
            TimeZone.setDefault(original)
        }
    }

    @Test
    fun emptyDayShowsEmptyState() {
        withScreen(pageBody(emptyList())) {
            awaitText(str(R.string.mylife_audio_empty_title))
            composeRule.onNodeWithText(str(R.string.mylife_audio_empty_title)).assertIsDisplayed()
        }
    }

    @Test
    fun failedLoadMoreDoesNotRetryStorm() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            val cursorCalls = AtomicInteger(0)
            // 3 段全部可见 ⇒ 立即触底；带 next_cursor ⇒ 立刻发起翻页；cursor 页回 500 ⇒ 失败
            val body = pageBody(
                listOf(
                    event(ts1, "s1", "第一段转写文字比较长", null),
                    event(ts2, "s2", "第二段转写文字比较长", null),
                    event(ts3, "s3", null, "第三段摘要"),
                ),
                nextCursor = "2026-09-22|3",
            )
            withScreen(
                body = body,
                onCursorRequest = { cursorCalls.incrementAndGet() },
            ) {
                awaitText("第一段转写文字比较长")
                // 第一次翻页确实发生（否则用例空转）
                composeRule.waitUntil(timeoutMillis = 5_000) { cursorCalls.get() >= 1 }
                // 静置：修复前 loadingMore 回落 → effect 重启 → 立即重发（风暴）
                Thread.sleep(1_500)
                assertEquals("失败后不得自动重试（须滚离阈值区再回来）", 1, cursorCalls.get())
            }
        } finally {
            TimeZone.setDefault(original)
        }
    }

    @Test
    fun energyEnvelopeRendersOnlyWhenDataPresent() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            // s1 带能量（100 帧全峰值 → 柱体顶到带顶）；s2 无能量 → 纯轨道
            val b64 = java.util.Base64.getEncoder().encodeToString(ByteArray(100) { 255.toByte() })
            val body = pageBody(
                listOf(
                    event(ts1, "s1", "带能量的段转写文本", null,
                          extra = ""","energy":"$b64","energy_v":1,"frame_ms":20,"db_min":-70.0,"db_max":0.0"""),
                    event(ts2, "s2", "无能量的段转写文本", null),
                ),
            )
            withScreen(body) {
                awaitText("带能量的段转写文本")
                val bars = composeRule.onAllNodesWithTag(PROGRESS_BAR_TAG)
                val withEnergy = linePixelsInTopQuarter(bars[0].captureToImage().asAndroidBitmap())
                val withoutEnergy = linePixelsInTopQuarter(bars[1].captureToImage().asAndroidBitmap())
                assertTrue("有能量的段应在带顶绘制包络柱（Line 像素 > 0，实际 $withEnergy）", withEnergy > 0)
                assertEquals("无能量的段应降级为纯轨道（顶部无柱）", 0, withoutEnergy)
            }
        } finally {
            TimeZone.setDefault(original)
        }
    }

    /** 统计图顶部 25% 区域里 `Line` 色像素数（包络未播柱的颜色；纯轨道形态该区为空）。 */
    private fun linePixelsInTopQuarter(bmp: android.graphics.Bitmap): Int {
        val line = MylifeColors.Line.toArgb()
        var n = 0
        for (y in 0 until (bmp.height / 4).coerceAtLeast(1)) {
            for (x in 0 until bmp.width) if (bmp.getPixel(x, y) == line) n++
        }
        return n
    }

    @Test
    fun barOnNonPlayingCardStartsThatSegment() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            val body = pageBody(
                listOf(
                    event(ts1, "s1", "第一段转写文字比较长", null),
                    event(ts2, "s2", "第二段转写文字比较长", null),
                ),
            )
            withScreen(body) { player ->
                awaitText("第二段转写文字比较长")
                // 未播放任何段时，点第二张卡的轨道 → 从该位置开始播放第二段（§4.5 修订）
                composeRule.onAllNodesWithTag(PROGRESS_BAR_TAG)[1]
                    .performTouchInput { click(center) }
                composeRule.waitForIdle()
                assertEquals(listOf("rs1:audio:s2"), player.toggles.map { it.first })
                assertTrue("切段后应立刻定位", player.seeks.isNotEmpty())
            }
        } finally {
            TimeZone.setDefault(original)
        }
    }

    @Test
    fun timeHeaderJumpsToEventListWithSegmentTimeAndPending() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            val navs = mutableListOf<Pair<Long, Boolean>>()
            val body = pageBody(listOf(event(ts1, "s1", "第一段转写文字比较长", null)))
            withScreen(
                body = body,
                pending = true,   // 路由快照 pending（overview 未发射 → 不收敛）
                onNavigateToEventList = { ts, p -> navs += ts to p },
            ) {
                awaitText("第一段转写文字比较长")

                // 时间头 = 时间 + ››（§9 定案 B 药丸），整组一个可点语义节点
                composeRule.onAllNodesWithText("››").assertCountEquals(1)
                val header = composeRule
                    .onNodeWithContentDescription(context.getString(R.string.mylife_audio_time_desc, "09:12"))
                    .fetchSemanticsNode()
                // 触控带 ≥32dp（§9.2；回归保护：clickable 必须在 padding 之前才计入 padding）
                val minTouchPx = with(composeRule.density) { 32.dp.toPx() }
                assertTrue(
                    "触控带高度应 ≥32dp，实际 ${header.boundsInRoot.height}px（阈值 ${minTouchPx}px）",
                    header.boundsInRoot.height >= minTouchPx,
                )

                composeRule.onNodeWithContentDescription(context.getString(R.string.mylife_audio_time_desc, "09:12")).performClick()
                composeRule.waitForIdle()

                // 跳转参数 = 该段的精确 tsMs + 画廊已收敛的 pending
                assertEquals(listOf(ts1 to true), navs)
            }
        } finally {
            TimeZone.setDefault(original)
        }
    }
}
