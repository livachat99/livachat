package dev.livachat.app.media

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.domain.model.AudioRoute
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AudioRouteManager @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val audioManager: AudioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    private val audioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()

    private var focusRequest: AudioFocusRequest? = null
    private var hasFocus = false

    fun switchRoute(route: AudioRoute): Boolean {
        val ok = when (route) {
            // 2026-10-11 用户决策（D10 定案）：恒置 isSpeakerphoneOn=true 保持删除——本处不写
            // speakerphoneOn："默认=扬声器"由 AudioPlaybackController 的 D10 默认选择规则以 track 级
            // setPreferredDevice 钉住（仅内置时显式选扬声器；真机探针：无 force 下 preferred 即路由
            // 扬声器，且 FORCE_SPEAKER 常驻会阻碍 SCO 建链、压过平台对外设的自动仲裁）。
            // 保留 MODE_IN_COMMUNICATION 与其余分支。
            AudioRoute.SPEAKER -> true
            AudioRoute.EARPIECE -> {
                audioManager.isSpeakerphoneOn = false
                true
            }
            AudioRoute.BLUETOOTH -> {
                if (isBluetoothAvailable()) {
                    audioManager.isSpeakerphoneOn = false
                    true
                } else {
                    false
                }
            }
        }
        if (ok) {
            audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        }
        return ok
    }

    fun isBluetoothAvailable(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return false
        val devices = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        return devices.any { device ->
            device.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
                device.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
                device.type == AudioDeviceInfo.TYPE_BLE_HEADSET
        }
    }

    fun requestAudioFocus(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE)
                .setAudioAttributes(audioAttributes)
                .setOnAudioFocusChangeListener { focusChange ->
                    hasFocus = focusChange == AudioManager.AUDIOFOCUS_GAIN
                }
                .build()
            focusRequest = request
            val result = audioManager.requestAudioFocus(request)
            hasFocus = result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
            return hasFocus
        } else {
            @Suppress("DEPRECATION")
            val result = audioManager.requestAudioFocus(
                null,
                AudioManager.STREAM_VOICE_CALL,
                AudioManager.AUDIOFOCUS_GAIN,
            )
            hasFocus = result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
            return hasFocus
        }
    }

    fun abandonAudioFocus() {
        focusRequest?.let {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                audioManager.abandonAudioFocusRequest(it)
            }
        }
        focusRequest = null
        hasFocus = false
        audioManager.mode = AudioManager.MODE_NORMAL
    }
}
