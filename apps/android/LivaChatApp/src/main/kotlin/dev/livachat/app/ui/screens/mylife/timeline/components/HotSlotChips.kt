package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LocalMinimumInteractiveComponentSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.timeline.HotSlot
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.util.localizeDigits
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val TIME_FORMAT = DateTimeFormatter.ofPattern("HH:mm")

/**
 * hot 时段 chip 行（docs/android_timeline_hotslot.md §4.2）：仅日卡展示，标签只含开始时间
 * `▸ 09:12`；点击 → hotSlotView（消费事件，不触达卡片本体）。
 * FlowRow：多个 slot 一行放不下时自动换行（Row 单行会溢出/压缩）。
 */
@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
fun HotSlotChips(
    slots: List<HotSlot>,
    onSlotClick: (HotSlot) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (slots.isEmpty()) return
    val locale = LocalConfiguration.current.locales[0]
    FlowRow(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(Spacing.xs),
        // 行距与 topic chip 行完全一致（xs + 2dp 垂直 padding）
        verticalArrangement = Arrangement.spacedBy(Spacing.xs),
    ) {
        slots.forEach { slot ->
            val label = formatStart(slot, locale)
            // 可点击 Surface 默认强制 48dp 最小触控高度（Material3），会把 chip 撑高、
            // 放大行距观感；关闭后 chip 高度回到内容高度，与 topic chip 视觉一致
            CompositionLocalProvider(LocalMinimumInteractiveComponentSize provides Dp.Unspecified) {
                Surface(
                    onClick = { onSlotClick(slot) },
                    shape = RoundedCornerShape(50),
                    color = MylifeColors.Panel.copy(alpha = 0.8f),
                    contentColor = MylifeColors.Gold,
                    modifier = Modifier,
                ) {
                    Text(
                        text = "▸ $label",
                        modifier = Modifier.padding(horizontal = Spacing.sm, vertical = 2.dp),
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
            }
        }
    }
}

/** 只显示开始时间（§4.2 Q5 同口径）；设备本地时区，绝对 epoch ms。internal 供单测直取；数字经 [localizeDigits]（§20 缺口⑤）。 */
internal fun formatStart(slot: HotSlot, locale: Locale): String =
    localizeDigits(
        TIME_FORMAT.format(Instant.ofEpochMilli(slot.startMs).atZone(ZoneId.systemDefault()).toLocalTime()),
        locale,
    )
