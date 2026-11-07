package dev.livachat.app.media

import android.media.AudioDeviceInfo
import dev.livachat.app.R
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * docs/android_audio_input_devices.md §7.1 ⑬：标签规则纯 JVM 测试（无需 Robolectric——
 * shadow builder 造不出 address/productName，标签逻辑必须独立成纯函数可测，
 * docs/android_audio_input_devices.md §2.3）。
 * i18n（docs/android_multilingual.md §2.3.5）：断言 [DeviceLabelSpec] 规格（资源 id/动态段），不绑文案。
 */
class AudioInputDeviceLabelTest {

    private fun dev(
        id: Int,
        type: Int,
        productName: String? = null,
        address: String? = null,
    ) = AudioInputDevice(id = id, type = type, productName = productName, address = address)

    @Test
    fun `builtin mic productName equal to model falls back to type label`() {
        val d = dev(0, AudioDeviceInfo.TYPE_BUILTIN_MIC, productName = "LIO-AN00")
        assertEquals(
            listOf(d to DeviceLabelSpec(nameRes = R.string.device_builtin_mic)),
            labeledInputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `informative productName wins over type label`() {
        val d = dev(0, AudioDeviceInfo.TYPE_USB_DEVICE, productName = "Blue Yeti")
        assertEquals(
            listOf(d to DeviceLabelSpec(dynamicName = "Blue Yeti")),
            labeledInputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `blank productName falls back to type label`() {
        val d = dev(0, AudioDeviceInfo.TYPE_BLUETOOTH_SCO, productName = "  ")
        assertEquals(
            listOf(d to DeviceLabelSpec(nameRes = R.string.device_bt_sco)),
            labeledInputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `single device gets no suffix`() {
        val d = dev(13, AudioDeviceInfo.TYPE_BUILTIN_MIC, productName = "LIO-AN00", address = "bottom")
        assertEquals(
            listOf(d to DeviceLabelSpec(nameRes = R.string.device_builtin_mic)),
            labeledInputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `same type multiple devices use address suffix`() {
        val front = dev(13, AudioDeviceInfo.TYPE_BUILTIN_MIC, productName = "LIO-AN00", address = "bottom")
        val back = dev(15, AudioDeviceInfo.TYPE_BUILTIN_MIC, productName = "LIO-AN00", address = "back")
        assertEquals(
            listOf(
                front to DeviceLabelSpec(nameRes = R.string.device_builtin_mic, suffixText = "bottom"),
                back to DeviceLabelSpec(nameRes = R.string.device_builtin_mic, suffixText = "back"),
            ),
            labeledInputDevices(listOf(front, back), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `same type without address uses id-ordered ordinal and is stable across list order`() {
        val big = dev(20, AudioDeviceInfo.TYPE_WIRED_HEADSET, productName = "LIO-AN00")
        val small = dev(10, AudioDeviceInfo.TYPE_WIRED_HEADSET, productName = "LIO-AN00")
        val expected = mapOf(
            small to DeviceLabelSpec(nameRes = R.string.device_wired_headset, suffixText = "1"),
            big to DeviceLabelSpec(nameRes = R.string.device_wired_headset, suffixText = "2"),
        )
        assertEquals(expected, labeledInputDevices(listOf(small, big), deviceModel = "LIO-AN00").toMap())
        assertEquals(expected, labeledInputDevices(listOf(big, small), deviceModel = "LIO-AN00").toMap())
    }

    @Test
    fun `mixed types with informative product names get no suffix`() {
        val hw = dev(21, AudioDeviceInfo.TYPE_BUILTIN_MIC, productName = "LIO-AN00", address = "bottom")
        val usb = dev(22, AudioDeviceInfo.TYPE_USB_HEADSET, productName = "Blue Yeti")
        assertEquals(
            listOf(
                hw to DeviceLabelSpec(nameRes = R.string.device_builtin_mic),
                usb to DeviceLabelSpec(dynamicName = "Blue Yeti"),
            ),
            labeledInputDevices(listOf(hw, usb), deviceModel = "LIO-AN00"),
        )
    }

    // ── 长名只显示产品段（docs/android_audio_input_devices.md §2.3）────────────────

    @Test
    fun `usb long name keeps only product segment after separator`() {
        // 真机 USB 麦实测原名："USB-Audio - HUAWEI USB-C HEADSET"（' - ' 前为驱动样式前缀）
        val d = dev(0, AudioDeviceInfo.TYPE_USB_DEVICE, productName = "USB-Audio - HUAWEI USB-C HEADSET")
        assertEquals(
            listOf(d to DeviceLabelSpec(dynamicName = "HUAWEI USB-C HEADSET")),
            labeledInputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `only the first separator is stripped`() {
        val d = dev(0, AudioDeviceInfo.TYPE_USB_DEVICE, productName = "Foo - Bar - Mic")
        assertEquals(
            listOf(d to DeviceLabelSpec(dynamicName = "Bar - Mic")),
            labeledInputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `name without separator is unchanged`() {
        val d = dev(0, AudioDeviceInfo.TYPE_BLUETOOTH_SCO, productName = "FreeBuds Pro 3")
        assertEquals(
            listOf(d to DeviceLabelSpec(dynamicName = "FreeBuds Pro 3")),
            labeledInputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }
}
