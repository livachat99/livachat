package dev.livachat.app.ui.screens.mylife.audiogallery

import androidx.lifecycle.ViewModelStore
import dev.livachat.app.testStringContext
import dev.livachat.app.data.remote.mylife.dto.AudioSegment
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.app.data.repository.AudioPage
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.AudioPlayer
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * AudioGallery VM 状态机（docs/android_timeline_audio_gallery.md §6-2）：分页去重 / toggle 转发 /
 * scrub 抑制回写 / 失败静默 / 离屏停播 / pending 单向收敛。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AudioGalleryViewModelTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<MylifeRepository>(relaxed = true)
    private val player = mockk<AudioPlayer>(relaxed = true)

    private val overviewFlow = MutableStateFlow<OverviewDto?>(null)
    private val playerFlow = MutableStateFlow(AudioPlayer.State())

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        every { repository.overview } returns overviewFlow
        every { player.state } returns playerFlow
    }

    private val stores = mutableListOf<ViewModelStore>()

    @After
    fun tearDown() {
        // clear 触发 onCleared（取消 viewModelScope + 停播），避免协程泄漏串扰同 JVM 内后续测试类
        stores.forEach { it.clear() }
        stores.clear()
        Dispatchers.resetMain()
    }

    private fun createViewModel(): AudioGalleryViewModel {
        val vm = AudioGalleryViewModel(repository, player, testStringContext())
        ViewModelStore().apply {
            put("vm-${stores.size}", vm)
            stores += this
        }
        return vm
    }

    private fun segment(ref: String, ts: Long = 1_000L) = AudioSegment(
        eventRef = ref, tsMs = ts, mediaUrl = "https://h/$ref.ogg",
        transcript = null, summary = null, durationMs = 60_000,
    )

    private fun page(refs: List<String>, total: Int = refs.size, next: String? = null) = AudioPage(
        segments = refs.map { segment(it, ts = 1_000L + refs.indexOf(it)) },
        total = total,
        nextCursor = next,
    )

    private fun data(vm: AudioGalleryViewModel): AudioGalleryState.Data =
        vm.uiState.value.state as AudioGalleryState.Data

    @Test
    fun `首屏加载成功落 Data 且顶栏段数切为 total`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a", "b"))
        val vm = createViewModel()
        vm.load("2026-09-22", countSnapshot = 9)
        advanceUntilIdle()

        assertEquals(listOf("a", "b"), data(vm).segments.map { it.eventRef })
        assertEquals(2, vm.uiState.value.headerCount)   // 快照 9 → total 2
    }

    @Test
    fun `首屏快照在加载完成前即生效`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a"))
        val vm = createViewModel()
        vm.load("2026-09-22", countSnapshot = 14)
        assertEquals(14, vm.uiState.value.headerCount)   // 未 advance：仍为快照
        advanceUntilIdle()
        assertEquals(1, vm.uiState.value.headerCount)
    }

    @Test
    fun `分页追加按 eventRef 去重`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns
            page(listOf("a", "b"), total = 3, next = "2026-09-22|2")
        coEvery { repository.audioSegments("2026-09-22", "2026-09-22|2", any()) } returns
            page(listOf("b", "c"), total = 3, next = null)
        val vm = createViewModel()
        vm.load("2026-09-22", 3)
        advanceUntilIdle()
        vm.loadMore()
        advanceUntilIdle()

        assertEquals(listOf("a", "b", "c"), data(vm).segments.map { it.eventRef })
        assertFalse(data(vm).loadingMore)
        assertNull(data(vm).nextCursor)
    }

    @Test
    fun `loadMore 失败静默保留已加载页`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns
            page(listOf("a"), total = 2, next = "2026-09-22|1")
        coEvery { repository.audioSegments("2026-09-22", "2026-09-22|1", any()) } throws
            RuntimeException("boom")
        val vm = createViewModel()
        vm.load("2026-09-22", 2)
        advanceUntilIdle()
        vm.loadMore()
        advanceUntilIdle()

        assertEquals(listOf("a"), data(vm).segments.map { it.eventRef })
        assertFalse(data(vm).loadingMore)
        assertEquals("2026-09-22|1", data(vm).nextCursor)   // 游标保留 → 滚动可重试
    }

    @Test
    fun `首屏失败落 Error 并可 retry`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } throws RuntimeException("网断了")
        val vm = createViewModel()
        vm.load("2026-09-22", 1)
        advanceUntilIdle()
        assertTrue(vm.uiState.value.state is AudioGalleryState.Error)

        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a"))
        vm.retry()
        advanceUntilIdle()
        assertEquals(listOf("a"), data(vm).segments.map { it.eventRef })
    }

    @Test
    fun `空列表落 Empty`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns
            AudioPage(emptyList(), total = 0, nextCursor = null)
        val vm = createViewModel()
        vm.load("2026-09-22", 0)
        advanceUntilIdle()
        assertTrue(vm.uiState.value.state is AudioGalleryState.Empty)
    }

    @Test
    fun `toggle 转发 eventRef 与 mediaUrl`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a"))
        val vm = createViewModel()
        vm.load("2026-09-22", 1)
        advanceUntilIdle()
        val seg = data(vm).segments.first()

        vm.onToggle(seg)
        verify { player.toggle("a", "https://h/a.ogg") }
    }

    @Test
    fun `scrub 期间不回写播放位置、松手下发 seek`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a"))
        val vm = createViewModel()
        vm.load("2026-09-22", 1)
        advanceUntilIdle()
        val seg = data(vm).segments.first()

        vm.onScrub(seg, 23_000)
        assertEquals(23_000L, vm.uiState.value.scrubMs)
        assertEquals("a", vm.uiState.value.scrubRef)   // scrub 归属到具体段
        verify(exactly = 0) { player.seekTo(any()) }   // 拖动中不下发

        vm.onScrubEnd(seg, 23_000)
        assertNull(vm.uiState.value.scrubMs)
        assertNull(vm.uiState.value.scrubRef)
        verify(exactly = 1) { player.seekTo(23_000L) }
    }

    @Test
    fun `拖动非播放段 = 先切段再定位（2026-09-24 修订）`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a", "b"))
        val vm = createViewModel()
        vm.load("2026-09-22", 2)
        advanceUntilIdle()
        // 当前在播 a；拖 b 的轨道 → 必须切到 b 再从该位置播（不得把位置下发给 a）
        playerFlow.value = AudioPlayer.State(key = "a", isPlaying = true)
        advanceUntilIdle()
        val b = data(vm).segments.first { it.eventRef == "b" }

        vm.onScrubEnd(b, 30_000)
        verify(exactly = 1) { player.toggle("b", "https://h/b.ogg") }
        verify(exactly = 1) { player.seekTo(30_000L) }
    }

    @Test
    fun `拖动当前播放段 = 只 seek 不切段（不改播放状态）`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a"))
        val vm = createViewModel()
        vm.load("2026-09-22", 1)
        advanceUntilIdle()
        playerFlow.value = AudioPlayer.State(key = "a", isPlaying = true)
        advanceUntilIdle()
        val a = data(vm).segments.first()

        vm.onScrubEnd(a, 12_000)
        verify(exactly = 0) { player.toggle(any(), any()) }
        verify(exactly = 1) { player.seekTo(12_000L) }
    }

    @Test
    fun `播放位置回写不影响 scrub 值（E7 拖动优先）`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a"))
        val vm = createViewModel()
        vm.load("2026-09-22", 1)
        advanceUntilIdle()
        val seg = data(vm).segments.first()

        vm.onScrub(seg, 10_000)
        playerFlow.value = AudioPlayer.State(key = "a", isPlaying = true, positionMs = 55_000)
        advanceUntilIdle()
        assertEquals(10_000L, vm.uiState.value.scrubMs)          // 仍是本地 scrub 值
        assertEquals(55_000L, vm.uiState.value.player.positionMs) // 播放器值照常入 state
    }

    @Test
    fun `播放中不重取首页、停播后恢复自愈重取`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a", "b"))
        val vm = createViewModel()
        vm.load("2026-09-22", 2)
        advanceUntilIdle()

        // 进入播放态（全局单播：key 非空）
        playerFlow.value = AudioPlayer.State(key = "a", isPlaying = true)
        advanceUntilIdle()
        overviewFlow.value = OverviewDto(
            timezone = "Asia/Shanghai",
            days = listOf(OverviewDayDto(date = "2026-09-22")),
        )
        advanceUntilIdle()
        // 播放中不重取：重取会替换为首页，播放段若在页 ≥2 会从列表消失（有声无控件可停）
        coVerify(exactly = 1) { repository.audioSegments("2026-09-22", null, any()) }
        assertEquals(listOf("a", "b"), data(vm).segments.map { it.eventRef })

        // 停播（key 复位）→ 恢复 overview 驱动的自愈重取
        playerFlow.value = AudioPlayer.State()
        advanceUntilIdle()
        overviewFlow.value = OverviewDto(
            timezone = "Asia/Shanghai",
            days = listOf(OverviewDayDto(date = "2026-09-21"), OverviewDayDto(date = "2026-09-22")),
        )
        advanceUntilIdle()
        coVerify(exactly = 2) { repository.audioSegments("2026-09-22", null, any()) }
    }

    @Test
    fun `onCleared 停播`() = runTest(dispatcher) {
        createViewModel()
        verify(exactly = 0) { player.stop() }

        stores.last().clear()   // clear 触发 onCleared

        verify { player.stop() }
    }

    @Test
    fun `pending 随 overview 轮询单向收敛（不因回翻为 true 复活）`() = runTest(dispatcher) {
        coEvery { repository.audioSegments("2026-09-22", null, any()) } returns page(listOf("a"))
        every { repository.hasPendingAnalysis() } returns false
        val vm = createViewModel()
        vm.load("2026-09-22", 1, pending = true)
        advanceUntilIdle()
        // 首屏加载完成但 overview 尚未 emit（流初值 null）→ pending 保持路由快照
        assertTrue(vm.uiState.value.pending)

        overviewFlow.value = OverviewDto(
            timezone = "Asia/Shanghai",
            days = listOf(OverviewDayDto(date = "2026-09-22", hasPending = false)),
        )
        advanceUntilIdle()
        assertFalse(vm.uiState.value.pending)   // 队列已无 pending → 收敛

        overviewFlow.value = OverviewDto(
            timezone = "Asia/Shanghai",
            days = listOf(OverviewDayDto(date = "2026-09-22", hasPending = true)),
        )
        advanceUntilIdle()
        assertFalse(vm.uiState.value.pending)   // 单向：不复活
    }
}
