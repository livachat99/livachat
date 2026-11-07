package dev.livachat.app.ui.screens.conversation

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.RadioButtonChecked
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.livachat.app.media.deviceLabelText
import dev.livachat.app.R
import dev.livachat.app.media.AudioInputDevice
import dev.livachat.app.media.labeledInputDevices
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

/** 行尾选中圆点 testTag（仪器测试定位用；长名不遮挡的判据 = 该行圆点仍可见，docs/android_audio_input_devices.md §5.3）。 */
internal const val MIC_DEVICE_RADIO_TAG = "micDeviceRadio"

/**
 * 麦克风输入设备选择弹窗（docs/android_audio_input_devices.md §5.3；D1=含「系统默认」、D2=A 点选即生效）。
 *
 * 首项「系统默认」（id=null = 取消指定）；设备列表直连 `manager.inputDevices`
 * （弹窗打开期间设备变化实时刷新）；展示名走列表上下文标签规则（docs/android_audio_input_devices.md §2.3）。
 * 点任一项即生效并关窗；外点/返回 = 取消（无底部按钮，M3 要求 confirmButton 非空 → 置空实现）。
 */
@Composable
fun MicDevicePicker(
    devices: List<AudioInputDevice>,
    selectedDeviceId: Int?,
    onSelect: (Int?) -> Unit,
    onDismiss: () -> Unit,
) {
    // 选中项不在当前列表（设备被拔、控制器尚未重建清选择）→ 与「系统默认」同显
    // （docs/android_audio_input_devices.md §4.4）
    val effectiveSelected = selectedDeviceId?.takeIf { id -> devices.any { it.id == id } }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                stringResource(R.string.conv_select_mic_device),
                modifier = Modifier.fillMaxWidth(),
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.titleMedium,
                color = TextPrimary,
            )
        },
        text = {
            Column(modifier = Modifier.fillMaxWidth()) {
                DeviceOption(
                    title = stringResource(R.string.conv_system_default),
                    checked = effectiveSelected == null,
                    onClick = { onSelect(null) },
                )
                labeledInputDevices(devices).forEach { (dev, label) ->
                    val labelText = deviceLabelText(label)
                    DeviceOption(
                        title = labelText,
                        checked = dev.id == effectiveSelected,
                        onClick = { onSelect(dev.id) },
                    )
                }
            }
        },
        confirmButton = {},
    )
}

@Composable
private fun DeviceOption(
    title: String,
    checked: Boolean,
    onClick: () -> Unit,
) {
    // 行距收紧（docs/android_audio_input_devices.md §5.3）：去掉行内垂直 padding，行高即 48dp 触达下限；
    // 名称占满剩余宽度并单行省略（同节）——过长不得推走/遮住右侧选中圆点
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .height(48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = title,
            color = TextPrimary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.width(Spacing.md))
        Icon(
            imageVector = if (checked) Icons.Filled.RadioButtonChecked else Icons.Filled.RadioButtonUnchecked,
            contentDescription = null,
            tint = if (checked) BrandIndigo else TextSecondary,
            modifier = Modifier.size(22.dp).testTag(MIC_DEVICE_RADIO_TAG),
        )
    }
}
