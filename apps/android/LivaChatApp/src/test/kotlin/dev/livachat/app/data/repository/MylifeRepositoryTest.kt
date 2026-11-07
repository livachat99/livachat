package dev.livachat.app.data.repository

import dev.livachat.app.testStringContext
import dev.livachat.app.data.remote.mylife.MylifeApi
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.sdk.LivaChatConfig
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import java.util.TimeZone
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MylifeRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var repository: MylifeRepository

    // 轮询协程作用域：Unconfined 跑至首个挂起点（first{}），测试结束 cancel 防泄漏
    private val createdScopes = mutableListOf<CoroutineScope>()

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        createdScopes += scope
        repository = newRepository(scope)
    }

    private fun testConfig() = LivaChatConfig(
        serverUrl = "wss://example.com/v1/ws/stream",
        token = "dev-token",
        deviceId = "dev-1",
        clientVersion = "android-1.0.0",
        logFilePath = "/tmp/livachat.log",
    )

    private fun newRepository(scope: CoroutineScope): MylifeRepository {
        val config = testConfig()
        return MylifeRepository(
            MylifeApi(OkHttpClient(), server.url("/api/mylife"), config, testStringContext()),
            server.url("/api/mylife"),
            config,
            scope,
        )
    }

    @After
    fun tearDown() {
        createdScopes.forEach { it.cancel() }
        createdScopes.clear()
        server.shutdown()
    }

    private fun enqueueJson(body: String, status: Int = 200) {
        server.enqueue(
            MockResponse()
                .setResponseCode(status)
                .setHeader("Content-Type", "application/json")
                .setBody(body),
        )
    }

    @Test
    fun `overview 第二次 refresh 命中缓存不发请求`() = runBlocking {
        enqueueJson("""{"schema_version":1,"timezone":"Asia/Shanghai","days":[],"total_sessions":0}""")
        repository.refreshOverview()
        repository.refreshOverview()
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `overview force 刷新跳过缓存`() = runBlocking {
        enqueueJson("""{"schema_version":1,"timezone":"Asia/Shanghai","days":[],"total_sessions":0}""")
        enqueueJson("""{"schema_version":1,"timezone":"Asia/Shanghai","days":[],"total_sessions":0}""")
        repository.refreshOverview()
        repository.refreshOverview(force = true)
        assertEquals(2, server.requestCount)
    }

    @Test
    fun `overview 请求携带系统时区`() = runBlocking {
        enqueueJson("""{"schema_version":1,"timezone":"Asia/Shanghai","days":[],"total_sessions":0}""")
        repository.refreshOverview()
        val req = server.takeRequest()
        assertEquals(TimeZone.getDefault().id, req.requestUrl!!.queryParameter("timezone"))
    }

    // ---- overview 分页（docs/bff_service_interfaces.md §8）----

    @Test
    fun `overview 分页：loadMore 用 next_cursor 追加`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":4,"next_cursor":"2026-09-29",
               |"days":[{"date":"2026-09-30"},{"date":"2026-09-29"}]}""".trimMargin(),
        )
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":4,"next_cursor":null,
               |"days":[{"date":"2026-09-28"},{"date":"2026-09-27"}]}""".trimMargin(),
        )
        repository.refreshOverview(force = true, limit = 2)
        assertTrue(repository.loadMoreOverview(limit = 2))
        val win = repository.overview.value!!
        assertEquals(listOf("2026-09-30", "2026-09-29", "2026-09-28", "2026-09-27"),
            win.days.map { it.date })
        assertNull(win.nextCursor)                       // 已到最早
        assertEquals(4, win.total)

        val r1 = server.takeRequest()
        assertEquals("2", r1.requestUrl!!.queryParameter("limit"))
        assertNull(r1.requestUrl!!.queryParameter("cursor"))   // 第一页不带 cursor
        val r2 = server.takeRequest()
        assertEquals("2", r2.requestUrl!!.queryParameter("limit"))
        assertEquals("2026-09-29", r2.requestUrl!!.queryParameter("cursor"))
    }

    @Test
    fun `overview 分页：窗口重取按 date 去重（新页优先）且游标取更深`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":10,"next_cursor":"2026-09-28",
               |"days":[{"date":"2026-09-30","summary":"旧"},{"date":"2026-09-29"},{"date":"2026-09-28"}]}"""
                .trimMargin(),
        )
        repository.refreshOverview(force = true, limit = 3)
        // 轮询/刷新路径：重取最新 2 天（30 内容更新），游标 null = 已到最早
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":10,"next_cursor":null,
               |"days":[{"date":"2026-09-30","summary":"新"},{"date":"2026-09-29"}]}""".trimMargin(),
        )
        repository.refreshOverview(force = true, limit = 2)
        val win = repository.overview.value!!
        assertEquals(listOf("2026-09-30", "2026-09-29", "2026-09-28"), win.days.map { it.date })
        assertEquals("新", win.days[0].summary)          // 新页优先
        assertNull(win.nextCursor)                       // 两侧任一为 null（已达最早）→ null
    }

    @Test
    fun `overview 分页：cursor 定位取数（scope 刷新路径）绕过缓存并合并`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":3,"next_cursor":"2026-09-30",
               |"days":[{"date":"2026-09-30"}]}""".trimMargin(),
        )
        repository.refreshOverview(force = true, limit = 1)
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":3,"next_cursor":"2026-03-30",
               |"days":[{"date":"2026-03-31"},{"date":"2026-03-30"}]}""".trimMargin(),
        )
        // cursor 非 null → 即使 force=false 也取数（下拉刷新 drilled scope 路径）
        repository.refreshOverview(limit = 2, cursor = "2026-04-01")
        val win = repository.overview.value!!
        assertEquals(listOf("2026-09-30", "2026-03-31", "2026-03-30"), win.days.map { it.date })
        assertEquals("2026-03-30", win.nextCursor)       // 更深的一方（日期较小）
        server.takeRequest()                             // 首屏
        val scoped = server.takeRequest()                // scope 定位取数
        assertEquals("2026-04-01", scoped.requestUrl!!.queryParameter("cursor"))
        assertEquals("2", scoped.requestUrl!!.queryParameter("limit"))
    }

    @Test
    fun `overview 分页：loadMore 遇 tz 变化回第一页（不把更早页当窗口）`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":2,"next_cursor":"2026-09-30",
               |"days":[{"date":"2026-09-30"}]}""".trimMargin(),
        )
        repository.refreshOverview(force = true, limit = 1)
        enqueueJson(
            """{"schema_version":1,"timezone":"UTC","total":1,"next_cursor":"2026-08-01",
               |"days":[{"date":"2026-08-01"}]}""".trimMargin(),
        )
        enqueueJson(
            """{"schema_version":1,"timezone":"UTC","total":2,"next_cursor":"2026-08-03",
               |"days":[{"date":"2026-08-03"}]}""".trimMargin(),
        )
        assertTrue(repository.loadMoreOverview(1))
        val win = repository.overview.value!!
        assertEquals("UTC", win.timezone)
        assertEquals(listOf("2026-08-03"), win.days.map { it.date })   // 回第一页（非更早页 08-01）
        server.takeRequest()                                          // 首屏
        server.takeRequest()                                          // loadMore（tz 已变）
        val refetched = server.takeRequest()                          // 自动回第一页
        assertNull(refetched.requestUrl!!.queryParameter("cursor"))
    }

    @Test
    fun `overview 分页：tz 变化丢弃旧窗口（E6）`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":1,"next_cursor":"2026-09-30",
               |"days":[{"date":"2026-09-30"}]}""".trimMargin(),
        )
        repository.refreshOverview(force = true, limit = 5)
        enqueueJson(
            """{"schema_version":1,"timezone":"UTC","total":1,"next_cursor":null,
               |"days":[{"date":"2026-08-01"}]}""".trimMargin(),
        )
        repository.refreshOverview(force = true, limit = 5)
        val win = repository.overview.value!!
        assertEquals("UTC", win.timezone)
        assertEquals(listOf("2026-08-01"), win.days.map { it.date })   // 旧窗口（Asia/Shanghai）已丢弃
    }

    // ---- Gallery frames（docs/android_timeline_image_gallery.md §3.6）----

    @Test
    fun `frames 跳过空 mediaUrl 页内升序且绝对化`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"total":3,"next_cursor":"2026-08-21|3","days":[
               |{"date":"2026-08-21","events":[
               |{"ts_ms":300,"media_url":"/api/mylife/media/d/s/video/c.jpg","frame_id":"c","window_id":"w1"},
               |{"ts_ms":100,"media_url":"","frame_id":"a"},
               |{"ts_ms":200,"media_url":"/api/mylife/media/d/s/video/b.jpg","frame_id":"b","caption":"x"}]}]}""".trimMargin(),
        )
        val page = repository.frames(from = "2026-08-21", to = "2026-08-21")
        assertEquals(listOf(200L, 300L), page.frames.map { it.tsMs })
        assertEquals(listOf("b", "c"), page.frames.map { it.frameId })
        assertEquals(3, page.total) // C4/E8：total 仍按服务端计数（含被跳过条目）
        assertEquals("2026-08-21|3", page.nextCursor)
        assertTrue(page.frames[0].mediaUrl.startsWith(server.url("/").toString()))
        assertTrue(page.frames[0].mediaUrl.contains("token=dev-token"))
        assertNull(page.frames[0].windowId)
        assertEquals("w1", page.frames[1].windowId)
        assertEquals("x", page.frames[0].caption)
    }

    // ---- audioSegments 能量包络解码（docs/android_timeline_audio_gallery.md §10 A1′）----

    private fun audioEventJson(extra: String) =
        """{"ts_ms":1000,"type":"audio",""" +
            """"media_url":"/api/mylife/media/d/s/audio/a.ogg","event_ref":"s:audio:a",""" +
            """"transcript":"打开应用非常慢","duration_s":60$extra}"""

    @Test
    fun `audioSegments 解码能量包络`() = runBlocking {
        // [0, 128, 255] → dB 区间 [-70, 0] 上还原为 [-70, -34.7, 0]
        val b64 = java.util.Base64.getEncoder().encodeToString(
            byteArrayOf(0, 128.toByte(), 255.toByte()))
        enqueueJson(
            """{"total":1,"days":[{"date":"2026-09-22","events":[""" +
                audioEventJson(
                    ""","energy":"$b64","energy_v":1,"frame_ms":20,"db_min":-70.0,"db_max":0.0""",
                ) + "]}]}",
        )
        val page = repository.audioSegments(date = "2026-09-22")
        val env = page.segments.single().energy
        assertTrue("能量应被解码", env != null)
        assertEquals(20, env!!.frameMs)
        assertEquals(-70f, env.dbMin, 1e-3f)
        assertEquals(0f, env.dbMax, 1e-3f)
        assertEquals(listOf(0, 128, 255), env.values.map { it.toInt() and 0xFF })
    }

    @Test
    fun `audioSegments 能量降级：版本未知 缺失 非法 base64 区间非法`() = runBlocking {
        val b64 = java.util.Base64.getEncoder().encodeToString(byteArrayOf(1, 2, 3))
        // 4 条：版本 2 / 缺 energy / 非法 base64 / db_max <= db_min
        enqueueJson(
            """{"total":4,"days":[{"date":"2026-09-22","events":[""" +
                audioEventJson(""","energy":"$b64","energy_v":2,"frame_ms":20,"db_min":-70.0,"db_max":0.0""") + "," +
                audioEventJson(""","energy_v":1,"frame_ms":20,"db_min":-70.0,"db_max":0.0""") + "," +
                audioEventJson(""","energy":"!!!not-base64!!!","energy_v":1,"frame_ms":20,"db_min":-70.0,"db_max":0.0""") + "," +
                audioEventJson(""","energy":"$b64","energy_v":1,"frame_ms":20,"db_min":0.0,"db_max":0.0""") +
                "]}]}",
        )
        val page = repository.audioSegments(date = "2026-09-22")
        assertEquals(4, page.segments.size)                       // 事件本身不丢
        assertTrue(page.segments.all { it.energy == null })       // 能量全部降级
    }

    @Test
    fun `frames 跨日响应（days 日倒序）重排为全局升序`() = runBlocking {
        // 真实 /timeline 形状：days 日倒序（新→旧）、日内升序 → 仓库出口必须全局 ts 升序
        enqueueJson(
            """{"schema_version":1,"total":3,"days":[
               |{"date":"2026-09-19","events":[
               |{"ts_ms":2000,"media_url":"/api/mylife/media/d/s/video/c.jpg","frame_id":"c"}]},
               |{"date":"2026-09-15","events":[
               |{"ts_ms":1000,"media_url":"/api/mylife/media/d/s/video/a.jpg","frame_id":"a"},
               |{"ts_ms":1500,"media_url":"/api/mylife/media/d/s/video/b.jpg","frame_id":"b"}]}]}""".trimMargin(),
        )
        val page = repository.frames(from = "2026-09-15", to = "2026-09-19")
        assertEquals(listOf(1000L, 1500L, 2000L), page.frames.map { it.tsMs })
        assertEquals(listOf("a", "b", "c"), page.frames.map { it.frameId })
    }

    @Test
    fun `frames 请求携带范围 游标与系统时区`() = runBlocking {
        enqueueJson("""{"schema_version":1,"total":0,"days":[]}""")
        repository.frames(from = "2026-08-01", to = "2026-08-31", cursor = "c1")
        val url = server.takeRequest().requestUrl!!
        assertEquals("/api/mylife/timeline", url.encodedPath)
        assertEquals("2026-08-01", url.queryParameter("date_from"))
        assertEquals("2026-08-31", url.queryParameter("date_to"))
        assertEquals("video", url.queryParameter("types"))
        assertEquals("40", url.queryParameter("limit"))
        assertEquals("c1", url.queryParameter("cursor"))
        assertEquals(TimeZone.getDefault().id, url.queryParameter("timezone"))
    }

    @Test
    fun `downloadFrame 成功返回字节 失败收敛 null`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(okio.Buffer().write(byteArrayOf(9, 8))),
        )
        val ok = repository.downloadFrame(server.url("/api/mylife/media/d/s/video/a.jpg").toString())
        assertEquals(listOf<Byte>(9, 8), ok?.toList())

        server.enqueue(MockResponse().setResponseCode(503))
        assertNull(repository.downloadFrame(server.url("/api/mylife/media/d/s/video/b.jpg").toString()))
    }

    // ---- hotSlotView 取数（docs/android_timeline_hotslot.md §4.3）----

    private fun localDateOf(tsMs: Long): String =
        java.time.Instant.ofEpochMilli(tsMs).atZone(TimeZone.getDefault().toZoneId())
            .toLocalDate().toString()

    @Test
    fun `hotSlotEvents 取数日由 ms 现推 传五类与系统时区`() = runBlocking {
        val start = 1_750_000_000_000L
        val end = start + 3_600_000L
        enqueueJson("""{"days":[],"total":0}""")
        repository.hotSlotEvents(start, end)
        val url = server.takeRequest().requestUrl!!
        assertEquals(localDateOf(start), url.queryParameter("date_from"))
        assertEquals(localDateOf(end), url.queryParameter("date_to"))
        assertEquals("chat,agent_job,video,transcribe,translate", url.queryParameter("types"))
        assertEquals("500", url.queryParameter("limit"))
        assertEquals(TimeZone.getDefault().id, url.queryParameter("timezone"))
    }

    @Test
    fun `hotSlotEvents 窗口过滤 空行与无帧视频跳过 绝对化 越窗标记`() = runBlocking {
        enqueueJson(
            """
            {"days":[{"date":"2026-08-21","events":[
              {"ts_ms":1000,"type":"chat","role":"user","text":"你好","event_ref":"r1"},
              {"ts_ms":1100,"type":"chat","role":"assistant","text":"","event_ref":"r2"},
              {"ts_ms":1200,"type":"video","media_url":"","frame_id":"","event_ref":"r3"},
              {"ts_ms":1300,"type":"video","media_url":"/api/mylife/media/d/s/video/a.jpg","frame_id":"a","event_ref":"r4"},
              {"ts_ms":1400,"type":"agent_job","text":"","summary":"","agent_name":"Hermes","event_ref":"r5"},
              {"ts_ms":1500,"type":"transcribe","role":"user","text":"转录文本","event_ref":"r6"},
              {"ts_ms":1600,"type":"translate","role":"assistant","text":"Hello","event_ref":"r7"},
              {"ts_ms":9000,"type":"chat","role":"user","text":"窗外","event_ref":"r8"}
            ]}],"total":8}
            """.trimIndent(),
        )
        val page = repository.hotSlotEvents(1000L, 5000L)
        // 窗口含端点；空文本 chat、无 media_url/frame_id 的 video 已剔除；ts 升序
        assertEquals(listOf(1000L, 1300L, 1400L, 1500L, 1600L), page.events.map { it.tsMs })
        assertEquals(
            listOf("你好", "Hermes", "转录文本", "Hello"),
            page.events.filter { it.type != "video" }.map { it.text },
        )
        val frame = page.events.first { it.type == "video" }
        assertTrue(frame.mediaUrl!!.startsWith(server.url("/").toString()))
        assertTrue(frame.mediaUrl!!.contains("token=dev-token"))
        assertTrue(page.reachedWindowEnd) // ts=9000 越过窗口上界 → 调用方停止翻页
        assertNull(page.nextCursor)
    }

    @Test
    fun `hotSlotEvents 空窗口返回空列表且未越窗`() = runBlocking {
        enqueueJson("""{"days":[],"total":0}""")
        val page = repository.hotSlotEvents(1000L, 2000L)
        assertTrue(page.events.isEmpty())
        assertFalse(page.reachedWindowEnd)
    }

    @Test
    fun `hotSlotEvents video caption 原样透传（缺失 null 空串不归一）`() = runBlocking {
        enqueueJson(
            """
            {"days":[{"date":"2026-08-21","events":[
              {"ts_ms":1000,"type":"video","media_url":"/api/mylife/media/d/s/video/a.jpg","frame_id":"a","caption":"在浏览器中查看文档","event_ref":"r1"},
              {"ts_ms":1100,"type":"video","media_url":"/api/mylife/media/d/s/video/b.jpg","frame_id":"b","caption":"","event_ref":"r2"},
              {"ts_ms":1200,"type":"video","media_url":"/api/mylife/media/d/s/video/c.jpg","frame_id":"c","event_ref":"r3"}
            ]}],"total":3}
            """.trimIndent(),
        )
        val page = repository.hotSlotEvents(1000L, 2000L)
        val byRef = page.events.associateBy { it.eventRef }
        assertEquals("在浏览器中查看文档", byRef.getValue("r1").caption)
        assertEquals("", byRef.getValue("r2").caption) // 空串原样透传：缺失判定在 UI 层 isNotBlank
        assertNull(byRef.getValue("r3").caption)
    }

    // ---- pending 60s 轮询（§6.4，Repository 共享；从 TimelineViewModel 迁移）----
    // api 用 mockk 完全虚拟化：轮询时序在虚拟时钟下确定（真实 HTTP 与 runTest 虚拟时钟混用会挂起）

    private fun overviewDto(hasPending: Boolean) = OverviewDto(
        timezone = "Asia/Shanghai",
        days = listOf(OverviewDayDto(date = "2026-08-17", hasPending = hasPending)),
    )

    @Test
    fun `pending 时 60s 轮询强制刷新 overview`() = runTest {
        val api = mockk<MylifeApi>()
        coEvery { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) } returnsMany
            listOf(overviewDto(true), overviewDto(false))
        val repoScope = CoroutineScope(StandardTestDispatcher(testScheduler) + SupervisorJob())
        val config = testConfig()
        val repo = MylifeRepository(api, server.url("/api/mylife"), config, repoScope)
        try {
            repo.refreshOverview()
            runCurrent() // 轮询协程启动，first{} 命中 pending → 挂到 delay(60s)
            advanceTimeBy(60_000)
            runCurrent()
            coVerify(exactly = 2) { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) }
        } finally {
            repoScope.cancel()
        }
    }

    @Test
    fun `无 pending 时轮询挂起不发请求`() = runTest {
        val api = mockk<MylifeApi>()
        coEvery { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) } returns overviewDto(false)
        val repoScope = CoroutineScope(StandardTestDispatcher(testScheduler) + SupervisorJob())
        val config = testConfig()
        val repo = MylifeRepository(api, server.url("/api/mylife"), config, repoScope)
        try {
            repo.refreshOverview()
            runCurrent()
            advanceTimeBy(120_000)
            runCurrent()
            coVerify(exactly = 1) { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) }
        } finally {
            repoScope.cancel()
        }
    }

    // ---- removeEvents 与删除后延迟刷新（docs/android_timeline_image_gallery.md §9.4.3 / Q9.3）----

    @Test
    fun `removeEvents 直通 POST 并解析响应`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"removed":["a:video:f1"],
               |"skipped":[{"event_ref":"a:video:f2","reason":"not_found"}]}""".trimMargin(),
        )
        val resp = repository.removeEvents(listOf("a:video:f1", "a:video:f2"))

        assertEquals(listOf("a:video:f1"), resp.removed)
        assertEquals("not_found", resp.skipped[0].reason)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/mylife/events/remove", req.path?.substringBefore('?'))
        assertEquals(
            """{"event_refs":["a:video:f1","a:video:f2"]}""",
            req.body.readUtf8(),
        )
    }

    @Test
    fun `flush 无标记不发请求 有标记强刷已加载窗口且只发一次`() = runTest {
        val api = mockk<MylifeApi>()
        coEvery { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) } returns overviewDto(false)
        val repoScope = CoroutineScope(StandardTestDispatcher(testScheduler) + SupervisorJob())
        val config = testConfig()
        val repo = MylifeRepository(api, server.url("/api/mylife"), config, repoScope)
        try {
            repo.refreshOverview(force = true, limit = 5) // 构造已加载窗口（1 天）
            runCurrent()

            repo.flushPendingRemovalRefresh() // 无标记 → no-op，不发请求
            runCurrent()
            coVerify(exactly = 1) { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) }

            repo.markPendingRemovalRefresh()
            repo.flushPendingRemovalRefresh()
            runCurrent()
            // 强刷「已加载窗口」：limit = days.size(=1)，非默认页大小
            coVerify(exactly = 1) { api.overview(timezone = any(), deviceId = null, limit = 1, cursor = null) }

            repo.flushPendingRemovalRefresh() // 标记已清 → 再次调用 no-op
            runCurrent()
            coVerify(exactly = 1) { api.overview(timezone = any(), deviceId = null, limit = 1, cursor = null) }
        } finally {
            repoScope.cancel()
        }
    }
}
