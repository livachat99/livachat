package dev.livachat.app.ui.components

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.R
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ErrorBannerTest {

    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun recoverable_banner_shows_retry_and_close_buttons() {
        composeTestRule.setContent {
            LivaChatTheme {
                ErrorBanner(
                    message = BannerMessage("网络异常"),
                    onDismiss = {},
                    onRetry = {},
                )
            }
        }

        composeTestRule.onNodeWithText("网络异常").assertIsDisplayed()
        composeTestRule.onNodeWithContentDescription(context.getString(R.string.common_retry)).assertIsDisplayed()
        composeTestRule.onNodeWithContentDescription(context.getString(R.string.common_close)).assertIsDisplayed()
    }

    @Test
    fun fatal_banner_does_not_show_retry_button() {
        composeTestRule.setContent {
            LivaChatTheme {
                ErrorBanner(
                    message = BannerMessage("服务器错误", recoverable = false),
                    onDismiss = {},
                )
            }
        }

        composeTestRule.onNodeWithText("服务器错误").assertIsDisplayed()
        composeTestRule.onNodeWithContentDescription(context.getString(R.string.common_close)).assertIsDisplayed()
    }

    @Test
    fun close_button_calls_onDismiss() {
        var dismissed = false
        composeTestRule.setContent {
            LivaChatTheme {
                ErrorBanner(
                    message = BannerMessage("test"),
                    onDismiss = { dismissed = true },
                )
            }
        }

        composeTestRule.onNodeWithContentDescription(context.getString(R.string.common_close)).performClick()
        assertTrue(dismissed)
    }

    @Test
    fun null_message_hides_banner() {
        composeTestRule.setContent {
            LivaChatTheme {
                ErrorBanner(
                    message = null,
                    onDismiss = {},
                )
            }
        }

        composeTestRule.onNodeWithText("网络异常").assertIsNotDisplayed()
    }

    @Test
    fun retry_button_calls_onRetry() {
        var retried = false
        composeTestRule.setContent {
            LivaChatTheme {
                ErrorBanner(
                    message = BannerMessage("test", recoverable = true),
                    onDismiss = {},
                    onRetry = { retried = true },
                )
            }
        }

        composeTestRule.onNodeWithContentDescription(context.getString(R.string.common_retry)).performClick()
        assertTrue(retried)
    }
}
