package dev.livachat.app.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.Error
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.Warning
import kotlinx.coroutines.delay

data class BannerMessage(
    val text: String,
    val recoverable: Boolean = true,
    val id: String = java.util.UUID.randomUUID().toString(),
)

@Composable
fun ErrorBanner(
    message: BannerMessage?,
    onDismiss: () -> Unit,
    onRetry: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = message != null,
        enter = slideInVertically(initialOffsetY = { -it }) + fadeIn(),
        exit = slideOutVertically(targetOffsetY = { -it }) + fadeOut(),
        modifier = modifier,
    ) {
        message?.let { msg ->
            if (msg.recoverable) {
                LaunchedEffect(msg.id) {
                    delay(5000)
                    onDismiss()
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        if (msg.recoverable) Warning else Error,
                        MaterialTheme.shapes.medium,
                    )
                    .padding(Spacing.sm, Spacing.md),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    imageVector = Icons.Filled.Warning,
                    contentDescription = null,
                    tint = if (msg.recoverable) TextPrimary else TextPrimary,
                    modifier = Modifier.padding(start = Spacing.sm),
                )
                Spacer(modifier = Modifier.width(Spacing.sm))
                Text(
                    text = msg.text,
                    style = MaterialTheme.typography.labelLarge,
                    color = TextPrimary,
                    modifier = Modifier.weight(1f),
                )
                if (msg.recoverable && onRetry != null) {
                    IconButton(onClick = onRetry) {
                        Icon(
                            imageVector = Icons.Filled.Refresh,
                            contentDescription = stringResource(R.string.common_retry),
                            tint = TextPrimary,
                        )
                    }
                }
                IconButton(onClick = onDismiss) {
                    Icon(
                        imageVector = Icons.Filled.Close,
                        contentDescription = stringResource(R.string.common_close),
                        tint = TextPrimary,
                    )
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun ErrorBannerRecoverablePreview() {
    LivaChatTheme {
        Column {
            ErrorBanner(
                message = BannerMessage("网络连接不稳定，正在重试...", recoverable = true),
                onDismiss = {},
                onRetry = {},
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun ErrorBannerFatalPreview() {
    LivaChatTheme {
        Column {
            ErrorBanner(
                message = BannerMessage("服务器错误，请稍后再试", recoverable = false),
                onDismiss = {},
            )
        }
    }
}
