package dev.livachat.app.ui.screens.conversation

import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Undo
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.LivaChatTypography
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Surface
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AvatarCustomizationSheet(
    hasCustomAvatar: Boolean,
    onPickFromGallery: () -> Unit,
    onTakePhoto: () -> Unit,
    onRemoveCustomAvatar: () -> Unit,
    onDismiss: () -> Unit,
    avatarVisible: Boolean = true,
    onAvatarVisibleChange: (Boolean) -> Unit = {},
    conversationSceneEnabled: Boolean = false,
    onToggleConversationScene: (Boolean) -> Unit = {},
    lectureModeEnabled: Boolean = false,
    onToggleLectureMode: (Boolean) -> Unit = {},
    transcribeModeEnabled: Boolean = false,
    onToggleTranscribeMode: (Boolean) -> Unit = {},
    omniModeEnabled: Boolean = false,
    onToggleOmniMode: (Boolean) -> Unit = {},
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp),
        containerColor = Surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.lg)
                .padding(bottom = Spacing.md),
            verticalArrangement = Arrangement.Bottom,
        ) {
            // ── Group 1: Simultaneous translation ─────────────────────
            SectionHeader(stringResource(R.string.conv_welcome_chip_translate))

            SwitchRow(
                title = stringResource(R.string.conv_mode_dialogue),
                subtitle = stringResource(R.string.conv_mode_dialogue_desc),
                checked = conversationSceneEnabled,
                onCheckedChange = onToggleConversationScene,
                enabled = !lectureModeEnabled && !transcribeModeEnabled && !omniModeEnabled,
            )

            SwitchRow(
                title = stringResource(R.string.conv_mode_lecture),
                subtitle = stringResource(R.string.conv_mode_lecture_desc),
                checked = lectureModeEnabled,
                onCheckedChange = onToggleLectureMode,
                enabled = !conversationSceneEnabled && !transcribeModeEnabled && !omniModeEnabled,
            )

            SwitchRow(
                title = stringResource(R.string.conv_mode_transcribe),
                subtitle = stringResource(R.string.conv_mode_transcribe_desc),
                checked = transcribeModeEnabled,
                onCheckedChange = onToggleTranscribeMode,
                enabled = !conversationSceneEnabled && !lectureModeEnabled && !omniModeEnabled,
            )

            SwitchRow(
                title = stringResource(R.string.conv_omni),
                subtitle = stringResource(R.string.conv_omni_desc),
                checked = omniModeEnabled,
                onCheckedChange = onToggleOmniMode,
                enabled = !conversationSceneEnabled && !lectureModeEnabled && !transcribeModeEnabled,
            )

            HorizontalDivider(color = SurfaceVariant, modifier = Modifier.padding(vertical = Spacing.sm))

            // ── Group 2: Avatar selection ─────────────────────────────
            SectionHeader(stringResource(R.string.conv_avatar_picker_title), trailing = { ShowHideToggle(visible = avatarVisible, onToggle = onAvatarVisibleChange) })

            SheetOption(
                icon = { Icon(Icons.Filled.PhotoLibrary, null, tint = TextSecondary, modifier = Modifier.size(24.dp)) },
                text = stringResource(R.string.conv_avatar_from_gallery),
                onClick = { onPickFromGallery(); onDismiss() },
            )
            SheetOption(
                icon = { Icon(Icons.Filled.PhotoCamera, null, tint = TextSecondary, modifier = Modifier.size(24.dp)) },
                text = stringResource(R.string.conv_take_photo),
                onClick = { onTakePhoto(); onDismiss() },
            )
            if (hasCustomAvatar) {
                SheetOption(
                    icon = { Icon(Icons.AutoMirrored.Filled.Undo, null, tint = TextSecondary, modifier = Modifier.size(24.dp)) },
                    text = stringResource(R.string.conv_avatar_reset),
                    onClick = { onRemoveCustomAvatar(); onDismiss() },
                )
            }

        }
    }
}

@Composable
private fun SectionHeader(
    title: String,
    trailing: (@Composable () -> Unit)? = null,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = Spacing.xs),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, style = LivaChatTypography.labelLarge, color = BrandIndigo)
        trailing?.invoke()
    }
}

@Composable
private fun ShowHideToggle(visible: Boolean, onToggle: (Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .background(SurfaceVariant)
            .padding(2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PillSegment(stringResource(R.string.conv_avatar_scale_normal), selected = visible, onClick = { onToggle(true) })
        PillSegment(stringResource(R.string.conv_avatar_scale_shrink), selected = !visible, onClick = { onToggle(false) })
    }
}

@Composable
private fun PillSegment(text: String, selected: Boolean, onClick: () -> Unit) {
    Text(
        text = text,
        style = LivaChatTypography.labelLarge,
        color = if (selected) TextPrimary else TextSecondary,
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .clickable(onClick = onClick)
            .background(if (selected) BrandIndigo else Color.Transparent)
            .padding(horizontal = 12.dp, vertical = 4.dp),
    )
}

@Composable
private fun SheetOption(
    icon: @Composable () -> Unit,
    text: String,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .height(48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        icon()
        Spacer(Modifier.width(Spacing.md))
        Text(text, style = LivaChatTypography.labelLarge, color = TextPrimary)
    }
}

@Composable
private fun SwitchRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    enabled: Boolean = true,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = Spacing.sm),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, style = LivaChatTypography.labelLarge, color = TextPrimary)
            Text(subtitle, style = LivaChatTypography.bodySmall, color = TextSecondary)
        }
        Spacer(Modifier.width(Spacing.md))
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            enabled = enabled,
            colors = SwitchDefaults.colors(
                checkedThumbColor = TextPrimary,
                checkedTrackColor = BrandIndigo,
                uncheckedThumbColor = TextSecondary,
                uncheckedTrackColor = SurfaceVariant,
            ),
        )
    }
}
