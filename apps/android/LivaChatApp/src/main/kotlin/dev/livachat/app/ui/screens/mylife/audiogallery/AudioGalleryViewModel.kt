package dev.livachat.app.ui.screens.mylife.audiogallery

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.data.remote.mylife.dto.AudioSegment
import dev.livachat.app.data.repository.AudioPage
import dev.livachat.app.R
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.AudioPlayer
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * AudioGallery 屏 ViewModel（docs/android_timeline_audio_gallery.md §4.7）：
 * 首屏 + cursor 分页（页 40）、按 `eventRef` 去重追加（分析中插入新段 → OFFSET 游标页会重复）、
 * 失败静默保留已加载页、播放/暂停/seek 转发给 [AudioPlayer]（全局单播，VM 不持有播放状态）、
 * 离屏 `onCleared` → `player.stop()`。
 */
@HiltViewModel
class AudioGalleryViewModel @Inject constructor(
    private val repository: MylifeRepository,
    private val player: AudioPlayer,
    @ApplicationContext private val appContext: Context,
) : ViewModel() {

    private val _uiState = MutableStateFlow(AudioGalleryUiState())
    val uiState: StateFlow<AudioGalleryUiState> = _uiState.asStateFlow()

    private var date: String = ""
    private var loaded = false
    private var loadJob: Job? = null
    private val seenRefs = HashSet<String>()

    init {
        // 播放器全局态投影进本屏（Q8 单播：哪张卡显示进度由 player.key 决定）
        viewModelScope.launch {
            player.state.collect { s -> _uiState.update { it.copy(player = s) } }
        }
        // overview 轮询（60s 已在 Repository 共享）：分析中当日段数增长 → 重取首页；
        // pending 单向收敛（照 HotSlotViewModel：不因后续 overview 回翻为 true）。
        // ⚠️ 播放/暂停中（player.key != null）不重取：重取会替换为首页，若播放段在页 ≥2，
        // 卡片会从列表消失 → 有声无控件可停（全局单播下的边界，§4.7）
        viewModelScope.launch {
            repository.overview.collect { overview ->
                if (loaded && _uiState.value.player.key == null) reloadFirstPage()
                if (overview != null && _uiState.value.pending && !repository.hasPendingAnalysis()) {
                    _uiState.update { it.copy(pending = false) }
                }
            }
        }
    }

    /**
     * 首屏加载（Screen LaunchedEffect(date) 触发）；同日期已加载成功则跳过重拉
     * （此时 pending 也不重置，保收敛结果）。`pending` 为路由快照初值（§4.7）。
     */
    fun load(date: String, countSnapshot: Int, pending: Boolean = false) {
        if (date.isBlank()) return
        if (loaded && date == this.date && _uiState.value.state is AudioGalleryState.Data) return
        this.date = date
        _uiState.update { it.copy(pending = pending, headerCount = countSnapshot) }
        reloadFirstPage()
    }

    /** 错误重试 / pending 空态刷新入口（不走日期跳过逻辑）。 */
    fun retry() {
        if (date.isBlank()) return
        reloadFirstPage()
    }

    private fun reloadFirstPage() {
        loadJob?.cancel()
        seenRefs.clear()
        _uiState.update {
            // 已有数据时不落 Loading（轮询重取不闪屏、不丢滚动位置）
            if (it.state is AudioGalleryState.Data) it else it.copy(state = AudioGalleryState.Loading)
        }
        loadJob = viewModelScope.launch {
            try {
                applyPage(page = repository.audioSegments(date), replace = true)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // 刷新失败静默保留已有列表；仅首屏无数据时落错误态
                _uiState.update {
                    if (it.state is AudioGalleryState.Data) it
                    else it.copy(state = AudioGalleryState.Error(e.message ?: appContext.getString(R.string.mylife_load_failed)))
                }
            }
        }
    }

    /** 触底续页：`loadingMore` 互斥；失败静默保留已加载页（滚动可再次触发重试）。 */
    fun loadMore() {
        val data = _uiState.value.state as? AudioGalleryState.Data ?: return
        val cursor = data.nextCursor ?: return
        if (data.loadingMore) return
        _uiState.update { prev ->
            val d = prev.state as? AudioGalleryState.Data ?: return@update prev
            prev.copy(state = d.copy(loadingMore = true))
        }
        loadJob = viewModelScope.launch {
            try {
                applyPage(page = repository.audioSegments(date, cursor = cursor), replace = false)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                _uiState.update { prev ->
                    val d = prev.state as? AudioGalleryState.Data ?: return@update prev
                    prev.copy(state = d.copy(loadingMore = false))
                }
            }
        }
    }

    /** 播放/暂停切换（error 态 = 清错误 + 从头播，§4.6 重试语义在播放器内实现）。 */
    fun onToggle(segment: AudioSegment) {
        player.toggle(segment.eventRef, segment.mediaUrl)
    }

    /** 拖动中：只更新本地 scrub 位置（不下发 seek、不显示播放位置回写，§4.5 E7）。 */
    fun onScrub(segment: AudioSegment, ms: Long) {
        _uiState.update { it.copy(scrubMs = ms, scrubRef = segment.eventRef) }
    }

    /**
     * 拖动松手 / 点击轨道（§4.5 修订 2026-09-24）：**非当前播放段 = 先切段再定位**——轨道在任何
     * 卡片上都能拖/点（进屏未播放时也可用）；活动段语义不变（仅 seek，不改播放状态）。
     */
    fun onScrubEnd(segment: AudioSegment, ms: Long) {
        if (player.state.value.key != segment.eventRef) {
            player.toggle(segment.eventRef, segment.mediaUrl)
        }
        player.seekTo(ms)
        _uiState.update { it.copy(scrubMs = null, scrubRef = null) }
    }

    override fun onCleared() {
        player.stop()
    }

    // ── 内部 ──────────────────────────────────────────────────────────

    private fun applyPage(page: AudioPage, replace: Boolean) {
        _uiState.update { prev ->
            val existing = (prev.state as? AudioGalleryState.Data)?.segments.orEmpty()
            val base = if (replace) emptyList() else existing
            val fresh = if (replace) {
                seenRefs.clear()
                page.segments.onEach { seenRefs.add(it.eventRef) }
            } else {
                page.segments.filter { seenRefs.add(it.eventRef) }
            }
            val segments = base + fresh
            loaded = true
            prev.copy(
                state = if (segments.isEmpty()) AudioGalleryState.Empty
                else AudioGalleryState.Data(
                    segments = segments,
                    total = page.total,
                    nextCursor = page.nextCursor,
                    loadingMore = false,
                ),
                headerCount = page.total,
            )
        }
    }
}
