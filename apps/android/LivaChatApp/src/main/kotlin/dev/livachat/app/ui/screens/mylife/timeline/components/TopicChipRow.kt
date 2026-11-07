package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import dev.livachat.app.data.remote.mylife.dto.DayTopicDto
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing

/**
 * topic chip 组（docs/android_timeline_topics.md §3.1 视觉修订 / docs/bff_search_fts.md §10）：
 * FlowRow 自动换行。
 *
 * **可点与纯展示同款视觉 = 描边标签**（透明底 + `Line` 1dp 描边 + `Dust` 字，2026-09-21 定稿档）
 * ——金色语义只留 hot slot chip（金色药丸在日卡上太抢眼，2026-09-21 改回描边）。
 * 传入 `onTopicClick` 即在同一视觉上叠加点击（`Modifier.clickable`；不用 M3 `Surface`，避免引入
 * 48dp 最小触控强制把行高撑大——同 `HotSlotChips.kt` 先例）；不传则纯展示。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TopicChipRow(
    topics: List<DayTopicDto>,
    modifier: Modifier = Modifier,
    onTopicClick: ((DayTopicDto) -> Unit)? = null,
) {
    val shape = RoundedCornerShape(50)
    FlowRow(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(Spacing.xs),
        verticalArrangement = Arrangement.spacedBy(Spacing.xs),
    ) {
        topics.forEach { t ->
            val chip = Modifier.clip(shape).border(1.dp, MylifeColors.Line, shape)
            val tap = onTopicClick?.let { cb -> Modifier.clickable { cb(t) } } ?: Modifier
            Box(modifier = chip.then(tap)) {
                TopicChipText(topic = t.topic, color = MylifeColors.Dust)
            }
        }
    }
}

@Composable
private fun TopicChipText(topic: String, color: androidx.compose.ui.graphics.Color) {
    Text(
        text = topic,
        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
        style = MaterialTheme.typography.labelMedium,
        color = color,
    )
}
