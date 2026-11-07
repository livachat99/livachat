package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.platform.LocalConfiguration
import dev.livachat.app.ui.screens.mylife.timeline.monthDayText
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.SearchHitDto
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.util.localizeDigits
import java.util.Locale

internal data class HitGroup(val date: String, val hits: List<SearchHitDto>)

/**
 * 按 date 聚合（保持 hit 到达顺序）——BFF 已改为 bm25 相关度序，同日命中不再相邻，
 * 旧「连续同日分组」会产出重复日头。docs/bff_search_fts.md §3.4 A1。
 */
internal fun groupHits(hits: List<SearchHitDto>): List<HitGroup> {
    val groups = LinkedHashMap<String, MutableList<SearchHitDto>>()
    for (h in hits) {
        groups.getOrPut(h.date) { mutableListOf() }.add(h)
    }
    return groups.map { (date, list) -> HitGroup(date, list) }
}

/** 搜索结果列表（§3.4）：日头 + hit 行（类型图标 + 时间 + snippet 高亮）；滚动到底 loadMore。 */
@Composable
fun SearchResultList(
    hits: List<SearchHitDto>,
    total: Int,
    query: String,
    onHitClick: (SearchHitDto) -> Unit,
    onLoadMore: () -> Unit,
    modifier: Modifier = Modifier,
    listState: LazyListState = rememberLazyListState(),
) {
    val groups = remember(hits) { groupHits(hits) }
    // itemCount = 日头 + hit 行总数（单位与 LazyColumn item 下标一致）。
    // remember(itemCount) 键：翻页追加后 groups 重算 → itemCount 变化 → 重建 derivedStateOf，
    // 避免无 key remember 闭包捕获首帧旧 groups 导致阈值永不更新（陈旧捕获）。
    // 阈值距底部 3 个 item 触发（对齐滚动到底 loadMore 语义，§3.4）。
    val itemCount = groups.sumOf { it.hits.size + 1 }
    val shouldLoadMore by remember(itemCount) {
        derivedStateOf {
            val lastVisible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
            itemCount > 0 && lastVisible >= itemCount - 3
        }
    }
    LaunchedEffect(shouldLoadMore) {
        if (shouldLoadMore && groups.isNotEmpty()) onLoadMore()
    }
    Column(modifier = Modifier.fillMaxSize()) {
        Text(
            text = pluralStringResource(R.plurals.mylife_search_total, total, total),
            modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.xs),
            style = MaterialTheme.typography.labelMedium,
            color = MylifeColors.Dust,
        )
        // modifier（含下拉刷新 nestedScroll）作用在 LazyColumn 上才能接手势
        LazyColumn(state = listState, modifier = Modifier.fillMaxSize().then(modifier)) {
            groups.forEach { group ->
                item(key = "date-${group.date}") {
                    DayHeaderLabel(group.date)
                }
                group.hits.forEach { hit ->
                    // kind-date-tsMs-snippet 复合键（§0-R10 + P6-a）：去 sessionId 后同日同 session
                    // 多 topic 命中 date+tsMs 全同，加 kind 提高区分度
                    item(key = "hit-${hit.kind}-${hit.date}-${hit.tsMs}-${hit.snippet.hashCode()}") {
                        HitRow(hit = hit, query = query, onClick = { onHitClick(hit) })
                    }
                }
            }
        }
    }
}

@Composable
private fun DayHeaderLabel(date: String) {
    val locale = LocalConfiguration.current.locales[0]
    val label = date.substringAfter('-').let {
        monthDayText(it.substringBefore('-').toInt(), it.substringAfter('-').toInt(), locale)
    }
    Text(
        text = label,
        modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.xs),
        style = MaterialTheme.typography.titleMedium,
        color = MylifeColors.Star,
    )
}

@Composable
private fun HitRow(hit: SearchHitDto, query: String, onClick: () -> Unit) {
    val locale = LocalConfiguration.current.locales[0]
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = Spacing.md, vertical = Spacing.sm),
        verticalAlignment = Alignment.Top,
    ) {
        Text(kindIcon(hit.kind), style = MaterialTheme.typography.bodyMedium)
        Spacer(modifier = Modifier.width(Spacing.sm))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = formatTime(hit.tsMs, locale),
                style = MaterialTheme.typography.labelMedium,
                color = MylifeColors.Dust,
            )
            Text(
                text = highlight(hit.snippet, query),
                style = MaterialTheme.typography.bodyMedium,
                color = MylifeColors.Star,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** 命中子串 gold 高亮（§3.4）；查询词由 hit 携带时优先，否则退化无高亮。 */
private fun highlight(snippet: String, query: String): AnnotatedString {
    if (query.isBlank()) return AnnotatedString(snippet)
    val idx = snippet.indexOf(query, ignoreCase = true)
    if (idx < 0) return AnnotatedString(snippet)
    return buildAnnotatedString {
        append(snippet.substring(0, idx))
        withStyle(SpanStyle(color = MylifeColors.Gold)) {
            append(snippet.substring(idx, idx + query.length))
        }
        append(snippet.substring(idx + query.length))
    }
}

/** kind 集合 = 可搜=可展示五类（docs/bff_search_fts.md）；未知 kind（旧服务端）兜底 📍。 */
private fun kindIcon(kind: String): String = when (kind) {
    "chat" -> "💬"
    "transcribe" -> "📄"
    "translate" -> "🌐"
    "agent_job" -> "🤖"
    "video" -> "🖼️"
    else -> "📍"
}

private val TIME_FORMAT = java.time.format.DateTimeFormatter.ofPattern("HH:mm")

private fun formatTime(tsMs: Long?, locale: Locale): String {
    if (tsMs == null) return ""
    val time = java.time.Instant.ofEpochMilli(tsMs)
        .atZone(java.time.ZoneId.systemDefault()).toLocalTime()
    return localizeDigits(TIME_FORMAT.format(time), locale)
}
