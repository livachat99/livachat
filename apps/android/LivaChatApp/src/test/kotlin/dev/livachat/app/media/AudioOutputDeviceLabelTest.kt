package dev.livachat.app.media

import android.media.AudioDeviceInfo
import dev.livachat.app.R
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * docs/android_audio_output_devices.md §8.1：输出侧标签规则纯 JVM 测试（D1=B：复制输入侧
 * [AudioInputDeviceLabelTest] 的用例结构到输出版——两份实现行为锁；无需 Robolectric）。
 * i18n（docs/android_multilingual.md §2.3.5）：断言 [DeviceLabelSpec] 规格（资源 id/动态段），不绑文案。
 */
class AudioOutputDeviceLabelTest {

    private fun dev(
        id: Int,
        type: Int,
        productName: String? = null,
        address: String? = null,
    ) = AudioOutputDevice(id = id, type = type, productName = productName, address = address)

    @Test
    fun `builtin speaker productName equal to model falls back to type label`() {
        val d = dev(0, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, productName = "LIO-AN00")
        assertEquals(
            listOf(d to DeviceLabelSpec(nameRes = R.string.device_speaker)),
            labeledOutputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `informative productName wins over type label`() {
        val d = dev(0, AudioDeviceInfo.TYPE_USB_DEVICE, productName = "Blue Yeti")
        assertEquals(
            listOf(d to DeviceLabelSpec(dynamicName = "Blue Yeti")),
            labeledOutputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `blank productName falls back to type label`() {
        val d = dev(0, AudioDeviceInfo.TYPE_BLUETOOTH_SCO, productName = "  ")
        assertEquals(
            listOf(d to DeviceLabelSpec(nameRes = R.string.device_bt_sco)),
            labeledOutputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `hearing aid and unknown types have fallbacks`() {
        val aid = dev(0, AudioDeviceInfo.TYPE_HEARING_AID, productName = "LIO-AN00")
        val unknown = dev(1, 999, productName = "LIO-AN00")
        assertEquals(
            listOf(
                aid to DeviceLabelSpec(nameRes = R.string.device_hearing_aid),
                unknown to DeviceLabelSpec(nameRes = R.string.device_output_fallback),
            ),
            labeledOutputDevices(listOf(aid, unknown), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `single device gets no suffix`() {
        val d = dev(3, AudioDeviceInfo.TYPE_WIRED_HEADSET, productName = "LIO-AN00", address = "card=0")
        assertEquals(
            listOf(d to DeviceLabelSpec(nameRes = R.string.device_wired_headset)),
            labeledOutputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `same type multiple devices use address suffix`() {
        val a = dev(20, AudioDeviceInfo.TYPE_USB_DEVICE, productName = "LIO-AN00", address = "card=1")
        val b = dev(21, AudioDeviceInfo.TYPE_USB_DEVICE, productName = "LIO-AN00", address = "card=2")
        assertEquals(
            listOf(
                a to DeviceLabelSpec(nameRes = R.string.device_usb_device, suffixText = "card=1"),
                b to DeviceLabelSpec(nameRes = R.string.device_usb_device, suffixText = "card=2"),
            ),
            labeledOutputDevices(listOf(a, b), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `same type without address uses id-ordered ordinal and is stable across list order`() {
        val big = dev(20, AudioDeviceInfo.TYPE_WIRED_HEADPHONES, productName = "LIO-AN00")
        val small = dev(10, AudioDeviceInfo.TYPE_WIRED_HEADPHONES, productName = "LIO-AN00")
        val expected = mapOf(
            small to DeviceLabelSpec(nameRes = R.string.device_wired_headphones, suffixText = "1"),
            big to DeviceLabelSpec(nameRes = R.string.device_wired_headphones, suffixText = "2"),
        )
        assertEquals(expected, labeledOutputDevices(listOf(small, big), deviceModel = "LIO-AN00").toMap())
        assertEquals(expected, labeledOutputDevices(listOf(big, small), deviceModel = "LIO-AN00").toMap())
    }

    @Test
    fun `usb long name keeps only product segment after separator`() {
        // 真机 USB 麦实测原名（输出侧同规则）；" - " 前为驱动样式前缀
        val d = dev(0, AudioDeviceInfo.TYPE_USB_DEVICE, productName = "USB-Audio - HUAWEI USB-C HEADSET")
        assertEquals(
            listOf(d to DeviceLabelSpec(dynamicName = "HUAWEI USB-C HEADSET")),
            labeledOutputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `only the first separator is stripped`() {
        val d = dev(0, AudioDeviceInfo.TYPE_USB_DEVICE, productName = "Foo - Bar - Amp")
        assertEquals(
            listOf(d to DeviceLabelSpec(dynamicName = "Bar - Amp")),
            labeledOutputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `name without separator is unchanged`() {
        val d = dev(0, AudioDeviceInfo.TYPE_BLUETOOTH_SCO, productName = "KOTLL O5 Pro")
        assertEquals(
            listOf(d to DeviceLabelSpec(dynamicName = "KOTLL O5 Pro")),
            labeledOutputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }

    // ── BT 双链路同名（D5=B，2026-10-11 定案）────────────────────────

    @Test
    fun `bt dual entries with same product name get type suffixes`() {
        // 真机实测：同一耳机以 SCO 与 A2DP 两条同名同 MAC 出现 → 类型后缀区分
        val sco = dev(13131, AudioDeviceInfo.TYPE_BLUETOOTH_SCO, productName = "KOTLL O5 Pro", address = "8F:26:D0:50:72:72")
        val a2dp = dev(13133, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, productName = "KOTLL O5 Pro", address = "8F:26:D0:50:72:72")
        assertEquals(
            listOf(
                sco to DeviceLabelSpec(dynamicName = "KOTLL O5 Pro", suffixRes = R.string.device_tag_bt_voice),
                a2dp to DeviceLabelSpec(dynamicName = "KOTLL O5 Pro", suffixRes = R.string.device_tag_bt_music),
            ),
            labeledOutputDevices(listOf(sco, a2dp), deviceModel = "LIO-AN00"),
        )
    }

    @Test
    fun `a2dp only device falls back to bluetooth music label`() {
        val d = dev(0, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, productName = "LIO-AN00")
        assertEquals(
            // 兜底名与 TYPE_CONFLICT_TAGS 统一措辞（2026-10-11 终审：原"蓝牙音频"）
            listOf(d to DeviceLabelSpec(nameRes = R.string.device_bt_a2dp)),
            labeledOutputDevices(listOf(d), deviceModel = "LIO-AN00"),
        )
    }
}
