package dev.livachat.app.ui.screens.mylife.timeline

import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.app.data.remote.mylife.dto.SearchHitDto

data class TimelineUiState(
    // 粒度浏览（需求 3：默认日；选择器平级切换）
    val granularity: Granularity = Granularity.DAY,
    // 下钻范围过滤（§3.3.2）；null = 全量；手动切换粒度清 scope
    val scopeKey: String? = null,
    // overview（浏览数据源；§8 起 days = **已加载窗口**，nextCursor 非空 = 还有更早）
    val overview: OverviewDto? = null,
    val overviewLoading: Boolean = true,
    val overviewError: String? = null,
    // 聚合产物（aggregate 纯函数 + §8 E9 边界 group 过滤；Screen 不再自行 groupBy）
    val periods: List<PeriodDigest> = emptyList(),
    // 触底加载更早（§8.2.4）
    val loadingMore: Boolean = false,
    val loadMoreError: String? = null,
    // 搜索（query 非空时展示搜索结果列表，替代粒度列表）
    val query: String = "",
    val search: SearchState = SearchState.Idle,
    val isRefreshing: Boolean = false,
) {
    /** 还有更早的日卡未加载（footer 三态与触底判据）。 */
    val hasMore: Boolean get() = overview?.nextCursor != null
}

sealed interface SearchState {
    data object Idle : SearchState
    data object Loading : SearchState
    data class Data(
        val hits: List<SearchHitDto>,
        val total: Int,
        val nextCursor: String?,
        val loadingMore: Boolean,
    ) : SearchState
    data object Empty : SearchState
    data class Error(val message: String) : SearchState
}
