package dev.livachat.app.ui.screens.mylife.timeline

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import dev.livachat.app.data.remote.mylife.dto.SearchHitDto
import dev.livachat.app.ui.components.EmptyState
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.timeline.components.GranularitySelector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import dev.livachat.app.ui.screens.mylife.timeline.components.MonthHeader
import dev.livachat.app.ui.screens.mylife.timeline.components.PeriodCard
import dev.livachat.app.ui.screens.mylife.timeline.components.ScopePill
import dev.livachat.app.ui.screens.mylife.timeline.components.SearchBar
import dev.livachat.app.ui.screens.mylife.timeline.components.SearchResultList
import dev.livachat.app.ui.theme.Spacing

/** LazyColumn 扁平 item 结构（分组头 + 摘要卡片）。 */
private sealed interface TimelineListItem {
    data class GroupHeaderItem(val text: String) : TimelineListItem
    data class PeriodCardItem(val digest: PeriodDigest) : TimelineListItem

    fun key(): String = when (this) {
        is GroupHeaderItem -> "header-$text"
        is PeriodCardItem -> "period-${digest.key}"
    }
}

/** 分组头（§3.5）：DAY/WEEK 归月 key（周按周一所在月）；MONTH 归年 key；YEAR 平铺。 */
private fun groupHeaderOf(digest: PeriodDigest): String? = when (digest.granularity) {
    Granularity.DAY -> digest.key.take(7)
    Granularity.WEEK -> mondayOfIsoWeek(digest.key)?.let { "${it.year}-%02d".format(it.monthValue) }
    Granularity.MONTH -> digest.key.take(4)
    Granularity.YEAR -> null
}

private fun buildListItems(
    uiState: TimelineUiState,
): List<TimelineListItem> {
    val items = ArrayList<TimelineListItem>()
    var lastHeader: String? = null
    for (digest in uiState.periods) {
        val header = groupHeaderOf(digest)
        if (header != null && header != lastHeader) {
            items.add(TimelineListItem.GroupHeaderItem(header))
            lastHeader = header
        }
        items.add(TimelineListItem.PeriodCardItem(digest))
    }
    return items
}

