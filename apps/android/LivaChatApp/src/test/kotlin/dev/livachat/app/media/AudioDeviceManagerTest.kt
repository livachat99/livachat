package dev.livachat.app.media

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.shadows.AudioDeviceInfoBuilder

/**
 * docs/android_audio_input_devices.md §7.1 ①-③：初始枚举 / 白名单过滤 / 变化重枚举。
 * Robolectric shadow 回调同步派发且忽略 handler 参数（shadows-framework-4.12.2 源码核实）→ 无需 pump main looper。
 * 注意：`setInputDevices` 仅替换列表、不触发回调（建 manager 前调用才有效）；逐台增删用 add/removeDevice(notify=true)。
 * 夹具限制（实施期实测）：builder 构造的 TYPE_BUILTIN_MIC 经 `getType()` 往返为 TYPE_UNKNOWN(0)
 * （android-all 映射反转丢失该类型；真机探针 type=15 正常）→ 白名单代表设备用可往返的 TYPE_WIRED_HEADSET。
 */
@RunWith(RobolectricTestRunner::class)
class AudioDeviceManagerTest {

    private val context: Context = RuntimeEnvironment.getApplication()
    private val audioManager: AudioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    private fun device(type: Int): AudioDeviceInfo =
        AudioDeviceInfoBuilder.newBuilder().setType(type).build()

    @Test
    fun `initial enumeration snapshots current input devices`() {
        val headset = device(AudioDeviceInfo.TYPE_WIRED_HEADSET)
        // 先 setInputDevices 再构造 manager（该 API 不发回调，构造后才 set 不会进 flow）
        shadowOf(audioManager).setInputDevices(listOf(headset))

        val manager = AudioDeviceManager(context)

        assertEquals(1, manager.inputDevices.value.size)
        assertEquals(AudioDeviceInfo.TYPE_WIRED_HEADSET, manager.inputDevices.value.single().type)
    }

    @Test
    fun `whitelist filters non-mic input sources`() {
        shadowOf(audioManager).setInputDevices(
            listOf(
                device(AudioDeviceInfo.TYPE_WIRED_HEADSET),
                device(AudioDeviceInfo.TYPE_HDMI),
                device(AudioDeviceInfo.TYPE_TELEPHONY),
                device(AudioDeviceInfo.TYPE_REMOTE_SUBMIX),
            ),
        )

        val manager = AudioDeviceManager(context)

        assertEquals(
            listOf(AudioDeviceInfo.TYPE_WIRED_HEADSET),
            manager.inputDevices.value.map { it.type },
        )
    }

    @Test
    fun `device added re-enumerates via callback`() {
        val manager = AudioDeviceManager(context)
        assertTrue(manager.inputDevices.value.isEmpty())

        shadowOf(audioManager).addInputDevice(device(AudioDeviceInfo.TYPE_WIRED_HEADSET), true)

        assertEquals(1, manager.inputDevices.value.size)
        assertEquals(AudioDeviceInfo.TYPE_WIRED_HEADSET, manager.inputDevices.value.single().type)
    }

    @Test
    fun `device removed re-enumerates via callback`() {
        val manager = AudioDeviceManager(context)
        val headset = device(AudioDeviceInfo.TYPE_WIRED_HEADSET)
        shadowOf(audioManager).addInputDevice(headset, true)
        assertEquals(1, manager.inputDevices.value.size)

        shadowOf(audioManager).removeInputDevice(headset, true)

        assertTrue(manager.inputDevices.value.isEmpty())
    }

    // ── 输出方向（docs/android_audio_output_devices.md §8.1）────────────────────

    @Test
    fun `output initial enumeration snapshots current output devices`() {
        shadowOf(audioManager).setOutputDevices(
            listOf(device(AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)),
        )

        val manager = AudioDeviceManager(context)

        assertEquals(1, manager.outputDevices.value.size)
        assertEquals(AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, manager.outputDevices.value.single().type)
    }

    @Test
    fun `output whitelist filters non-selectable outputs`() {
        shadowOf(audioManager).setOutputDevices(
            listOf(
                device(AudioDeviceInfo.TYPE_WIRED_HEADSET),
                device(AudioDeviceInfo.TYPE_TELEPHONY),   // 通话下行（§9.1 实测混入项）
                device(AudioDeviceInfo.TYPE_HDMI),        // 非对话场景出口
            ),
        )

        val manager = AudioDeviceManager(context)

        assertEquals(
            listOf(AudioDeviceInfo.TYPE_WIRED_HEADSET),
            manager.outputDevices.value.map { it.type },
        )
    }

    @Test
    fun `output device added re-enumerates via callback`() {
        val manager = AudioDeviceManager(context)
        assertTrue(manager.outputDevices.value.isEmpty())

        shadowOf(audioManager).addOutputDevice(device(AudioDeviceInfo.TYPE_BLUETOOTH_SCO), true)

        assertEquals(1, manager.outputDevices.value.size)
        assertEquals(AudioDeviceInfo.TYPE_BLUETOOTH_SCO, manager.outputDevices.value.single().type)
    }

    @Test
    fun `callback refreshes both directions in one shot`() {
        val manager = AudioDeviceManager(context)
        assertTrue(manager.inputDevices.value.isEmpty())
        assertTrue(manager.outputDevices.value.isEmpty())

        // 一次输出设备变化 → 回调同时重算两向（§5.3；输入列表无变化、输出新增一台）
        shadowOf(audioManager).addOutputDevice(device(AudioDeviceInfo.TYPE_BUILTIN_SPEAKER), true)

        assertEquals(1, manager.outputDevices.value.size)
        assertTrue(manager.inputDevices.value.isEmpty())
    }
}
