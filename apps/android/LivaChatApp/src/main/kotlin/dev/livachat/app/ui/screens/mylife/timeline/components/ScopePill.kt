package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.platform.LocalConfiguration
import dev.livachat.app.ui.screens.mylife.timeline.yearText
import dev.livachat.app.ui.screens.mylife.timeline.monthYearText
import dev.livachat.app.R
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing

/**
 * 下钻范围指示（§3.3.2）："只看 2026年8月 ✕"，点击清除恢复该粒度全量。
 */
@Composable
fun ScopePill(scopeKey: String, onClear: () -> Unit, modifier: Modifier = Modifier) {
    val locale = LocalConfiguration.current.locales[0]
    val label = when {
        scopeKey.length == 4 -> yearText(scopeKey.toInt(), locale)
        scopeKey.length == 7 -> monthYearText(scopeKey.take(4).toInt(), scopeKey.substringAfter('-').toInt(), locale)
        else -> scopeKey
    }
    Surface(
        onClick = onClear,
        shape = RoundedCornerShape(50),
        color = MylifeColors.Gold.copy(alpha = 0.9f),
        modifier = modifier,
    ) {
        Text(
            text = stringResource(R.string.mylife_scope_filter, label),
            modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.sm),
            style = MaterialTheme.typography.labelMedium,
            color = MylifeColors.Abyss,
        )
    }
}
