package dev.livachat.app.ui.screens.mylife.timeline

import androidx.lifecycle.viewModelScope
import dev.livachat.app.testStringContext
import dev.livachat.app.data.remote.mylife.dto.DayTopicDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.app.data.remote.mylife.dto.SearchHitDto
import dev.livachat.app.data.remote.mylife.dto.SearchResponseDto
import dev.livachat.app.data.repository.MylifeRepository
import java.time.LocalDate
import java.time.ZoneId
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class TimelineViewModelTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<MylifeRepository>(relaxed = true)
    private val overviewFlow = MutableStateFlow<OverviewDto?>(null)

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        every { repository.overview } returns overviewFlow
    }

    private val createdVms = mutableListOf<TimelineViewModel>()

    @After
    fun tearDown() {
        createdVms.forEach { it.viewModelScope.cancel() }
        createdVms.clear()
        Dispatchers.resetMain()
    }

    private fun createViewModel(): TimelineViewModel {
        val vm = TimelineViewModel(repository, testStringContext())
        createdVms.add(vm)
        return vm
    }

    private fun digest(
        key: String,
        granularity: Granularity,
        slots: List<HotSlot> = emptyList(),
        pending: Boolean = false,
    ) = PeriodDigest(
        key = key,
        granularity = granularity,
        rangeLabel = key,
        hotSlots = slots,
        summaryLines = emptyList(),
        summaryLimit = 3,
        topics = emptyList(),
        images = emptyList(),
        imageTotal = 0,
        hasPending = pending,
    )

    private fun hit(id: String, ts: Long? = 1000L, kind: String = "chat") = SearchHitDto(
        date = "2026-08-21", tsMs = ts, kind = kind, snippet = "hi-$id",
    )

    private fun day(date: String, sid: String = "s1") = OverviewDayDto(date = date)

    private fun overview(vararg days: OverviewDayDto) = OverviewDto(
        timezone = "Asia/Shanghai", days = days.toList(),
    )

    /** §8：还有更早（nextCursor 非空 = footer「加载更早」/ 边界未闭合判据成立）。 */
    private fun overviewMore(nextCursor: String, vararg days: OverviewDayDto) = OverviewDto(
        timezone = "Asia/Shanghai", days = days.toList(), nextCursor = nextCursor,
    )

    /** 自 [from] 起倒推的 [count] 个连续日（窗口深度用例）。 */
    private fun daysBack(count: Int, from: LocalDate = LocalDate.of(2026, 9, 30)): List<OverviewDayDto> =
        (0 until count).map { day(from.minusDays(it.toLong()).toString()) }

    // ---- 粒度 / 下钻（§3.3.2）----

    @Test
    fun `手动切换粒度清 scope`() = withVm { vm ->
        vm.onDrillDown(digest("2026-08", Granularity.MONTH))
        vm.onGranularityChange(Granularity.WEEK)
        assertEquals(Granularity.WEEK, vm.uiState.value.granularity)
        assertNull(vm.uiState.value.scopeKey)
    }

    @Test
    fun `月卡下钻切日粒度并设 scope`() = withVm { vm ->
        vm.onDrillDown(digest("2026-08", Granularity.MONTH))
        assertEquals(Granularity.DAY, vm.uiState.value.granularity)
        assertEquals("2026-08", vm.uiState.value.scopeKey)
    }

    @Test
    fun `年卡下钻切月粒度并设 scope`() = withVm { vm ->
        vm.onDrillDown(digest("2026", Granularity.YEAR))
        assertEquals(Granularity.MONTH, vm.uiState.value.granularity)
        assertEquals("2026", vm.uiState.value.scopeKey)
    }

    @Test
    fun `日卡周卡不响应下钻`() = withVm { vm ->
        vm.onDrillDown(digest("2026-08-21", Granularity.DAY))
        vm.onDrillDown(digest("2026-W36", Granularity.WEEK))
        assertEquals(Granularity.DAY, vm.uiState.value.granularity)
        assertNull(vm.uiState.value.scopeKey)
    }

    @Test
    fun `scope pill 清除粒度不变`() = withVm { vm ->
        vm.onDrillDown(digest("2026-08", Granularity.MONTH))
        vm.onClearScope()
        assertNull(vm.uiState.value.scopeKey)
        assertEquals(Granularity.DAY, vm.uiState.value.granularity)
    }

    @Test
    fun `periods 随 overview 与粒度变化重聚合`() = withVm { vm ->
        overviewFlow.value = overview(day("2026-08-21"), day("2026-07-15"))
        runCurrent()
        assertEquals(listOf("2026-08-21", "2026-07-15"), vm.uiState.value.periods.map { it.key })
        vm.onGranularityChange(Granularity.MONTH)
        assertEquals(listOf("2026-08", "2026-07"), vm.uiState.value.periods.map { it.key })
    }

    @Test
    fun `scope 过滤后 periods 只含该范围`() = withVm { vm ->
        overviewFlow.value = overview(day("2026-08-21"), day("2026-07-15"))
        runCurrent()
        vm.onDrillDown(digest("2026-08", Granularity.MONTH))
        assertEquals(listOf("2026-08-21"), vm.uiState.value.periods.map { it.key })
    }

    // ---- Replay 导航（§4.3：ts_ms 主锚 + event_ref 第二锚）----

    @Test
    fun `hot slot 点击导航 hotSlotView 携带窗口与 pending`() = withVm { vm ->
        val events = collectHotSlotNavigation(vm)
        vm.onHotSlotClick(HotSlot(startMs = 12345L, endMs = 23456L), pending = true)
        runCurrent()
        assertEquals(listOf(HotSlotNavigation(12345L, 23456L, pending = true)), events)
    }

    @Test
    fun `hot slot 点击不导航 EventList`() = withVm { vm ->
        val navs = collectEventListNavigation(vm)
        vm.onHotSlotClick(HotSlot(startMs = 12345L, endMs = 23456L), pending = false)
        runCurrent()
        assertTrue(navs.isEmpty())
    }

    // ---- AudioGallery 导航（docs/android_timeline_audio_gallery.md §4.1）----

    @Test
    fun `日卡音频入口导航携带日期 label 段数与 pending`() = withVm { vm ->
        val navs = collectAudioNavigation(vm)
        vm.onAudioClick(digest("2026-08-21", Granularity.DAY, pending = true).copy(audioCount = 14))
        runCurrent()
        // 本文件 digest() 夹具的 rangeLabel = key（label 透传语义即「原样带过去」）
        assertEquals(
            listOf(AudioGalleryNavigation("2026-08-21", "2026-08-21", 14, pending = true)),
            navs,
        )
    }

    @Test
    fun `无音频日卡的音频入口点击不导航`() = withVm { vm ->
        val navs = collectAudioNavigation(vm)
        vm.onAudioClick(digest("2026-08-21", Granularity.DAY).copy(audioCount = 0))
        runCurrent()
        assertTrue(navs.isEmpty())
    }


    @Test
    fun `图片点击导航 Gallery 携带范围 label focus pending`() = withVm { vm ->
        val events = collectGalleryNavigation(vm)
        vm.onImageClick(
            PeriodImage(url = "u", tsMs = 999L, eventRef = "ref-1", frameId = "f-1"),
            digest("2026-08-21", Granularity.DAY, pending = true),
        )
        runCurrent()
        assertEquals(
            listOf(GalleryNavigation("2026-08-21", "2026-08-21", "2026-08-21", "f-1", pending = true)),
            events,
        )
    }

    @Test
    fun `图片点击周卡导航范围取 ISO 周一至周日`() = withVm { vm ->
        val events = collectGalleryNavigation(vm)
        vm.onImageClick(
            PeriodImage(url = "u", tsMs = 999L, eventRef = "ref-1", frameId = "f-1"),
            digest("2026-W36", Granularity.WEEK),
        )
        runCurrent()
        val nav = events.single()
        assertEquals("2026-08-31", nav.from)
        assertEquals("2026-09-06", nav.to)
        assertEquals("W36", nav.label.substringAfter('-'))
    }

    @Test
    fun `图片点击不导航 EventList（Q6）`() = withVm { vm ->
        val navs = collectEventListNavigation(vm)
        vm.onImageClick(
            PeriodImage(url = "u", tsMs = 999L, eventRef = "ref-1", frameId = "f-1"),
            digest("2026-08-21", Granularity.DAY),
        )
        runCurrent()
        assertTrue(navs.isEmpty())
    }

    @Test
    fun `搜索事件命中导航 EventList 携带 ts 与当日 pending`() = withVm { vm ->
        overviewFlow.value = overview(day("2026-08-21").copy(hasPending = true))
        runCurrent()
        val ts = LocalDate.of(2026, 8, 21).atTime(12, 0)
            .atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
        val navs = collectEventListNavigation(vm)
        vm.onSearchHitClick(hit("s1", ts = ts, kind = "chat"))
        runCurrent()
        assertEquals(listOf(EventListNavigation(ts, pending = true)), navs)
    }

    @Test
    fun `命中当日无 overview 条目时 pending 为 false`() = withVm { vm ->
        val navs = collectEventListNavigation(vm)
        vm.onSearchHitClick(hit("s1", ts = 1000L, kind = "chat"))
        runCurrent()
        assertEquals(listOf(EventListNavigation(1000L, pending = false)), navs)
    }

    @Test
    fun `命中 ts 为空不导航`() = withVm { vm ->
        val navs = collectEventListNavigation(vm)
        vm.onSearchHitClick(hit("s1", ts = null, kind = "chat"))
        runCurrent()
        assertTrue(navs.isEmpty())
    }

    private suspend fun kotlinx.coroutines.test.TestScope.collectEventListNavigation(
        vm: TimelineViewModel,
    ): MutableList<EventListNavigation> {
        val events = mutableListOf<EventListNavigation>()
        backgroundScope.launch {
            vm.eventListNavigation.collect { events.add(it) }
        }
        runCurrent()
        return events
    }

    private suspend fun kotlinx.coroutines.test.TestScope.collectHotSlotNavigation(
        vm: TimelineViewModel,
    ): MutableList<HotSlotNavigation> {
        val events = mutableListOf<HotSlotNavigation>()
        backgroundScope.launch {
            vm.hotSlotNavigation.collect { events.add(it) }
        }
        runCurrent()
        return events
    }

    private suspend fun kotlinx.coroutines.test.TestScope.collectGalleryNavigation(
        vm: TimelineViewModel,
    ): MutableList<GalleryNavigation> {
        val events = mutableListOf<GalleryNavigation>()
        backgroundScope.launch {
            vm.galleryNavigation.collect { events.add(it) }
        }
        runCurrent()
        return events
    }

    private suspend fun kotlinx.coroutines.test.TestScope.collectAudioNavigation(
        vm: TimelineViewModel,
    ): MutableList<AudioGalleryNavigation> {
        val events = mutableListOf<AudioGalleryNavigation>()
        backgroundScope.launch {
            vm.audioNavigation.collect { events.add(it) }
        }
        runCurrent()
        return events
    }

    // ---- 搜索（§3.4）----

    @Test
    fun `搜索防抖 350ms 内连续输入仅一次请求`() = withVm { vm ->
        coEvery { repository.search("ab", cursor = null) } returns SearchResponseDto("ab", 1, null, listOf(hit("s1")))
        vm.onQueryChange("a")
        vm.onQueryChange("ab")
        advanceTimeBy(350)
        runCurrent()
        coVerify(exactly = 1) { repository.search("ab", cursor = null) }
        coVerify(exactly = 0) { repository.search("a", cursor = null) }
    }

    @Test
    fun `min 2 字符以下回 Idle`() = withVm { vm ->
        vm.onQueryChange("a")
        advanceTimeBy(350)
        runCurrent()
        assertEquals(SearchState.Idle, vm.uiState.value.search)
        coVerify(exactly = 0) { repository.search(any(), any()) }
    }

    @Test
    fun `搜索成功进入 Data`() = withVm { vm ->
        coEvery { repository.search("旅行", cursor = null) } returns SearchResponseDto(
            "旅行", 2, "c1", listOf(hit("s1"), hit("s2")),
        )
        vm.onQueryChange("旅行")
        advanceTimeBy(350)
        runCurrent()
        val data = vm.uiState.value.search as SearchState.Data
        assertEquals(2, data.hits.size)
        assertEquals(2, data.total)
        assertEquals("c1", data.nextCursor)
    }

    @Test
    fun `搜索无结果进入 Empty`() = withVm { vm ->
        coEvery { repository.search("ab", cursor = null) } returns SearchResponseDto("ab", 0, null, emptyList())
        vm.onQueryChange("ab")
        advanceTimeBy(350)
        runCurrent()
        assertEquals(SearchState.Empty, vm.uiState.value.search)
    }

    @Test
    fun `搜索失败进入 Error 且重试可恢复`() = withVm { vm ->
        coEvery { repository.search("ab", cursor = null) } throws RuntimeException("boom")
        vm.onQueryChange("ab")
        advanceTimeBy(350)
        runCurrent()
        assertTrue(vm.uiState.value.search is SearchState.Error)

        coEvery { repository.search("ab", cursor = null) } returns SearchResponseDto("ab", 1, null, listOf(hit("s1")))
        vm.onSearchRetry()
        runCurrent()
        assertTrue(vm.uiState.value.search is SearchState.Data)
    }

    @Test
    fun `query 清空立即回 Idle`() = withVm { vm ->
        coEvery { repository.search("ab", cursor = null) } returns SearchResponseDto("ab", 1, null, listOf(hit("s1")))
        vm.onQueryChange("ab")
        advanceTimeBy(350)
        runCurrent()
        assertTrue(vm.uiState.value.search is SearchState.Data)
        vm.onQueryChange("")
        runCurrent()
        assertEquals(SearchState.Idle, vm.uiState.value.search)
    }

    @Test
    fun `加载更多累页追加 hit`() = withVm { vm ->
        coEvery { repository.search("ab", cursor = null) } returns SearchResponseDto("ab", 2, "c1", listOf(hit("s1")))
        vm.onQueryChange("ab")
        advanceTimeBy(350)
        runCurrent()
        coEvery { repository.search("ab", cursor = "c1") } returns SearchResponseDto("ab", 2, null, listOf(hit("s2")))
        vm.onSearchLoadMore()
        runCurrent()
        val data = vm.uiState.value.search as SearchState.Data
        assertEquals(listOf("hi-s1", "hi-s2"), data.hits.map { it.snippet })
        assertNull(data.nextCursor)
    }

    @Test
    fun `新 query 到达废弃上一页结果`() = withVm { vm ->
        coEvery { repository.search("ab", cursor = null) } returns SearchResponseDto("ab", 2, "c1", listOf(hit("s1")))
        coEvery { repository.search("cd", cursor = null) } returns SearchResponseDto("cd", 1, null, listOf(hit("s3", ts = 2000L)))
        vm.onQueryChange("ab")
        advanceTimeBy(350)
        runCurrent()
        vm.onQueryChange("cd")
        advanceTimeBy(350)
        runCurrent()
        val data = vm.uiState.value.search as SearchState.Data
        assertEquals(listOf("hi-s3"), data.hits.map { it.snippet })
        assertEquals(1, data.total)
    }

    @Test
    fun `onTopicSearch 置 query 并触发搜索流`() = withVm { vm ->
        coEvery { repository.search("旅行与天气", cursor = null) } returns
            SearchResponseDto("旅行与天气", 1, null, listOf(hit("s1")))
        vm.onTopicSearch(DayTopicDto("旅行与天气"))
        advanceTimeBy(350)
        runCurrent()
        assertEquals("旅行与天气", vm.uiState.value.query)
        assertTrue(vm.uiState.value.search is SearchState.Data)
    }

    @Test
    fun `onTopicSearch 优先用 search_words 空白回退 topic`() = withVm { vm ->
        coEvery { repository.search("三亚天气", cursor = null) } returns
            SearchResponseDto("三亚天气", 1, null, listOf(hit("s2")))
        vm.onTopicSearch(DayTopicDto("旅游与天气", searchWords = "三亚天气"))
        advanceTimeBy(350)
        runCurrent()
        assertEquals("三亚天气", vm.uiState.value.query)

        coEvery { repository.search("空白词", cursor = null) } returns
            SearchResponseDto("空白词", 1, null, listOf(hit("s3")))
        vm.onTopicSearch(DayTopicDto("空白词", searchWords = "   "))
        advanceTimeBy(350)
        runCurrent()
        assertEquals("空白词", vm.uiState.value.query)
    }

    // ---- 加载 / 刷新 ----

    @Test
    fun `VM 创建即触发首次 overview 加载`() = withVm { vm ->
        runCurrent()
        coVerify { repository.refreshOverview(force = true) }
    }

    @Test
    fun `首次加载失败后 overviewLoading 结束并置错误`() = withVm { vm ->
        coEvery { repository.refreshOverview(force = true) } throws RuntimeException("boom")
        runCurrent()
        assertFalse(vm.uiState.value.overviewLoading)
        assertEquals("boom", vm.uiState.value.overviewError)
    }

    @Test
    fun `加载成功后清除 overviewError`() = withVm { vm ->
        coEvery { repository.refreshOverview(force = true) } throws RuntimeException("boom")
        runCurrent()
        assertFalse(vm.uiState.value.overviewLoading)
        assertEquals("boom", vm.uiState.value.overviewError)

        coEvery { repository.refreshOverview(force = true) } returns overview(day("2026-08-21"))
        overviewFlow.value = overview(day("2026-08-21")) // 模拟 Repository 内部更新 StateFlow
        vm.refresh()
        runCurrent()
        assertNull(vm.uiState.value.overviewError)
        assertEquals(1, vm.uiState.value.periods.size)
    }

    @Test
    fun `刷新 force 刷 overview 并结束刷新态`() = withVm { vm ->
        vm.refresh()
        runCurrent()
        coVerify(exactly = 2) { repository.refreshOverview(force = true) }
        assertFalse(vm.uiState.value.isRefreshing)
    }

    @Test
    fun `搜索模式刷新重跑当前 query`() = withVm { vm ->
        coEvery { repository.search("ab", cursor = null) } returns SearchResponseDto("ab", 1, null, listOf(hit("s1")))
        vm.onQueryChange("ab")
        advanceTimeBy(350)
        runCurrent()
        coEvery { repository.search("ab", cursor = null) } returns SearchResponseDto("ab", 1, null, listOf(hit("s2", ts = 2000L)))
        vm.refresh()
        runCurrent()
        coVerify(exactly = 2) { repository.search("ab", cursor = null) }
        val data = vm.uiState.value.search as SearchState.Data
        assertEquals("hi-s2", data.hits[0].snippet)
        assertFalse(vm.uiState.value.isRefreshing)
    }

    // ---- §8 分页：触底加载 / 边界 group / scope 刷新 / 粒度补窗 ----

    @Test
    fun `触底加载更早一页且 loadingMore 互斥`() = withVm { vm ->
        overviewFlow.value = overviewMore("2026-09-01", day("2026-09-30"))
        runCurrent()
        vm.onLoadMore()
        vm.onLoadMore()                                   // 第二次被 loadingMore 互斥
        runCurrent()
        coVerify(exactly = 1) { repository.loadMoreOverview(30) }
        assertFalse(vm.uiState.value.loadingMore)
    }

    @Test
    fun `无更多时触底不发请求`() = withVm { vm ->
        overviewFlow.value = overview(day("2026-09-30"))   // nextCursor = null
        runCurrent()
        vm.onLoadMore()
        runCurrent()
        coVerify(exactly = 0) { repository.loadMoreOverview(any()) }
    }

    @Test
    fun `触底加载失败落 loadMoreError 供 footer 重试`() = withVm { vm ->
        overviewFlow.value = overviewMore("2026-09-01", day("2026-09-30"))
        runCurrent()
        coEvery { repository.loadMoreOverview(any()) } throws RuntimeException("boom")
        vm.onLoadMore()
        runCurrent()
        assertEquals("boom", vm.uiState.value.loadMoreError)
        assertFalse(vm.uiState.value.loadingMore)

        // 新窗口到达（轮询/刷新）→ 错误态解除，footer 不再卡在「加载失败」
        overviewFlow.value = overviewMore("2026-08-31", day("2026-09-30"), day("2026-09-29"))
        runCurrent()
        assertNull(vm.uiState.value.loadMoreError)
    }

    @Test
    fun `drilled scope 内触底不翻页（范围数据已全量加载）`() = withVm { vm ->
        overviewFlow.value = overviewMore(
            "2026-08-01", day("2026-08-20"), day("2026-08-19"),
        )
        vm.onDrillDown(digest("2026-08", Granularity.MONTH))   // → DAY 粒度 + scope=2026-08
        runCurrent()
        vm.onLoadMore()
        runCurrent()
        coVerify(exactly = 0) { repository.loadMoreOverview(any()) }
    }

    @Test
    fun `tz 变化清 scope（日界重排）`() = withVm { vm ->
        overviewFlow.value = overview(day("2026-08-20"))
        vm.onDrillDown(digest("2026-08", Granularity.MONTH))
        runCurrent()
        assertEquals("2026-08", vm.uiState.value.scopeKey)

        overviewFlow.value = OverviewDto(timezone = "UTC", days = listOf(day("2026-08-19")))
        runCurrent()
        assertNull(vm.uiState.value.scopeKey)
    }

    @Test
    fun `触底加载用当前粒度的页大小`() = withVm { vm ->
        overviewFlow.value = daysBack(90).let { overviewMore("2026-07-01", *it.toTypedArray()) }
        runCurrent()
        vm.onGranularityChange(Granularity.MONTH)          // 窗口已 90 天 → 无需补窗
        vm.onLoadMore()
        runCurrent()
        coVerify { repository.loadMoreOverview(90) }
    }

    @Test
    fun `边界 group：最旧未闭合的月卡不上屏（footer 占位）`() = withVm { vm ->
        // 窗口 = 09-30/09-29 + 08-20（8 月只加载了后半段）→ 2026-08 未闭合
        overviewFlow.value = overviewMore(
            "2026-08-20", day("2026-09-30"), day("2026-09-29"), day("2026-08-20"),
        )
        runCurrent()
        vm.onGranularityChange(Granularity.MONTH)
        runCurrent()
        assertEquals(listOf("2026-09"), vm.uiState.value.periods.map { it.key })

        // 已到最早（nextCursor = null）→ 未闭合判定不成立，两张卡都上屏
        overviewFlow.value = overview(
            day("2026-09-30"), day("2026-09-29"), day("2026-08-20"),
        )
        runCurrent()
        assertEquals(listOf("2026-09", "2026-08"), vm.uiState.value.periods.map { it.key })
    }

    @Test
    fun `边界 group：日粒度卡片恒闭合`() = withVm { vm ->
        overviewFlow.value = overviewMore("2026-09-29", day("2026-09-30"), day("2026-09-29"))
        runCurrent()
        assertEquals(listOf("2026-09-30", "2026-09-29"), vm.uiState.value.periods.map { it.key })
    }

    @Test
    fun `drilled scope 刷新按 scope 窗口重取（不进假空态）`() = withVm { vm ->
        vm.onDrillDown(digest("2026-08", Granularity.MONTH))   // granularity=DAY + scope=2026-08
        runCurrent()
        vm.refresh()
        runCurrent()
        // cursor = scope 末日次日（E12 备注）；limit = scope 天数（2026-08 = 31 天）
        coVerify { repository.refreshOverview(force = true, limit = 31, cursor = "2026-09-01") }
        assertFalse(vm.uiState.value.isRefreshing)
    }

    @Test
    fun `切粒度窗口不足且有更早数据时按该粒度页大小补取一页`() = withVm { vm ->
        overviewFlow.value = overviewMore("2026-09-01", *daysBack(30).toTypedArray())
        runCurrent()
        vm.onGranularityChange(Granularity.YEAR)           // 30 < 366 且 hasMore → 补窗
        runCurrent()
        coVerify { repository.refreshOverview(force = true, limit = 366) }
    }

    @Test
    fun `切粒度窗口已足够时不补取`() = withVm { vm ->
        overviewFlow.value = overviewMore("2026-09-01", *daysBack(400).toTypedArray())
        runCurrent()
        vm.onGranularityChange(Granularity.YEAR)
        runCurrent()
        coVerify(exactly = 0) { repository.refreshOverview(force = true, limit = 366) }
    }

    @Test
    fun `窗口已完整（无更早）时切粒度纯客户端重聚合不发请求`() = withVm { vm ->
        overviewFlow.value = overview(*daysBack(10).toTypedArray())   // nextCursor = null：10 天即全量
        runCurrent()
        vm.onGranularityChange(Granularity.WEEK)
        vm.onGranularityChange(Granularity.MONTH)
        vm.onGranularityChange(Granularity.YEAR)
        runCurrent()
        coVerify(exactly = 0) { repository.refreshOverview(force = true, limit = 90) }
        coVerify(exactly = 0) { repository.refreshOverview(force = true, limit = 366) }
    }

    private fun withVm(block: suspend kotlinx.coroutines.test.TestScope.(TimelineViewModel) -> Unit) = runTest {
        val vm = createViewModel()
        try {
            block(vm)
        } finally {
            vm.viewModelScope.cancel()
        }
    }
}
