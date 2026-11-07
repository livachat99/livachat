package dev.livachat.app.ui.screens.mylife.audiogallery.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing

/** 箭头字形（§9.2 定案图 `mock_jump_B.png`；若改 ASCII `>>` 需同步 §9 文档）。 */
internal const val TIME_JUMP_ARROW = "››"

/**
 * 段的时间头（docs/android_timeline_audio_gallery.md §9，定案候选 **B 药丸**）：
 * 「时间 + `››`」整组一个点击目标，点击 → eventListViewer（该时刻 ±10 分钟）。
 *
 * - 不用 M3 `Surface`（会强制 48dp 最小触控、撑高行距）——`clip` + `clickable`（同 `AudioEntryButton`/
 *   `TopicChipRow` 的取舍）；**触控带**（外层 `Box`：`heightIn(min = 32.dp)` + `clickable` 之后的
 *   `padding(horizontal = xs)`）比视觉药丸更高更宽，视觉药丸尺寸不受影响。
 * - `onTimeClick == null` = 纯展示（视觉同款、无点击/无语义），对齐 topic chip 的两态一致口径。
 */
@Composable
internal fun AudioTimeHeader(
    label: String,
    modifier: Modifier = Modifier,
    onTimeClick: (() -> Unit)? = null,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(top = Spacing.sm, bottom = Spacing.xs),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // 触控带（≥32dp 高、比视觉药丸左右各宽 4dp）：clickable 在 padding 之前 ⇒ 这段 padding
        // 计入触控区；背景留在内层 Row，视觉药丸尺寸不变（§9.2 触控要求）
        val timeDesc = stringResource(R.string.mylife_audio_time_desc, label)
        Box(
            modifier = Modifier
                .heightIn(min = 32.dp)
                .clip(RoundedCornerShape(50))
                .then(
                    if (onTimeClick != null) {
                        Modifier
                            .clickable(onClick = onTimeClick, role = Role.Button)
                            .semantics(mergeDescendants = true) {
                                contentDescription = timeDesc
                            }
                    } else {
                        Modifier
                    },
                )
                .padding(horizontal = Spacing.xs),
            contentAlignment = Alignment.Center,
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .clip(RoundedCornerShape(50))
                    .background(MylifeColors.Panel.copy(alpha = 0.8f))
                    .padding(horizontal = Spacing.sm, vertical = 2.dp),
            ) {
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelMedium,
                    color = MylifeColors.Dust,
                )
                Spacer(modifier = Modifier.width(Spacing.xs))
                Text(
                    text = TIME_JUMP_ARROW,
                    style = MaterialTheme.typography.labelMedium,
                    color = MylifeColors.Gold,
                )
            }
        }
    }
}
