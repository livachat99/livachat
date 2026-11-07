package dev.livachat.app.media

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import dagger.hilt.android.AndroidEntryPoint
import dev.livachat.app.MainActivity
import dev.livachat.app.R
import javax.inject.Inject

/**
 * 麦克风前台服务薄壳（docs/android_background_support.md §5.1）。
 *
 * 唯一职责是满足 targetSdk 34 的 while-in-use 麦克风豁免要求：以
 * foregroundServiceType="microphone" 调 startForeground()，让后台录音
 * 不被系统喂全零静音数据；采集控制仍由 [AudioCaptureController]
 * （同进程单例直调）驱动。
 */
@AndroidEntryPoint
class MicCaptureService : Service() {

    @Inject lateinit var controller: AudioCaptureController

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 守卫：迟到的启动（start→stop 竞态下 stopService 早于本回调、系统重放）若录音已停
        // （_isCapturing=false），立即自杀——否则 startForeground 产生孤儿 FGS + 通知残留。
        // controller 与 service 同进程单例，查询无跨进程失真。
        if (!controller.isCapturing.value) {
            stopSelf()
            return START_NOT_STICKY
        }
        // startForeground 必须在 startForegroundService 后 ~5s 内调用；
        // 同步完成，无 await，天然满足平台限制。
        try {
            startForegroundCompat()
        } catch (_: Exception) {
            // API 34 while-in-use：startForeground 调用时刻 app 已退后台即抛
            // SecurityException（controller 侧 catch 接不到这里），无法成为前台 FGS——
            // 主动自杀降级（录音继续但本次失去豁免），避免被系统判超时杀
            stopSelf()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        // 仅系统回收路径会走到这里（正常 stop 已先清 controller 标志），
        // controller 据此决定是否重启保持豁免（见 onMicServiceDestroyed）
        controller.onMicServiceDestroyed()
    }

    private fun startForegroundCompat() {
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, getString(R.string.notif_mic_channel), NotificationManager.IMPORTANCE_LOW),
            )
        }
        // 通知由系统进程代为启动 Activity（非 Activity 上下文），必须带 NEW_TASK，
        // 否则点击通知抛 "Calling startActivity() from outside of an Activity context"
        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_SINGLE_TOP or
                    Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification_mic)
            .setContentTitle(getString(R.string.notif_mic_title))
            .setContentText(getString(R.string.notif_mic_text))
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "microphone"
        // 录屏 FGS 已占 1001；两 FGS 并存时不同 ID 保证通知不互相覆盖
        private const val NOTIFICATION_ID = 1002
    }
}
