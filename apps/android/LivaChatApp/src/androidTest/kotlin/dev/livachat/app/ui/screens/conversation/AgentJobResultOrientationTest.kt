package dev.livachat.app.ui.screens.conversation

import android.content.pm.ActivityInfo
import androidx.activity.ComponentActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.ui.theme.LivaChatTheme
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 覆盖层的方向效应（docs/android_agent_job_view.md §6）：组合期间把 Activity 锁到
 * `FULL_SENSOR`（横竖屏自动切换），离场恢复进入前的值。
 *
 * 仅属性级断言：测试用 [ComponentActivity] 没有 MainActivity 的清单配置，真旋转会走
 * 「销毁重建」路径，与生产（configChanges 不重建）非同一条路径——真机旋转行为归手工验证；
 * 清单声明本身由 `MainActivityManifestTest` 守护。
 */
@RunWith(AndroidJUnit4::class)
class AgentJobResultOrientationTest {

    @get:Rule
    val rule = createAndroidComposeRule<ComponentActivity>()

    private var showOverlay by mutableStateOf(false)

    @Test
    fun fullSensorWhileComposed_restoredOnExit() {
        val orientationBefore = rule.activity.requestedOrientation

        rule.setContent {
            LivaChatTheme {
                if (showOverlay) {
                    AgentJobResultOverlay(
                        text = "结果正文",
                        timestamp = Instant.now(),
                        onDismiss = {},
                        onLinkClick = {},
                    )
                }
            }
        }
        rule.waitForIdle()
        assertEquals(orientationBefore, rule.activity.requestedOrientation)

        rule.runOnUiThread { showOverlay = true }
        rule.waitForIdle()
        assertEquals(
            "覆盖层打开期间应解锁到 FULL_SENSOR",
            ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR,
            rule.activity.requestedOrientation,
        )

        rule.runOnUiThread { showOverlay = false }
        rule.waitForIdle()
        assertEquals(
            "覆盖层离场应恢复进入前的方向",
            orientationBefore,
            rule.activity.requestedOrientation,
        )
    }
}
