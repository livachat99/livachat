package dev.livachat.app.ui.components

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.SurfaceVariant

@Composable
fun LoadingSkeleton(
    lineCount: Int = 3,
    modifier: Modifier = Modifier,
) {
    val transition = rememberInfiniteTransition(label = "shimmer")
    val shimmerOffset by transition.animateFloat(
        initialValue = -1f,
        targetValue = 2f,
        animationSpec = infiniteRepeatable(
            animation = tween(1200, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "shimmerOffset",
    )

    val shimmerBrush = Brush.linearGradient(
        colors = listOf(
            SurfaceVariant.copy(alpha = 0.3f),
            SurfaceVariant.copy(alpha = 0.6f),
            SurfaceVariant.copy(alpha = 0.3f),
        ),
        start = Offset(shimmerOffset * 1000f, 0f),
        end = Offset(shimmerOffset * 1000f + 500f, 0f),
    )

    Column(modifier = modifier.padding(Spacing.md)) {
        repeat(lineCount) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = Spacing.sm),
            ) {
                Box(
                    modifier = Modifier
                        .width(40.dp)
                        .height(40.dp)
                        .clip(RoundedCornerShape(20.dp))
                        .background(shimmerBrush),
                )
                Spacer(modifier = Modifier.width(Spacing.sm))
                Column(modifier = Modifier.weight(1f)) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth(if (it % 3 == 0) 0.7f else 0.9f)
                            .height(14.dp)
                            .clip(RoundedCornerShape(7.dp))
                            .background(shimmerBrush),
                    )
                    Spacer(modifier = Modifier.height(Spacing.xs))
                    Box(
                        modifier = Modifier
                            .fillMaxWidth(0.5f)
                            .height(12.dp)
                            .clip(RoundedCornerShape(6.dp))
                            .background(shimmerBrush),
                    )
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun LoadingSkeletonPreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            LoadingSkeleton(lineCount = 5)
        }
    }
}
