package dev.livachat.app.ui.screens.mylife.hotslot

import androidx.lifecycle.viewModelScope
import dev.livachat.app.testStringContext
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.app.data.repository.HotSlotPage
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.ImageGallerySaver
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
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

/** hotSlotView VM（docs/android_timeline_hotslot.md §4.3）：状态机 + 续页终止 + 跨页去重 + Q11。 */
@OptIn(ExperimentalCoroutinesApi::class)
class HotSlotViewModelTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<MylifeRepository>(relaxed = true)
    private val saver = mockk<ImageGallerySaver>(relaxed = true)
    private val overviewFlow = MutableStateFlow<OverviewDto?>(null)
    private val createdVms = mutableListOf<HotSlotViewModel>()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        every { repository.overview } returns overviewFlow
    }

    @After
    fun tearDown() {
        createdVms.forEach { it.viewModelScope.cancel() }
        createdVms.clear()
        Dispatchers.resetMain()
    }

    private fun ev(ts: Long, ref: String = "r-$ts") =
        HotSlotEvent(tsMs = ts, type = "chat", role = "user", text = "t$ts", eventRef = ref)

    private fun page(
        events: List<HotSlotEvent>,
        nextCursor: String? = null,
        reachedWindowEnd: Boolean = false,
    ) = HotSlotPage(events = events, nextCursor = nextCursor, reachedWindowEnd = reachedWindowEnd)

    private fun withVm(block: suspend TestScope.(HotSlotViewModel) -> Unit) = runTest {
        val vm = HotSlotViewModel(repository, saver, testStringContext())
        createdVms.add(vm)
        try {
            block(vm)
        } finally {
            vm.viewModelScope.cancel()
        }
    }

    private fun HotSlotViewModel.events(): List<HotSlotEvent> =
        (uiState.value.state as HotSlotState.Data).events

    @Test
    fun `首页加载即 Data`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(listOf(ev(1000), ev(1500)))
        vm.load(1000L, 2000L)
        runCurrent()
        assertEquals(listOf(1000L, 1500L), vm.events().map { it.tsMs })
    }

    @Test
    fun `空窗口进入 Empty`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(emptyList())
        vm.load(1000L, 2000L)
        runCurrent()
        assertEquals(HotSlotState.Empty, vm.uiState.value.state)
    }

    @Test
    fun `加载失败进入 Error 重试恢复`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } throws RuntimeException("boom")
        vm.load(1000L, 2000L)
        runCurrent()
        assertTrue(vm.uiState.value.state is HotSlotState.Error)

        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(listOf(ev(1000)))
        vm.retry()
        runCurrent()
        assertEquals(listOf(1000L), vm.events().map { it.tsMs })
    }

    @Test
    fun `触底续页追加 越窗即终止且不再发请求`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000)), nextCursor = "d|1")
        coEvery { repository.hotSlotEvents(1000L, 2000L, "d|1") } returns
            page(listOf(ev(1500)), nextCursor = "d|2", reachedWindowEnd = true)

        vm.load(1000L, 2000L)
        runCurrent()
        vm.loadMore()
        runCurrent()
        assertEquals(listOf(1000L, 1500L), vm.events().map { it.tsMs })
        assertTrue((vm.uiState.value.state as HotSlotState.Data).exhausted)

        // 终止后 loadMore no-op：不再发请求
        vm.loadMore()
        runCurrent()
        coVerify(exactly = 1) { repository.hotSlotEvents(1000L, 2000L, "d|1") }
    }

    @Test
    fun `跨页 event_ref 去重（OFFSET 位移防重复）`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000, "a"), ev(1100, "b")), nextCursor = "d|2")
        coEvery { repository.hotSlotEvents(1000L, 2000L, "d|2") } returns
            page(listOf(ev(1100, "b"), ev(1200, "c")), nextCursor = null)

        vm.load(1000L, 2000L)
        runCurrent()
        vm.loadMore()
        runCurrent()
        assertEquals(listOf("a", "b", "c"), vm.events().map { it.eventRef })
        assertTrue((vm.uiState.value.state as HotSlotState.Data).exhausted) // nextCursor == null
    }

    @Test
    fun `首页整页落在窗口之前时继续追击不落空态`() = withVm { vm ->
        // 重度日（事件 > 单页 500 条）窗口靠后：第 1 页整页早于窗口，内容在第 2 页
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(emptyList(), nextCursor = "d|500")
        coEvery { repository.hotSlotEvents(1000L, 2000L, "d|500") } returns
            page(listOf(ev(1500)), nextCursor = null)

        vm.load(1000L, 2000L)
        runCurrent()
        assertEquals(listOf(1500L), vm.events().map { it.tsMs })
        coVerify(exactly = 1) { repository.hotSlotEvents(1000L, 2000L, "d|500") }
    }

    @Test
    fun `追击到底仍无内容才落空态`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(emptyList(), nextCursor = "d|500")
        coEvery { repository.hotSlotEvents(1000L, 2000L, "d|500") } returns
            page(emptyList(), nextCursor = null)

        vm.load(1000L, 2000L)
        runCurrent()
        assertEquals(HotSlotState.Empty, vm.uiState.value.state)
    }

    @Test
    fun `overview 实例变化重取首页替换列表（Q11）`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(listOf(ev(1000)))
        vm.load(1000L, 2000L)
        runCurrent()
        assertEquals(listOf(1000L), vm.events().map { it.tsMs })

        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1500), ev(1600), ev(1700)))
        overviewFlow.value = OverviewDto(timezone = "Asia/Shanghai") // 分析完成 → 轮询刷新 overview
        runCurrent()
        assertEquals(listOf(1500L, 1600L, 1700L), vm.events().map { it.tsMs })
        coVerify(exactly = 2) { repository.hotSlotEvents(1000L, 2000L, null) }
    }

    // ---- pending 收敛（§11 P1①，随 §10 批次）----

    /** 内容相异的 overview 实例（StateFlow 对相等值去重，不能复用同一实例触发重取）。 */
    private fun overview(date: String, pending: Boolean) =
        OverviewDto(timezone = "Asia/Shanghai", days = listOf(OverviewDayDto(date = date, hasPending = pending)))

    @Test
    fun `pending 初值随路由快照并在分析全部结束后单向收敛`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(listOf(ev(1000)))
        vm.load(1000L, 2000L, pending = true)
        runCurrent()
        assertTrue(vm.uiState.value.pending)   // 初值 = 路由快照

        // overview 仍显示有 pending 分析 → 保持 true（即使 overview 实例变化触发重取）
        every { repository.hasPendingAnalysis() } returns true
        overviewFlow.value = overview("2026-08-21", pending = true)
        runCurrent()
        assertTrue(vm.uiState.value.pending)

        // 队列内已无 pending 分析 → 单向收敛为 false
        every { repository.hasPendingAnalysis() } returns false
        overviewFlow.value = overview("2026-08-21", pending = false)
        runCurrent()
        assertFalse(vm.uiState.value.pending)

        // 之后即使 overview 再次显示有 pending（新一批分析），也不回翻
        every { repository.hasPendingAnalysis() } returns true
        overviewFlow.value = overview("2026-08-22", pending = true)
        runCurrent()
        assertFalse(vm.uiState.value.pending)
    }

    @Test
    fun `同窗口重复 load 不重置已收敛的 pending`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(listOf(ev(1000)))
        every { repository.hasPendingAnalysis() } returns false
        vm.load(1000L, 2000L, pending = true)
        runCurrent()
        overviewFlow.value = overview("2026-08-21", pending = false)
        runCurrent()
        assertFalse(vm.uiState.value.pending)

        vm.load(1000L, 2000L, pending = true)   // 同窗口已加载 → 直接返回，pending 保持收敛值
        runCurrent()
        assertFalse(vm.uiState.value.pending)
    }

    // ---- 锚点追击（docs/android_timeline_topics.md §3.3.3，R1）----

    @Test
    fun `锚点追击：锚点在后续页`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000)), nextCursor = "c1")
        coEvery { repository.hotSlotEvents(1000L, 2000L, "c1") } returns
            page(listOf(ev(1500)), nextCursor = "c2")

        vm.load(1000L, 2000L)
        runCurrent()
        vm.ensureLoadedUntil(1500L)
        runCurrent()
        assertEquals(listOf(1000L, 1500L), vm.events().map { it.tsMs })
        coVerify(exactly = 1) { repository.hotSlotEvents(1000L, 2000L, "c1") }
    }

    @Test
    fun `锚点已被已加载事件覆盖则不再追击`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000), ev(1500)), nextCursor = "c1")

        vm.load(1000L, 2000L)
        runCurrent()
        vm.ensureLoadedUntil(1500L)
        runCurrent()
        coVerify(exactly = 0) { repository.hotSlotEvents(1000L, 2000L, "c1") }
    }

    @Test
    fun `窗口已终止时锚点追击不发请求`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000)), reachedWindowEnd = true)

        vm.load(1000L, 2000L)
        runCurrent()
        vm.ensureLoadedUntil(1500L)
        runCurrent()
        coVerify(exactly = 1) { repository.hotSlotEvents(1000L, 2000L, any()) } // 仅首页
    }

    @Test
    fun `追击页数硬上限 5 页后停`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000)), nextCursor = "c1")
        (1..6).forEach { i ->
            coEvery { repository.hotSlotEvents(1000L, 2000L, "c$i") } returns
                page(listOf(ev(1000L + i)), nextCursor = "c${i + 1}")
        }

        vm.load(1000L, 2000L)
        runCurrent()
        vm.ensureLoadedUntil(9000L)   // 永远追不上 → 走预算上限
        runCurrent()
        assertEquals(6, vm.events().size)                                        // 1 + 5 页
        coVerify(exactly = 1) { repository.hotSlotEvents(1000L, 2000L, "c5") }
        coVerify(exactly = 0) { repository.hotSlotEvents(1000L, 2000L, "c6") }   // 不无限翻页
    }

    @Test
    fun `追击失败消耗预算（防失败重试风暴）`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000)), nextCursor = "c1")
        coEvery { repository.hotSlotEvents(1000L, 2000L, "c1") } throws RuntimeException("boom")

        vm.load(1000L, 2000L)
        runCurrent()
        repeat(8) {                 // 模拟 Screen effect 重入（失败翻转 loadingMore 驱动）
            vm.ensureLoadedUntil(2000L)
            runCurrent()
        }
        coVerify(exactly = 5) { repository.hotSlotEvents(1000L, 2000L, "c1") }   // 尝试计数：5 次后停
        assertEquals(listOf(1000L), vm.events().map { it.tsMs })                 // 已加载页保留
        assertFalse((vm.uiState.value.state as HotSlotState.Data).loadingMore)   // finally 复位
    }

    @Test
    fun `追击期间触底 loadMore 互斥（约定 4）`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000)), nextCursor = "c1")
        val gate = CompletableDeferred<HotSlotPage>()
        coEvery { repository.hotSlotEvents(1000L, 2000L, "c1") } coAnswers { gate.await() }

        vm.load(1000L, 2000L)
        runCurrent()
        vm.ensureLoadedUntil(2000L)
        runCurrent()          // 追击挂在 c1 请求上
        vm.loadMore()         // 触底并发：被阻塞，不得与追击竞争同一 nextCursor
        runCurrent()
        coVerify(exactly = 1) { repository.hotSlotEvents(1000L, 2000L, "c1") }

        gate.complete(page(listOf(ev(1500)), nextCursor = "c2"))
        runCurrent()
        assertEquals(listOf(1000L, 1500L), vm.events().map { it.tsMs })
    }

    @Test
    fun `Q11 重取后追击预算清零可重追（约定 3）`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000)), nextCursor = "c1")
        (1..6).forEach { i ->
            coEvery { repository.hotSlotEvents(1000L, 2000L, "c$i") } returns
                page(listOf(ev(1000L + i)), nextCursor = "c${i + 1}")
        }

        vm.load(1000L, 2000L)
        runCurrent()
        vm.ensureLoadedUntil(9000L)
        runCurrent()          // 第一轮预算耗尽

        overviewFlow.value = OverviewDto(timezone = "Asia/Shanghai")   // Q11 → reloadFirstPage
        runCurrent()
        vm.ensureLoadedUntil(9000L)
        runCurrent()
        // 第二轮重新追击：c1 被请求两次（预算已随重取清零）
        coVerify(exactly = 2) { repository.hotSlotEvents(1000L, 2000L, "c1") }
    }

    // ---- §9 帧全屏查看器 + 保存到相册 ----

    private val jpegBytes = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0x01)

    private fun videoFrame(ts: Long = 2000L, caption: String? = "画面描述") = HotSlotEvent(
        tsMs = ts, type = "video", frameId = "f-$ts",
        mediaUrl = "https://example.com/$ts.jpg", caption = caption, eventRef = "v-$ts",
    )

    @Test
    fun `查看器开合：置 viewerFrame 清旧提示 关闭置空`() = withVm { vm ->
        vm.onSavePermissionDenied()
        runCurrent()
        assertEquals("res:${R.string.common_save_failed_storage}", vm.uiState.value.imageSaveTip)

        val frame = videoFrame()
        vm.onFrameClick(frame)
        assertEquals(frame, vm.uiState.value.viewerFrame)
        assertNull(vm.uiState.value.imageSaveTip) // 重开不显示陈语文案

        vm.onViewerDismiss()
        assertNull(vm.uiState.value.viewerFrame)
        assertFalse(vm.uiState.value.saving)
    }

    @Test
    fun `查看器打开时 Q11 重取替换列表不影响 viewerFrame`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(listOf(ev(1000)))
        vm.load(1000L, 2000L)
        runCurrent()

        val frame = videoFrame()
        vm.onFrameClick(frame)
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(listOf(ev(1500)))
        overviewFlow.value = OverviewDto(timezone = "Asia/Shanghai")
        runCurrent()

        assertEquals(listOf(1500L), vm.events().map { it.tsMs }) // 列表已被替换
        assertEquals(frame, vm.uiState.value.viewerFrame) // 查看器持事件对象，不失效（Q15）
    }

    @Test
    fun `权限拒绝提示 2s 后清除`() = withVm { vm ->
        vm.onSavePermissionDenied()
        runCurrent()
        assertEquals("res:${R.string.common_save_failed_storage}", vm.uiState.value.imageSaveTip)

        advanceTimeBy(2_000)
        runCurrent()
        assertNull(vm.uiState.value.imageSaveTip)
    }

    @Test
    fun `保存成功展示提示 2s 后清除`() = withVm { vm ->
        val frame = videoFrame()
        vm.onFrameClick(frame)
        coEvery { repository.downloadFrame(frame.mediaUrl!!) } returns jpegBytes
        coEvery { saver.saveJpegBytes(any(), any()) } returns true

        vm.saveViewerFrame()
        runCurrent()
        assertEquals("res:${R.string.common_saved_to_gallery}", vm.uiState.value.imageSaveTip)
        assertFalse(vm.uiState.value.saving) // finally 复位

        advanceTimeBy(2_000)
        runCurrent()
        assertNull(vm.uiState.value.imageSaveTip)
    }

    @Test
    fun `下载失败与 saver 失败均提示保存失败`() = withVm { vm ->
        vm.onFrameClick(videoFrame())
        coEvery { repository.downloadFrame(any()) } returns null
        vm.saveViewerFrame()
        runCurrent()
        assertEquals("res:${R.string.common_save_failed}", vm.uiState.value.imageSaveTip)
        coVerify(exactly = 0) { saver.saveJpegBytes(any(), any()) }

        coEvery { repository.downloadFrame(any()) } returns jpegBytes
        coEvery { saver.saveJpegBytes(any(), any()) } returns false
        vm.saveViewerFrame()
        runCurrent()
        assertEquals("res:${R.string.common_save_failed}", vm.uiState.value.imageSaveTip)
    }

    @Test
    fun `保存中重入点击仅触发一次下载`() = withVm { vm ->
        vm.onFrameClick(videoFrame())
        val gate = CompletableDeferred<ByteArray>()
        coEvery { repository.downloadFrame(any()) } coAnswers { gate.await() }
        coEvery { saver.saveJpegBytes(any(), any()) } returns true

        vm.saveViewerFrame()
        runCurrent()
        assertTrue(vm.uiState.value.saving)

        vm.saveViewerFrame() // 连点：守卫吞掉（下载仍在飞）
        gate.complete(jpegBytes)
        runCurrent()

        coVerify(exactly = 1) { repository.downloadFrame(any()) }
        coVerify(exactly = 1) { saver.saveJpegBytes(any(), any()) }
        assertFalse(vm.uiState.value.saving)
    }

    @Test
    fun `保存中关闭查看器取消在飞且不落提示`() = withVm { vm ->
        vm.onFrameClick(videoFrame())
        val gate = CompletableDeferred<ByteArray>()
        coEvery { repository.downloadFrame(any()) } coAnswers { gate.await() }
        coEvery { saver.saveJpegBytes(any(), any()) } returns true

        vm.saveViewerFrame()
        runCurrent()
        assertTrue(vm.uiState.value.saving)

        vm.onViewerDismiss()
        runCurrent()
        gate.complete(jpegBytes) // 下载稍后完成也已无意义：保存协程已取消
        runCurrent()

        coVerify(exactly = 0) { saver.saveJpegBytes(any(), any()) } // 未落库
        assertNull(vm.uiState.value.imageSaveTip)
        assertFalse(vm.uiState.value.saving)
        assertNull(vm.uiState.value.viewerFrame)
    }

    @Test
    fun `无查看器或 mediaUrl 空白时保存 no-op`() = withVm { vm ->
        vm.saveViewerFrame() // 未打开查看器
        runCurrent()
        coVerify(exactly = 0) { repository.downloadFrame(any()) }

        vm.onFrameClick(videoFrame().copy(mediaUrl = ""))
        vm.saveViewerFrame()
        runCurrent()
        coVerify(exactly = 0) { repository.downloadFrame(any()) }
        assertFalse(vm.uiState.value.saving)
    }

    @Test
    fun `agent 结果覆盖层开关 + 与帧查看器互斥`() = withVm { vm ->
        vm.onAgentJobClick("t-r1", 42)
        assertEquals("t-r1", vm.uiState.value.viewerAgentJobKey)
        assertEquals(42, vm.uiState.value.agentJobInitialCharOffset)

        // 打开帧查看器 → 清 agent 身份（互斥）
        vm.onFrameClick(ev(1000))
        assertNull(vm.uiState.value.viewerAgentJobKey)
        assertNull(vm.uiState.value.agentJobInitialCharOffset)

        // 反向：打开 agent 覆盖层 → 清帧查看器
        vm.onAgentJobClick("t-r2", null)
        assertNull(vm.uiState.value.viewerFrame)

        vm.onAgentJobViewerDismiss()
        assertNull(vm.uiState.value.viewerAgentJobKey)
        assertNull(vm.uiState.value.agentJobInitialCharOffset)
    }

    @Test
    fun `load 与 retry 清空两个覆盖层，Q11 轮询不清（红线）`() = withVm { vm ->
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns page(listOf(ev(1000)))
        vm.load(1000L, 2000L)
        runCurrent()

        // 两个覆盖层互斥：先帧查看器
        vm.onFrameClick(ev(1000))
        assertTrue(vm.uiState.value.viewerFrame != null)
        assertNull(vm.uiState.value.viewerAgentJobKey)

        // 红线：Q11 轮询（overview 实例变化 → reloadFirstPage）不得清覆盖层
        overviewFlow.value = OverviewDto(timezone = "Asia/Shanghai")
        runCurrent()
        assertTrue("轮询不得关掉用户正在看的帧查看器", vm.uiState.value.viewerFrame != null)

        // 切到 agent 覆盖层（互斥：帧查看器被清）
        vm.onAgentJobClick("t-1000", 3)
        assertNull(vm.uiState.value.viewerFrame)
        assertTrue(vm.uiState.value.viewerAgentJobKey != null)

        overviewFlow.value = OverviewDto(timezone = "Asia/Tokyo")   // 再次实例变化触发轮询
        runCurrent()
        assertTrue("轮询不得关掉用户正在看的 agent 覆盖层", vm.uiState.value.viewerAgentJobKey != null)

        // 显式重取入口才清（两个覆盖层都清，O21）
        coEvery { repository.hotSlotEvents(3000L, 4000L, null) } returns page(emptyList())
        vm.load(3000L, 4000L)
        runCurrent()
        assertNull(vm.uiState.value.viewerFrame)
        assertNull(vm.uiState.value.viewerAgentJobKey)
        assertNull(vm.uiState.value.agentJobInitialCharOffset)
    }

    @Test
    fun `Q11 轮询重取把 rows 截回首页（时间线兜底载荷的前提）`() = withVm { vm ->
        // 首页 + 第二页（loadMore 追加）：第 2 页的行身份为 t-r2
        coEvery { repository.hotSlotEvents(1000L, 2000L, null) } returns
            page(listOf(ev(1000, "r1")), nextCursor = "c1")
        coEvery { repository.hotSlotEvents(1000L, 2000L, "c1") } returns page(listOf(ev(1500, "r2")))
        vm.load(1000L, 2000L)
        runCurrent()
        vm.loadMore()
        runCurrent()
        assertEquals(listOf(1000L, 1500L), vm.events().map { it.tsMs })

        vm.onAgentJobClick("t-r2", null)

        // Q11 轮询（overview 实例变化 → reloadFirstPage → accept(replace=true)）只留首页：
        // 第 2 页事件从 rows 消失 → Screen 侧身份解析会「未命中」，但覆盖层身份不清（红线）
        overviewFlow.value = OverviewDto(timezone = "Asia/Shanghai")
        runCurrent()
        assertEquals(listOf(1000L), vm.events().map { it.tsMs })
        assertEquals("t-r2", vm.uiState.value.viewerAgentJobKey)
    }
}
