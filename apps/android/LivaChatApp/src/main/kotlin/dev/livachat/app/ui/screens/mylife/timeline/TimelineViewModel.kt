package dev.livachat.app.ui.screens.mylife.timeline

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.DayTopicDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.app.data.remote.mylife.dto.SearchHitDto
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.data.repository.localDateOf
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.temporal.ChronoUnit
import javax.inject.Inject

/**
 * 搜索命中 → EventListViewer 导航（docs/android_timeline_topics.md §3.3.1）：
 * tsMs = 被点事件时刻（新屏取数窗口中心）；pending = 命中当日 hasPending（顶栏「分析中…」）。
 */
data class EventListNavigation(
    val tsMs: Long,
    val pending: Boolean,
)

/**
 * hot slot → hotSlotView 导航（docs/android_timeline_hotslot.md §4.3）：取数窗口
 * [startMs, endMs] + pending（顶栏「分析中…」，来自日卡 hasPending）。
 */
data class HotSlotNavigation(
    val startMs: Long,
    val endMs: Long,
    val pending: Boolean,
)

/**
 * Gallery 导航（docs/android_timeline_image_gallery.md §3.2 R2）：被点卡片的时间范围 + 展示
 * label + focus 帧（被点的那张，gallery 首屏命中则滚动定位）+ pending（顶栏「分析中…」）。
 */
data class GalleryNavigation(
    val from: String,
    val to: String,
    val label: String,
    val focusFrameId: String?,
    val pending: Boolean,
)

/**
 * AudioGallery 导航（docs/android_timeline_audio_gallery.md §4.1 A4，仅日卡）：日期（= digest.key）
 * + 展示 label + 段数快照（新屏首屏成功前顶栏「N 段」，§4.7）+ pending（顶栏「分析中…」）。
 */
data class AudioGalleryNavigation(
    val date: String,
    val label: String,
    val count: Int,
    val pending: Boolean,
)

/**
 * §8.2.4：overview 页大小随粒度——DAY/WEEK 取首屏速度（30 天）；MONTH/YEAR 让一页尽量覆盖
 * 一个聚合单位（90 天 ≈ 3 个月；366 天 = 一个自然年），减少边界补页次数。
 */
internal fun pageSizeFor(granularity: Granularity): Int = when (granularity) {
    Granularity.DAY, Granularity.WEEK -> 30
    Granularity.MONTH -> 90
    Granularity.YEAR -> 366
}

/**
 * scope key → 闭区间本地日范围（drilled scope 刷新按范围定位窗口用，§8.3 E10/E12 备注）。
 * 形状判定：10 = 日、`-W` = ISO 周、7 = 月、4 = 年；未知形状 → null（调用方回退为清 scope）。
 */
internal fun scopeDateRange(scopeKey: String): Pair<String, String>? = when {
    scopeKey.length == 10 -> runCatching { LocalDate.parse(scopeKey) }.getOrNull()
        ?.let { it.toString() to it.toString() }
    scopeKey.contains("-W") -> mondayOfIsoWeek(scopeKey)?.let { mon ->
        mon.toString() to mon.plusDays(6).toString()
    }
    scopeKey.length == 7 -> runCatching { LocalDate.parse("$scopeKey-01") }.getOrNull()
        ?.let { f -> f.toString() to f.withDayOfMonth(f.lengthOfMonth()).toString() }
    scopeKey.length == 4 -> runCatching { LocalDate.parse("$scopeKey-01-01") }.getOrNull()
        ?.let { f -> f.toString() to f.withDayOfYear(f.lengthOfYear()).toString() }
    else -> null
}

/** 闭区间日数（含两端）→ scope 窗口的 limit（§8.3 E10）。 */
private fun daysInRange(range: Pair<String, String>): Int =
    (ChronoUnit.DAYS.between(LocalDate.parse(range.first), LocalDate.parse(range.second)) + 1)
        .toInt().coerceIn(1, 400)

/** 闭区间末日次日（scope 窗口定位游标：`cursor = <scope 末日次日>`，§8.3 E12 备注）。 */
private fun dayAfter(range: Pair<String, String>): String =
    LocalDate.parse(range.second).plusDays(1).toString()

