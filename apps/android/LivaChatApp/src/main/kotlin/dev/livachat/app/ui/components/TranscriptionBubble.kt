package dev.livachat.app.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import dev.livachat.app.ui.theme.BrandIndigo
import androidx.compose.ui.graphics.Color
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

@Composable
fun TranscriptionBubble(
    text: String,
    isFinal: Boolean,
    modifier: Modifier = Modifier,
    aiText: String? = null,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = Spacing.md),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Start,
    ) {
        Surface(
            modifier = Modifier,
            shape = RoundedCornerShape(12.dp),
            color = BrandIndigo.copy(alpha = 0.88f),
        ) {
            Column(
                modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.sm),
            ) {
                if (!aiText.isNullOrEmpty()) {
                    Surface(
                        color = Color.Black.copy(alpha = 0.85f),
                        shape = RoundedCornerShape(8.dp),
                        modifier = Modifier.padding(bottom = 4.dp),
                    ) {
                        Text(
                            text = aiText,
                            style = MaterialTheme.typography.bodyLarge,
                            color = TextPrimary,
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        )
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = text,
                        style = MaterialTheme.typography.bodyLarge,
                        color = TextPrimary,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    if (!isFinal) {
                        Spacer(modifier = Modifier.width(4.dp))
                        BouncingDots()
                    }
                }
            }
        }
    }
}

@Composable
private fun BouncingDots() {
    val transition = rememberInfiniteTransition(label = "dots")
    val dotCount = 3
    val dots = (0 until dotCount).map { i ->
        transition.animateFloat(
            initialValue = 0.3f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = tween(600, delayMillis = i * 200),
                repeatMode = RepeatMode.Reverse,
            ),
            label = "dot$i",
        )
    }

    Row {
        dots.forEach { alpha ->
            Text(
                text = ".",
                style = MaterialTheme.typography.bodyLarge,
                color = TextSecondary.copy(alpha = alpha.value),
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun TranscriptionPreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            Column(modifier = Modifier.padding(Spacing.md)) {
                TranscriptionBubble(
                    text = "我今天想聊聊关于",
                    isFinal = false,
                )
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun TranscriptionFinalPreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            Column(modifier = Modifier.padding(Spacing.md)) {
                TranscriptionBubble(
                    text = "你今天过得怎么样？",
                    isFinal = true,
                )
            }
        }
    }
}
