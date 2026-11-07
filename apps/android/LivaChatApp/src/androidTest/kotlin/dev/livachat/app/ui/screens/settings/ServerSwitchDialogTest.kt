package dev.livachat.app.ui.screens.settings

import android.accessibilityservice.AccessibilityService
import android.app.Activity
import android.view.inputmethod.InputMethodManager
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import dev.livachat.app.R
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 服务器配置保存弹窗（文案泛化 Q2；docs/setting_server_address.md §3.4）：
 * 正文单句、仅「退出」单按钮、返回键不可绕过、点「退出」触发回调。
 * 视觉项（标题 16sp 居中、按钮水平居中）由真机截图核验。
 */
@RunWith(AndroidJUnit4::class)
class ServerSwitchDialogTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun showDialog(onExit: () -> Unit = {}) {
        composeRule.setContent {
            LivaChatTheme {
                ServerSwitchDialog(onExit = onExit)
            }
        }
    }

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)

    @Test
    fun showsSingleSentenceBodyAndSingleExitButton() {
        showDialog()
        composeRule.onNodeWithText(str(R.string.settings_server_updated)).assertIsDisplayed()
        composeRule.onNodeWithText(str(R.string.settings_restart_to_apply)).assertIsDisplayed()
        // 旧文案（切换语义 / 含地址句）已不复存在
        composeRule.onAllNodesWithText("切换服务器").assertCountEquals(0)
        composeRule.onAllNodesWithText("服务器已切换至「局域网服务器」，重新打开 App 后生效。")
            .assertCountEquals(0)
        // 仅保留「退出」
        composeRule.onNodeWithText(str(R.string.settings_exit)).assertIsDisplayed()
        composeRule.onAllNodesWithText("稍后").assertCountEquals(0)
    }

    /** 结束前一个用例可能残留的输入法焦点（全量套件里会抢走目标窗口）。 */
    private fun hideImeIfAny() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        var activity: Activity? = null
        instrumentation.runOnMainSync {
            activity = ActivityLifecycleMonitorRegistry.getInstance()
                .getActivitiesInStage(Stage.RESUMED).firstOrNull()
        }
        val resumed = activity ?: return
        instrumentation.runOnMainSync {
            resumed.getSystemService(InputMethodManager::class.java)
                ?.hideSoftInputFromWindow(resumed.window.decorView.windowToken, 0)
        }
    }

    @Test
    fun backPressDoesNotDismiss() {
        showDialog()
        hideImeIfAny()
        composeRule.waitForIdle()
        // AlertDialog 是独立窗口（持有焦点），Espresso.pressBack 挑应用主窗口根会因无焦点超时；
        // 用系统级返回投递到当前焦点窗口，等价于真机返回键。
        val backSent = InstrumentationRegistry.getInstrumentation().uiAutomation
            .performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
        assertTrue("系统返回事件应投递成功", backSent)
        InstrumentationRegistry.getInstrumentation().waitForIdleSync()
        composeRule.waitForIdle()
        composeRule.onNodeWithText(str(R.string.settings_server_updated)).assertIsDisplayed()
    }

    @Test
    fun exitButtonInvokesCallback() {
        var exits = 0
        showDialog(onExit = { exits++ })
        composeRule.onNodeWithText(str(R.string.settings_exit)).performClick()
        composeRule.waitForIdle()
        assertEquals(1, exits)
    }
}