@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class TimelineViewModel @Inject constructor(
    private val repository: MylifeRepository,
    @ApplicationContext private val appContext: Context,
) : ViewModel() {

    private val _uiState = MutableStateFlow(TimelineUiState())
    val uiState: StateFlow<TimelineUiState> = _uiState.asStateFlow()

    private val _eventListNavigation = MutableSharedFlow<EventListNavigation>(extraBufferCapacity = 1)
    val eventListNavigation: SharedFlow<EventListNavigation> = _eventListNavigation.asSharedFlow()

    private val _galleryNavigation = MutableSharedFlow<GalleryNavigation>(extraBufferCapacity = 1)
    val galleryNavigation: SharedFlow<GalleryNavigation> = _galleryNavigation.asSharedFlow()

    private val _hotSlotNavigation = MutableSharedFlow<HotSlotNavigation>(extraBufferCapacity = 1)
    val hotSlotNavigation: SharedFlow<HotSlotNavigation> = _hotSlotNavigation.asSharedFlow()

    private val _audioNavigation = MutableSharedFlow<AudioGalleryNavigation>(extraBufferCapacity = 1)
    val audioNavigation: SharedFlow<AudioGalleryNavigation> = _audioNavigation.asSharedFlow()

    private val queryFlow = MutableStateFlow("")

    private var loadMoreJob: Job? = null

    init {
        // 首次加载：force 重新请求（对齐 Web staleTime=0 挂载即 refetch 语义）。
        // Repository 单例内存缓存 + 60s 轮询仅 hasPending 触发，非 force 会永远返回
        // 分析完成前的旧快照（新生成的 day_summary 看不到）。§8：首屏带粒度页大小。
        viewModelScope.launch {
            try {
                repository.refreshOverview(
                    force = true, limit = pageSizeFor(_uiState.value.granularity))
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(overviewLoading = false, overviewError = e.message ?: appContext.getString(R.string.mylife_load_failed))
                }
            }
        }
        viewModelScope.launch {
            repository.overview.collect { overview ->
                _uiState.update { prev ->
                    // §8 E6：生效时区变了（日界整体重排）→ 清 scope，drill 范围不再对应旧日界
                    val tzChanged = overview != null && prev.overview != null &&
                        prev.overview.timezone != overview.timezone
                    prev.copy(
                        overview = overview,
                        overviewLoading = false,
                        // 加载成功即清错误（重试成功路径），避免 ErrorHint 残留
                        overviewError = if (overview != null) null else prev.overviewError,
                        // 新窗口到达 = 触底错误态解除（否则 footer 会卡在「加载失败」且自动加载被禁用）
                        loadMoreError = null,
                        scopeKey = if (tzChanged) null else prev.scopeKey,
                    ).recompute()
                }
            }
        }
        viewModelScope.launch {
            queryFlow
                // 空串立即发射（清空 query 即恢复粒度浏览，§3.4 退出语义）
                .debounce { if (it.isBlank()) 0L else SEARCH_DEBOUNCE_MS }
                .distinctUntilChanged()
                .flatMapLatest { q -> searchFlow(q) }
                .collect { state -> _uiState.update { it.copy(search = state) } }
        }
        // pending 60s 轮询在 Repository 共享；消化后 overview 更新 → periods 自动重聚合
    }

    // ---- 粒度 / 下钻（§3.3.2）----

    /** 手动切换粒度：清 scope（含切回当前粒度），重聚合；窗口比该粒度页浅时补取一页（§8.2.4）。 */
    fun onGranularityChange(granularity: Granularity) {
        _uiState.update {
            it.copy(granularity = granularity, scopeKey = null).recompute()
        }
        ensureWindow(granularity)
    }

    /**
     * §8.2.4：切到 MONTH/YEAR 后，已加载窗口可能只有 DAY 粒度的 30 天——按该粒度页大小
     * 从最新补取一页（与已加载集合合并，保深度；失败静默，触底/下拉刷新可重试）。
     *
     * 窗口**已完整**（`nextCursor == null`）或已够深时不发请求：切换粒度本身是纯客户端
     * 重聚合（`recompute`），短历史下再取一次也只会拿回同样的天。
     */
    private fun ensureWindow(granularity: Granularity) {
        val overview = _uiState.value.overview ?: return   // 首屏未到位：init 正在取，避免重复请求
        if (overview.nextCursor == null) return
        val need = pageSizeFor(granularity)
        if (overview.days.size >= need) return
        viewModelScope.launch {
            try {
                repository.refreshOverview(force = true, limit = need)
            } catch (_: Exception) {
                // 浏览态不因补页失败转错误态（窗口已有数据可渲染）
            }
        }
    }

    /** 月/年卡本体点击 → 下钻：粒度切换 + 范围过滤（§3.3.1）。 */
    fun onDrillDown(digest: PeriodDigest) {
        when (digest.granularity) {
            Granularity.MONTH -> _uiState.update {
                it.copy(granularity = Granularity.DAY, scopeKey = digest.key).recompute()
            }
            Granularity.YEAR -> _uiState.update {
                it.copy(granularity = Granularity.MONTH, scopeKey = digest.key).recompute()
            }
            else -> Unit // DAY/WEEK 不参与下钻
        }
    }

    /** scope pill 清除：粒度不变，恢复该粒度全量。 */
    fun onClearScope() {
        _uiState.update { it.copy(scopeKey = null).recompute() }
    }

    // ---- 卡片/命中导航（hotSlot / Gallery / 搜索命中 → EventList）----

    /** hot slot chip 点击（仅日卡）：导航 hotSlotView，取数窗口 = [startMs, endMs]（§4.3）。 */
    fun onHotSlotClick(slot: HotSlot, pending: Boolean) {
        _hotSlotNavigation.tryEmit(HotSlotNavigation(slot.startMs, slot.endMs, pending))
    }

    /** 卡片图片点击（v3→Gallery，R2/Q6）：按 digest 粒度范围进入 Gallery，focus 被点帧。 */
    fun onImageClick(image: PeriodImage, digest: PeriodDigest) {
        val (from, to) = digest.dateRange() ?: return
        _galleryNavigation.tryEmit(
            GalleryNavigation(
                from = from,
                to = to,
                label = digest.rangeLabel,
                focusFrameId = image.frameId,
                pending = digest.hasPending,
            ),
        )
    }

    /** 日卡音频入口点击（仅日卡、段数非零）：进入 AudioGallery，日期 = digest.key（§4.1 A4）。 */
    fun onAudioClick(digest: PeriodDigest) {
        if (digest.audioCount <= 0) return
        _audioNavigation.tryEmit(
            AudioGalleryNavigation(
                date = digest.key,
                label = digest.rangeLabel,
                count = digest.audioCount,
                pending = digest.hasPending,
            ),
        )
    }

    /** 搜索命中分派（docs/bff_search_fts.md §3.4 A2）：五类事件命中 → EventList(ts + pending)；ts 空 → no-op。 */
    fun onSearchHitClick(hit: SearchHitDto) {
        hit.tsMs?.let { ts -> _eventListNavigation.tryEmit(EventListNavigation(ts, pendingForDay(ts))) }
    }

    /** 命中当日 hasPending（本地日归属与取数共用口径 localDateOf；无该日 → false）。 */
    private fun pendingForDay(tsMs: Long): Boolean {
        val date = localDateOf(tsMs)
        return _uiState.value.overview?.days?.firstOrNull { it.date == date }?.hasPending ?: false
    }

    /**
     * topic chip 点击（docs/android_timeline_topics.md §3.2）：置 query 复用搜索流。
     * §10：优先用 search_words（可直接搜到重点事件的原文片段），缺省/空白回退 topic 名。
     */
    fun onTopicSearch(topic: DayTopicDto) = onQueryChange(
        topic.searchWords?.takeIf { it.isNotBlank() } ?: topic.topic)

    // ---- 搜索（§3.4）----

    fun onQueryChange(query: String) {
        _uiState.update { it.copy(query = query) }
        queryFlow.value = query
    }

    /** 加载更多：游标累页；loadMore 请求被新 query 的搜索流取消。 */
    fun onSearchLoadMore() {
        val state = _uiState.value.search as? SearchState.Data ?: return
        val cursor = state.nextCursor ?: return
        if (state.loadingMore) return
        val q = _uiState.value.query.trim()
        if (q.length < MIN_QUERY_LENGTH) return
        loadMoreJob?.cancel()
        loadMoreJob = viewModelScope.launch {
            _uiState.update { prev ->
                prev.copy(search = (prev.search as? SearchState.Data)?.copy(loadingMore = true) ?: prev.search)
            }
            try {
                val resp = repository.search(q, cursor = cursor)
                _uiState.update { prev ->
                    val data = prev.search as? SearchState.Data ?: return@update prev
                    prev.copy(
                        search = data.copy(
                            hits = data.hits + resp.hits,
                            total = resp.total,
                            nextCursor = resp.nextCursor,
                            loadingMore = false,
                        ),
                    )
                }
            } catch (_: Exception) {
                _uiState.update { prev ->
                    prev.copy(search = (prev.search as? SearchState.Data)?.copy(loadingMore = false) ?: prev.search)
                }
            }
        }
    }

    /** 搜索错误重试：直接重跑当前 query（绕过 debounce/distinctUntilChanged）。 */
    fun onSearchRetry() = searchImmediate()

    /** IME action=search 立即触发（§3.4）：跳过 350ms 防抖直接搜当前 query。 */
    fun onSearchImmediate() = searchImmediate()

    private fun searchImmediate() {
        val q = _uiState.value.query.trim()
        if (q.length < MIN_QUERY_LENGTH) return
        viewModelScope.launch {
            searchFlow(q).collect { state -> _uiState.update { it.copy(search = state) } }
        }
    }

    private fun searchFlow(query: String): Flow<SearchState> = flow {
        val trimmed = query.trim()
        loadMoreJob?.cancel()
        if (trimmed.length < MIN_QUERY_LENGTH) {
            emit(SearchState.Idle)
            return@flow
        }
        emit(SearchState.Loading)
        try {
            val resp = repository.search(trimmed)
            emit(
                if (resp.hits.isEmpty()) SearchState.Empty
                else SearchState.Data(resp.hits, resp.total, resp.nextCursor, loadingMore = false),
            )
        } catch (e: Exception) {
            emit(SearchState.Error(e.message ?: appContext.getString(R.string.mylife_search_failed)))
        }
    }

    // ---- 刷新 / 加载 ----

    /**
     * 下拉刷新：搜索模式重跑当前 query；否则重取浏览窗口。
     *
     * §8.2.4 E10：drilled scope 内**不可只回第一页**（scope 过滤后 periods 为空 → 假空态），
     * 改用 `cursor = <scope 末日次日>` 定位取该 scope 窗口（limit = scope 天数，与已加载
     * 集合合并保深度）；scope 形状非法（不该发生）→ 清 scope 回全局。
     */
    fun refresh() {
        _uiState.update { it.copy(isRefreshing = true) }
        val q = _uiState.value.query.trim()
        if (q.length >= MIN_QUERY_LENGTH) {
            viewModelScope.launch {
                try {
                    val resp = repository.search(q)
                    _uiState.update {
                        it.copy(
                            isRefreshing = false,
                            search = if (resp.hits.isEmpty()) SearchState.Empty
                            else SearchState.Data(resp.hits, resp.total, resp.nextCursor, loadingMore = false),
                        )
                    }
                } catch (e: Exception) {
                    _uiState.update {
                        it.copy(isRefreshing = false, search = SearchState.Error(e.message ?: appContext.getString(R.string.mylife_search_failed)))
                    }
                }
            }
            return
        }
        val state = _uiState.value
        val scope = state.scopeKey
        val range = scope?.let(::scopeDateRange)
        viewModelScope.launch {
            try {
                when {
                    scope != null && range != null -> repository.refreshOverview(
                        force = true, limit = daysInRange(range), cursor = dayAfter(range))
                    else -> {
                        if (scope != null) _uiState.update { it.copy(scopeKey = null).recompute() }
                        repository.refreshOverview(
                            force = true, limit = pageSizeFor(state.granularity))
                    }
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(overviewLoading = false, overviewError = e.message ?: appContext.getString(R.string.mylife_load_failed))
                }
            } finally {
                _uiState.update { it.copy(isRefreshing = false) }
            }
        }
    }

    /**
     * 触底加载更早一页（§8.2.4）：同步置 loadingMore 做互斥；失败落 loadMoreError 供 footer 重试。
     *
     * drilled scope 内直接 no-op：下钻范围的数据在进入时就已全量加载（可点的卡必已闭合），
     * 翻页只会取回被 scope 过滤掉的更早日卡——不产生新 item 却每轮触发一次请求（空转翻页）。
     */
    fun onLoadMore() {
        val state = _uiState.value
        if (state.loadingMore || !state.hasMore || state.scopeKey != null) return
        _uiState.update { it.copy(loadingMore = true, loadMoreError = null) }
        viewModelScope.launch {
            try {
                repository.loadMoreOverview(pageSizeFor(state.granularity))
                _uiState.update { it.copy(loadingMore = false) }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(loadingMore = false, loadMoreError = e.message ?: appContext.getString(R.string.mylife_load_failed))
                }
            }
        }
    }

    private fun TimelineUiState.recompute(): TimelineUiState =
        copy(periods = renderablePeriods(overview, granularity, scopeKey))

    /**
     * §8 E9：聚合后剔除**最旧的未闭合 group**（不上屏，footer「加载更早」占位）——防半截月/年卡。
     *
     * 判定 = `next_cursor != null` 且 窗口最旧日 > 该 group 首日（**日历比较**）。不可用
     * 「group 首日是否有行」——无事件日不落行（bff §7 E12/E6），该判定恒不成立。
     * 含窗口最旧日的 group 至多一个；DAY 卡恒闭合（首日 = 当日）。
     */
    private fun renderablePeriods(
        overview: OverviewDto?,
        granularity: Granularity,
        scopeKey: String?,
    ): List<PeriodDigest> {
        val days = overview?.days.orEmpty()
        val uiLocale = appContext.resources.configuration.locales[0]
        val digests = PeriodAggregator.aggregate(days, granularity, scopeKey, uiLocale)
        if (overview?.nextCursor == null || days.isEmpty()) return digests
        val oldest = days.minOf { it.date }
        return digests.filter { digest ->
            val start = digest.dateRange()?.first
            start == null || oldest <= start
        }
    }

    private companion object {
        const val MIN_QUERY_LENGTH = 2
        const val SEARCH_DEBOUNCE_MS = 350L
    }
}
