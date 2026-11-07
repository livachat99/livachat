package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.platform.LocalConfiguration
import dev.livachat.app.R
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.timeline.Granularity
import dev.livachat.app.ui.theme.Spacing

private val Granularity.labelRes: Int
    get() = when (this) {
        Granularity.DAY -> R.string.mylife_granularity_day
        Granularity.WEEK -> R.string.mylife_granularity_week
        Granularity.MONTH -> R.string.mylife_granularity_month
        Granularity.YEAR -> R.string.mylife_granularity_year
    }

/** 粒度选择器（§3.2）：segmented 4 选 1；选中以金色对勾标识（框架 icon 槽位，无底色高亮），未选 dust；切换清 scope（VM 内）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GranularitySelector(
    selected: Granularity,
    onSelect: (Granularity) -> Unit,
    modifier: Modifier = Modifier,
) {
    SingleChoiceSegmentedButtonRow(modifier = modifier.fillMaxWidth().padding(horizontal = Spacing.md)) {
        Granularity.entries.forEachIndexed { index, granularity ->
            SegmentedButton(
                selected = selected == granularity,
                onClick = { onSelect(granularity) },
                shape = SegmentedButtonDefaults.itemShape(index = index, count = Granularity.entries.size),
                colors = SegmentedButtonDefaults.colors(
                    activeContainerColor = MylifeColors.Panel.copy(alpha = 0.6f),
                    activeContentColor = MylifeColors.Dust,
                    inactiveContainerColor = MylifeColors.Panel.copy(alpha = 0.6f),
                    inactiveContentColor = MylifeColors.Dust,
                ),
                icon = {
                    SegmentedButtonDefaults.Icon(
                        active = selected == granularity,
                        activeContent = {
                            Icon(
                                imageVector = Icons.Filled.Check,
                                contentDescription = null,
                                modifier = Modifier.size(SegmentedButtonDefaults.IconSize),
                                tint = MylifeColors.Gold,
                            )
                        },
                        inactiveContent = { Spacer(modifier = Modifier.size(SegmentedButtonDefaults.IconSize)) },
                    )
                },
            ) {
                Text(stringResource(granularity.labelRes))
            }
        }
    }
}
