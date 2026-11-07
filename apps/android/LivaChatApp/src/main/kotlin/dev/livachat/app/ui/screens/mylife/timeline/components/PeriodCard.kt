package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.painter.ColorPainter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.platform.LocalConfiguration
import coil.compose.AsyncImage
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.DayTopicDto
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.timeline.Granularity
import dev.livachat.app.ui.screens.mylife.timeline.HotSlot
import dev.livachat.app.ui.screens.mylife.timeline.PeriodDigest
import dev.livachat.app.ui.screens.mylife.timeline.PeriodImage
import dev.livachat.app.ui.theme.Spacing

/**
 * 统一时间范围卡片（§3.3）：标题行 + hot 时段 chips + summary + 图片区 + topics（2026-09-21 起 topics 居末）。
 * 只含三类内容；stats 徽章/counts 已随需求 4 废弃。
 * 本体点击（onBodyClick，null = 不可点）由调用方按粒度分派；chips 仅日卡非空
 * （docs/android_timeline_hotslot.md §4.2），点击 → hotSlotView，`digest.hasPending`
 * 随事件下传（新屏顶栏「分析中…」），不触达卡片本体。
 * topic chip：传入 onTopicClick 时可点（内层 chip 先消费点击，不触发卡片本体下钻）；
 * 不传则纯展示（视觉两态一致，见 TopicChipRow）。
 * 音频入口：传入 onAudioClick 且 `digest.audioCount > 0` 时显示（仅日卡非零），与 chips
 * 同行靠右 → AudioGallery（docs/android_timeline_audio_gallery.md §4.1）。
 */
