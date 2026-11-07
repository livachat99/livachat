package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.platform.LocalConfiguration
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing

/** 按 YYYY-MM 分组（保持输入顺序），对齐 Web groupByMonth。 */
fun groupByMonth(days: List<OverviewDayDto>): List<Pair<String, List<OverviewDayDto>>> {
    val groups = ArrayList<Pair<String, List<OverviewDayDto>>>()
    for (d in days) {
        val m = d.date.take(7)
        if (groups.isNotEmpty() && groups.last().first == m) {
            groups[groups.size - 1] = m to groups.last().second + d
        } else {
            groups.add(m to listOf(d))
        }
    }
    return groups
}

/** 年月大字分隔（headlineMedium, gold/70%），对齐 Web `{y}年 · {m}月`。 */
@Composable
fun MonthHeader(month: String, modifier: Modifier = Modifier) {
    val (y, m) = month.split('-').let { it[0] to it.getOrElse(1) { "" } }
    val yInt = y.toIntOrNull()
    val mInt = m.toIntOrNull()
    val locale = LocalConfiguration.current.locales[0]
    Text(
        text = if (yInt != null && mInt != null) {
            val monthPart = when (locale.language) {
                "zh" -> "${mInt}月"
                // ru：standalone 主格全称「август」，与 MylifeDateLabels.monthYearText 同源（§11 缺口②）
                "ru" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL_STANDALONE, locale)
                // de：全称「August」，与 MylifeDateLabels.monthYearText 同源（§12 缺口②）
                "de" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // fr：全称小写「janvier」，与 MylifeDateLabels.monthYearText 同源（§13 缺口②）
                "fr" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // it：全称小写「agosto」，与 MylifeDateLabels.monthYearText 同源（§14 缺口②）
                "it" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // es：全称小写「agosto」，与 MylifeDateLabels.monthYearText 同源（§15 缺口②；de 由资源模板承载）
                "es" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // pt：全称小写「agosto」，与 MylifeDateLabels.monthYearText 同源（§16 缺口②；de 由资源模板承载）
                "pt" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // tr：全称「Ağustos」，与 MylifeDateLabels.monthYearText 同源（§17 缺口②）
                "tr" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // hi：全称「अगस्त」，与 MylifeDateLabels.monthYearText 同源（§18 缺口②）
                "hi" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // id/in：全称「Agustus」，与 MylifeDateLabels.monthYearText 同源（§19 缺口② 双键）
                "id", "in" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // th：全称「สิงหาคม」，与 MylifeDateLabels.monthYearText 同源（§21）
                "th" -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.FULL, locale)
                // vi：standalone 首字母大写「Tháng 8」，与 MylifeDateLabels.monthYearText 同源（§21）
                "vi" -> java.time.Month.of(mInt).getDisplayName(
                    java.time.format.TextStyle.FULL_STANDALONE,
                    locale,
                )
                else -> java.time.Month.of(mInt).getDisplayName(java.time.format.TextStyle.SHORT, locale)
            }
            stringResource(R.string.mylife_overview_month, yInt, monthPart)
        } else {
            "${y}年 · ${m}月"
        },
        modifier = modifier.padding(horizontal = Spacing.md, vertical = Spacing.sm),
        style = MaterialTheme.typography.headlineMedium,
        color = MylifeColors.Gold.copy(alpha = 0.7f),
    )
}

/** 分析中…脉冲点（对齐 Web animate-pulse），pending 卡片顶部复用（§3.3）。 */
@Composable
fun PulsingDot(modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "pulse")
    val alpha by transition.animateFloat(
        initialValue = 0.3f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(600),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulseAlpha",
    )
    Box(
        modifier = modifier
            .width(8.dp)
            .height(8.dp)
            .clip(RoundedCornerShape(50))
            .background(MylifeColors.Gold)
            .alpha(alpha),
    )
}
