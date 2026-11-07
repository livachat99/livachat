package dev.livachat.app.media

import android.media.AudioDeviceInfo
import android.os.Build
import dev.livachat.app.R

/**
 * 音频输出设备（App 层展示模型，docs/android_audio_output_devices.md §2.1）。
 * 纯数据：单条自身无自洽展示名——"同 type 多条需区分后缀"必须持有列表上下文（见 [labeledOutputDevices]）。
 * D1=B 定案：算法与输入侧 [labeledInputDevices] 全同（复制版），仅兜底表不同。
 * i18n（docs/android_multilingual.md §2.3.5）：产出 [DeviceLabelSpec] 规格，文案由 UI 层组合。
 */
data class AudioOutputDevice(
    val id: Int,
    val type: Int,
    val productName: String?,
    val address: String?,
)

private val TYPE_FALLBACK_LABELS = mapOf(
    AudioDeviceInfo.TYPE_BUILTIN_SPEAKER to R.string.device_speaker,
    AudioDeviceInfo.TYPE_BUILTIN_EARPIECE to R.string.device_earpiece,
    AudioDeviceInfo.TYPE_WIRED_HEADSET to R.string.device_wired_headset,
    AudioDeviceInfo.TYPE_WIRED_HEADPHONES to R.string.device_wired_headphones,
    AudioDeviceInfo.TYPE_BLUETOOTH_SCO to R.string.device_bt_sco,
    // 与 TYPE_CONFLICT_TAGS 统一措辞（2026-10-11 终审：原"蓝牙音频"）
    AudioDeviceInfo.TYPE_BLUETOOTH_A2DP to R.string.device_bt_a2dp,
    AudioDeviceInfo.TYPE_BLE_HEADSET to R.string.device_ble_headset,
    AudioDeviceInfo.TYPE_BLE_SPEAKER to R.string.device_ble_speaker,
    AudioDeviceInfo.TYPE_USB_HEADSET to R.string.device_usb_headset,
    AudioDeviceInfo.TYPE_USB_DEVICE to R.string.device_usb_device,
    AudioDeviceInfo.TYPE_HEARING_AID to R.string.device_hearing_aid,
)

private val OUTPUT_FALLBACK = R.string.device_output_fallback

/** 跨 type 同名冲突的类型后缀文案资源（2026-10-11 用户定稿：蓝牙语音 / 蓝牙音乐）。 */
private val TYPE_CONFLICT_TAGS = mapOf(
    AudioDeviceInfo.TYPE_BLUETOOTH_SCO to R.string.device_tag_bt_voice,
    AudioDeviceInfo.TYPE_BLUETOOTH_A2DP to R.string.device_tag_bt_music,
)

private fun baseSpecOf(dev: AudioOutputDevice, deviceModel: String): DeviceLabelSpec {
    val informative = dev.productName
        ?.takeIf { it.isNotBlank() && it != deviceModel }
        // 长名只显示产品段（docs/android_audio_input_devices.md §2.3 同规则）：' - ' 前为 ALSA 驱动/类别样式前缀
        ?.let { raw ->
            raw.substringAfter(" - ", missingDelimiterValue = "")
                .takeIf { it.isNotBlank() } ?: raw
        }
    return if (informative != null) {
        DeviceLabelSpec(dynamicName = informative)
    } else {
        DeviceLabelSpec(nameRes = TYPE_FALLBACK_LABELS[dev.type] ?: OUTPUT_FALLBACK)
    }
}

/**
 * 列表上下文的展示标签（docs/android_audio_output_devices.md §2.3 标签规则；D1=B 复制输入侧 [labeledInputDevices]）：
 * base = productName（非空且 ≠ 机型名时；内置实测=机型名被滤）否则 type 兜底资源；
 * 长名取产品段（docs/android_audio_input_devices.md §2.3 同规则：含 " - " 时取首个分隔符之后，无分隔符/分离为空保留原名）；
 * 同 type 多条 → 先按 id 排序（防标签在两次刷新间对调）再追加区分后缀：address 非空用 address，否则稳定序号；
 * **跨 type 同名冲突**（BT 双链路：同一耳机以 SCO+A2DP 两条同名出现，2026-10-11 D5=B 定案）
 * → 追加类型后缀（如「（蓝牙语音）」「（蓝牙音乐）」）区分。
 * 纯函数（deviceModel 参数注入）供纯 JVM 测试；生产调用用默认 Build.MODEL。
 */
fun labeledOutputDevices(
    devices: List<AudioOutputDevice>,
    deviceModel: String = Build.MODEL,
): List<Pair<AudioOutputDevice, DeviceLabelSpec>> {
    val byType = devices.groupBy { it.type }
    val specs = devices.map { dev -> dev to baseSpecOf(dev, deviceModel) }
    val baseCounts = specs.groupingBy { it.second.dynamicName ?: it.second.nameRes }.eachCount()
    return specs.map { (dev, spec) ->
        val sameType = byType.getValue(dev.type)
        when {
            // 同 type 多条（既有规则优先）：address 非空用 address，否则按 id 排序的稳定序号
            sameType.size > 1 -> {
                val suffix = dev.address?.takeIf { it.isNotBlank() }
                    ?: (sameType.sortedBy { it.id }.indexOf(dev) + 1).toString()
                dev to spec.copy(suffixText = suffix)
            }
            // 跨 type 同名 → 类型后缀（D5=B；文案 蓝牙语音/蓝牙音乐）
            baseCounts.getValue(spec.dynamicName ?: spec.nameRes) > 1 -> {
                val tagRes = TYPE_CONFLICT_TAGS[dev.type]
                    ?: TYPE_FALLBACK_LABELS[dev.type]
                    ?: OUTPUT_FALLBACK
                dev to spec.copy(suffixRes = tagRes)
            }
            else -> dev to spec
        }
    }
}
