package dev.livachat.app.media

import android.content.Context
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 音频设备管理（docs/android_audio_input_devices.md §3）：输入/输出设备的检测、枚举与状态广播。
 * 同一 AudioDeviceCallback 刷新两侧列表；输入侧消费方见输入设备文档，输出侧见
 * docs/android_audio_output_devices.md。
 */
@Singleton
class AudioDeviceManager @Inject constructor(
    @ApplicationContext context: Context,
) {
    private val audioManager: AudioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    private val _inputDevices = MutableStateFlow<List<AudioInputDevice>>(emptyList())

    /** 输入侧对外产物（UI/采集消费面）。 */
    val inputDevices: StateFlow<List<AudioInputDevice>> = _inputDevices.asStateFlow()

    private val _outputDevices = MutableStateFlow<List<AudioOutputDevice>>(emptyList())

    /** 输出侧对外产物（docs/android_audio_output_devices.md §3）。 */
    val outputDevices: StateFlow<List<AudioOutputDevice>> = _outputDevices.asStateFlow()

    init {
        // 进程级注册、不注销。回调不区分输入/输出方向（变化的是设备集合）——
        // 两向均刷新（整体重算，规模 ≤ 个位数）。
        audioManager.registerAudioDeviceCallback(
            object : AudioDeviceCallback() {
                override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>) {
                    refreshInputs()
                    refreshOutputs()
                }

                override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>) {
                    refreshInputs()
                    refreshOutputs()
                }
            },
            Handler(Looper.getMainLooper()),
        )
        // 初始快照的唯一同步保证（非冗余）：生产平台注册回调是 post 到 handler 的异步投递，首帧不能等回调。
        refreshInputs()
        refreshOutputs()
    }

    private fun refreshInputs() {
        _inputDevices.value = audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS)
            .filter { it.type in ALLOWED_INPUT_TYPES }
            .map {
                AudioInputDevice(
                    id = it.id,
                    type = it.type,
                    productName = it.productName?.toString(),
                    address = if (Build.VERSION.SDK_INT >= 28) it.address else null,
                )
            }
    }

    private fun refreshOutputs() {
        _outputDevices.value = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            .filter { it.type in ALLOWED_OUTPUT_TYPES }
            .map {
                AudioOutputDevice(
                    id = it.id,
                    type = it.type,
                    productName = it.productName?.toString(),
                    address = if (Build.VERSION.SDK_INT >= 28) it.address else null,
                )
            }
    }

    companion object {
        // 明确麦克风语义的类型白名单（docs/android_audio_input_devices.md §2.2）：名单外（TELEPHONY/REMOTE_SUBMIX/HDMI…
        // 真机实测混入项，docs/android_audio_input_devices.md §2.2）一律不展示。TYPE_BLE_HEADSET 为编译期内联常量（值 26），
        // <31 平台不会产出该值，无需运行期保护。
        private val ALLOWED_INPUT_TYPES = setOf(
            AudioDeviceInfo.TYPE_BUILTIN_MIC,
            AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
            AudioDeviceInfo.TYPE_BLE_HEADSET,
            AudioDeviceInfo.TYPE_USB_DEVICE,
            AudioDeviceInfo.TYPE_USB_HEADSET,
        )

        // 输出白名单（docs/android_audio_output_devices.md §2.2）：明确"用户可选出声口"语义。
        // 排除项依据真机实测（§9.1）：TYPE_DOCK（幻影底座条目）、TYPE_TELEPHONY（通话下行）。
        // 【2026-10-11 决策更新（D3 修订）】：TYPE_BLUETOOTH_A2DP 定案**保留**——真机实验证实其
        // 作为播放出口可用且可听（A2DP_SOFTWARE_ENCODING_DATAPATH STARTED，§11 增补）；与 SCO
        // 同名双行按 D5=B 加类型后缀区分（labeledOutputDevices）。
        private val ALLOWED_OUTPUT_TYPES = setOf(
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
            AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
            AudioDeviceInfo.TYPE_BLE_HEADSET,
            AudioDeviceInfo.TYPE_BLE_SPEAKER,
            AudioDeviceInfo.TYPE_USB_DEVICE,
            AudioDeviceInfo.TYPE_USB_HEADSET,
            AudioDeviceInfo.TYPE_HEARING_AID,
        )
    }
}
