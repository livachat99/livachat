package dev.livachat.app.ui.components

import android.view.ViewGroup
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cameraswitch
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.draw.clip
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import dev.livachat.app.R
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextPrimary

@Composable
fun VideoPreview(
    isFrontCamera: Boolean,
    onSwitchCamera: () -> Unit,
    onPreviewViewCreated: (PreviewView) -> Unit = {},
    transcription: String = "",
    isTranscriptionFinal: Boolean = false,
    lastAiText: String = "",
    modifier: Modifier = Modifier,
) {
    BoxWithConstraints(modifier = modifier.fillMaxSize()) {
        val maxAiHeight = maxHeight * 2 / 3

        Box(modifier = Modifier.fillMaxSize()) {
            // Camera preview
            AndroidView(
                factory = { ctx ->
                    PreviewView(ctx).apply {
                        layoutParams = ViewGroup.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT,
                        )
                        scaleType = PreviewView.ScaleType.FILL_CENTER
                        implementationMode = PreviewView.ImplementationMode.COMPATIBLE
                    }.also { onPreviewViewCreated(it) }
                },
                modifier = Modifier.fillMaxSize(),
            )

            // AI text + transcription stacked at bottom-center
            if (lastAiText.isNotEmpty() || transcription.isNotEmpty()) {
                Column(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(Spacing.md),
                    horizontalAlignment = Alignment.Start,
                    verticalArrangement = Arrangement.spacedBy(Spacing.sm),
                ) {
                    if (lastAiText.isNotEmpty()) {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .heightIn(max = maxAiHeight)
                                .verticalScroll(rememberScrollState())
                                .padding(horizontal = Spacing.md),
                        ) {
                            Surface(
                                color = SurfaceVariant.copy(alpha = 0.7f),
                                shape = RoundedCornerShape(12.dp),
                            ) {
                                Text(
                                    text = lastAiText,
                                    style = MaterialTheme.typography.bodyLarge,
                                    color = TextPrimary,
                                    modifier = Modifier.padding(
                                        horizontal = Spacing.md,
                                        vertical = Spacing.sm,
                                    ),
                                )
                            }
                        }
                    }
                    if (transcription.isNotEmpty()) {
                        TranscriptionBubble(
                            text = transcription,
                            isFinal = isTranscriptionFinal,
                        )
                    }
                }
            }
        }

        // Top-right: switch front/back camera button
        IconButton(
            onClick = onSwitchCamera,
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(Spacing.md)
                .size(40.dp)
                .clip(CircleShape)
                .background(SurfaceVariant.copy(alpha = 0.8f)),
        ) {
            Icon(
                imageVector = Icons.Filled.Cameraswitch,
                contentDescription = if (isFrontCamera) stringResource(R.string.conv_switch_to_back_camera) else stringResource(R.string.conv_switch_to_front_camera),
                tint = TextPrimary,
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun VideoPreviewPreview() {
    LivaChatTheme {
        Box(modifier = Modifier.size(300.dp, 400.dp)) {
            VideoPreview(
                isFrontCamera = true,
                onSwitchCamera = {},
            )
        }
    }
}
