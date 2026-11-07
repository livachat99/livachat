package dev.livachat.app.data.remote.mylife

import dev.livachat.app.testStringContext
import dev.livachat.sdk.LivaChatConfig
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class MylifeApiTest {

    private lateinit var server: MockWebServer
    private lateinit var api: MylifeApi

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        api = MylifeApi(OkHttpClient(), server.url("/api/mylife"), testConfig, testStringContext())
    }

    private val testConfig = LivaChatConfig(
        serverUrl = "wss://example.com/v1/ws/stream",
        token = "dev-token",
        deviceId = "dev-1",
        clientVersion = "android-1.0.0",
        logFilePath = "/tmp/livachat.log",
    )

    @After
    fun tearDown() {
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
    fun `overview 拼 token 与可选参数`() = runBlocking {
        enqueueJson("""{"schema_version":1,"timezone":"Asia/Shanghai","days":[],"total_sessions":0}""")
        api.overview(timezone = "Asia/Shanghai", deviceId = "dev-1")
        val req = server.takeRequest()
        assertEquals("/api/mylife/overview", req.path?.substringBefore('?'))
        val query = req.requestUrl!!.queryParameterNames
        assertEquals("dev-token", req.requestUrl!!.queryParameter("token"))
        assertEquals("Asia/Shanghai", req.requestUrl!!.queryParameter("timezone"))
        assertEquals("dev-1", req.requestUrl!!.queryParameter("device_id"))
    }

    @Test
    fun `overview 分页参数 limit cursor（缺省不发）`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":3,"next_cursor":"2026-08-20",
               |"days":[{"date":"2026-08-21"}]}""".trimMargin(),
        )
        val dto = api.overview(timezone = "Asia/Shanghai", limit = 30, cursor = "2026-08-21")
        val query = server.takeRequest().requestUrl!!
        assertEquals("30", query.queryParameter("limit"))
        assertEquals("2026-08-21", query.queryParameter("cursor"))
        assertEquals("2026-08-20", dto.nextCursor)
        assertEquals(3, dto.total)

        enqueueJson("""{"schema_version":1,"timezone":"Asia/Shanghai","days":[]}""")
        api.overview()
        val bare = server.takeRequest().requestUrl!!
        assertNull(bare.queryParameter("limit"))       // 缺省不发 → bff 全量模式
        assertNull(bare.queryParameter("cursor"))
    }

    @Test
    fun `search 拼装 q limit cursor`() = runBlocking {
        enqueueJson("""{"query":"旅行","total":0,"hits":[]}""")
        api.search(q = "旅行", cursor = "c1")
        val query = server.takeRequest().requestUrl!!
        assertEquals("/api/mylife/search", query.encodedPath)
        assertEquals("旅行", query.queryParameter("q"))
        assertEquals("50", query.queryParameter("limit"))
        assertEquals("c1", query.queryParameter("cursor"))
    }

    @Test
    fun `search 无 cursor 时省略参数`() = runBlocking {
        enqueueJson("""{"query":"a","total":1,"hits":[]}""")
        api.search(q = "a")
        assertNull(server.takeRequest().requestUrl!!.queryParameter("cursor"))
    }

    @Test
    fun `search 解析 5 类 hit 且 ts_ms 可空`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"query":"hello","total":2,"next_cursor":"c2","hits":[
               |{"date":"2026-08-17","ts_ms":1720000000000,
               |"kind":"transcript","snippet":"say hello world"},
               |{"date":"2026-08-17","ts_ms":null,
               |"kind":"topic","snippet":"hello topic"}]}""".trimMargin(),
        )
        val dto = api.search(q = "hello")
        assertEquals(2, dto.hits.size)
        assertEquals("transcript", dto.hits[0].kind)
        assertEquals(1720000000000L, dto.hits[0].tsMs)
        assertEquals("c2", dto.nextCursor)
        assertNull(dto.hits[1].tsMs)
    }

    @Test
    fun `search 400 bad_request 错误信封`() = runBlocking {
        enqueueJson("""{"error":{"code":"bad_request","message":"empty query"}}""", status = 400)
        try {
            api.search(q = "")
            fail("应抛出 MylifeApiException")
        } catch (e: MylifeApiException) {
            assertEquals(400, e.status)
            assertEquals("bad_request", e.code)
            assertEquals("empty query", e.message)
        }
    }

    @Test
    fun `错误信封解析为 MylifeApiException`() = runBlocking {
        enqueueJson(
            """{"error":{"code":"not_found","message":"no session at ts"}}""",
            status = 404,
        )
        try {
            api.search(q = "x")
            fail("应抛出 MylifeApiException")
        } catch (e: MylifeApiException) {
            assertEquals(404, e.status)
            assertEquals("not_found", e.code)
            assertEquals("no session at ts", e.message)
        }
    }

    @Test
    fun `非 JSON 错误体回退 internal`() = runBlocking {
        enqueueJson("not json", status = 500)
        try {
            api.overview()
            fail("应抛出 MylifeApiException")
        } catch (e: MylifeApiException) {
            assertEquals(500, e.status)
            assertEquals("internal", e.code)
        }
    }

    @Test
    fun `ignoreUnknownKeys 容错未知字段`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","unknown_top":true,
               |"days":[{"date":"2026-08-17","counts":{"chat":1},"unknown_day":1,
               |"summary":"日总结","has_pending":true,
               |"hot_slots":[{"start_ms":100,"end_ms":200,"count":5}],
               |"images":[{"frame_id":"f1","ts_ms":300,"event_ref":"r1","media_url":"/m/f1.jpg"}],
               |"topics":[{"topic":"AI","detail":"详情","search_words":"AI 助手"}]}]}""".trimMargin(),
        )
        val dto = api.overview()
        assertEquals(1, dto.days.size)
        val day = dto.days[0]
        assertEquals("日总结", day.summary)
        assertTrue(day.hasPending)
        assertEquals(5, day.hotSlots[0].count)
        assertEquals("r1", day.images[0].eventRef)
        assertEquals("AI", day.topics[0].topic)
        assertEquals("AI 助手", day.topics[0].searchWords)
    }

    @Test
    fun `timeline 拼装 date_from date_to types limit cursor timezone`() = runBlocking {
        enqueueJson("""{"schema_version":1,"timezone":"Asia/Shanghai","days":[],"next_cursor":null,"total":0}""")
        api.timeline(
            dateFrom = "2026-08-21", dateTo = "2026-08-21", cursor = "2026-08-21|40",
            timezone = "Asia/Shanghai",
        )
        val url = server.takeRequest().requestUrl!!
        assertEquals("/api/mylife/timeline", url.encodedPath)
        assertEquals("2026-08-21", url.queryParameter("date_from"))
        assertEquals("2026-08-21", url.queryParameter("date_to"))
        assertEquals("video", url.queryParameter("types"))
        assertEquals("40", url.queryParameter("limit"))
        assertEquals("2026-08-21|40", url.queryParameter("cursor"))
        assertEquals("Asia/Shanghai", url.queryParameter("timezone"))
    }

    @Test
    fun `timeline 解析 days events 与扁平 payload 字段`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":2,"next_cursor":"2026-08-21|2",
               |"days":[{"date":"2026-08-21","events":[
               |{"ts_ms":1000,"type":"video","media_url":"/api/mylife/media/d/s/video/f1.jpg",
               |"frame_id":"f1","window_id":"w1","caption":"浏览器","event_ref":"e1","objects":[]},
               |{"ts_ms":2000,"type":"video","media_url":null,"frame_id":null}]}]}""".trimMargin(),
        )
        val dto = api.timeline(dateFrom = "2026-08-21", dateTo = "2026-08-21")
        assertEquals(2, dto.total)
        assertEquals("2026-08-21|2", dto.nextCursor)
        val ev = dto.days[0].events[0]
        assertEquals(1000L, ev.tsMs)
        assertEquals("f1", ev.frameId)
        assertEquals("w1", ev.windowId)
        assertEquals("浏览器", ev.caption)
        assertEquals("e1", ev.eventRef)
        assertNull(dto.days[0].events[1].mediaUrl)
    }

    @Test
    fun `timeline 解析 hotSlotView 字段与缺字段容错`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"timezone":"Asia/Shanghai","total":4,
               |"days":[{"date":"2026-08-21","events":[
               |{"ts_ms":1000,"type":"chat","role":"user","text":"你好","summary":"你好","event_ref":"e1"},
               |{"ts_ms":2000,"type":"agent_job","agent_name":"hermes","job_key":"j1","text":"跑了任务"},
               |{"ts_ms":3000,"type":"translate","role":"assistant","text":"Hello","source":"chat_translate"},
               |{"ts_ms":4000}]}]}""".trimMargin(),
        )
        val dto = api.timeline(dateFrom = "2026-08-21", dateTo = "2026-08-21")
        val events = dto.days[0].events
        assertEquals("user", events[0].role)
        assertEquals("你好", events[0].text)
        assertEquals("hermes", events[1].agentName)
        assertEquals("j1", events[1].jobKey)
        assertEquals("Hello", events[2].text)
        // 缺字段行宽松解析（默认值）+ 未知键（source）忽略：不整包失败
        assertEquals("", events[3].type)
        assertNull(events[3].text)
    }

    @Test
    fun `downloadBytes 返回响应字节并带 token 直取`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "image/jpeg")
                .setBody(okio.Buffer().write(byteArrayOf(1, 2, 3))),
        )
        val bytes = api.downloadBytes(server.url("/api/mylife/media/d/s/video/f1.jpg?token=t").toString())
        assertEquals(listOf<Byte>(1, 2, 3), bytes.toList())
        assertEquals("/api/mylife/media/d/s/video/f1.jpg", server.takeRequest().path?.substringBefore('?'))
    }

    @Test
    fun `downloadBytes 非 2xx 抛错误`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500))
        try {
            api.downloadBytes(server.url("/api/mylife/media/d/s/video/f1.jpg").toString())
            fail("应抛出 MylifeApiException")
        } catch (e: MylifeApiException) {
            assertEquals(500, e.status)
        }
    }

    // ---- removeEvents（Gallery 编辑模式，docs/android_timeline_image_gallery.md §9.4.3）----

    @Test
    fun `removeEvents POST body 与 token 且解析 removed`() = runBlocking {
        enqueueJson("""{"schema_version":1,"removed":["a:video:f1"]}""")
        val dto = api.removeEvents(listOf("a:video:f1", "a:video:f2"))

        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/mylife/events/remove", req.path?.substringBefore('?'))
        assertEquals("dev-token", req.requestUrl!!.queryParameter("token"))
        assertEquals(
            """{"event_refs":["a:video:f1","a:video:f2"]}""",
            req.body.readUtf8(),
        )
        assertEquals(listOf("a:video:f1"), dto.removed)
    }

    @Test
    fun `removeEvents 解析 skipped 空窗与可选 vectors_pending`() = runBlocking {
        enqueueJson(
            """{"schema_version":1,"removed":[],"window_events_removed":["a:window:w1"],
               |"images_removed":[],"images_pending":["f9"],
               |"days_recomputed":[{"timezone":"Asia/Shanghai","local_date":"2026-10-07"}],
               |"skipped":[{"event_ref":"a:video:f1","reason":"not_found"}],
               |"vectors_pending":true}""".trimMargin(),
        )
        val dto = api.removeEvents(listOf("a:video:f1"))
        assertEquals(listOf("a:window:w1"), dto.windowEventsRemoved)
        assertEquals("f9", dto.imagesPending[0])
        assertEquals("2026-10-07", dto.daysRecomputed[0].localDate)
        assertEquals("not_found", dto.skipped[0].reason)
        assertEquals(true, dto.vectorsPending)
    }

    @Test
    fun `removeEvents 503 removal_disabled 错误信封`() = runBlocking {
        enqueueJson(
            """{"error":{"code":"removal_disabled","message":"bff.lvdata_api_url 未配置，remove 端点禁用"}}""",
            status = 503,
        )
        try {
            api.removeEvents(listOf("a:video:f1"))
            fail("应抛出 MylifeApiException")
        } catch (e: MylifeApiException) {
            assertEquals(503, e.status)
            assertEquals("removal_disabled", e.code)
        }
    }

    // ---- cancelAgentJob（docs/android_agent_job_status.md §7.1）----

    @Test
    fun `cancelAgentJob POST body token device_id 且解析 status`() = runBlocking {
        enqueueJson("""{"schema_version":1,"job_id":"job-1","status":"cancelled"}""")
        val dto = api.cancelAgentJob("job-1")

        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/mylife/agent-jobs/cancel", req.path?.substringBefore('?'))
        assertEquals("dev-token", req.requestUrl!!.queryParameter("token"))
        assertEquals("dev-1", req.requestUrl!!.queryParameter("device_id"))
        assertEquals("""{"job_id":"job-1"}""", req.body.readUtf8())
        assertEquals("job-1", dto.jobId)
        assertEquals("cancelled", dto.status)
    }

    @Test
    fun `cancelAgentJob 503 cancel_incomplete 错误信封`() = runBlocking {
        enqueueJson(
            """{"error":{"code":"cancel_incomplete","message":"job is still active"}}""",
            status = 503,
        )
        try {
            api.cancelAgentJob("job-1")
            fail("应抛出 MylifeApiException")
        } catch (e: MylifeApiException) {
            assertEquals(503, e.status)
            assertEquals("cancel_incomplete", e.code)
        }
    }
}
