package dev.livachat.app.ui.screens.mylife.hotslot.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.painter.ColorPainter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import coil.compose.AsyncImage
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent
import dev.livachat.app.ui.components.AgentJobResultText
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.hotslot.BubbleSide
import dev.livachat.app.ui.screens.mylife.hotslot.HotSlotRow
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

/**
 * 帧行（docs/android_timeline_hotslot.md §4.3）：≤3 张固定 1/3 宽、左对齐，
 * 末行不足 3 张以等宽占位撑满（区别于 PeriodCard 的 weight 拉伸，与 Gallery 3 列一致）；
 * 单张可点 → 全屏查看器（§9）。
 */
@Composable
fun FrameRowItem(
    frames: List<HotSlotEvent>,
    onFrameClick: (HotSlotEvent) -> Unit = {},
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier.fillMaxWidth().padding(vertical = Spacing.xs),
        horizontalArrangement = Arrangement.spacedBy(Spacing.xs),
    ) {
        frames.forEachIndexed { index, frame ->
            Box(
                modifier = Modifier
                    .weight(1f)
                    // clip 在 clickable 前 → 涟漪受 8dp 圆角约束（对齐 gallery FrameGrid）
                    .clip(RoundedCornerShape(8.dp))
                    .clickable { onFrameClick(frame) }
                    .testTag("hotSlotFrame$index"),
            ) {
                AsyncImage(
                    model = frame.mediaUrl,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    placeholder = ColorPainter(MylifeColors.Panel),
                    error = ColorPainter(MylifeColors.Panel),
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(1f),
                )
            }
        }
        repeat((3 - frames.size).coerceAtLeast(0)) {
            Spacer(modifier = Modifier.weight(1f))
        }
    }
}

/**
 * 行唯一入口（docs/android_timeline_hotslot.md §10.3 ㉟）：可选分钟分隔标签（T1 居中）→
 * `Text` 行气泡 / `Frames` 转调 [FrameRowItem]。两屏共用；帧行组件因此保持零改动。
 * `onLinkClick`：null = 正文纯 `Text`；非 null 时 **agent_job 行**走 Markdown（§12 R1=A/R4=A）。
 * `onAgentJobClick`：非 null 时 agent_job 行整行可点（→ 全屏查看，docs/android_agent_job_view.md §12.3.3），
 * 并附带该行正文的折叠与「还有 N 字」入口行；其它行零变化。
 */
@Composable
fun HotSlotRowItem(
    row: HotSlotRow,
    onFrameClick: (HotSlotEvent) -> Unit = {},
    onLinkClick: ((String) -> Unit)? = null,
    onAgentJobClick: ((rowKey: String, charOffset: Int?) -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth()) {
        row.timeLabel?.let { MinuteDivider(it) }
        when (row) {
            is HotSlotRow.Text -> BubbleRowItem(row, onLinkClick, onAgentJobClick)
            is HotSlotRow.Frames -> FrameRowItem(frames = row.frames, onFrameClick = onFrameClick)
        }
    }
}

/**
 * 分钟分隔行（T1，§10.3）：居中 `HH:mm`，气泡**之外**——屏底色 / MyLife 板（配色边界见 §10.3）。
 */
@Composable
private fun MinuteDivider(label: String) {
    Text(
        text = label,
        style = MaterialTheme.typography.labelMedium,
        color = MylifeColors.Dust,
        textAlign = TextAlign.Center,
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = Spacing.sm, bottom = Spacing.xs),
    )
}

/**
 * 文本气泡（§10.3 D1=C）：结构规格照搬 `MessageBubble`（对齐/圆角/最大宽度/内边距/正文），
 * 配色用全局 LivaChatTheme——用户侧 `BrandIndigo` 实底 + `TextPrimary`；
 * AI/Agent 侧 `surface` + `SurfaceVariant` 描边 + `onSurface`；Agent 头行 = SmartToy + 「Agent任务」。
 */
