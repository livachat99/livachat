package dev.livachat.app.media

import android.media.AudioDeviceInfo
import android.os.Build
import dev.livachat.app.R

/**
 * 音频输入设备（App 层展示模型，docs/android_audio_input_devices.md §2.1）。
 * 纯数据：单条自身无自洽展示名——"同 type 多条需区分后缀"必须持有列表上下文（见 [labeledInputDevices]）。
 * i18n（docs/android_multilingual.md §2.3.5）：产出 [DeviceLabelSpec] 规格，文案由 UI 层组合。
 */
data class AudioInputDevice(
    val id: Int,
    val type: Int,
    val productName: String?,
    val address: String?,
)

private val TYPE_FALLBACK_LABELS = mapOf(
    AudioDeviceInfo.TYPE_BUILTIN_MIC to R.string.device_builtin_mic,
    AudioDeviceInfo.TYPE_WIRED_HEADSET to R.string.device_wired_headset,
    AudioDeviceInfo.TYPE_BLUETOOTH_SCO to R.string.device_bt_sco,
    AudioDeviceInfo.TYPE_BLE_HEADSET to R.string.device_ble_headset,
    AudioDeviceInfo.TYPE_USB_DEVICE to R.string.device_usb_device,
    AudioDeviceInfo.TYPE_USB_HEADSET to R.string.device_usb_headset,
)

private val INPUT_FALLBACK = R.string.device_input_fallback

/**
 * 列表上下文的展示标签（docs/android_audio_input_devices.md §2.3 标签规则）：
 * base = productName（非空且 ≠ 机型名时；内置麦实测=机型名被滤）否则 type 兜底资源；
 * 同 type 多条 → 先按 id 排序（防标签在两次刷新间对调）再追加区分后缀：address 非空用 address，否则稳定序号。
 * 纯函数（deviceModel 参数注入）供纯 JVM 测试；生产调用用默认 Build.MODEL。
 */
fun labeledInputDevices(
    devices: List<AudioInputDevice>,
    deviceModel: String = Build.MODEL,
): List<Pair<AudioInputDevice, DeviceLabelSpec>> {
    val byType = devices.groupBy { it.type }
    return devices.map { dev ->
        val informative = dev.productName
            ?.takeIf { it.isNotBlank() && it != deviceModel }
            // 长名只显示产品段（docs/android_audio_input_devices.md §2.3）：真机 USB 麦实测
            // "USB-Audio - HUAWEI USB-C HEADSET"——
            // ' - ' 前为 ALSA 驱动/类别样式前缀（设备自报名的一部分），按需求取首个 ' - ' 之后；
            // 无分隔符或分离结果为空则保留原名。
            // 单样本启发式：仅 USB 麦观察所得；外推风险与收敛方向（type 闸门 / ALSA 前缀白名单）
            // 见 docs/android_audio_input_devices.md §8 watch 项
            ?.let { raw ->
                raw.substringAfter(" - ", missingDelimiterValue = "")
                    .takeIf { it.isNotBlank() } ?: raw
            }
        val spec = if (informative != null) {
            DeviceLabelSpec(dynamicName = informative)
        } else {
            DeviceLabelSpec(nameRes = TYPE_FALLBACK_LABELS[dev.type] ?: INPUT_FALLBACK)
        }
        val sameType = byType.getValue(dev.type)
        if (sameType.size <= 1) {
            dev to spec
        } else {
            val suffix = dev.address?.takeIf { it.isNotBlank() }
                ?: (sameType.sortedBy { it.id }.indexOf(dev) + 1).toString()
            dev to spec.copy(suffixText = suffix)
        }
    }
}