@Composable
fun TimelineScreen(
    onNavigateToGallery: (from: String, to: String, label: String, focusFrameId: String?, pending: Boolean) -> Unit,
    onNavigateToHotSlot: (startMs: Long, endMs: Long, pending: Boolean) -> Unit,
    onNavigateToEventList: (tsMs: Long, pending: Boolean) -> Unit,
    onNavigateToAudio: (date: String, label: String, count: Int, pending: Boolean) -> Unit,
    viewModel: TimelineViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()
    val listState = rememberLazyListState()
    // 搜索列表独立滚动状态：浏览/搜索模式切换互不干扰（共用 state 会保留错误位置）
    val searchListState = rememberLazyListState()

    // 点击搜索框外部：收起键盘并清除焦点（未输入内容时复原；query 内容不受影响）。
    // 注：edge-to-edge 下 WindowInsets.ime 可用；tap 检测仍为主路径，ime 监听作为兜底。
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current
    val imeVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
    LaunchedEffect(imeVisible) {
        if (!imeVisible) focusManager.clearFocus()
    }

    // 一次性导航事件（搜索命中 → EventList，docs/android_timeline_topics.md §3.3.1）
    LaunchedEffect(Unit) {
        viewModel.eventListNavigation.collect { nav ->
            onNavigateToEventList(nav.tsMs, nav.pending)
        }
    }

    // 一次性导航事件（卡片图片 → Gallery，R2）
    LaunchedEffect(Unit) {
        viewModel.galleryNavigation.collect { nav ->
            onNavigateToGallery(nav.from, nav.to, nav.label, nav.focusFrameId, nav.pending)
        }
    }

    // 一次性导航事件（hot slot chip → hotSlotView，docs/android_timeline_hotslot.md §4.3）
    LaunchedEffect(Unit) {
        viewModel.hotSlotNavigation.collect { nav ->
            onNavigateToHotSlot(nav.startMs, nav.endMs, nav.pending)
        }
    }

    // 一次性导航事件（日卡音频入口 → AudioGallery，docs/android_timeline_audio_gallery.md §4.1）
    LaunchedEffect(Unit) {
        viewModel.audioNavigation.collect { nav ->
            onNavigateToAudio(nav.date, nav.label, nav.count, nav.pending)
        }
    }

    // 粒度切换/下钻后滚动归位。
    // 仅"真的切换"才归位：LaunchedEffect 在首次组合也会执行（从 hotslot/Gallery 返回、tab 切回
    // 都会重建组合），无条件 scrollToItem(0) 会把用户原位置重置到最新一天。
    var lastGranularity by remember { mutableStateOf<Granularity?>(null) }
    LaunchedEffect(uiState.granularity) {
        val prev = lastGranularity
        lastGranularity = uiState.granularity
        if (prev != null && prev != uiState.granularity) {
            listState.scrollToItem(0)
        }
    }

    // 搜索模式消费一次返回键：仅清 query 不退出路由（§3.4）
    BackHandler(enabled = uiState.query.isNotEmpty()) {
        viewModel.onQueryChange("")
    }

    val searchMode = uiState.query.isNotEmpty()
    val listItems = remember(uiState.periods, uiState.granularity, uiState.scopeKey) {
        buildListItems(uiState)
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .pointerInput(Unit) {
                detectTapGestures {
                    keyboardController?.hide()
                    focusManager.clearFocus()
                }
            },
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            SearchBar(
                query = uiState.query,
                onQueryChange = viewModel::onQueryChange,
                onSearchImmediate = viewModel::onSearchImmediate,
                modifier = Modifier.padding(bottom = Spacing.sm),
            )
            if (searchMode) {
                SearchModeContent(
                    uiState = uiState,
                    listState = searchListState,
                    // 搜索命中分派（docs/android_timeline_topics.md §3.3.1）：所有带 ts 命中 → EventList
                    onHitClick = viewModel::onSearchHitClick,
                    onLoadMore = viewModel::onSearchLoadMore,
                    onRetry = viewModel::onSearchRetry,
                )
            } else {
                BrowseModeContent(
                    uiState = uiState,
                    listItems = listItems,
                    listState = listState,
                    viewModel = viewModel,
                )
            }
        }

        // 底部：scope pill（下钻，§3.6）
        Column(
            modifier = Modifier.align(Alignment.BottomCenter),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            uiState.scopeKey?.let { scope ->
                ScopePill(
                    scopeKey = scope,
                    onClear = viewModel::onClearScope,
                    modifier = Modifier.padding(bottom = Spacing.md),
                )
            }
        }
    }
}

