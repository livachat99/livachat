package dev.livachat.app.ui.screens.mylife.hotslot

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent
import dev.livachat.app.data.repository.HotSlotPage
import dev.livachat.app.R
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.ImageGallerySaver
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * hotSlotView ViewModel（docs/android_timeline_hotslot.md §4.3）：
 * 首页即渲染 + 触底 cursor 续页；翻页停止 = 页内越过窗口上界或游标耗尽（Repository 透出
 * `reachedWindowEnd`）；跨页按 `event_ref` 去重（`date|offset` 游标遇录制中插入会位移）；
 * Q11：collect `repository.overview`，实例变化（pending 消化）即重取首页替换列表。
 * §9 帧全屏查看器：持事件对象（Q15）+ 保存到相册（下载 → `ImageGallerySaver`，三文案 2s TTL）。
 */
@HiltViewModel
class HotSlotViewModel @Inject constructor(
    private val repository: MylifeRepository,
    private val imageGallerySaver: ImageGallerySaver,
    @ApplicationContext private val appContext: Context,
) : ViewModel() {

    private val _uiState = MutableStateFlow(HotSlotUiState())
    val uiState: StateFlow<HotSlotUiState> = _uiState.asStateFlow()

    private var startMs: Long = 0L
    private var endMs: Long = 0L
    private var nextCursor: String? = null
    private var exhausted = false
    private var loaded = false
    private val seenRefs = HashSet<String>()
    private var loadJob: Job? = null
    private var chaseJob: Job? = null
    private var chasing = false
    private var chasePages = 0
    private var saveJob: Job? = null
    private var tipJob: Job? = null

    /** 跨页去重（§4.3）：event_ref 恒非空（bff 不透出 event_id，event_ref 是唯一稳定锚）。 */
    private fun HotSlotPage.freshEvents(): List<HotSlotEvent> =
        events.filter { it.eventRef?.let(seenRefs::add) ?: true }

    init {
        // Q11：overview 实例变化 → 重取首页替换列表（60s 轮询已在 Repository 共享，零额外网络）
        viewModelScope.launch {
            repository.overview.collect { overview ->
                if (loaded) reloadFirstPage()
                // §11 P1①：路由快照 pending 的单向收敛——队列内已无 pending 分析时，
                // 顶栏「分析中…」与 pending 空态随轮询自动消失（不因后续 overview 回翻为 true）
                if (overview != null && _uiState.value.pending && !repository.hasPendingAnalysis()) {
                    _uiState.update { it.copy(pending = false) }
                }
            }
        }
    }

    /**
     * 首屏加载（Screen LaunchedEffect 触发）；同窗口已加载成功则跳过重拉
     * （此时 pending 也不重置，保收敛结果）。`pending` 为路由快照初值（§11 P1①）。
     */
    fun load(startMs: Long, endMs: Long, pending: Boolean = false) {
        if (startMs <= 0L || endMs < startMs) return
        if (loaded && startMs == this.startMs && endMs == this.endMs &&
            _uiState.value.state is HotSlotState.Data
        ) {
            return
        }
        this.startMs = startMs
        this.endMs = endMs
        _uiState.update { it.copy(pending = pending) }
        clearOverlays()   // 重取入口才清（红线：不得进 reloadFirstPage）
        reloadFirstPage()
    }

    /** 错误重试 / pending 空态刷新入口。 */
    fun retry() {
        if (startMs <= 0L) return
        clearOverlays()   // 重取入口才清（红线：不得进 reloadFirstPage）
        reloadFirstPage()
    }

    private fun reloadFirstPage() {
        loadJob?.cancel()
        chaseJob?.cancel()   // 重取与追击互斥
        chasing = false      // 立即复位：launch 前即被取消的协程不会走 finally（异步取消窗口）
        chasePages = 0       // 追击预算随重取清零（约定 3：否则一次 Q11 重取会吃掉预算、永久退化）
        seenRefs.clear()
        nextCursor = null
        exhausted = false
        // 已有数据时不落 Loading（Q11 轮询重取不闪屏、不丢滚动位置）
        _uiState.update { if (it.state is HotSlotState.Data) it else it.copy(state = HotSlotState.Loading) }
        loadJob = viewModelScope.launch {
            try {
                fetchPages(firstPage = true)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // 刷新失败静默保留已有列表；仅首屏无数据时落错误态
                _uiState.update {
                    if (it.state is HotSlotState.Data) it
                    else it.copy(state = HotSlotState.Error(e.message ?: appContext.getString(R.string.mylife_load_failed)))
                }
            }
        }
    }

    /** 触底续页：互斥 + 终止后 no-op（边界重复触发无副作用；追击期间被 `chasing` 阻塞，约定 4）。 */
    fun loadMore() {
        val data = _uiState.value.state as? HotSlotState.Data ?: return
        if (chasing || data.loadingMore || exhausted || nextCursor == null) return
        _uiState.update { prev ->
            val d = prev.state as? HotSlotState.Data ?: return@update prev
            prev.copy(state = d.copy(loadingMore = true))
        }
        loadJob = viewModelScope.launch {
            try {
                fetchPages(firstPage = false)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // 静默保留已加载页（滚动可再次触发重试；与 Gallery 同口径）
            }
        }
    }

    /**
     * 取数主循环（§4.3）：从当前 `nextCursor` 连续翻页，直到**已见窗口内内容**或**翻页终止**。
     * 首页整页可能全部落在窗口之前（重度日事件 > 单页 500 条且窗口靠后）——此时不能停：
     * 空列表会落 `Empty` 且无列表可滚动，触底续页永不触发，内容被误判为"无内容"。
     * `firstPage` 追击期间保持 Loading（不闪空态），且以替换语义应用页（重取不吃旧列表）。
     */
    private suspend fun fetchPages(firstPage: Boolean) {
        var replace = firstPage
        try {
            while (true) {
                val next = accept(repository.hotSlotEvents(startMs, endMs, nextCursor), replace = replace)
                replace = false
                loaded = true
                if (next == null || hasEvents()) return
                if (firstPage) _uiState.update { it.copy(state = HotSlotState.Loading) }
            }
        } finally {
            // 结束（含取消/失败）后列表态恒回落非加载中，避免续页入口被 loadingMore 卡死
            _uiState.update { prev ->
                (prev.state as? HotSlotState.Data)?.let { prev.copy(state = it.copy(loadingMore = false)) } ?: prev
            }
        }
    }

    private fun hasEvents(): Boolean =
        (_uiState.value.state as? HotSlotState.Data)?.events?.isNotEmpty() == true

    // ---- 锚点追击（docs/android_timeline_topics.md §3.3.3 实现约定，R1）----

    /**
     * 续页直至**事件层**覆盖 anchorTs（约定 1：events 升序，"越过 anchor"即充分，无需等精确行）
     * 或 `exhausted`/预算耗尽。供 EventListViewer 定位锚点行前调用；Screen 以
     * `LaunchedEffect(uiState.state, anchorTs)` 响应式触发（约定 2）——Q11 重取（events 替换）
     * 后自动重入，一次性 suspend 调用会被 Q11 静默取消且无人重试。
     * 预算只计"窗口内已有内容"之后的追击页（G2）；首段追击（fetchPages 跳过窗口前整页）不计。
     */
    fun ensureLoadedUntil(anchorTs: Long) {
        if (anchorTs <= 0L) return
        val data = _uiState.value.state as? HotSlotState.Data ?: return
        if (chasing || data.loadingMore || data.exhausted) return
        if (coversAnchor(anchorTs) || nextCursor == null || chasePages >= MAX_CHASE_PAGES) return
        chasing = true
        _uiState.update { prev ->
            (prev.state as? HotSlotState.Data)?.let { prev.copy(state = it.copy(loadingMore = true)) } ?: prev
        }
        chaseJob = viewModelScope.launch {
            try {
                while (chasePages < MAX_CHASE_PAGES) {
                    chasePages++   // 先计预算：失败也消耗——否则失败翻转 loadingMore 触发 effect 重入，预算永不减
                    val next = accept(repository.hotSlotEvents(startMs, endMs, nextCursor), replace = false)
                    if (next == null || coversAnchor(anchorTs)) return@launch
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // 静默保留已加载页（与 loadMore 同口径）；定位退化由 Screen 兜底
            } finally {
                chasing = false
                _uiState.update { prev ->
                    (prev.state as? HotSlotState.Data)?.let { prev.copy(state = it.copy(loadingMore = false)) } ?: prev
                }
            }
        }
    }

    private fun coversAnchor(anchorTs: Long): Boolean =
        (_uiState.value.state as? HotSlotState.Data)?.events?.any { it.tsMs >= anchorTs } == true

    /**
     * 应用一页：跨页去重 + 终止判据 + 空态判定；返回下一页游标（null = 翻页终止）。
     * `replace`：重取首页时丢弃旧列表（否则 `seen` 已清空会把同一批事件重复追加）。
     */
    private fun accept(page: HotSlotPage, replace: Boolean): String? {
        val fresh = page.freshEvents()
        nextCursor = page.nextCursor
        exhausted = page.reachedWindowEnd || page.nextCursor == null
        _uiState.update { prev ->
            val existing = if (replace) emptyList() else (prev.state as? HotSlotState.Data)?.events.orEmpty()
            val merged = existing + fresh
            prev.copy(
                state = if (merged.isEmpty()) {
                    HotSlotState.Empty
                } else {
                    HotSlotState.Data(merged, loadingMore = true, exhausted = exhausted)
                },
            )
        }
        return if (exhausted) null else nextCursor
    }

    // ---- 帧全屏查看器（§9.3）----

    /** 打开查看器：持事件对象（Q15）+ 清旧提示（重开不显示陈语文案）。 */
    fun onFrameClick(frame: HotSlotEvent) {
        tipJob?.cancel()
        tipJob = null
        // 与 agent 结果覆盖层互斥（同一时刻只一个覆盖层）
        _uiState.update {
            it.copy(
                viewerFrame = frame,
                imageSaveTip = null,
                viewerAgentJobKey = null,
                agentJobInitialCharOffset = null,
            )
        }
    }

    /**
     * agent job 结果行点击（§12.3.3）：只存**行身份**与落位偏移，正文由 Screen 侧按 key 现取
     * （重取/分析重写后不陈旧）；与帧查看器互斥打开。
     */
    fun onAgentJobClick(rowKey: String, charOffset: Int?) {
        saveJob?.cancel()
        saveJob = null
        tipJob?.cancel()
        tipJob = null
        _uiState.update {
            it.copy(
                viewerAgentJobKey = rowKey,
                agentJobInitialCharOffset = charOffset,
                viewerFrame = null,
                saving = false,
                imageSaveTip = null,
            )
        }
    }

    /** 关闭 agent 结果覆盖层（对齐 [onViewerDismiss] 的清态口径）。 */
    fun onAgentJobViewerDismiss() {
        _uiState.update {
            it.copy(viewerAgentJobKey = null, agentJobInitialCharOffset = null)
        }
    }

    /** 关闭查看器：取消在飞保存与提示（避免"看不见的保存"与陈旧提示）。 */
    fun onViewerDismiss() {
        saveJob?.cancel()
        saveJob = null
        tipJob?.cancel()
        tipJob = null
        _uiState.update {
            it.copy(viewerFrame = null, saving = false, imageSaveTip = null)
        }
    }

    /**
     * 关闭两个覆盖层（重取入口调用）。
     * **⚠️ 只允许在 [load]/[retry] 公开入口体内调用，不得加进 [reloadFirstPage]**——Q11 轮询
     * （60s overview 驱动，init 里直接调 reloadFirstPage）若在此清空，会把用户正在看的覆盖层静默关掉。
     */
    private fun clearOverlays() {
        saveJob?.cancel()
        saveJob = null
        tipJob?.cancel()
        tipJob = null
        _uiState.update {
            it.copy(
                viewerFrame = null,
                saving = false,
                imageSaveTip = null,
                viewerAgentJobKey = null,
                agentJobInitialCharOffset = null,
            )
        }
    }

    /**
     * 保存当前帧到相册：**以 VM 的 `viewerFrame` 为准**（无参数——单图无翻页，
     * 权限回调期间无法切帧，不存在 gallery"回调晚于翻页"竞态）。进行中忽略再次点击。
     */
    fun saveViewerFrame() {
        if (_uiState.value.saving) return
        val url = _uiState.value.viewerFrame?.mediaUrl?.takeIf { it.isNotBlank() } ?: return
        _uiState.update { it.copy(saving = true) }
        saveJob = viewModelScope.launch {
            try {
                val bytes = repository.downloadFrame(url)
                // runCatching 只包 saver：downloadFrame 取消需照常上抛
                val ok = bytes != null &&
                    runCatching { imageGallerySaver.saveJpegBytes(bytes) }.getOrDefault(false)
                showTipWithTimeout(
                    if (ok) appContext.getString(R.string.common_saved_to_gallery)
                    else appContext.getString(R.string.common_save_failed),
                )
            } finally {
                // 覆盖取消路径，防 flag 卡死、保存入口永久静默失效
                _uiState.update { it.copy(saving = false) }
            }
        }
    }

    /** 权限被拒（API ≤ 28）：只提示，可再次点击重试。 */
    fun onSavePermissionDenied() {
        showTipWithTimeout(appContext.getString(R.string.common_save_failed_storage))
    }

    /** 统一提示 TTL：先取消上一个定时器再展示 2s（防顺序保存串台）。 */
    private fun showTipWithTimeout(text: String) {
        tipJob?.cancel()
        tipJob = viewModelScope.launch {
            _uiState.update { it.copy(imageSaveTip = text) }
            delay(TIP_TTL_MS)
            _uiState.update { it.copy(imageSaveTip = null) }
        }
    }

    private companion object {
        /** 锚点追击页数硬上限（只计首段落地后的追击页；超限按 Q5 静默退化，docs/android_timeline_topics.md §3.3.3）。 */
        const val MAX_CHASE_PAGES = 5

        const val TIP_TTL_MS = 2_000L
    }
}
