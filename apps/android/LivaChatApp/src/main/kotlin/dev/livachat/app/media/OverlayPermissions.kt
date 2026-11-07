package dev.livachat.app.media

import android.content.Context
import android.provider.Settings
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 悬浮窗特殊权限判定（docs/android_screen_recording.md §3）。
 *
 * `SYSTEM_ALERT_WINDOW` 非运行时权限：只能跳系统设置页由用户开启，故本类只承担
 * 判定这一唯一入口（跳转 Intent 的构造归 Screen，UI 导航职责）；VM 单测可 mock。
 * Go 版本设备上 `canDrawOverlays()` 恒 false（平台限制，§3.9）。
 */
@Singleton
class OverlayPermissions @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    fun canDraw(): Boolean = Settings.canDrawOverlays(context)
}
