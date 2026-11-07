package dev.livachat.app.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.NoPhotography
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.VolumeOff
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextSecondary
import kotlinx.coroutines.flow.StateFlow

@Composable
fun BottomToolbar(
    isMicOn: Boolean,
    isSpeakerOn: Boolean,
    isCameraOn: Boolean,
    onToggleMic: () -> Unit,
    onToggleSpeaker: () -> Unit,
    onToggleCamera: () -> Unit,
    // 图库选图（docs/camera_enhance.md §9）：摄像头关闭时在「摄像头」前挂「+」并开放整行点击。
    // 必选（无默认空实现）：D5=常驻使该入口在所有模式可点，新增调用点漏接线必须编译失败，
    // 不得退化为「点击无反应」的死控件（§9.10）。
    onPickGallery: () -> Unit,
    modifier: Modifier = Modifier,
    micLevel: StateFlow<Int>? = null,
    // 多设备时在「麦克风」文字前挂 ^ 指示并开放整行点击（docs/android_audio_input_devices.md §5.2）
    showMicDeviceSelector: Boolean = false,
    onOpenMicDevicePicker: (() -> Unit)? = null,
    // 输出侧同构（docs/android_audio_output_devices.md §5.2，D2=B 复制）
    showOutputDeviceSelector: Boolean = false,
    onOpenOutputDevicePicker: (() -> Unit)? = null,
) {
    val micCaption = if (isMicOn) stringResource(R.string.conv_toolbar_mic_on) else stringResource(R.string.conv_toolbar_mic_off)
    // 三列（图标+文字）整体下移 4dp（用户 UI 反馈，记录见 docs/android_audio_input_devices.md §5.2）：
    // 垂直 padding 由 md/md(16/16) 改为 (md+4)/(md-4)——总高不变、三列互相对齐关系不变，仅内容整体下移。
    val contentDrop = 4.dp
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = Spacing.lg)
            .padding(top = Spacing.md + contentDrop, bottom = Spacing.md - contentDrop),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ToolbarButton(
            icon = if (isMicOn) Icons.Filled.Mic else Icons.Filled.MicOff,
            isActive = isMicOn,
            caption = micCaption,
            onClick = onToggleMic,
            // v2：mic 开启时话筒头即音量表（MicVolumeGlyph 内 collect，50Hz tick
            // 重组范围=图标自身）；自绘图标无 Icon 子节点，contentDescription 由
            // 外层语义 Box 承载（并入 IconButton 合并树）
            iconContent = if (isMicOn) micLevel?.let { flow ->
                {
                    Box(Modifier.semantics { contentDescription = micCaption }) {
                        MicVolumeGlyph(flow = flow)
                    }
                }
            } else null,
            captionLeading = if (showMicDeviceSelector) {
                {
                    Icon(
                        imageVector = Icons.Filled.KeyboardArrowUp,
                        contentDescription = stringResource(R.string.conv_select_mic_device),
                        tint = TextSecondary,
                        modifier = Modifier.size(16.dp),
                    )
                }
            } else null,
            onCaptionClick = if (showMicDeviceSelector) onOpenMicDevicePicker else null,
        )
        ToolbarButton(
            icon = if (isSpeakerOn) Icons.Filled.VolumeUp else Icons.Filled.VolumeOff,
            isActive = isSpeakerOn,
            caption = if (isSpeakerOn) stringResource(R.string.conv_toolbar_speaker_on) else stringResource(R.string.conv_toolbar_speaker_off),
            onClick = onToggleSpeaker,
            captionLeading = if (showOutputDeviceSelector) {
                {
                    Icon(
                        imageVector = Icons.Filled.KeyboardArrowUp,
                        contentDescription = stringResource(R.string.conv_select_speaker_device),
                        tint = TextSecondary,
                        modifier = Modifier.size(16.dp),
                    )
                }
            } else null,
            onCaptionClick = if (showOutputDeviceSelector) onOpenOutputDevicePicker else null,
        )
        ToolbarButton(
            icon = if (isCameraOn) Icons.Filled.CameraAlt else Icons.Filled.NoPhotography,
            isActive = isCameraOn,
            caption = if (isCameraOn) stringResource(R.string.conv_toolbar_camera_on) else stringResource(R.string.conv_toolbar_camera_off),
            onClick = onToggleCamera,
            // 摄像头开启时隐藏 + 且整行不可点（docs/camera_enhance.md §9 D9）；
            // 其余模式常驻（D5），选图后的回合行为由服务端模式门控/omni 分支决定。
            captionLeading = if (!isCameraOn) {
                {
                    Icon(
                        imageVector = Icons.Filled.Add,
                        contentDescription = stringResource(R.string.conv_avatar_from_gallery),
                        tint = TextSecondary,
                        modifier = Modifier.size(16.dp),
                    )
                }
            } else null,
            onCaptionClick = if (!isCameraOn) onPickGallery else null,
        )
    }
}

