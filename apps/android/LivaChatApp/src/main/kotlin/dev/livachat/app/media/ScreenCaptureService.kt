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
 * 屏幕录制前台服务薄壳（docs/android_screen_recording.md §4.3）。
 *
 * 唯一职责是满足 targetSdk 34 的 FGS 硬性要求：以
 * foregroundServiceType="mediaProjection" 调 startForeground()，
 * 之后把控制权交给 [ScreenCaptureController]（同进程单例直调）。
 */
@AndroidEntryPoint
class ScreenCaptureService : Service() {

    @Inject lateinit var controller: ScreenCaptureController

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // startForeground 必须在 startForegroundService 后 ~5s 内调用；
        // 同步完成，无 await，天然满足平台限制。
        startForegroundCompat()
        // 上报本实例的代次：controller 据此校验回调归属，
        // 防 stop→start 竞态下旧 service 实例的 ready 误消费新一轮授权 token
        controller.onForegroundServiceReady(intent?.getIntExtra(EXTRA_GENERATION, -1) ?: -1)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        controller.onForegroundServiceDestroyed()
    }

    private fun startForegroundCompat() {
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, getString(R.string.notif_screen_channel), NotificationManager.IMPORTANCE_LOW),
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
            .setSmallIcon(R.drawable.ic_notification_record)
            .setContentTitle(getString(R.string.notif_screen_title))
            .setContentText(getString(R.string.notif_screen_text))
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "screen_capture"
        private const val NOTIFICATION_ID = 1001

        // start()/自愈重启时由 controller 写入，标记该次启动的代次（见 onStartCommand）
        const val EXTRA_GENERATION = "livachat_screen_capture_generation"
    }
}
