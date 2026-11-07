package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.platform.LocalConfiguration
import dev.livachat.app.R
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing

/**
 * 日卡音频入口（docs/android_timeline_audio_gallery.md §4.1 A3，定案图 `mock_daycard_A1.png`）：
 * 圆形 Panel 底 + Gold `GraphicEq` + 段数文本（Dust）。
 *
 * 不用 M3 `Surface`/`IconButton`（会引入 48dp 最小触控强制、撑高 chips 行）——同 `TopicChipRow`
 * 与 `HotSlotChips` 的既有取舍；整行可点，TalkBack 读「语音记录，N 段」。
 */
@Composable
fun AudioEntryButton(
    count: Int,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val audioDesc = pluralStringResource(R.plurals.mylife_audio_entry_desc, count, count)
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .clip(RoundedCornerShape(50))
            .clickable(onClick = onClick)
            .padding(horizontal = Spacing.xs)
            .semantics(mergeDescendants = true) { contentDescription = audioDesc },
    ) {
        Box(
            modifier = Modifier
                .clip(CircleShape)
                .background(MylifeColors.Panel.copy(alpha = 0.8f))
                .padding(horizontal = Spacing.sm, vertical = 3.dp),
        ) {
            Icon(
                imageVector = Icons.Filled.GraphicEq,
                contentDescription = null,
                tint = MylifeColors.Gold,
                modifier = Modifier.size(18.dp),
            )
        }
        Text(
            text = "$count",
            style = MaterialTheme.typography.labelMedium,
            color = MylifeColors.Dust,
            modifier = Modifier.padding(start = Spacing.xs),
        )
    }
}
