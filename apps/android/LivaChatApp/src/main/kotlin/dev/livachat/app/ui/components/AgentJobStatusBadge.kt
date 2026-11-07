package dev.livachat.app.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Surface
import dev.livachat.app.ui.theme.TextPrimary

/**
 * Agent job status pill shown to the right of the avatar (C2/C3).
 * Visual recipe mirrors the translate badge: BrandIndigo capsule at 0.7 alpha,
 * 1.5dp Surface border, 14dp SmartToy icon in TextPrimary (D10).
 */
@Composable
fun AgentJobStatusBadge(
    count: Int,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = count > 0,
        enter = fadeIn(tween(200)) + scaleIn(tween(200)),
        exit = fadeOut(tween(150)),
        modifier = modifier,
    ) {
        // 28dp visual capsule inside a 48dp-high touch target (accessibility
        // minimum). heightIn only stretches the height — the capsule keeps its
        // exact position and size, no horizontal offset. The clickable lives on
        // the Box so the whole target responds; the capsule stays visual.
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier
                .heightIn(min = 48.dp)
                .clip(RoundedCornerShape(14.dp))
                .clickable(onClick = onClick),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .height(28.dp)
                    .clip(RoundedCornerShape(14.dp))
                    .background(BrandIndigo.copy(alpha = 0.7f))
                    .border(1.5.dp, Surface, RoundedCornerShape(14.dp))
                    .padding(horizontal = Spacing.sm),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Icon(
                    imageVector = Icons.Filled.SmartToy,
                    contentDescription = pluralStringResource(R.plurals.conv_agent_jobs_running, count, count),
                    tint = TextPrimary,
                    modifier = Modifier.size(14.dp),
                )
                Text(
                    text = if (count > 99) "99+" else count.toString(),
                    style = MaterialTheme.typography.labelMedium,
                    color = TextPrimary,
                )
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun AgentJobStatusBadgePreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            AgentJobStatusBadge(count = 3, onClick = {})
        }
    }
}
