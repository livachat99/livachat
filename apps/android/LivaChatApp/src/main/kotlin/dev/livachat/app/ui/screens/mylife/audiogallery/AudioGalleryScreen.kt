package dev.livachat.app.ui.screens.mylife.audiogallery

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import dev.livachat.app.ui.components.EmptyState
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.MylifeTheme
import dev.livachat.app.ui.screens.mylife.audiogallery.components.AudioSegmentCard
import dev.livachat.app.ui.theme.Spacing

/** 触底加载提前量（行数，对齐 HotSlotScreen / FrameGrid）。 */
private const val LOAD_MORE_THRESHOLD = 4

/**
 * AudioGallery（独立全屏路由，docs/android_timeline_audio_gallery.md §4.7）：
 * 该日语音段按 ts 升序单流（**每段一个居中时间头** + 段卡片），顶栏返回/label/段数/分析中。
 * 自带 `MylifeTheme` + Abyss 底色（不经过宿主，故不复制星空背景，对齐 FrameGalleryScreen）；
 * 时间头点击 → eventListViewer（该时刻 ±10 分钟，§9）。
 */
@Composable
fun AudioGalleryScreen(
    date: String,
    label: String,
    count: Int,
    pending: Boolean,
    onNavigateToEventList: (tsMs: Long, pending: Boolean) -> Unit,
    onNavigateBack: () -> Unit,
    viewModel: AudioGalleryViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()
    val listState = rememberLazyListState()

    LaunchedEffect(date, pending) { viewModel.load(date, count, pending) }

    val segments = (uiState.state as? AudioGalleryState.Data)?.segments.orEmpty()
    // 每段一个时间标签（与 segments 一一对应），随追加页重算
    val uiLocale = LocalConfiguration.current.locales[0]
    val labels = remember(segments, uiLocale) { segmentTimeLabels(segments, locale = uiLocale) }
    val loadingMore = (uiState.state as? AudioGalleryState.Data)?.loadingMore == true

    val atListEnd by remember(segments.size) {
        derivedStateOf {
            val last = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1
            segments.isNotEmpty() && last >= segments.size - LOAD_MORE_THRESHOLD
        }
    }
    // 触底预取：**每个「新触发」只发一次**——失败后 `loadingMore` 回落不会立刻重发
    // （否则断网下 OkHttp 秒抛 → 立即重试风暴，先例缺陷）；重新武装 = 滚离阈值区再回来，
    // 或上一页成功追加（segments.size 变化，保证短页自动续填）
    var lastAttemptSize by remember { mutableIntStateOf(-1) }
    var leftEndSinceAttempt by remember { mutableStateOf(true) }
    LaunchedEffect(atListEnd, segments.size, loadingMore) {
        if (!atListEnd) {
            leftEndSinceAttempt = true
            return@LaunchedEffect
        }
        val freshTrigger = segments.size != lastAttemptSize || leftEndSinceAttempt
        if (!loadingMore && freshTrigger) {
            lastAttemptSize = segments.size
            leftEndSinceAttempt = false
            viewModel.loadMore()
        }
    }

    MylifeTheme {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(MylifeColors.Abyss)
                .statusBarsPadding(),
        ) {
            Column(modifier = Modifier.fillMaxSize().navigationBarsPadding()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = Spacing.sm, vertical = Spacing.xs),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = onNavigateBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.common_back), tint = MylifeColors.Dust)
                    }
                    Text(
                        text = label,
                        style = MaterialTheme.typography.titleMedium,
                        color = MylifeColors.Gold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    if (uiState.pending) {
                        Text(
                            text = stringResource(R.string.mylife_analyzing),
                            style = MaterialTheme.typography.labelMedium,
                            color = MylifeColors.Gold.copy(alpha = 0.8f),
                            modifier = Modifier.padding(end = Spacing.sm),
                        )
                    }
                    // 段数：首屏成功前 = 路由快照（audioCount），成功后 = 响应 total（§4.7）
                    Text(
                        text = pluralStringResource(R.plurals.mylife_segments_count, uiState.headerCount, uiState.headerCount),
                        style = MaterialTheme.typography.labelMedium,
                        color = MylifeColors.Dust,
                        modifier = Modifier.padding(end = Spacing.xs),
                    )
                }

                when (val state = uiState.state) {
                    AudioGalleryState.Loading -> LoadingHint(stringResource(R.string.mylife_loading), Modifier.weight(1f))

                    AudioGalleryState.Empty -> EmptyState(
                        icon = Icons.Filled.Info,
                        title = stringResource(R.string.mylife_audio_empty_title),
                        subtitle = stringResource(R.string.mylife_analysis_pending),
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                    )

                    is AudioGalleryState.Error -> ErrorHint(
                        message = state.message,
                        onRetry = viewModel::retry,
                        modifier = Modifier.weight(1f),
                    )

                    is AudioGalleryState.Data -> LazyColumn(
                        state = listState,
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                    ) {
                        itemsIndexed(state.segments, key = { _, s -> s.eventRef }) { index, segment ->
                            AudioSegmentCard(
                                segment = segment,
                                timeLabel = labels.getOrNull(index),
                                player = uiState.player,
                                // scrub 位置只喂给正在拖的那张卡（否则全局值会让所有卡同时显示同一位置）
                                scrubMs = uiState.scrubMs.takeIf { uiState.scrubRef == segment.eventRef },
                                onToggle = { viewModel.onToggle(segment) },
                                onScrub = { viewModel.onScrub(segment, it) },
                                onScrubEnd = { viewModel.onScrubEnd(segment, it) },
                                // 时间头点击 → eventListViewer（该段时刻 ±10 分钟，§9）；
                                // pending 用画廊已收敛值（点击时刻最准）
                                onTimeClick = { onNavigateToEventList(segment.tsMs, uiState.pending) },
                            )
                        }
                        if (state.loadingMore) {
                            item { LoadingHint(stringResource(R.string.mylife_loading)) }
                        }
                    }
                }
            }
        }
    }
}

// ---- 本地态提示（拷贝一份与 hotSlot/TimelineScreen 私有实现解耦，对齐 Gallery C1 先例）----

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
        Spacer(modifier = Modifier.height(Spacing.md))
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MylifeColors.Red)
        Spacer(modifier = Modifier.height(Spacing.md))
        OutlinedButton(onClick = onRetry) { Text(stringResource(R.string.common_retry), color = MylifeColors.Gold) }
    }
}
