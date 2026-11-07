package dev.livachat.app.ui.screens.mylife.gallery

import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.viewModelScope
import dev.livachat.app.testStringContext
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.FrameDto
import dev.livachat.app.data.remote.mylife.dto.RemoveResponseDto
import dev.livachat.app.data.remote.mylife.dto.SkippedRefDto
import dev.livachat.app.data.repository.FramePage
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.ImageGallerySaver
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.verify
import java.io.InterruptedIOException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class FrameGalleryViewModelTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<MylifeRepository>(relaxed = true)
    private val saver = mockk<ImageGallerySaver>(relaxed = true)
    private val createdVms = mutableListOf<FrameGalleryViewModel>()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        createdVms.forEach { it.viewModelScope.cancel() }
        createdVms.clear()
        Dispatchers.resetMain()
    }

    private fun frame(id: String, ts: Long = 1000L) = FrameDto(
        tsMs = ts,
        mediaUrl = "https://example.com/$id.jpg",
        frameId = id,
    )

    private fun withVm(block: suspend kotlinx.coroutines.test.TestScope.(FrameGalleryViewModel) -> Unit) = runTest {
        val vm = FrameGalleryViewModel(repository, saver, testStringContext())
        createdVms.add(vm)
        try {
            block(vm)
        } finally {
            vm.viewModelScope.cancel()
        }
    }

    @Test
    fun `首屏加载成功进入 Data`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1"), frame("f2", 2000L)), total = 2, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        val data = vm.uiState.value.state as FrameGalleryState.Data
        assertEquals(listOf("f1", "f2"), data.frames.map { it.frameId })
        assertEquals(2, data.total)
        assertNull(data.nextCursor)
        assertFalse(data.loadingMore)
    }

    @Test
    fun `首屏空列表进入 Empty`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(emptyList(), total = 0, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        assertEquals(FrameGalleryState.Empty, vm.uiState.value.state)
    }

    @Test
    fun `首屏失败进入 Error 重试可恢复`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } throws RuntimeException("boom")
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        assertEquals("boom", (vm.uiState.value.state as FrameGalleryState.Error).message)

        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1")), total = 1, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        assertTrue(vm.uiState.value.state is FrameGalleryState.Data)
    }

    @Test
    fun `同范围已加载成功不重拉`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1")), total = 1, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        coVerify(exactly = 1) { repository.frames("2026-08-21", "2026-08-21") }
    }

    @Test
    fun `loadMore 追加下一页`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1", 1000L)), total = 3, nextCursor = "c1")
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        coEvery { repository.frames("2026-08-21", "2026-08-21", cursor = "c1") } returns
            FramePage(listOf(frame("f2", 2000L), frame("f3", 3000L)), total = 3, nextCursor = null)
        vm.loadMore()
        runCurrent()
        val data = vm.uiState.value.state as FrameGalleryState.Data
        assertEquals(listOf("f1", "f2", "f3"), data.frames.map { it.frameId })
        assertNull(data.nextCursor)
        assertFalse(data.loadingMore)
    }

    @Test
    fun `append 按 frameId 去重（A1 游标漂移重复）`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1", 1000L), frame("f2", 2000L)), total = 3, nextCursor = "c1")
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        // 分析中插入新帧 → 下一页与已加载页重叠（f2 重复出现）
        coEvery { repository.frames("2026-08-21", "2026-08-21", cursor = "c1") } returns
            FramePage(listOf(frame("f2", 2000L), frame("f3", 3000L)), total = 4, nextCursor = null)
        vm.loadMore()
        runCurrent()
        val data = vm.uiState.value.state as FrameGalleryState.Data
        assertEquals(listOf("f1", "f2", "f3"), data.frames.map { it.frameId })
        assertEquals(4, data.total) // total 透传服务端最新计数（E8）
    }

    @Test
    fun `loadMore 进行中重复调用被互斥`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1")), total = 2, nextCursor = "c1")
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        coEvery { repository.frames("2026-08-21", "2026-08-21", cursor = "c1") } returns
            FramePage(listOf(frame("f2", 2000L)), total = 2, nextCursor = null)
        vm.loadMore()
        vm.loadMore()
        runCurrent()
        coVerify(exactly = 1) { repository.frames("2026-08-21", "2026-08-21", cursor = "c1") }
        assertEquals(2, (vm.uiState.value.state as FrameGalleryState.Data).frames.size)
    }

    @Test
    fun `nextCursor 耗尽后 loadMore 不发请求`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1")), total = 1, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        vm.loadMore()
        runCurrent()
        coVerify(exactly = 1) { repository.frames("2026-08-21", "2026-08-21", any(), any()) }
    }

    @Test
    fun `loadMore 失败静默保留已有页与游标`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1")), total = 2, nextCursor = "c1")
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        coEvery { repository.frames("2026-08-21", "2026-08-21", cursor = "c1") } throws
            RuntimeException("boom")
        vm.loadMore()
        runCurrent()
        val data = vm.uiState.value.state as FrameGalleryState.Data
        assertEquals(1, data.frames.size)
        assertFalse(data.loadingMore)
        assertEquals("c1", data.nextCursor) // 游标保留 → 滚动可再次触发重试
    }

    @Test
    fun `viewer 打开关闭只动 viewerIndex`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1"), frame("f2", 2000L)), total = 2, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
        vm.onFrameClick(1)
        assertEquals(1, vm.uiState.value.viewerIndex)
        vm.onViewerDismiss()
        assertNull(vm.uiState.value.viewerIndex)
        assertEquals(2, (vm.uiState.value.state as FrameGalleryState.Data).frames.size)
    }

    @Test
    fun `空白范围 load 被忽略`() = withVm { vm ->
        vm.load("", "2026-08-21")
        runCurrent()
        coVerify(exactly = 0) { repository.frames(any(), any(), any(), any()) }
        assertEquals(FrameGalleryState.Loading, vm.uiState.value.state)
    }

    // ---- 保存到相册（§3.5；对齐 conversation 二期口径）----

    private val jpegBytes = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0x01)

    /** 载入两张帧并让 repository 命中（保存用例公共前置）。 */
    private suspend fun kotlinx.coroutines.test.TestScope.loadTwoFrames(vm: FrameGalleryViewModel) {
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frame("f1", 1000L), frame("f2", 2000L)), total = 2, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
    }

    @Test
    fun `保存成功展示提示 2s 后清除`() = withVm { vm ->
        loadTwoFrames(vm)
        coEvery { repository.downloadFrame("https://example.com/f1.jpg") } returns jpegBytes
        coEvery { saver.saveJpegBytes(any(), any()) } returns true

        vm.saveFrameById("f1")
        runCurrent()
        assertEquals("res:${R.string.common_saved_to_gallery}", vm.uiState.value.imageSaveTip)
        assertNull(vm.uiState.value.savingFrameId) // finally 复位

        advanceTimeBy(2_000)
        runCurrent()
        assertNull(vm.uiState.value.imageSaveTip)
    }

    @Test
    fun `保存中状态置位且连点被重入守卫吞掉`() = withVm { vm ->
        loadTwoFrames(vm)
        val gate = CompletableDeferred<ByteArray>()
        coEvery { repository.downloadFrame(any()) } coAnswers { gate.await() }
        coEvery { saver.saveJpegBytes(any(), any()) } returns true

        vm.saveFrameById("f1")
        runCurrent()
        assertNotNull(vm.uiState.value.savingFrameId)

        vm.saveFrameById("f1") // 连点：守卫吞掉（下载仍在飞）
        gate.complete(jpegBytes)
        runCurrent()

        coVerify(exactly = 1) { repository.downloadFrame(any()) }
        coVerify(exactly = 1) { saver.saveJpegBytes(any(), any()) }
        assertNull(vm.uiState.value.savingFrameId)
    }

    @Test
    fun `下载失败展示失败提示且不落库`() = withVm { vm ->
        loadTwoFrames(vm)
        coEvery { repository.downloadFrame(any()) } returns null

        vm.saveFrameById("f1")
        runCurrent()
        assertEquals("res:${R.string.common_save_failed}", vm.uiState.value.imageSaveTip)
        coVerify(exactly = 0) { saver.saveJpegBytes(any(), any()) }
    }

    @Test
    fun `saver 失败展示失败提示`() = withVm { vm ->
        loadTwoFrames(vm)
        coEvery { repository.downloadFrame(any()) } returns jpegBytes
        coEvery { saver.saveJpegBytes(any(), any()) } returns false

        vm.saveFrameById("f1")
        runCurrent()
        assertEquals("res:${R.string.common_save_failed}", vm.uiState.value.imageSaveTip)
    }

    @Test
    fun `权限拒绝提示且 2s 清除`() = withVm { vm ->
        vm.onSavePermissionDenied()
        runCurrent()
        assertEquals("res:${R.string.common_save_failed_storage}", vm.uiState.value.imageSaveTip)

        advanceTimeBy(2_000)
        runCurrent()
        assertNull(vm.uiState.value.imageSaveTip)
    }

    @Test
    fun `连续两次保存第二次提示完整存活 2s`() = withVm { vm ->
        loadTwoFrames(vm)
        coEvery { repository.downloadFrame(any()) } returns jpegBytes
        coEvery { saver.saveJpegBytes(any(), any()) } returns true

        vm.saveFrameById("f1")
        runCurrent()
        advanceTimeBy(1_000)
        runCurrent()
        vm.saveFrameById("f2")
        runCurrent()

        // 距第一次保存已 2s：若旧定时器未取消，会把第二次的提示提前清掉（二期同款用例）
        advanceTimeBy(1_000)
        runCurrent()
        assertEquals("res:${R.string.common_saved_to_gallery}", vm.uiState.value.imageSaveTip)

        advanceTimeBy(1_000)
        runCurrent()
        assertNull(vm.uiState.value.imageSaveTip)
    }

    @Test
    fun `未知 frameId 不触发保存`() = withVm { vm ->
        loadTwoFrames(vm)
        vm.saveFrameById("nope")
        runCurrent()
        coVerify(exactly = 0) { repository.downloadFrame(any()) }
        assertNull(vm.uiState.value.imageSaveTip)
    }

    @Test
    fun `关闭查看器取消在飞保存并清提示`() = withVm { vm ->
        loadTwoFrames(vm)
        val gate = CompletableDeferred<ByteArray>()
        coEvery { repository.downloadFrame(any()) } coAnswers { gate.await() }
        coEvery { saver.saveJpegBytes(any(), any()) } returns true

        vm.onFrameClick(0)
        vm.saveFrameById("f1")
        runCurrent()
        assertNotNull(vm.uiState.value.savingFrameId)

        vm.onViewerDismiss()
        runCurrent()
        gate.complete(jpegBytes) // 下载稍后完成也已无意义：保存协程已取消
        runCurrent()

        coVerify(exactly = 0) { saver.saveJpegBytes(any(), any()) } // 未落库
        assertNull(vm.uiState.value.imageSaveTip)
        assertNull(vm.uiState.value.savingFrameId)
        assertNull(vm.uiState.value.viewerIndex)
    }

    @Test
    fun `重开查看器清旧提示`() = withVm { vm ->
        loadTwoFrames(vm)
        vm.onSavePermissionDenied()
        runCurrent()
        assertNotNull(vm.uiState.value.imageSaveTip)

        vm.onFrameClick(0)
        assertNull(vm.uiState.value.imageSaveTip)
    }

    // ---- 编辑模式与删除（§9.4.2；Q9.1–Q9.9 拍板口径）----

    private fun frameWithRef(id: String, ts: Long = 1000L, ref: String? = "rs:video:$id") = FrameDto(
        tsMs = ts,
        mediaUrl = "https://example.com/$id.jpg",
        frameId = id,
        eventRef = ref,
    )

    /** 载入三张带 eventRef 的帧（编辑模式用例公共前置）。 */
    private suspend fun kotlinx.coroutines.test.TestScope.loadEditableFrames(vm: FrameGalleryViewModel) {
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(
                listOf(frameWithRef("f1", 1000L), frameWithRef("f2", 2000L), frameWithRef("f3", 3000L)),
                total = 3,
                nextCursor = null,
            )
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()
    }

    private fun kotlinx.coroutines.test.TestScope.selectFrames(
        vm: FrameGalleryViewModel,
        vararg ids: String,
    ) {
        vm.enterEditMode()
        val frames = (vm.uiState.value.state as FrameGalleryState.Data).frames
        ids.forEach { id -> vm.toggleSelect(frames.first { it.frameId == id }) }
        runCurrent()
    }

    @Test
    fun `编辑模式进入退出清空选择`() = withVm { vm ->
        loadEditableFrames(vm)
        selectFrames(vm, "f1")
        assertTrue(vm.uiState.value.editMode)
        assertEquals(setOf("f1"), vm.uiState.value.selectedFrameIds)

        vm.exitEditMode()
        assertFalse(vm.uiState.value.editMode)
        assertTrue(vm.uiState.value.selectedFrameIds.isEmpty())
    }

    @Test
    fun `非编辑模式点选择框 no-op`() = withVm { vm ->
        loadEditableFrames(vm)
        val frames = (vm.uiState.value.state as FrameGalleryState.Data).frames
        vm.toggleSelect(frames[0])
        assertTrue(vm.uiState.value.selectedFrameIds.isEmpty())
    }

    @Test
    fun `无 eventRef 的帧不可选（防御）`() = withVm { vm ->
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frameWithRef("f1", ref = null)), total = 1, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()

        vm.enterEditMode()
        vm.toggleSelect((vm.uiState.value.state as FrameGalleryState.Data).frames[0])
        assertTrue(vm.uiState.value.selectedFrameIds.isEmpty())
    }

    @Test
    fun `选择上限 50 第 51 张提示不选中 取消后可再选`() = withVm { vm ->
        val frames = (1..51).map { frameWithRef("f$it", it * 1000L) }
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(frames, total = 51, nextCursor = null)
        vm.load("2026-08-21", "2026-08-21")
        runCurrent()

        vm.enterEditMode()
        frames.take(50).forEach { vm.toggleSelect(it) }
        assertEquals(50, vm.uiState.value.selectedFrameIds.size)

        vm.toggleSelect(frames[50])
        runCurrent()
        assertEquals(50, vm.uiState.value.selectedFrameIds.size) // 第 51 张不选中（Q9.6）
        assertEquals("res:${R.string.mylife_max_selection}", vm.uiState.value.deleteTip)

        vm.toggleSelect(frames[0]) // 取消一张后腾出名额
        vm.toggleSelect(frames[50])
        assertTrue(frames[50].frameId in vm.uiState.value.selectedFrameIds)
    }

    @Test
    fun `requestDelete 空选择 no-op 有选择开弹窗 dismiss 关闭`() = withVm { vm ->
        loadEditableFrames(vm)
        vm.requestDelete()
        assertFalse(vm.uiState.value.deleteConfirm) // 非编辑模式 + 空选择 → no-op

        selectFrames(vm, "f1")
        vm.requestDelete()
        assertTrue(vm.uiState.value.deleteConfirm)

        vm.dismissDeleteConfirm()
        assertFalse(vm.uiState.value.deleteConfirm)
    }

    @Test
    fun `删除成功剔除重载退出编辑并标记延迟刷新`() = withVm { vm ->
        loadEditableFrames(vm)
        selectFrames(vm, "f1", "f2")
        coEvery { repository.removeEvents(listOf("rs:video:f1", "rs:video:f2")) } returns
            RemoveResponseDto(removed = listOf("rs:video:f1", "rs:video:f2"))
        // 重载后的首页（剩余 f3）
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frameWithRef("f3", 3000L)), total = 1, nextCursor = null)

        vm.requestDelete()
        vm.deleteSelected()
        runCurrent()

        assertEquals("resq:${R.plurals.mylife_photos_deleted}", vm.uiState.value.deleteTip)
        assertFalse(vm.uiState.value.editMode)
        assertTrue(vm.uiState.value.selectedFrameIds.isEmpty())
        assertFalse(vm.uiState.value.deleting)
        val data = vm.uiState.value.state as FrameGalleryState.Data
        assertEquals(listOf("f3"), data.frames.map { it.frameId })
        assertEquals(1, data.total)
        verify { repository.markPendingRemovalRefresh() } // Q9.3：不直接刷新 overview
        coVerify(exactly = 1) { repository.removeEvents(listOf("rs:video:f1", "rs:video:f2")) }
    }

    @Test
    fun `部分成功 not_found 静默其余计失败`() = withVm { vm ->
        loadEditableFrames(vm)
        selectFrames(vm, "f1", "f2", "f3")
        coEvery { repository.removeEvents(any()) } returns RemoveResponseDto(
            removed = listOf("rs:video:f1"),
            skipped = listOf(
                SkippedRefDto(eventRef = "rs:video:f2", reason = "not_found"),
                SkippedRefDto(eventRef = "rs:video:f3", reason = "not_video"),
            ),
        )
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(emptyList(), total = 0, nextCursor = null)

        vm.requestDelete()
        vm.deleteSelected()
        runCurrent()

        // gone = removed + not_found = 2；fail = not_video = 1（Q9.4）
        assertEquals("res:${R.string.mylife_photos_deleted_partial}", vm.uiState.value.deleteTip)
        assertEquals(FrameGalleryState.Empty, vm.uiState.value.state)
    }

    @Test
    fun `全部 not_found 视作已删`() = withVm { vm ->
        loadEditableFrames(vm)
        selectFrames(vm, "f1")
        coEvery { repository.removeEvents(any()) } returns RemoveResponseDto(
            skipped = listOf(SkippedRefDto(eventRef = "rs:video:f1", reason = "not_found")),
        )
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(listOf(frameWithRef("f2"), frameWithRef("f3")), total = 2, nextCursor = null)

        vm.requestDelete()
        vm.deleteSelected()
        runCurrent()

        assertEquals("resq:${R.plurals.mylife_photos_deleted}", vm.uiState.value.deleteTip)
        verify { repository.markPendingRemovalRefresh() }
    }

    @Test
    fun `删除超时提示并保留选择与编辑模式`() = withVm { vm ->
        loadEditableFrames(vm)
        selectFrames(vm, "f1", "f2")
        coEvery { repository.removeEvents(any()) } throws InterruptedIOException("timeout")

        vm.requestDelete()
        vm.deleteSelected()
        runCurrent()

        assertEquals("res:${R.string.mylife_delete_timeout}", vm.uiState.value.deleteTip)
        assertTrue(vm.uiState.value.editMode)
        assertEquals(setOf("f1", "f2"), vm.uiState.value.selectedFrameIds)
        assertFalse(vm.uiState.value.deleting)
        verify(exactly = 0) { repository.markPendingRemovalRefresh() }
    }

    @Test
    fun `删除其它失败提示删除失败`() = withVm { vm ->
        loadEditableFrames(vm)
        selectFrames(vm, "f1")
        coEvery { repository.removeEvents(any()) } throws RuntimeException("502")

        vm.requestDelete()
        vm.deleteSelected()
        runCurrent()

        assertEquals("res:${R.string.mylife_delete_failed}", vm.uiState.value.deleteTip)
        assertTrue(vm.uiState.value.editMode)
    }

    @Test
    fun `删除中交互全拦截 完成后恢复`() = withVm { vm ->
        loadEditableFrames(vm)
        selectFrames(vm, "f1")
        val gate = CompletableDeferred<RemoveResponseDto>()
        coEvery { repository.removeEvents(any()) } coAnswers { gate.await() }
        coEvery { repository.frames("2026-08-21", "2026-08-21") } returns
            FramePage(emptyList(), total = 0, nextCursor = null)

        vm.requestDelete()
        vm.deleteSelected()
        runCurrent()
        assertTrue(vm.uiState.value.deleting)

        val frames = (vm.uiState.value.state as FrameGalleryState.Data).frames
        vm.toggleSelect(frames[1]) // 拦截：勾选
        vm.exitEditMode()          // 拦截：退出编辑
        vm.requestDelete()         // 拦截：重开弹窗
        vm.onFrameClick(0)         // 拦截：开查看器（Q9.5——否则重载后页索引错位）
        assertEquals(setOf("f1"), vm.uiState.value.selectedFrameIds)
        assertTrue(vm.uiState.value.editMode)
        assertFalse(vm.uiState.value.deleteConfirm)
        assertNull(vm.uiState.value.viewerIndex)

        gate.complete(RemoveResponseDto(removed = listOf("rs:video:f1")))
        runCurrent()
        assertFalse(vm.uiState.value.deleting)
        assertFalse(vm.uiState.value.editMode)
        assertEquals("resq:${R.plurals.mylife_photos_deleted}", vm.uiState.value.deleteTip)
    }

    @Test
    fun `onCleared 触发 flushPendingRemovalRefresh`() = withVm { vm ->
        val store = ViewModelStore()
        store.put("gallery", vm)
        store.clear() // → ViewModel.onCleared
        verify { repository.flushPendingRemovalRefresh() }
    }
}