@Composable
private fun ToolbarButton(
    icon: ImageVector,
    isActive: Boolean,
    caption: String,
    onClick: () -> Unit,
    iconContent: (@Composable () -> Unit)? = null,
    captionLeading: (@Composable () -> Unit)? = null,
    onCaptionClick: (() -> Unit)? = null,
) {
    var pressed by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(
        targetValue = if (pressed) 0.92f else 1.0f,
        animationSpec = spring(dampingRatio = 0.5f, stiffness = 500f),
        label = "scale",
    )
    val containerColor by animateColorAsState(
        targetValue = if (isActive) MaterialTheme.colorScheme.primary else Color.Transparent,
        animationSpec = spring(dampingRatio = 0.5f),
        label = "containerBg",
    )

    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        IconButton(
            onClick = {
                pressed = true
                onClick()
            },
            modifier = Modifier
                .size(48.dp)
                .scale(scale)
                .then(
                    if (isActive) Modifier
                    else Modifier.border(1.dp, SurfaceVariant, CircleShape),
                ),
            interactionSource = remember { MutableInteractionSource() },
        ) {
            if (iconContent != null) {
                iconContent()
            } else {
                Icon(
                    imageVector = icon,
                    contentDescription = caption,
                    tint = if (isActive) MaterialTheme.colorScheme.primary else TextSecondary,
                )
            }
        }
        // 图标↔caption 间距收紧（真机实测墨迹间距 22.7dp 过大）：无行间 Spacer、行顶 padding 归零，
        // 仅留底部 padding 维持触区高度（16sp 文本 + 8dp = 24dp 与收紧前一致）；三列统一以保持对齐。
        // 触达取舍（见 docs/android_audio_input_devices.md §5.2）：48dp 垂直触达会把
        // 本行撑高致列间错位，或向上侵占正上方 48dp 静音按钮触区——故取整行可点
        // （横 ≥48dp、纵约 24dp）+ TalkBack 可达（^ 带 contentDescription）。
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                // 三列文字（^麦克风/扬声器/摄像头）整体下移 1dp，图标不动（用户 UI 反馈，记录见
                // docs/android_audio_input_devices.md §5.2）；offset 不影响布局与其他元素
                .offset(y = 1.dp)
                .then(
                    if (onCaptionClick != null) {
                        // clickable 必须在 padding 外层：Compose 触区 = 链中 clickable 自身边界，
                        // 置于内层时触区仅文本行高（≈16dp，反向对照实测）、被 8dp padding 切掉
                        // （docs/android_audio_input_devices.md §5.2）
                        Modifier.clickable(onClick = onCaptionClick)
                    } else Modifier,
                )
                .padding(bottom = Spacing.sm),
        ) {
            if (captionLeading != null) {
                captionLeading()
                Spacer(modifier = Modifier.width(2.dp))
            }
            Text(
                text = caption.split(" ").first(),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
            if (captionLeading != null) {
                // 对称占位（=^ 16dp + 间距 2dp）：使「麦克风」文字中心与上方图标轴对齐——
                // ^ 悬挂在文字左侧、不参与居中（真机实测不加占位时文字右偏 9dp）
                Spacer(modifier = Modifier.width(18.dp))
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun BottomToolbarAllOnPreview() {
    LivaChatTheme {
        Surface(color = Background) {
            BottomToolbar(
                isMicOn = true,
                isSpeakerOn = true,
                isCameraOn = true,
                onToggleMic = {},
                onToggleSpeaker = {},
                onToggleCamera = {},
                onPickGallery = {},
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun BottomToolbarAllOffPreview() {
    LivaChatTheme {
        Surface(color = Background) {
            BottomToolbar(
                isMicOn = false,
                isSpeakerOn = false,
                isCameraOn = false,
                onToggleMic = {},
                onToggleSpeaker = {},
                onToggleCamera = {},
                onPickGallery = {},
            )
        }
    }
}
