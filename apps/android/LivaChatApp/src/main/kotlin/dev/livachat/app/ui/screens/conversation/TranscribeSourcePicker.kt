package dev.livachat.app.ui.screens.conversation

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.RadioButtonChecked
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

/**
 * 转录模式录音源选择对话框（fd_pipeline_transcribe_mode.md §6.4；
 * docs/android_audio_capture.md §3.3 已落地）。
 *
 * 选择写入 UiState.transcribeAudioSource，由 ConversationViewModel 派生观测
 * collector 下发到 AudioCaptureController：原声 → MIC、语音 → VOICE_COMMUNICATION。
 */
@Composable
fun TranscribeSourcePicker(
    initialSource: TranscribeAudioSource = TranscribeAudioSource.VOICE,
    onConfirm: (TranscribeAudioSource) -> Unit,
    onDismiss: () -> Unit,
) {
    var selected by remember { mutableStateOf(initialSource) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                stringResource(R.string.conv_transcribe_source_title),
                modifier = Modifier.fillMaxWidth(),
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.titleMedium,
                color = TextPrimary,
            )
        },
        text = {
            Column(modifier = Modifier.fillMaxWidth()) {
                SourceOption(
                    title = stringResource(R.string.conv_transcribe_source_original),
                    subtitle = stringResource(R.string.conv_transcribe_source_original_desc),
                    checked = selected == TranscribeAudioSource.RAW,
                    onClick = { selected = TranscribeAudioSource.RAW },
                )
                SourceOption(
                    title = stringResource(R.string.conv_transcribe_source_voice),
                    subtitle = stringResource(R.string.conv_transcribe_source_voice_desc),
                    checked = selected == TranscribeAudioSource.VOICE,
                    onClick = { selected = TranscribeAudioSource.VOICE },
                )
            }
        },
        confirmButton = {
            // 与其它弹窗一致：按钮并排居中（单 slot 内全宽居中 Row）
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
            ) {
                TextButton(onClick = onDismiss) {
                    Text(stringResource(R.string.common_cancel), color = TextSecondary)
                }
                TextButton(onClick = { onConfirm(selected) }) {
                    Text(stringResource(R.string.conv_picker_confirm), color = BrandIndigo)
                }
            }
        },
    )
}

@Composable
private fun SourceOption(
    title: String,
    subtitle: String,
    checked: Boolean,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = Spacing.sm)
            .height(48.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, color = TextPrimary)
            Text(subtitle, color = TextSecondary)
        }
        Spacer(Modifier.width(Spacing.md))
        Icon(
            imageVector = if (checked) Icons.Filled.RadioButtonChecked else Icons.Filled.RadioButtonUnchecked,
            contentDescription = null,
            tint = if (checked) BrandIndigo else TextSecondary,
            modifier = Modifier.size(22.dp),
        )
    }
}