@Composable
private fun BubbleRowItem(
    row: HotSlotRow.Text,
    onLinkClick: ((String) -> Unit)?,
    onAgentJobClick: ((rowKey: String, charOffset: Int?) -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val isUser = row.side == BubbleSide.USER
    // agent 行才可点（§12.3.3）；偏移 state 住在行组件内（共享文本块只经 onContentTap 上报，§12.3.1）
    val clickableAsAgentJob = row.side == BubbleSide.AGENT && onAgentJobClick != null
    var tappedCharOffset by remember { mutableStateOf<Int?>(null) }
    val maxWidth = LocalConfiguration.current.screenWidthDp.dp * 0.75f
    val shape = if (isUser) {
        RoundedCornerShape(16.dp, 16.dp, 16.dp, 8.dp)
    } else {
        RoundedCornerShape(16.dp, 16.dp, 8.dp, 16.dp)
    }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        if (isUser) Spacer(modifier = Modifier.weight(1f))

        Box(
            modifier = Modifier
                .widthIn(max = maxWidth)
                .clip(shape)
                .then(
                    if (isUser) {
                        Modifier.background(BrandIndigo)
                    } else {
                        Modifier
                            .background(MaterialTheme.colorScheme.surface)
                            .border(1.dp, MaterialTheme.colorScheme.surfaceVariant, shape)
                    },
                )
                // 整行可点（仅 agent job 结果行）：涟漪受圆角约束（对齐会话气泡 §12.3.3）
                .then(
                    if (clickableAsAgentJob) {
                        Modifier.clickable { onAgentJobClick!!(row.key, tappedCharOffset) }
                    } else {
                        Modifier
                    },
                )
                .padding(horizontal = Spacing.md, vertical = Spacing.sm),
        ) {
            Column {
                if (row.side == BubbleSide.AGENT) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(bottom = Spacing.xs),
                    ) {
                        Icon(
                            imageVector = Icons.Filled.SmartToy,
                            contentDescription = "Agent",
                            tint = TextSecondary,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(
                            text = stringResource(R.string.mylife_agent_task),
                            style = MaterialTheme.typography.labelMedium,
                            color = TextSecondary,
                        )
                        if (clickableAsAgentJob) {
                            Spacer(modifier = Modifier.width(Spacing.sm))
                            Icon(
                                imageVector = Icons.Filled.OpenInFull,
                                contentDescription = stringResource(R.string.common_view_full_result),
                                tint = TextSecondary,
                                modifier = Modifier.size(14.dp),
                            )
                        }
                    }
                }
                // §12 R1=A：仅 agent_job 行 Markdown 化（R4=A：无回调时回退纯文本）
                if (row.side == BubbleSide.AGENT && onLinkClick != null) {
                    // 共享折叠文本块（§12.3.1）：含表格的 body 会自动给整宽（规避 Markwon 表格
                    // 0 宽首测列宽坍缩，2026-09-22 真机实证）；非 agent 行不折叠
                    AgentJobResultText(
                        markdown = row.body,
                        onLinkClick = onLinkClick,
                        collapsible = clickableAsAgentJob,
                        onContentTap = if (clickableAsAgentJob) {
                            { offset -> tappedCharOffset = offset }
                        } else {
                            null
                        },
                    )
                } else {
                    Text(
                        text = row.body,
                        style = MaterialTheme.typography.bodyLarge,
                        color = if (isUser) TextPrimary else MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        }

        if (!isUser) Spacer(modifier = Modifier.weight(1f))
    }
}

// ---- hotSlotView 本地态提示（对齐 Gallery C1：拷贝一份与 TimelineScreen 私有实现解耦）----

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
        OutlinedButton(onClick = onRetry) { Text(stringResource(R.string.common_retry), color = MylifeColors.Gold) }
        Spacer(modifier = Modifier.weight(1f))
    }
}