@Composable
fun PeriodCard(
    digest: PeriodDigest,
    onBodyClick: (() -> Unit)?,
    onHotSlotClick: (HotSlot, Boolean) -> Unit,
    onImageClick: (PeriodImage) -> Unit,
    onTopicClick: ((DayTopicDto) -> Unit)? = null,
    onAudioClick: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val shape = RoundedCornerShape(12.dp)
    val surfaceModifier = modifier.fillMaxWidth().padding(horizontal = Spacing.md, vertical = Spacing.xs)
    val content: @Composable ColumnScope.() -> Unit = {
        if (digest.hasPending) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                PulsingDot()
                Spacer(modifier = Modifier.width(Spacing.sm))
                Text(stringResource(R.string.mylife_analyzing), style = MaterialTheme.typography.bodyMedium, color = MylifeColors.Dust)
            }
            Spacer(modifier = Modifier.height(Spacing.sm))
        }
        TitleRow(digest)
        // chips 行 + 音频入口同行靠右（docs/android_timeline_audio_gallery.md §4.1 A1）：
        // 当日无 hotSlot 但有音频时也要出行（否则入口无处可挂，Q4）；
        // Alignment.Top 保证 chips 折行（小屏/字体放大）时入口与首行对齐
        val audioClick = onAudioClick
        val hasAudioEntry = digest.audioCount > 0 && audioClick != null
        if (digest.hotSlots.isNotEmpty() || hasAudioEntry) {
            Spacer(modifier = Modifier.height(Spacing.sm))
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.Top,
            ) {
                if (digest.hotSlots.isNotEmpty()) {
                    HotSlotChips(
                        slots = digest.hotSlots,
                        onSlotClick = { onHotSlotClick(it, digest.hasPending) },
                        modifier = Modifier.weight(1f),
                    )
                } else {
                    Spacer(modifier = Modifier.weight(1f))
                }
                if (audioClick != null && digest.audioCount > 0) {
                    AudioEntryButton(count = digest.audioCount, onClick = audioClick)
                }
            }
        }
        SummaryBlock(digest)
        if (digest.images.isNotEmpty()) {
            val showCountBadge = digest.imageTotal > digest.images.size
            Spacer(modifier = Modifier.height(Spacing.sm))
            // 角标对格外露 10dp：仅显示角标时补足顶部间距，避免压到上一行文字（2026-09-20）
            if (showCountBadge) Spacer(modifier = Modifier.height(10.dp))
            // 缩略图网格（3 列；帧数按粒度 day3/week9/month12/year15，聚合器已轮转截断）
            // v3：图片可点击 → Gallery（日期范围 + focus 锚；docs/android_timeline_image_gallery.md R2）
            // 2026-09-20：实际帧数 > 卡片张数时，首行末图右上角显示计数徽标（数字 = 真实帧总数）；
            // 角标对格外露（右 +10dp / 上 −10dp，外露量恒定 < 卡片内边距 16dp 不会被圆角裁切），
            // 故角标整体可点（外露部分落在卡片内边距上，否则点击无反应）
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
                digest.images.chunked(3).forEachIndexed { rowIndex, rowImages ->
                    Row(horizontalArrangement = Arrangement.spacedBy(Spacing.xs)) {
                        rowImages.forEachIndexed { indexInRow, img ->
                            Box(modifier = Modifier.weight(1f)) {
                                AsyncImage(
                                    model = img.url,
                                    contentDescription = null,
                                    contentScale = ContentScale.Crop,
                                    // 占位/错误态（§4-7「方图灰底」）：加载失败不再是 Abyss 色空洞
                                    placeholder = ColorPainter(MylifeColors.Panel),
                                    error = ColorPainter(MylifeColors.Panel),
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .aspectRatio(1f)
                                        .clip(RoundedCornerShape(8.dp))
                                        .clickable { onImageClick(img) },
                                )
                                if (showCountBadge && rowIndex == 0 && indexInRow == rowImages.lastIndex) {
                                    Text(
                                        text = "${digest.imageTotal}",
                                        color = MylifeColors.Star,
                                        style = MaterialTheme.typography.labelSmall,
                                        modifier = Modifier
                                            .align(Alignment.TopEnd)
                                            .offset(x = 10.dp, y = (-10).dp)
                                            .clickable { onImageClick(img) }
                                            .background(
                                                Color.Black.copy(alpha = 0.6f),
                                                RoundedCornerShape(50),
                                            )
                                            .padding(horizontal = 6.dp, vertical = 1.dp),
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
        if (digest.topics.isNotEmpty()) {
            // 与上一区块（图片区/summary）间距 = sm：实机反馈 xs(4dp) 太贴（2026-09-21）
            Spacer(modifier = Modifier.height(Spacing.sm))
            // chip 组（2026-09-21 修订：置于图片区之后；视觉见 TopicChipRow）
            TopicChipRow(topics = digest.topics, onTopicClick = onTopicClick)
        }
    }
    val cardColor = MylifeColors.Panel.copy(alpha = 0.6f)
    if (onBodyClick != null) {
        Surface(onClick = onBodyClick, shape = shape, color = cardColor, modifier = surfaceModifier) {
            Column(modifier = Modifier.padding(Spacing.md)) { content() }
        }
    } else {
        Surface(shape = shape, color = cardColor, modifier = surfaceModifier) {
            Column(modifier = Modifier.padding(Spacing.md)) { content() }
        }
    }
}

/**
 * summary 块（§3.3）：日卡全部显示（不限行数、不折叠）；
 * 周/月/年卡默认显示 summaryLimit 条（每条 2 行截断），超出 "+N 条" 折叠展开。
 */
@Composable
private fun ColumnScope.SummaryBlock(digest: PeriodDigest) {
    // key 用 digest.key：periods 重聚合产生新实例时保留展开状态
    var expanded by remember(digest.key) { mutableStateOf(false) }
    val showAll = digest.granularity == Granularity.DAY || expanded
    val visible = if (showAll) digest.summaryLines else digest.summaryLines.take(digest.summaryLimit)
    visible.forEach { line ->
        Spacer(modifier = Modifier.height(Spacing.xs))
        Text(
            text = line,
            style = MaterialTheme.typography.bodyMedium,
            color = MylifeColors.Star,
            maxLines = if (showAll) Int.MAX_VALUE else 2,
            overflow = TextOverflow.Ellipsis,
            modifier = if (digest.granularity == Granularity.DAY) Modifier
            else Modifier.clickable { expanded = !expanded },
        )
    }
    val hidden = digest.summaryLines.size - digest.summaryLimit
    if (digest.granularity != Granularity.DAY && hidden > 0 && !expanded) {
        Spacer(modifier = Modifier.height(Spacing.xs))
        Text(
            text = stringResource(R.string.mylife_more_items, hidden),
            style = MaterialTheme.typography.labelMedium,
            color = MylifeColors.Gold,
            modifier = Modifier.clickable { expanded = true },
        )
    }
}

/** 标题行（§3.3）：日带星期几；周右侧小字"第33周"；月/年范围标签。 */
@Composable
private fun TitleRow(digest: PeriodDigest) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = digest.rangeLabel,
            style = MaterialTheme.typography.titleMedium,
            color = MylifeColors.Star,
        )
        if (digest.granularity == Granularity.WEEK) {
            Spacer(modifier = Modifier.width(Spacing.sm))
            Text(
                text = stringResource(R.string.mylife_week_label, digest.key.substringAfter("-W").toIntOrNull() ?: 0),
                style = MaterialTheme.typography.labelMedium,
                color = MylifeColors.Dust,
            )
        }
    }
}
