package dev.livachat.app.ui.screens.mylife.gallery.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.painter.ColorPainter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import coil.compose.AsyncImage
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.FrameDto
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing

/** 触底加载提前量（item 数）：可见项进入末 N 项即预取下一页。 */
private const val LOAD_MORE_THRESHOLD = 6

/**
 * 3 列方图网格（§3.4）：点击回调 + 触底预取 + 底部加载行；
 * highlightedIndex（focus 命中）画金色描边短暂高亮。
 * 编辑模式（§9.4.1）：每格右下角选择框（自消费 tap，图片其余区域仍进查看器）。
 */
@Composable
fun FrameGrid(
    frames: List<FrameDto>,
    loadingMore: Boolean,
    gridState: LazyGridState,
    onFrameClick: (Int) -> Unit,
    onLoadMore: () -> Unit,
    modifier: Modifier = Modifier,
    highlightedIndex: Int? = null,
    contentPadding: PaddingValues = PaddingValues(Spacing.xs),
    editMode: Boolean = false,
    selectedFrameIds: Set<String> = emptySet(),
    onToggleSelect: (FrameDto) -> Unit = {},
) {
    val atListEnd by remember(frames.size) {
        derivedStateOf {
            val last = gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1
            last >= frames.size - LOAD_MORE_THRESHOLD
        }
    }
    // 触底预取：**每个「新触发」只发一次**——失败后 `loadingMore` 回落不会立刻重发
    // （否则断网下 OkHttp 秒抛 → 立即重试风暴；docs/android_timeline_audio_gallery.md §9.11、
    // 既有债务条目 docs/android_timeline_topics.md §8）；nextCursor 耗尽时 VM 侧直接 no-op。
    // 重新武装 = 滚离阈值区再回来，或上一页成功追加（frames.size 变化，保证短页自动续填）
    var lastAttemptSize by remember { mutableIntStateOf(-1) }
    var leftEndSinceAttempt by remember { mutableStateOf(true) }
    LaunchedEffect(atListEnd, frames.size, loadingMore) {
        if (!atListEnd) {
            leftEndSinceAttempt = true
            return@LaunchedEffect
        }
        val freshTrigger = frames.size != lastAttemptSize || leftEndSinceAttempt
        if (!loadingMore && freshTrigger) {
            lastAttemptSize = frames.size
            leftEndSinceAttempt = false
            onLoadMore()
        }
    }

    LazyVerticalGrid(
        columns = GridCells.Fixed(3),
        state = gridState,
        modifier = modifier.fillMaxWidth(),
        contentPadding = contentPadding,
        horizontalArrangement = Arrangement.spacedBy(Spacing.xs),
        verticalArrangement = Arrangement.spacedBy(Spacing.xs),
    ) {
        itemsIndexed(frames, key = { _, f -> f.frameId }) { index, frame ->
            val shape = RoundedCornerShape(8.dp)
            Box(modifier = Modifier.aspectRatio(1f)) {
                AsyncImage(
                    model = frame.mediaUrl,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    // 占位/错误态（§4-7「方图灰底」）：加载失败不再是 Abyss 色空洞，可感知"此处应有一图"
                    placeholder = ColorPainter(MylifeColors.Panel),
                    error = ColorPainter(MylifeColors.Panel),
                    modifier = Modifier
                        .fillMaxSize()
                        .clip(shape)
                        .then(
                            if (index == highlightedIndex) {
                                Modifier.border(2.dp, MylifeColors.Gold, shape)
                            } else {
                                Modifier
                            },
                        )
                        .clickable { onFrameClick(index) },
                )
                if (editMode) {
                    SelectionBox(
                        selected = frame.frameId in selectedFrameIds,
                        enabled = !frame.eventRef.isNullOrEmpty(),
                        onClick = { onToggleSelect(frame) },
                        modifier = Modifier.align(Alignment.BottomEnd),
                    )
                }
            }
        }
        if (loadingMore) {
            item(span = { GridItemSpan(maxLineSpan) }) {
                LoadingHint(stringResource(R.string.mylife_loading))
            }
        }
    }
}

/**
 * 编辑模式选择框（§9.4.1）：右下角 22dp 方框（未选 = 白描边 + 半透明黑底；已选 = Gold
 * 实底 + 勾）。命中区经内边距外扩（22 + 6×2 = 34dp）；clickable 自消费 tap → onToggleSelect，
 * 不触发图片点击（图片其余区域仍 onFrameClick 进查看器）。`enabled=false`（无 eventRef，
 * 防御 Q9.7）→ 置灰不可点。
 */
@Composable
private fun SelectionBox(
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val selectDesc = stringResource(R.string.mylife_select_photo)
    Box(
        modifier = modifier
            .padding(2.dp)
            .semantics { contentDescription = selectDesc }
            .then(if (enabled) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(6.dp),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(22.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(
                    when {
                        selected -> MylifeColors.Gold
                        enabled -> Color.Black.copy(alpha = 0.35f)
                        else -> Color.Black.copy(alpha = 0.2f)
                    },
                )
                .border(
                    width = 1.5.dp,
                    color = when {
                        selected -> MylifeColors.Gold
                        enabled -> Color.White.copy(alpha = 0.85f)
                        else -> Color.White.copy(alpha = 0.3f)
                    },
                    shape = RoundedCornerShape(4.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            if (selected) {
                Icon(
                    imageVector = Icons.Filled.Check,
                    contentDescription = null,
                    tint = MylifeColors.Abyss,
                    modifier = Modifier.size(14.dp),
                )
            }
        }
    }
}

// ---- Gallery 本地态提示（C1：4 行组件拷贝一份，与 TimelineScreen 私有实现解耦）----

@Composable
internal fun LoadingHint(text: String, modifier: Modifier = Modifier) {
    Box(modifier = modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        Text(text, style = MaterialTheme.typography.labelMedium, color = MylifeColors.Dust)
    }
}

@Composable
internal fun ErrorHint(message: String, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth().padding(Spacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(modifier = Modifier.weight(1f))
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MylifeColors.Red)
        Spacer(modifier = Modifier.height(Spacing.md))
        OutlinedButton(onClick = onRetry) {
            Text(stringResource(R.string.common_retry), color = MylifeColors.Gold)
        }
        Spacer(modifier = Modifier.weight(1f))
    }
}
