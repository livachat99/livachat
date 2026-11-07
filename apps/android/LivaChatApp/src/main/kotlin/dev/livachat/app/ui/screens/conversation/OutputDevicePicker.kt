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
import dev.livachat.app.media.AudioOutputDevice
import dev.livachat.app.media.labeledOutputDevices
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

/** 行尾选中圆点 testTag（仪器测试定位用；长名不遮挡的判据 = 该行圆点仍可见）。 */
internal const val OUTPUT_DEVICE_RADIO_TAG = "outputDeviceRadio"

/**
 * 扬声器输出设备选择弹窗（docs/android_audio_output_devices.md §5.3；D2=B：镜像复制 [MicDevicePicker]，
 * 除标题（「选择扬声器设备」，模式同输入侧）/数据源外逐项同规范——首项「系统默认」、点选即生效并关窗、
 * 名称 weight+单行省略、行高 48dp、行尾圆点；外点/返回 = 取消（confirmButton 置空实现））。
 *
 * 设备列表直连 `manager.outputDevices`（弹窗打开期间设备变化实时刷新）；
 * 展示名走列表上下文标签规则（§5.2，D1=B 的 labeledOutputDevices）。
 */
@Composable
fun OutputDevicePicker(
    devices: List<AudioOutputDevice>,
    selectedDeviceId: Int?,
    onSelect: (Int?) -> Unit,
    onDismiss: () -> Unit,
) {
    // 选中项不在当前列表（设备移除、控制器尚未重建清选择）→ 与「系统默认」同显（§5.4 同义）
    val effectiveSelected = selectedDeviceId?.takeIf { id -> devices.any { it.id == id } }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                stringResource(R.string.conv_select_speaker_device),
                modifier = Modifier.fillMaxWidth(),
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.titleMedium,
                color = TextPrimary,
            )
        },
        text = {
            Column(modifier = Modifier.fillMaxWidth()) {
                OutputDeviceOption(
                    title = stringResource(R.string.conv_system_default),
                    checked = effectiveSelected == null,
                    onClick = { onSelect(null) },
                )
                labeledOutputDevices(devices).forEach { (dev, label) ->
                    val labelText = deviceLabelText(label)
                    OutputDeviceOption(
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
private fun OutputDeviceOption(
    title: String,
    checked: Boolean,
    onClick: () -> Unit,
) {
    // 与 MicDevicePicker.DeviceOption 逐项同规范（行距 48dp；名称占满剩余宽度并单行省略，过长不遮圆点）
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
            modifier = Modifier.size(22.dp).testTag(OUTPUT_DEVICE_RADIO_TAG),
        )
    }
}