@Composable
private fun ColumnScope.BrowseModeContent(
    uiState: TimelineUiState,
    listItems: List<TimelineListItem>,
    listState: LazyListState,
    viewModel: TimelineViewModel,
) {
    GranularitySelector(
        selected = uiState.granularity,
        onSelect = viewModel::onGranularityChange,
    )
    Spacer(modifier = Modifier.height(Spacing.sm))
    val showFooter = uiState.scopeKey == null
    when {
        uiState.overviewLoading -> LoadingHint(stringResource(R.string.mylife_loading), Modifier.weight(1f))
        uiState.overviewError != null -> ErrorHint(
            message = uiState.overviewError,
            onRetry = viewModel::refresh,
            modifier = Modifier.weight(1f),
        )
        // hasMore 时即使是空列表也走列表分支：留 footer 供加载更早（§8 边界 group 不上屏的兜底）
        uiState.periods.isEmpty() && !uiState.hasMore -> EmptyState(
            icon = Icons.Filled.Info,
            // scope 空态带范围（§3.6），如"2026年8月没有记录"
            title = uiState.scopeKey?.let {
                stringResource(
                    R.string.mylife_no_records_in_scope,
                    scopeLabel(it, LocalConfiguration.current.locales[0]),
                )
            }
                ?: stringResource(R.string.mylife_no_records_yet),
            modifier = Modifier.weight(1f).fillMaxWidth(),
        )
        else -> LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize().testTag(TIMELINE_LIST_TAG),
        ) {
            listItems.forEach { item ->
                item(key = item.key()) {
                    when (item) {
                        is TimelineListItem.GroupHeaderItem -> GroupHeader(item.text)
                        is TimelineListItem.PeriodCardItem -> PeriodCard(
                            digest = item.digest,
                            onBodyClick = cardBodyClick(item.digest, viewModel),
                            onHotSlotClick = viewModel::onHotSlotClick,
                            onImageClick = { viewModel.onImageClick(it, item.digest) },
                            // topic 点击已恢复（放宽回退落地后全语料可搜 96%/88%，docs/bff_search_fts.md §10.1⑩）
                            onTopicClick = viewModel::onTopicSearch,
                            onAudioClick = { viewModel.onAudioClick(item.digest) },
                        )
                    }
                }
            }
            // §8.2.4：footer 三态 + 触底加载（边界未闭合 group 由 footer 占位，不上屏为卡）。
            // drilled scope 内**不出 footer**：下钻范围的数据在进入时就已全量加载（可点的卡必已闭合），
            // 翻页只会取回被 scope 过滤掉的更早日卡——不产生新 item 却每轮触发一次请求（空转翻页）。
            if (showFooter) {
                item(key = "timeline-footer") {
                    TimelineListFooter(uiState = uiState, onLoadMore = viewModel::onLoadMore)
                }
            }
        }
    }
    // 触底加载更早一页（§8.2.4）：阈值距底部 3 个 item（对齐 SearchResultList 的 loadMore 语义）。
    // remember(itemCount, hasMore) 键：翻页追加后重建，避免陈旧捕获。
    val itemCount = listItems.size + if (showFooter) 1 else 0
    val shouldLoadMore by remember(itemCount, uiState.hasMore) {
        derivedStateOf {
            val lastVisible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
            itemCount > 0 && lastVisible >= itemCount - 3
        }
    }
    // **每次自动触发必须带来 item 增长**（lastAutoLoadCount）：否则 loadingMore 的 true→false 会
    // 反复重新武装本 effect——列表不增长时（如被过滤/无新卡）会一轮一页地把历史全部拉完。
    // 离开底部（shouldLoadMore=false）时重新武装。
    var lastAutoLoadCount by remember { mutableStateOf(-1) }
    LaunchedEffect(shouldLoadMore, uiState.hasMore, uiState.loadingMore, itemCount,
                   uiState.loadMoreError, showFooter) {
        if (!shouldLoadMore) {
            lastAutoLoadCount = -1
            return@LaunchedEffect
        }
        if (showFooter && uiState.hasMore && !uiState.loadingMore && uiState.loadMoreError == null &&
            itemCount != lastAutoLoadCount
        ) {
            lastAutoLoadCount = itemCount
            viewModel.onLoadMore()
        }
    }
}

/** 触底 footer 三态（§8.2.4）：加载中 / 已到最早（无更早） / 失败重试。 */
@Composable
private fun TimelineListFooter(uiState: TimelineUiState, onLoadMore: () -> Unit) {
    val text = when {
        uiState.loadMoreError != null -> stringResource(R.string.mylife_load_failed_retry)
        uiState.loadingMore -> stringResource(R.string.mylife_loading_earlier)
        uiState.hasMore -> stringResource(R.string.mylife_load_earlier)
        else -> stringResource(R.string.mylife_reached_start)
    }
    val color = if (uiState.loadMoreError != null) MaterialTheme.colorScheme.error else MylifeColors.Dust
    Text(
        text = text,
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = uiState.hasMore || uiState.loadMoreError != null) { onLoadMore() }
            .padding(horizontal = Spacing.md, vertical = Spacing.md),
        textAlign = TextAlign.Center,
        style = MaterialTheme.typography.labelMedium,
        color = color,
    )
}

