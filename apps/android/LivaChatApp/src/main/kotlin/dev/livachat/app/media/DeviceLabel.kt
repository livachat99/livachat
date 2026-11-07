package dev.livachat.app.media

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R

/**
 * 设备展示标签规格（i18n，docs/android_multilingual.md §2.3.5/§3.3）：
 * 纯函数只产出规格（可 JVM 直测），文案组合在 UI 层（[deviceLabelText]）。
 * base = [dynamicName]（产品名，不翻译）?: [nameRes] 资源；
 * 后缀 = [suffixText]（address/序号）?: [suffixRes]（类型后缀资源）；有后缀时经 [R.string.device_label_join] 包裹。
 */
data class DeviceLabelSpec(
    val dynamicName: String? = null,
    val nameRes: Int? = null,
    val suffixText: String? = null,
    val suffixRes: Int? = null,
)

@Composable
fun deviceLabelText(spec: DeviceLabelSpec): String {
    val base = spec.dynamicName ?: stringResource(requireNotNull(spec.nameRes))
    val suffix = spec.suffixText ?: spec.suffixRes?.let { stringResource(it) }
    return if (suffix == null) base else stringResource(R.string.device_label_join, base, suffix)
}
