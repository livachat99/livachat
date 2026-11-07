package dev.livachat.app.ui.screens.settings

import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 紧凑输入框空值占位（docs/setting_server_address.md §10.3 D1）：空值显示占位文案
 * （地址 / 密钥两文案），输入后有值即隐。边框随聚焦变色的视觉项由真机截图核验。
 */
@RunWith(AndroidJUnit4::class)
class CompactFieldTest {

    @get:Rule
    val composeRule = createComposeRule()

    private val fieldTag = "compactField"

    private fun show(placeholder: String) {
        composeRule.setContent {
            LivaChatTheme {
                var value by remember { mutableStateOf("") }
                CompactField(
                    value = value,
                    onValueChange = { value = it },
                    placeholder = placeholder,
                    keyboardOptions = KeyboardOptions.Default,
                    trailing = {},
                    modifier = Modifier.testTag(fieldTag),
                )
            }
        }
    }

    @Test
    fun emptyValue_showsAddressPlaceholder() {
        show("ip:port 或 域名:port")
        composeRule.onNodeWithText("ip:port 或 域名:port").assertIsDisplayed()
    }

    @Test
    fun emptyValue_showsTokenPlaceholder() {
        show("查看 server 配置文件获取")
        composeRule.onNodeWithText("查看 server 配置文件获取").assertIsDisplayed()
    }

    @Test
    fun typedValue_hidesPlaceholder() {
        show("ip:port 或 域名:port")
        composeRule.onNodeWithTag(fieldTag).performTextInput("example.com:9000")
        composeRule.waitForIdle()
        composeRule.onNodeWithText("ip:port 或 域名:port").assertDoesNotExist()
        // 用存在性断言：performTextInput 会拉起 IME，API 36 下测试 Activity 的窗口 inset/可见矩形
        // 在输入法动画期间不稳定，assertIsDisplayed 会误报（API 31 真机与 Settings 页手工验证均正常）。
        composeRule.onNodeWithText("example.com:9000").assertExists()
    }
}
