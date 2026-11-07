package dev.livachat.app

import android.content.ComponentName
import android.content.Context
import android.content.pm.ActivityInfo
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 清单声明回归（docs/android_agent_job_view.md §4.4/§6）：agent job 结果全屏查看期间运行时解锁旋转，
 * 依赖 MainActivity 声明 `configChanges` 让旋转不重建 Activity；同时基础竖屏锁必须保持。
 */
@RunWith(AndroidJUnit4::class)
class MainActivityManifestTest {

    private fun mainActivityInfo(): ActivityInfo {
        val context = ApplicationProvider.getApplicationContext<Context>()
        return context.packageManager.getActivityInfo(
            ComponentName(context, "dev.livachat.app.MainActivity"),
            0,
        )
    }

    @Test
    fun mainActivity_declaresConfigChangesForRotation() {
        val expected = ActivityInfo.CONFIG_ORIENTATION or
            ActivityInfo.CONFIG_SCREEN_SIZE or
            ActivityInfo.CONFIG_SCREEN_LAYOUT or
            ActivityInfo.CONFIG_SMALLEST_SCREEN_SIZE or
            ActivityInfo.CONFIG_KEYBOARD_HIDDEN

        val actual = mainActivityInfo().configChanges

        assertEquals(
            "MainActivity 必须声明 configChanges（否则旋转重建 Activity）",
            expected,
            actual and expected,
        )
    }

    @Test
    fun mainActivity_staysPortraitLocked() {
        assertEquals(
            "基础方向锁必须保持竖屏（仅全屏查看期间运行时解锁）",
            ActivityInfo.SCREEN_ORIENTATION_PORTRAIT,
            mainActivityInfo().screenOrientation,
        )
    }
}