/** scope key → 展示文案（locale 化）："2026-08" → "2026年8月"/"Aug 2026"；"2026" → "2026年"/"2026"。 */
private fun scopeLabel(scopeKey: String, locale: java.util.Locale): String = when {
    scopeKey.length == 4 -> yearText(scopeKey.toInt(), locale)
    scopeKey.length == 7 -> monthYearText(scopeKey.take(4).toInt(), scopeKey.substringAfter('-').toInt(), locale)
    else -> scopeKey
}

@Composable
private fun ColumnScope.SearchModeContent(
    uiState: TimelineUiState,
    listState: LazyListState,
    onHitClick: (SearchHitDto) -> Unit,
    onLoadMore: () -> Unit,
    onRetry: () -> Unit,
) {
    when (val search = uiState.search) {
        // R2：Idle = query < 2 字符（含点短名 topic chip）——提示而非「搜索中…」死等（§3.2 边界）
        SearchState.Idle -> LoadingHint(stringResource(R.string.mylife_search_min_chars), Modifier.weight(1f))
        SearchState.Loading -> LoadingHint(stringResource(R.string.mylife_searching), Modifier.weight(1f))
        is SearchState.Error -> ErrorHint(message = search.message, onRetry = onRetry, modifier = Modifier.weight(1f))
        SearchState.Empty -> EmptyState(
            icon = Icons.Filled.Info,
            title = stringResource(R.string.mylife_search_no_results),
            modifier = Modifier.weight(1f).fillMaxWidth(),
        )
        is SearchState.Data -> SearchResultList(
            hits = search.hits,
            total = search.total,
            query = uiState.query,
            onHitClick = onHitClick,
            onLoadMore = onLoadMore,
            listState = listState,
        )
    }
}

/** 卡片本体点击分派：月/年 → 下钻；日/周不可点（周卡本体 Replay 入口已随 hot slot 改版移除）。 */
internal fun cardBodyClick(digest: PeriodDigest, viewModel: TimelineViewModel): (() -> Unit)? = when (digest.granularity) {
    Granularity.DAY, Granularity.WEEK -> null
    Granularity.MONTH, Granularity.YEAR -> ({ viewModel.onDrillDown(digest) })
}

@Composable
private fun GroupHeader(header: String) {
    // 月/年大字分隔（§3.5）；月 key 走 MonthHeader，年 key 单独渲染（"2026年" 无月段）
    if (header.length == 7) {
        MonthHeader(month = header)
    } else {
        Text(
            text = yearText(header.toInt(), LocalConfiguration.current.locales[0]),
            modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.sm),
            style = MaterialTheme.typography.headlineMedium,
            color = MylifeColors.Gold.copy(alpha = 0.7f),
        )
    }
}

@Composable
private fun LoadingHint(text: String, modifier: Modifier = Modifier) {
    Box(modifier = modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        Text(text, style = MaterialTheme.typography.labelMedium, color = MylifeColors.Dust)
    }
}

@Composable
private fun ErrorHint(message: String, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth().padding(Spacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(modifier = Modifier.weight(1f))
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MylifeColors.Red)
        Spacer(modifier = Modifier.height(Spacing.md))
        androidx.compose.material3.OutlinedButton(onClick = onRetry) {
            Text(stringResource(R.string.common_retry), color = MylifeColors.Gold)
        }
        Spacer(modifier = Modifier.weight(1f))
    }
}

/** 浏览模式列表 testTag（仪器测试：滚动位置保持回归用）。 */
internal const val TIMELINE_LIST_TAG = "timelineList"
