package dev.livachat.app.mock.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState

@Composable
fun StatusIndicator(
    connectionState: ConnectionState,
    sessionState: SessionState,
    modifier: Modifier = Modifier,
) {
    val color by animateColorAsState(
        targetValue = when (connectionState) {
            ConnectionState.CONNECTED -> Color(0xFF4CAF50)
            ConnectionState.CONNECTING, ConnectionState.RECONNECTING -> Color(0xFFFFC107)
            ConnectionState.DISCONNECTED -> Color(0xFFF44336)
        },
        label = "statusColor",
    )

    Row(
        modifier = modifier.padding(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(12.dp)
                .clip(CircleShape)
                .background(color)
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text = when (sessionState) {
                SessionState.IDLE -> "Idle"
                SessionState.LISTENING -> "Listening"
                SessionState.THINKING -> "Thinking"
                SessionState.SPEAKING -> "Speaking"
            },
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
