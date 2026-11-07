package dev.livachat.app.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.Error
import dev.livachat.app.ui.theme.Info
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Success
import dev.livachat.app.ui.theme.TextSecondary
import dev.livachat.app.ui.theme.Warning
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState

enum class IndicatorState {
    IDLE, CONNECTING, LISTENING, THINKING, SPEAKING, DISCONNECTED
}

val IndicatorState.color: Color
    get() = when (this) {
        IndicatorState.IDLE -> Success
        IndicatorState.CONNECTING -> Warning
        IndicatorState.LISTENING -> Info
        IndicatorState.THINKING -> Warning
        IndicatorState.SPEAKING -> Success
        IndicatorState.DISCONNECTED -> Error
    }

/** 状态文案资源 id（SPEAKING 无文案 → null）；纯函数，供测试与 UI 层解析共用。 */
val IndicatorState.labelRes: Int?
    get() = when (this) {
        IndicatorState.IDLE -> R.string.conv_status_idle
        IndicatorState.CONNECTING -> R.string.conv_status_connecting
        IndicatorState.LISTENING -> R.string.conv_status_listening
        IndicatorState.THINKING -> R.string.conv_status_thinking
        IndicatorState.SPEAKING -> null
        IndicatorState.DISCONNECTED -> R.string.conv_status_disconnected
    }

val IndicatorState.label: String
    @Composable get() = labelRes?.let { stringResource(it) } ?: ""

fun SessionState.toIndicatorState(): IndicatorState = when (this) {
    SessionState.IDLE -> IndicatorState.IDLE
    SessionState.LISTENING -> IndicatorState.LISTENING
    SessionState.THINKING -> IndicatorState.THINKING
    SessionState.SPEAKING -> IndicatorState.SPEAKING
}

fun ConnectionState.toIndicatorState(): IndicatorState = when (this) {
    ConnectionState.CONNECTED -> IndicatorState.IDLE
    ConnectionState.CONNECTING -> IndicatorState.CONNECTING
    ConnectionState.RECONNECTING -> IndicatorState.CONNECTING
    ConnectionState.DISCONNECTED -> IndicatorState.DISCONNECTED
}

@Composable
fun StatusIndicator(
    state: IndicatorState,
    showLabel: Boolean = true,
    dotSize: Dp = 8.dp,
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
) {
    val dotColor by animateColorAsState(
        targetValue = state.color,
        animationSpec = tween(200),
        label = "dotColor",
    )

    Row(
        modifier = modifier.then(
            if (onClick != null) Modifier.clickable { onClick() } else Modifier,
        ),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        Box(
            modifier = Modifier
                .size(dotSize)
                .clip(CircleShape)
                .background(dotColor),
        )
        if (showLabel && state.label.isNotEmpty()) {
            Spacer(modifier = Modifier.width(Spacing.sm))
            Text(
                text = state.label,
                style = MaterialTheme.typography.labelLarge,
                color = TextSecondary,
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun StatusIndicatorPreview() {
    LivaChatTheme {
        Surface(color = dev.livachat.app.ui.theme.Background) {
            Column(
                modifier = Modifier.padding(Spacing.md),
                verticalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                IndicatorState.entries.forEach { state ->
                    StatusIndicator(state = state)
                }
            }
        }
    }
}
